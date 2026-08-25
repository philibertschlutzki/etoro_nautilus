"""Issue #1248 (GH #1118) — ``oos_min_psr`` aus ``eligible_requires_all`` entfernen (gemessener
Grenzbeitrag null).

Symptom. ``check_gate_collinearity_decision_required`` (severity blocking) feuert in 13/13
Studies für das Paar (``oos_min_psr``, ``oos_min_alpha_tstat``), ρ = 0,9214–0,9991.
``check_gate_marginal_contribution`` misst ``marginal_delta = 0,0`` für ``oos_min_psr`` über 1627
Beobachtungen; ``gate_inventory`` zeigt 160 Rejections bei 0 Solo-Rejections — jede PSR-Ablehnung
war bereits durch das Alpha-t-Stat-Gate gedeckt.

Fix.
1. ``tournament.json['eligible_requires_all']`` verliert ``oos_min_psr``.
2. ``oos_min_psr`` bleibt weiche Near-Miss-Distanz und in ``gate_consolidation_priority``.
3. Schema-Kommentar dokumentiert die Entscheidung (``decided_in_issue: 1248``).
4. ``reward.assert_eligible_requires_all_not_redundant`` bestätigt die Entfernung als konsistent.
"""
import json
from pathlib import Path

from automation.backtest_runner import _evaluate_oos_eligibility
from automation.optimizer import reward


def _load_production_tournament_cfg() -> dict:
    return json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_oos_min_psr_removed_from_eligible_requires_all():
    tcfg = _load_production_tournament_cfg()
    assert "oos_min_psr" not in tcfg["eligible_requires_all"]
    assert "min_psr" not in tcfg["eligible_requires_all"]


def test_oos_min_psr_threshold_and_priority_survive_removal():
    """PSR bleibt als weiche Near-Miss-Distanz und in der Konsolidierungs-Prioritaet erhalten —
    nur die harte Konjunktions-Mitgliedschaft entfaellt."""
    tcfg = _load_production_tournament_cfg()
    assert "oos_min_psr" in tcfg
    assert "oos_min_psr" in tcfg["gate_consolidation_priority"]


def test_reward_semantics_version_bumped_for_eligibility_change():
    opt_cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert opt_cfg["reward_semantics_version"] >= 26


# ── Akzeptanzkriterium: ein Trial, der NUR an oos_min_psr scheiterte, wird jetzt eligible ───────
def _oos_metrics(*, total_trades=50, alpha_tstat=3.0, psr=0.5):
    return {
        "total_trades": total_trades, "max_drawdown": 0.02, "win_rate": 0.4,
        "total_return": 0.02, "expectancy": 0.001, "sortino_ratio": 1.0,
        "profit_factor": 1.2, "median_position_notional": 1000.0,
        "psr": psr, "oos_alpha_tstat": alpha_tstat,
    }


def test_trial_failing_only_psr_is_now_eligible():
    """Vorher (PSR im Konjunktions-Gate) waere dieser Trial NICHT eligible (psr=0.5 < 0.6);
    nach der Entfernung entscheidet ausschliesslich oos_min_alpha_tstat."""
    tcfg = _load_production_tournament_cfg()
    m = _oos_metrics(psr=0.5, alpha_tstat=3.0)  # psr unter der (weiterhin konfigurierten) 0.6-Schwelle
    ev = _evaluate_oos_eligibility(m, tcfg)
    assert ev["oos_eligible"] is True
    # oos_gate_deltas bleibt weiterhin gestempelt (Near-Miss-Telemetrie), nur nicht mehr Gate-bindend.
    assert "oos_min_psr" in ev["oos_gate_deltas"]
    assert ev["oos_gate_deltas"]["oos_min_psr"] < 0  # PSR liegt unter der Schwelle (Near-Miss sichtbar)


def test_trial_failing_alpha_tstat_remains_ineligible_regardless_of_psr():
    tcfg = _load_production_tournament_cfg()
    m = _oos_metrics(psr=0.9, alpha_tstat=0.3)
    ev = _evaluate_oos_eligibility(m, tcfg)
    assert ev["oos_eligible"] is False
    assert any("oos_min_alpha_tstat" in r for r in ev["oos_rejection_reasons"])


# ── assert_eligible_requires_all_not_redundant bestaetigt Konsistenz ────────────────────────────
def test_redundancy_guard_confirms_no_further_action_needed():
    """Mit oos_min_psr bereits entfernt darf der Waechter keine erneute Empfehlung aussprechen,
    selbst wenn die (jetzt irrelevante) historische Kollinearitaet weiterhin in trial_gate_deltas
    auftaucht — die Klausel steht nicht mehr in eligible_requires_all und kann daher nicht mehr
    als 'weiterhin unkonsolidiert' gemeldet werden."""
    tcfg = _load_production_tournament_cfg()
    # Synthetische Kohorte: oos_min_psr und oos_min_alpha_tstat stark korreliert (wie im Referenzlauf).
    trial_gate_deltas = [
        {"oos_min_alpha_tstat": d, "oos_min_psr": d * 0.1, "oos_max_drawdown": 0.05}
        for d in (-0.5, -0.3, -0.1, 0.1, 0.3, 0.5, 0.2, -0.2, 0.4, -0.4)
    ]
    still_redundant = reward.assert_eligible_requires_all_not_redundant(
        trial_gate_deltas, tcfg["eligible_requires_all"], tcfg)
    assert "oos_min_psr" not in still_redundant
    assert "min_psr" not in still_redundant
