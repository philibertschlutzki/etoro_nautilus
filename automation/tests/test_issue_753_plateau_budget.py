"""Issue #753 (P0) — Der Plateau-Guard beendet die Study exakt an der TPE-Startup-Grenze.

Root-Cause: `floor_plateau_callback` band den ZERO_ELIGIBLE-Abbruch an dieselbe Schwelle
(`n_startup_trials + floor_plateau_k`, Default `floor_plateau_k=0`), ab der `TPESampler
(n_startup_trials=...)` erst zu MODELLIEREN beginnt. Die ersten `n_startup_trials` Trials sind reine
Zufallsziehungen; der Guard toetete die Study exakt an dem Punkt, an dem die Bayes-Optimierung
beginnen wuerde — "0 von n_startup_trials Zufallsziehungen feasible" ist KEINE Aussage ueber den
Suchraum (die feasible Region ist per Konstruktion klein).

Fix: zwei getrennte Schwellen. `min_for_structural` (Datengeometrie/Trade-Frequenz, seit #805
`n_startup_trials + derive_structural_min_modelled_trials(...)` statt der entfernten flachen
`floor_plateau_k`-Konstante — siehe test_issue_805_structural_min_modelled.py fuer die #805-Migration
dieser Schwelle) und `min_for_zero_eligible` (Signalqualitaet, NEU `n_startup_trials +
plateau_min_modelled_trials`, Default-Fallback `max(32, 2*n_startup_trials)`).
Die ZERO_ELIGIBLE-Abbruchentscheidung wird ausschliesslich ueber die MODELLIERTEN Trials (Index >=
n_startup_trials) getroffen — die Zufalls-Startup-Phase darf das Urteil nicht mitbestimmen.
"""
import logging

import optuna

from automation.optimizer import run_optimization as ro


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


def _quiet_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    return lg


def _non_eligible_trials(n):
    return [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8)
            for _ in range(n)]


# ── Akzeptanzkriterium 1: bei 16/32/63 abgeschlossenen Trials kein Stop, bei 64 doch ─────────────
def test_zero_eligible_no_stop_below_budget_stop_at_budget():
    W = {"n_startup_trials": 16, "plateau_min_modelled_trials": 48}
    for n in (16, 32, 63):
        study = _FakeStudy(_non_eligible_trials(n))
        ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=16,
                                  logger=_quiet_logger(f"t753_n{n}"), stop_on_plateau=True)
        assert study.stopped is False, f"durfte bei {n} Trials (< 16+48=64) nicht stoppen"

    study64 = _FakeStudy(_non_eligible_trials(64))
    ro.floor_plateau_callback(study64, study64.trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t753_n64"), stop_on_plateau=True)
    assert study64.stopped is True, "musste bei 64 Trials (== 16+48) stoppen"


# ── Akzeptanzkriterium 2: STRUCTURAL_ALL_UNEVALUABLE bleibt unbeeinflusst von plateau_min_modelled_trials
def test_structural_all_unevaluable_unaffected_by_plateau_min_modelled_trials():
    # Issue #805 — floor_plateau_k entfernt; structural_min_modelled_trials_per_dim=2 (ohne
    # strategy=... uebergeben ⇒ flacher Zuschlag ceil(2)=2) haelt denselben Test-Aufbau lebendig
    # (n_startup_trials + Zuschlag = 3+2 = 5), nur der Zuschlag darf seit #805 nie mehr 0 sein.
    W = {"n_startup_trials": 3, "structural_min_modelled_trials_per_dim": 2,
         # Absichtlich riesig — darf den STRUCTURAL-Zweig NICHT beeinflussen.
         "plateau_min_modelled_trials": 10_000}
    unevaluable = [_FakeTrial(-9.85, oos_evaluated=False) for _ in range(4)]
    study_below = _FakeStudy(unevaluable)
    ro.floor_plateau_callback(study_below, study_below.trials[-1], weights=W, n_startup_trials=3,
                              logger=_quiet_logger("t753_struct_below"), stop_on_plateau=True)
    assert study_below.stopped is False

    at_threshold = [_FakeTrial(-9.85, oos_evaluated=False) for _ in range(5)]
    study_at = _FakeStudy(at_threshold)
    ro.floor_plateau_callback(study_at, study_at.trials[-1], weights=W, n_startup_trials=3,
                              logger=_quiet_logger("t753_struct_at"), stop_on_plateau=True)
    assert study_at.stopped is True, (
        "STRUCTURAL_ALL_UNEVALUABLE muss weiterhin bei n_startup+structural_extra stoppen")


# ── Akzeptanzkriterium 3: p_eligible ignoriert die Startup-Kohorte ────────────────────────────────
def test_zero_eligible_decision_ignores_eligible_startup_trials():
    """Fixture: die ersten n_startup_trials Trials sind ALLE eligible (reiner Zufallstreffer der
    Startup-Phase), alle MODELLIERTEN Trials sind non-eligible. Die alte Voll-Kohorten-Logik haette
    das NICHT als Zero-Eligible-Plateau erkannt (es gibt ja eligible Trials im Cohort) — die neue,
    ausschliesslich auf den modellierten Trials basierende Entscheidung MUSS trotzdem abbrechen."""
    W = {"n_startup_trials": 16, "plateau_min_modelled_trials": 0}
    startup_all_eligible = [
        _FakeTrial(1.0, oos_evaluated=True, oos_eligible=True, oos_total_trades=30)
        for _ in range(16)
    ]
    modelled_all_non_eligible = _non_eligible_trials(16)
    study = _FakeStudy(startup_all_eligible + modelled_all_non_eligible)
    ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t753_ignore_startup"), stop_on_plateau=True)
    assert study.stopped is True
    assert study.user_attrs.get("zero_eligible_plateau_warned") is True


def test_zero_eligible_decision_not_triggered_by_eligible_modelled_trial():
    """Gegenprobe: liegt der eligible Trial IN der modellierten Kohorte, darf kein Abbruch erfolgen —
    unabhaengig davon, dass die Startup-Kohorte 0 eligible Trials enthielt."""
    W = {"n_startup_trials": 16, "plateau_min_modelled_trials": 0}
    startup_all_non_eligible = _non_eligible_trials(16)
    modelled_with_one_eligible = _non_eligible_trials(15) + [
        _FakeTrial(1.0, oos_evaluated=True, oos_eligible=True, oos_total_trades=30)
    ]
    study = _FakeStudy(startup_all_non_eligible + modelled_with_one_eligible)
    ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t753_modelled_has_eligible"), stop_on_plateau=True)
    assert study.stopped is False
    assert study.user_attrs.get("zero_eligible_plateau_warned") is None


# ── Akzeptanzkriterium 4: fehlender Key ⇒ Fallback max(32, 2*n_startup_trials), nicht 0 ──────────
def test_missing_plateau_min_modelled_trials_falls_back_to_max_32_or_2x_startup():
    W = {"n_startup_trials": 10}  # kein plateau_min_modelled_trials-Key
    below_fallback = _non_eligible_trials(41)   # 10 + max(32, 20) = 42
    study_below = _FakeStudy(below_fallback)
    ro.floor_plateau_callback(study_below, study_below.trials[-1], weights=W, n_startup_trials=10,
                              logger=_quiet_logger("t753_fallback_below"), stop_on_plateau=True)
    assert study_below.stopped is False

    at_fallback = _non_eligible_trials(42)
    study_at = _FakeStudy(at_fallback)
    ro.floor_plateau_callback(study_at, study_at.trials[-1], weights=W, n_startup_trials=10,
                              logger=_quiet_logger("t753_fallback_at"), stop_on_plateau=True)
    assert study_at.stopped is True


def test_invalid_plateau_min_modelled_trials_value_falls_back_too():
    W = {"n_startup_trials": 10, "plateau_min_modelled_trials": "not-a-number"}
    at_fallback = _non_eligible_trials(42)
    study = _FakeStudy(at_fallback)
    ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=10,
                              logger=_quiet_logger("t753_fallback_invalid"), stop_on_plateau=True)
    assert study.stopped is True


# ── STUDY_EARLY_STOP-Event traegt n_modelled_trials/min_constraint_violation_* ────────────────────
def test_study_early_stop_event_carries_modelled_trial_diagnostics():
    events = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            if "[JSON_EVENT]" in msg:
                import json
                events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))

    lg = logging.getLogger("t753_event")
    lg.handlers.clear()
    lg.addHandler(_CaptureHandler())
    lg.setLevel(logging.INFO)
    lg.propagate = False

    W = {"n_startup_trials": 16, "plateau_min_modelled_trials": 0}
    study = _FakeStudy(_non_eligible_trials(20))
    ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=16,
                              logger=lg, stop_on_plateau=True)

    early_stop = [e for e in events if e.get("event_type") == "STUDY_EARLY_STOP"]
    assert len(early_stop) == 1
    assert early_stop[0]["n_modelled_trials"] == 4
    assert "min_constraint_violation_first" in early_stop[0]
    assert "min_constraint_violation_last" in early_stop[0]
