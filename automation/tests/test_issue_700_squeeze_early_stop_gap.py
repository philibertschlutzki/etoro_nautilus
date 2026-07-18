"""Issue #700 (P2) — `SqueezeBreakout`: volles 180-Trial-Budget ohne Winner + Early-Stop-Lücke.

Root-Cause: der ZERO_ELIGIBLE_PLATEAU-Zweig in `floor_plateau_callback` verlangte STRIKT
`all(oos_evaluated is True)` über den gesamten Cohort. Ein GEMISCHTER Cohort (einige Trials
evaluable, einige nicht — z. B. durch Trade-Cap-Treffer/hohe Frequenz) fiel dadurch durch BEIDE
Netze: weder "kein Trial evaluable" (STRUCTURAL_ALL_UNEVALUABLE) noch "alle Trials evaluable, 0
eligible" (ZERO_ELIGIBLE_PLATEAU) — kein Early-Stop feuerte, das volle 180-Trial-Budget verbrannte.

Fix: die Bedingung ist jetzt `p_eligible(evaluierte Trials) == 0`, UNABHÄNGIG davon, ob ALLE oder
nur EIN TEIL der Trials evaluiert wurden — ob gestoppt wird, hängt nicht mehr an der binding_cause-
Klassifikation (die nur die URSACHE telemetriert). Zusätzlich: eine per-16-Trial-Fenster
p_eligible-Kurve (`sweep_diagnostics.eligibility_curve`) unterscheidet transiente von permanenter
Null-Eligibilität.
"""
import logging

import optuna

from automation.optimizer import run_optimization as ro
from automation.optimizer.sweep_diagnostics import eligibility_curve

W = {"penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25, "n_startup_trials": 16}


class _FakeTrial:
    def __init__(self, value, *, oos_evaluated=None, oos_eligible=None,
                 oos_total_trades=None, hit_trade_cap=None,
                 state=optuna.trial.TrialState.COMPLETE):
        self.value = value
        self.state = state
        self.user_attrs = {}
        if oos_evaluated is not None:
            self.user_attrs["oos_evaluated"] = oos_evaluated
        if oos_eligible is not None:
            self.user_attrs["oos_eligible"] = oos_eligible
        if oos_total_trades is not None:
            self.user_attrs["oos_total_trades"] = oos_total_trades
        if hit_trade_cap is not None:
            self.user_attrs["hit_trade_cap"] = hit_trade_cap


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials
        self._attrs = {}
        self.stopped = False

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v

    def stop(self):
        self.stopped = True


def _capturing_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    recs = []

    class _H(logging.Handler):
        def emit(self, r):
            recs.append(r)

    lg.addHandler(_H())
    lg.setLevel(logging.WARNING)
    lg.propagate = False
    return lg, recs


def _warned(recs, needle):
    return [r for r in recs if needle in r.getMessage()]


# ── eligibility_curve: pure unit ─────────────────────────────────────────────────────────────────
def test_eligibility_curve_windows_of_16():
    trials = [{"oos_eligible": False}] * 16 + [{"oos_eligible": True}] * 8 + [{"oos_eligible": False}] * 16
    curve = eligibility_curve(trials, window=16)
    assert curve == [0.0, 0.5, 0.0]


def test_eligibility_curve_empty():
    assert eligibility_curve([], window=16) == []


def test_eligibility_curve_none_is_not_eligible():
    trials = [{"oos_eligible": None}] * 5
    assert eligibility_curve(trials, window=16) == [0.0]


# ── floor_plateau_callback: der geschlossene Lücken-Fall (Kernkriterium #700) ────────────────────
def test_mixed_cohort_with_zero_eligible_now_fires_where_it_previously_fell_through():
    """SqueezeBreakout-Analogon: ein GEMISCHTER Cohort (12 evaluable, 8 nicht-evaluable) mit 0
    eligiblen Trials unter den evaluierten — vorher fiel das durch BEIDE Netze (weder all(False)
    noch all(True) evaluated_flags), jetzt feuert ZERO_ELIGIBLE_PLATEAU korrekt."""
    lg, recs = _capturing_logger("test700a")
    trials = (
        [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8) for _ in range(12)]
        + [_FakeTrial(-9.8, oos_evaluated=False) for _ in range(8)]
    )
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg,
                              stop_on_plateau=True)
    assert _warned(recs, "Zero-Eligible-Plateau")
    assert not _warned(recs, "Floor-Plateau")
    assert study.stopped is True
    assert study.user_attrs.get("zero_eligible_plateau_warned") is True


def test_mixed_cohort_message_reports_evaluated_subset_counts():
    lg, recs = _capturing_logger("test700b")
    trials = (
        [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=5, hit_trade_cap=True)
         for _ in range(12)]
        + [_FakeTrial(-9.8, oos_evaluated=False) for _ in range(8)]
    )
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg)
    msgs = _warned(recs, "Zero-Eligible-Plateau")
    assert msgs
    msg = msgs[0].getMessage()
    assert "12/20 Trials wurden evaluiert" in msg
    assert "median oos_total_trades=5" in msg
    assert "12/12 Trials trafen die Haltedauer-/Trade-Cap-Grenze" in msg
    assert "p_eligible je 16-Trial-Fenster" in msg


def test_still_silent_when_no_trial_ever_reached_eligibility_determination():
    """Ein Cohort, in dem KEIN Trial je eine oos_eligible-Determination erreichte (z. B. ein
    unvollstaendiges Test-Fixture ohne echte Eligibility-Stempelung), darf NICHT als Zero-Eligible-
    Plateau fehlklassifiziert werden — es gibt schlicht keine Aussage ueber Eligibility."""
    lg, recs = _capturing_logger("test700c")
    trials = [_FakeTrial(-9.85, oos_evaluated=False),
              _FakeTrial(0.5, oos_evaluated=True),  # oos_eligible NIE gesetzt (kein echter Run)
              _FakeTrial(-9.9, oos_evaluated=False)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3, logger=lg,
                              stop_on_plateau=True)
    assert not _warned(recs, "Zero-Eligible-Plateau")
    assert not _warned(recs, "Floor-Plateau")
    assert study.stopped is False


def test_still_fires_for_homogeneous_all_evaluated_cohort_bit_identical():
    """Regression-Anker: der ALTE Kernfall (#656, ALLE Trials evaluiert, 0 eligible) bleibt
    unveraendert erkannt (Telemetrie bit-identisch fuer den homogenen Fall)."""
    lg, recs = _capturing_logger("test700d")
    trials = [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False,
                         oos_total_trades=8, hit_trade_cap=True)
              for _ in range(20)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg)
    msgs = _warned(recs, "Zero-Eligible-Plateau")
    assert msgs
    msg = msgs[0].getMessage()
    assert "20/20 Trials wurden evaluiert" in msg
    assert "median oos_total_trades=8" in msg
    assert "20/20 Trials trafen die Haltedauer-/Trade-Cap-Grenze" in msg


def test_still_silent_when_at_least_one_evaluated_trial_is_eligible_mixed_cohort():
    """Mindestens EIN eligibler Trial unter einem gemischten Cohort ⇒ kein struktureller Kollaps."""
    lg, recs = _capturing_logger("test700e")
    trials = (
        [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8) for _ in range(11)]
        + [_FakeTrial(1.2, oos_evaluated=True, oos_eligible=True, oos_total_trades=30)]
        + [_FakeTrial(-9.8, oos_evaluated=False) for _ in range(8)]
    )
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg)
    assert not _warned(recs, "Zero-Eligible-Plateau")


def test_event_payload_carries_p_eligible_windows_and_n_evaluated():
    events = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            if "[JSON_EVENT]" in msg:
                import json
                events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))

    lg = logging.getLogger("test700f")
    lg.handlers.clear()
    lg.addHandler(_CaptureHandler())
    lg.setLevel(logging.INFO)
    lg.propagate = False

    trials = (
        [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8) for _ in range(12)]
        + [_FakeTrial(-9.8, oos_evaluated=False) for _ in range(8)]
    )
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg,
                              stop_on_plateau=True)

    zep = [e for e in events if e.get("event_type") == "ZERO_ELIGIBLE_PLATEAU"]
    assert len(zep) == 1
    assert zep[0]["n_evaluated"] == 12
    # 20 completed Trials gesamt (12 evaluiert + 8 nicht), Fenster=16 ⇒ zwei Fenster [0:16], [16:20].
    assert zep[0]["p_eligible_windows"] == [0.0, 0.0]

    early_stop = [e for e in events if e.get("event_type") == "STUDY_EARLY_STOP"]
    assert len(early_stop) == 1
    assert early_stop[0]["reason"] == "STRUCTURAL_ZERO_ELIGIBLE"
