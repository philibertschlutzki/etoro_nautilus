"""Issue #808 — die Tier-Eskalation ist bei GENAU EINEM eligiblen Trial strukturell tot: `pstdev`
über n=1 ist per Konstruktion 0 (bzw. bei n<2 gar nicht definiert), wird aber trotzdem gegen die
Signal-Schwelle geprüft. `feasible_p_eligible=0.01` bei ~100 Trials bedeutet genau EINEN eligiblen
Trial — die Study, die gerade eben ihren ERSTEN Treffer gefunden hat (der staerkste denkbare Beleg,
dass die feasible Region NICHT leer ist), bekam bislang KEIN zusätzliches Budget.

Fix: ein DRITTER, gleichrangiger Arm (`gradient_signal_arm`) — der Entdeckungs-Arm:
`1 <= n_eligible < tier_escalation_min_eligible_for_variance` (Default 5) ⇒ Signal, UNABHÄNGIG von
der (mangels Stichprobe nicht schätzbaren) Streuung. `pstdev` wird erst AB dieser Schwelle als
Kriterium herangezogen.
"""
import json
from pathlib import Path

from automation.optimizer.run_optimization import gradient_signal_arm, study_shows_gradient_signal


# ---------------------------------------------------------------------------
# gradient_signal_arm: die drei Arme + Praezedenz
# ---------------------------------------------------------------------------

def test_exactly_one_eligible_trial_is_a_discovery_signal():
    """Akzeptanzkriterium #808: Study mit genau 1 eligiblen Trial ⇒ True, arm=='discovery'."""
    arm = gradient_signal_arm([2.0], evaluable_fraction=0.01, tau=1e-3)
    assert arm == "discovery"
    assert study_shows_gradient_signal([2.0], evaluable_fraction=0.01, tau=1e-3) is True


def test_zero_eligible_and_zero_constraint_progress_remains_none():
    """Akzeptanzkriterium #808: 0 eligible UND constraint_improvement_rate=0 ⇒ weiterhin False."""
    arm = gradient_signal_arm([], evaluable_fraction=0.0, tau=1e-3,
                              constraint_improvement_rate=0.0, tau_c=0.05)
    assert arm == "none"
    assert study_shows_gradient_signal(
        [], evaluable_fraction=0.0, tau=1e-3, constraint_improvement_rate=0.0, tau_c=0.05) is False


def test_two_to_four_eligible_trials_are_also_discovery_regardless_of_variance():
    """Der Entdeckungs-Arm deckt den GESAMTEN [1, min_eligible_for_variance)-Bereich ab, nicht nur
    n=1 — auch 2-4 identische (varianzlose) eligible Rewards sind ein Entdeckungssignal."""
    for n in (2, 3, 4):
        arm = gradient_signal_arm([7.0] * n, evaluable_fraction=0.02, tau=1e-3)
        assert arm == "discovery", f"n_eligible={n} sollte discovery liefern"


def test_five_or_more_eligible_trials_fall_through_to_reward_variance_arm():
    """Ab der konfigurierten Schwelle (Default 5) uebernimmt wieder der Reward-Varianz-Arm — flache
    (varianzlose) Rewards liefern dann 'none', nicht mehr 'discovery'."""
    arm_flat = gradient_signal_arm([7.0] * 5, evaluable_fraction=0.05, tau=1e-3)
    assert arm_flat == "none"
    arm_varied = gradient_signal_arm([1.0, 2.0, 3.0, 4.0, 5.0], evaluable_fraction=0.05, tau=1e-3)
    assert arm_varied == "reward_variance"


def test_discovery_arm_respects_configured_threshold():
    """min_eligible_for_variance ist deklarativ konfigurierbar (Zero-Hardcoding) — bei einer
    abgesenkten Schwelle (z. B. 3) wird n_eligible=3 bereits dem Reward-Varianz-Arm zugeordnet,
    nicht mehr 'discovery'."""
    arm = gradient_signal_arm([5.0, 5.0, 5.0], evaluable_fraction=0.03, tau=1e-3,
                              min_eligible_for_variance=3)
    assert arm == "none"  # 3 identische Rewards, oberhalb der abgesenkten Schwelle ⇒ keine Streuung
    arm_discovery = gradient_signal_arm([5.0, 5.0], evaluable_fraction=0.02, tau=1e-3,
                                        min_eligible_for_variance=3)
    assert arm_discovery == "discovery"  # 2 < 3 ⇒ weiterhin Entdeckungs-Arm


def test_discovery_arm_requires_a_nonzero_evaluable_fraction():
    """Defensiver Gleichlauf mit dem Reward-Arm: evaluable_fraction<=0 blockiert auch den
    Entdeckungs-Arm (in der Praxis unerreichbar — n_eligible>=1 impliziert p_eligible>0 — aber die
    Funktion bleibt fuer widersprüchliche synthetische Eingaben defensiv konsistent)."""
    arm = gradient_signal_arm([2.0], evaluable_fraction=0.0, tau=1e-3)
    assert arm == "none"


def test_constraint_arm_still_wins_when_discovery_and_reward_are_silent():
    """Kein eligibler Trial (Entdeckungs-/Reward-Arm beide stumm), aber der Sampler naehert sich
    der feasiblen Region an ⇒ constraint_progress."""
    arm = gradient_signal_arm([], evaluable_fraction=0.0, tau=1e-3,
                              constraint_improvement_rate=0.3, tau_c=0.05)
    assert arm == "constraint_progress"


def test_discovery_arm_takes_precedence_over_a_flat_constraint_signal():
    """Auch wenn der Constraint-Arm NICHT feuert, gewinnt der Entdeckungs-Arm bei 1-4 eligiblen
    Trials — die drei Arme sind gleichrangig (irgendeiner reicht), keine Rangfolge unter den
    feuernden Armen ist noetig, aber die Klassifikation muss deterministisch EINEN Namen liefern."""
    arm = gradient_signal_arm([2.0], evaluable_fraction=0.01, tau=1e-3,
                              constraint_improvement_rate=0.0, tau_c=0.05)
    assert arm == "discovery"


# ---------------------------------------------------------------------------
# Produktions-Config
# ---------------------------------------------------------------------------

def test_production_config_declares_the_new_threshold():
    opt = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert opt.get("tier_escalation_min_eligible_for_variance") == 5


# ---------------------------------------------------------------------------
# _emit_study_summary / report._study_record: gradient_signal_arm-Telemetrie
# ---------------------------------------------------------------------------

def test_emit_study_summary_reports_discovery_arm(monkeypatch):
    import time
    import automation.optimizer.run_optimization as ro

    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    captured = []
    monkeypatch.setattr(ro, "emit_execution_event",
                        lambda logger, name, payload: captured.append((name, payload)))

    class _T:
        def __init__(self, value, eligible, number=None):
            self.value = value
            self.number = number
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": eligible, "backtest_ms": 5}

    class _S:
        study_name = "study_discovery"
        # 99 non-eligible + exactly 1 eligible Trial (feasible_p_eligible ~ 0.01, das #808-Symptom).
        trials = [_T(-1.0, False, number=i) for i in range(99)] + [_T(2.0, True, number=99)]
        best_value = 2.0
        _attrs = {}

        @property
        def user_attrs(self):
            return dict(self._attrs)

    ro._emit_study_summary(_S(), "USDZAR.ETORO", time.perf_counter())
    payload = captured[-1][1]
    assert payload["gradient_signal"] is True
    assert payload["gradient_signal_arm"] == "discovery"


def test_report_study_record_carries_gradient_signal_arm():
    from automation.optimizer.report import _study_record

    class _T:
        def __init__(self, value, eligible):
            self.value = value
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": eligible}

    class _S:
        trials = [_T(-1.0, False) for _ in range(9)] + [_T(2.0, True)]
        best_value = 2.0
        user_attrs = {}

    record, _checks = _study_record({"symbol": "USDZAR.ETORO", "strategy": "S"}, _S())
    assert record["gradient_signal_arm"] == "discovery"
    assert record["gradient_signal"] is True
