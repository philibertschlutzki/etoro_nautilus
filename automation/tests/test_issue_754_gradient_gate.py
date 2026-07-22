"""Issue #754 (P0) — Das Gradienten-Gate der Tier-Eskalation ist zirkulär.

Root-Cause: `study_shows_gradient_signal` maß Signal ausschließlich über die eligible Kohorte
(`feasible_rewards`). Nach #753 ist diese Kohorte in der Mehrheit der Studies leer, solange die
Suche noch läuft ⇒ `gradient_signal = False` ⇒ keine Tier-Eskalation — obwohl das Fehlen von
eligiblen Trials KEINE Aussage über die Sinnhaftigkeit von mehr Budget ist. Die Study braucht
eligible Trials, um mehr Budget zu bekommen, UND mehr Budget, um eligible Trials zu finden
(Selbstblockade).

Fix: ein zweiter, gleichrangiger Arm — Constraint-Fortschritt (`constraint_improvement_rate`, aus
`oos_constraint_violations` über die MODELLIERTEN Trials) — ist auch bei leerer feasibler Region
definiert. Ausserdem: eine per #753 vorzeitig gestoppte Study liefert `gradient_signal = None`
(unbekannt), nicht `False` (widerlegt) — das Gate wird erst NACH vollem Basisbudget ausgewertet.
"""
import json
import logging
import time
from pathlib import Path

import automation.optimizer.run_optimization as ro
from automation.optimizer.run_optimization import study_shows_gradient_signal, _emit_study_summary


# ── study_shows_gradient_signal: der neue Constraint-Arm (reine Funktion) ─────────────────────────
def test_constraint_arm_alone_triggers_signal_with_empty_feasible_region():
    """Leere feasible Region (keine Rewards) + klare Constraint-Annaeherung ⇒ Signal — vorher IMMER
    False, weil der reine Reward-Arm bei leerer Kohorte strukturell nie feuern kann."""
    assert study_shows_gradient_signal(
        [], evaluable_fraction=0.0, tau=1e-3,
        constraint_improvement_rate=0.3, tau_c=0.05) is True


def test_flat_constraint_progress_with_empty_feasible_region_no_signal():
    assert study_shows_gradient_signal(
        [], evaluable_fraction=0.0, tau=1e-3,
        constraint_improvement_rate=0.01, tau_c=0.05) is False


def test_missing_constraint_improvement_rate_falls_back_to_reward_arm_only():
    """Kein constraint_improvement_rate übergeben (None) ⇒ bit-identisches Verhalten zum #568-Status
    quo (nur der Reward-Arm zählt)."""
    assert study_shows_gradient_signal([5.0, 5.0, 5.0], evaluable_fraction=1.0, tau=1e-3) is False
    assert study_shows_gradient_signal([1.0, 3.0, 5.0], evaluable_fraction=1.0, tau=1e-3) is True


def test_either_arm_triggers_signal():
    """Reward-Arm ODER Constraint-Arm — beide gleichrangig, keiner dominiert den anderen."""
    assert study_shows_gradient_signal(
        [1.0, 3.0, 5.0], evaluable_fraction=1.0, tau=1e-3,
        constraint_improvement_rate=0.0, tau_c=0.05) is True
    assert study_shows_gradient_signal(
        [5.0, 5.0], evaluable_fraction=1.0, tau=1e-3,
        constraint_improvement_rate=0.3, tau_c=0.05) is True


# ── _emit_study_summary: gradient_signal ist tri-state (True/False/None) ──────────────────────────
class _T:
    def __init__(self, value, *, evaluated=True, eligible=False, constraint_violation=None, number=None):
        self.value = value
        self.number = number
        self.user_attrs = {"oos_evaluated": evaluated, "oos_eligible": eligible, "backtest_ms": 5}
        if constraint_violation is not None:
            self.user_attrs["oos_constraint_violations"] = (constraint_violation,)


class _S:
    def __init__(self, trials, *, early_stopped_key=None):
        self.study_name = "study_X"
        self.trials = trials
        self.best_value = max((t.value for t in trials), default=None)
        self._attrs = {}
        if early_stopped_key:
            self._attrs[early_stopped_key] = True

    @property
    def user_attrs(self):
        return dict(self._attrs)


def _capture(monkeypatch):
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    captured = []
    monkeypatch.setattr(ro, "emit_execution_event",
                        lambda logger, name, payload: captured.append((name, payload)))
    return captured


def test_early_stopped_study_yields_none_not_false(monkeypatch):
    """Akzeptanzkriterium: eine vorzeitig gestoppte Study (floor_plateau_warned) liefert
    gradient_signal=None, NICHT False — auch wenn Reward-/Constraint-Arme sonst False waeren."""
    captured = _capture(monkeypatch)
    trials = [_T(-0.5, evaluated=True, eligible=False, constraint_violation=1.0, number=i)
              for i in range(20)]
    study = _S(trials, early_stopped_key="zero_eligible_plateau_warned")
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), n_startup_trials=16)
    payload = captured[-1][1]
    assert payload["gradient_signal"] is None


def test_completed_study_with_empty_feasible_region_but_constraint_progress_yields_true(monkeypatch):
    """Kernfall #754: Study lief VOLLSTAENDIG (kein Early-Stop), 0 eligible Trials, aber die
    Constraint-Verletzung sinkt klar ueber die modellierten Trials ⇒ gradient_signal=True (waere vor
    #754 IMMER False gewesen — Selbstblockade)."""
    captured = _capture(monkeypatch)
    n_startup = 16
    startup = [_T(-9.0, evaluated=True, eligible=False, constraint_violation=1.0, number=i)
               for i in range(n_startup)]
    # Modellierte Trials: Constraint-Verletzung sinkt deutlich von 1.0 auf 0.1.
    modelled = (
        [_T(-9.0, evaluated=True, eligible=False, constraint_violation=1.0, number=n_startup + i)
         for i in range(8)]
        + [_T(-9.0, evaluated=True, eligible=False, constraint_violation=0.1, number=n_startup + 8 + i)
           for i in range(8)]
    )
    study = _S(startup + modelled)  # kein Early-Stop-User-Attr ⇒ Study lief durch
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), n_startup_trials=n_startup)
    payload = captured[-1][1]
    assert payload["gradient_signal"] is True
    assert payload["constraint_improvement_rate"] == 0.9


def test_completed_study_with_empty_feasible_region_and_flat_constraints_yields_false(monkeypatch):
    """Gegenprobe: Study lief vollstaendig durch, 0 eligible Trials, Constraint-Verletzung bleibt
    konstant ⇒ gradient_signal=False (echtes 'kein Signal', nicht 'unbeantwortet')."""
    captured = _capture(monkeypatch)
    n_startup = 16
    trials = [_T(-9.0, evaluated=True, eligible=False, constraint_violation=1.0, number=i)
              for i in range(n_startup + 16)]
    study = _S(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), n_startup_trials=n_startup)
    payload = captured[-1][1]
    assert payload["gradient_signal"] is False
    assert payload["constraint_improvement_rate"] == 0.0


def test_event_carries_gradient_signal_and_constraint_progress_state(monkeypatch):
    events = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            pass

    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    captured = []
    monkeypatch.setattr(ro, "emit_execution_event",
                        lambda logger, name, payload: captured.append((name, payload)))

    trials = [_T(-9.0, evaluated=True, eligible=False, constraint_violation=1.0, number=i)
              for i in range(32)]
    study = _S(trials, early_stopped_key="floor_plateau_warned")
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), n_startup_trials=16)
    payload = captured[-1][1]
    assert "constraint_improvement_rate" in payload
    assert "min_constraint_violation_first" in payload
    assert "min_constraint_violation_last" in payload
    assert payload["gradient_signal"] is None


# ── Produktions-Config hat die neue Schwelle deklariert ────────────────────────────────────────────
def test_production_config_declares_tau_c():
    opt = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert "tier_escalation_min_constraint_progress" in opt
    assert opt["tier_escalation_min_constraint_progress"] > 0.0
