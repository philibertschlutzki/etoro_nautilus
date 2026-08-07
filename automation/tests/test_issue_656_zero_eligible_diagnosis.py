"""Issue #656 — AdxAtr/TrendPullback: 0 eligible Trials trotz vier toter Gates — Suchraum-Diagnose.

Beide Strategien erzeugten über die GESAMTE Study NULL eligible Trials — obwohl die Hürde durch die
vier toten Gates (#649) niedriger als beabsichtigt war. Nach dem #649-Fix wird die Hürde HÖHER —
diese Strategien liefen dann garantiert leer, es sei denn, die Ursache ist Suchraum-seitig.

Fix: `floor_plateau_callback` erkennt jetzt ZUSÄTZLICH zum #413-„kein Trial je evaluable"-Guard ein
strukturell ANDERES Kollaps-Muster: ALLE Trials wurden evaluiert (echte OOS-Backtests liefen durch),
aber KEINER war je `oos_eligible` — der Suchraum erzeugt strukturell keinen eligiblen Lauf. Der Guard
warnt (mit Trade-Count-/Boundary-Diagnose) und beendet die Study früh (`stop_on_plateau`), statt die
restlichen Trials nutzlos durchlaufen zu lassen (analog zum #409/#413-Floor-Guard).
"""
import logging

import optuna

from automation.optimizer import run_optimization as ro

# Issue #753 — plateau_min_modelled_trials=0 haelt die ZERO_ELIGIBLE-Abbruchschwelle bei den fuer
# dieses Testmodul urspruenglich gewaehlten Kohortengroessen (n_startup_trials, kein Zusatzbudget) —
# diese Tests pruefen die KLASSIFIKATION (mixed/homogener Cohort), nicht die #753-Budget-Schwelle
# selbst (dafuer siehe test_issue_753_plateau_budget.py).
W = {"penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25, "n_startup_trials": 16,
     "plateau_min_modelled_trials": 0}


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


def test_zero_eligible_plateau_fires_when_all_evaluated_but_none_eligible():
    """Kernfall (#656): alle Trials evaluiert, keiner eligible ⇒ WARNING (nicht der #413-Guard,
    der nur bei 'kein Trial je evaluable' feuert)."""
    lg, recs = _capturing_logger("test656a")
    trials = [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8)
              for _ in range(20)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg)
    assert _warned(recs, "Zero-Eligible-Plateau")
    assert not _warned(recs, "Floor-Plateau")   # der #413-Guard darf NICHT mitfeuern


def test_no_warning_when_some_trial_eligible():
    """Mindestens ein eligibler Trial ⇒ kein struktureller Kollaps, kein Warning."""
    lg, recs = _capturing_logger("test656b")
    trials = [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8)
              for _ in range(19)]
    trials.append(_FakeTrial(1.2, oos_evaluated=True, oos_eligible=True, oos_total_trades=30))
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg)
    assert not _warned(recs, "Zero-Eligible-Plateau")


def test_warns_once_per_study():
    lg, recs = _capturing_logger("test656c")
    trials = [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8)
              for _ in range(20)]
    study = _FakeStudy(trials)
    for _ in range(4):
        ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg)
    assert len(_warned(recs, "Zero-Eligible-Plateau")) == 1


def test_stop_on_plateau_ends_study_early():
    """Akzeptanzkriterium (#656): ein strukturell 0-eligibler Suchraum wird früh beendet, statt
    die restlichen Trials nutzlos durchlaufen zu lassen (analog #456)."""
    lg, recs = _capturing_logger("test656d")
    trials = [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8)
              for _ in range(20)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg,
                             stop_on_plateau=True)
    assert study.stopped is True


def test_diagnosis_reports_median_trade_count_and_boundary_hits():
    """Diagnose-Telemetrie (#656-Akzeptanzkriterium): Trade-Count-Verteilung + Trade-Cap-Treffer
    müssen im Warning sichtbar sein, damit ein Mensch Bounds vs. strukturellen Skip entscheiden kann."""
    lg, recs = _capturing_logger("test656e")
    trials = [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False,
                         oos_total_trades=5, hit_trade_cap=True)
              for _ in range(20)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg)
    msgs = _warned(recs, "Zero-Eligible-Plateau")
    assert msgs
    assert "median oos_total_trades=5" in msgs[0].getMessage()
    # Issue #972 — Nenner ist jetzt len(completed) (ALLE Trials), nicht n_evaluated (die
    # Ueberlebenden); hier sind beide Zahlen zufaellig gleich (homogene Kohorte).
    assert "20/20 ALLER Trials trafen die Haltedauer-/Trade-Cap-Grenze" in msgs[0].getMessage()


def test_silent_before_startup_threshold():
    lg, recs = _capturing_logger("test656f")
    trials = [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8)
              for _ in range(3)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16, logger=lg)
    assert not _warned(recs, "Zero-Eligible-Plateau")
