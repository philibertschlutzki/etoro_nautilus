"""Issue #1093/#1241 (P1) — t(α)-Vorfilter statt Exzess/Exposure-Ranking.

Symptom. Auf drei steigenden Symbolen bestand 0 von 42 Studies das absolute Excess-Return-Gate
(PLTR Median-Exzess −32,10 %, NVDA −16,04 %, ASML −6,06 %); auf fünf fallenden Symbolen war der
Exzess in 107 von 112 Studies trivial positiv, ohne dass eine einzige Strategie einen positiven
Return lieferte (NATGAS: 14/14 Exzess positiv, 1/14 Return positiv). Ein absolutes Excess-Gate ohne
Exposure-Normierung misst im fallenden Markt negatives Beta (Nichtstun erfüllt es trivial), im
steigenden ist es durch Alpha allein oft nicht erreichbar (absoluter Endpunkt-Vergleich).

Root-Cause. ``oos_alpha_tstat`` (OLS-Regression Strategie- vs. Benchmark-Perioden-Returns,
``backtest_runner._alpha_beta_regression``, #986/#1140) ist bereits exposure-bereinigt (β trennt
Marktbeteiligung von Alpha) und war bislang NUR Telemetrie, nie ein Gate.

Fix.
1. ``oos_min_alpha_tstat`` (Default 2.0) als neue ``min_alpha_tstat``-Klausel in
   ``_evaluate_oos_eligibility``s ``condition_map`` (backtest_runner.py), registriert in
   ``OOS_CONDITION_MAP_KEYS``/``OOS_GATE_DELTA_KEYS`` (#649-Registry-Pflicht) und in
   ``tournament.json['eligible_requires_all']``.
2. ``oos_min_excess_return`` bleibt Telemetrie (war bereits seit #776 NICHT mehr Teil von
   ``eligible_requires_all`` — die eigene Kollinearitäts-Analyse jenes Issues kam unabhängig zum
   selben Schluss).
3. ``summary_de.py`` §2.3 sortiert jetzt nach t(α) statt Excess/Exposure (Excess/Exposure bleibt
   Spalte) — siehe ``test_issue_986_1140_alpha_beta_excess_per_exposure.py``.
4. ``reward.check_mandatory_gate_reachability_live`` (NEU, separat von
   ``check_any_arm_reachability_live``, das nur ``eligible_requires_any`` behandelt — eine
   Wiederverwendung hätte das MANDATORY Gate stillschweigend über
   ``any_arm_unreachable_policy='drop_arm'`` droppen können) macht sichtbar, wenn t(α) für ein
   Symbol/eine Strategie strukturell unerreichbar ist (in ``any_arm_live_unreachable`` gemergt,
   run_optimization.py).
"""
from automation.backtest_runner import (
    _evaluate_oos_eligibility, OOS_CONDITION_MAP_KEYS, OOS_GATE_DELTA_KEYS,
)
from automation.optimizer import reward


_TCFG = {
    "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
    "oos_min_win_rate": 0.0, "max_drawdown": 0.3,
    "oos_min_alpha_tstat": 2.0,
    "eligible_requires_all": ["min_alpha_tstat"],
}


def _oos_metrics(*, total_return, excess_return, alpha_tstat, total_trades=50):
    return {
        "total_trades": total_trades, "max_drawdown": 0.02, "win_rate": 0.4,
        "total_return": total_return, "expectancy": 0.001, "sortino_ratio": 1.0,
        "profit_factor": 1.2, "median_position_notional": 1000.0,
        "oos_excess_return": excess_return,
        "oos_alpha_tstat": alpha_tstat,
    }


# ── Akzeptanzkriterium (#1241): positiver Exzess, t(α)=0,3 ⇒ NICHT eligible ─────────────────────
def test_positive_excess_but_weak_alpha_tstat_is_not_eligible():
    """Kernreproduktion des Symptoms: ein trivial positiver Exzess (z. B. Nichtstun im fallenden
    Markt) reicht nicht mehr — t(α)=0,3 liegt weit unter der Schwelle 2,0."""
    m = _oos_metrics(total_return=0.02, excess_return=0.15, alpha_tstat=0.3)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is False
    assert any("oos_min_alpha_tstat" in r for r in ev["oos_rejection_reasons"])


def test_strong_alpha_tstat_is_eligible():
    """t(α) >= 2.0 (die konfigurierte Schwelle) besteht das Gate."""
    m = _oos_metrics(total_return=0.02, excess_return=0.01, alpha_tstat=2.5)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is True
    assert not any("oos_min_alpha_tstat" in r for r in ev["oos_rejection_reasons"])


def test_alpha_tstat_exactly_at_threshold_is_eligible():
    m = _oos_metrics(total_return=0.02, excess_return=0.01, alpha_tstat=2.0)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is True


def test_undefined_alpha_tstat_is_not_eligible():
    """Analog oos_min_psr: ein UNDEFINIERTER t(α) (z. B. < 3 Regressions-Perioden) ist NICHT
    eligible — kein impliziter Pass."""
    m = _oos_metrics(total_return=0.02, excess_return=0.01, alpha_tstat=None)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is False
    assert any("oos_min_alpha_tstat" in r for r in ev["oos_rejection_reasons"])


def test_gate_inactive_when_threshold_not_configured():
    """Fehlt oos_min_alpha_tstat in der Config ⇒ trivial erfüllt (rückwärtskompatibel, Zero-
    Hardcoding, analog oos_min_psr)."""
    cfg = dict(_TCFG)
    cfg.pop("oos_min_alpha_tstat")
    cfg["eligible_requires_all"] = []
    m = _oos_metrics(total_return=0.02, excess_return=0.01, alpha_tstat=0.1)
    ev = _evaluate_oos_eligibility(m, cfg)
    assert ev["oos_eligible"] is True


def test_gate_delta_stamped_machine_readable():
    """oos_gate_deltas['oos_min_alpha_tstat'] = actual - threshold (dieselbe Konvention wie
    oos_min_psr)."""
    m = _oos_metrics(total_return=0.02, excess_return=0.01, alpha_tstat=3.5)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_gate_deltas"]["oos_min_alpha_tstat"] == 1.5


# ── Akzeptanzkriterium (#1241, Fix Punkt 4): Registry-Pflicht aus #649 ──────────────────────────
def test_min_alpha_tstat_registered_in_condition_map_key_registries():
    """Ohne diese Registrierung würde tournament.json's neue Klausel in eligible_requires_all
    fail-loud an der Startup-Validierung scheitern (#649)."""
    assert "min_alpha_tstat" in OOS_CONDITION_MAP_KEYS
    assert "min_alpha_tstat" in OOS_GATE_DELTA_KEYS


def test_production_tournament_json_wires_the_new_gate():
    """Die AUSGELIEFERTE tournament.json listet oos_min_alpha_tstat in eligible_requires_all UND
    in gate_consolidation_priority (#810-Pflicht, sonst GATE_PRIORITY_COVERAGE_MISSING)."""
    import json
    from pathlib import Path
    tcfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    assert "oos_min_alpha_tstat" in tcfg["eligible_requires_all"]
    assert "oos_min_alpha_tstat" in tcfg["gate_consolidation_priority"]
    assert tcfg["oos_min_alpha_tstat"] == 2.0
    # oos_min_excess_return bleibt Telemetrie (bereits seit #776 kein Konjunktions-Mitglied).
    assert "min_excess_return" not in tcfg["eligible_requires_all"]
    assert "oos_min_excess_return" not in tcfg["eligible_requires_all"]
    assert "oos_min_excess_return" in tcfg


# ── Akzeptanzkriterium (#1241, Fix Punkt 4): symbolweite Unerreichbarkeit sichtbar ──────────────
def test_mandatory_gate_reachability_live_flags_structurally_unreachable_alpha_gate():
    """Bleibt t(α) in JEDEM Trial dieser Study unter der Schwelle, macht die neue Diagnose das
    SICHTBAR (analog #660 für eligible_requires_any), statt die Study lautlos auf 0 eligible
    Trials laufen zu lassen."""
    tcfg = {"oos_min_alpha_tstat": 2.0, "eligible_requires_all": ["min_alpha_tstat"]}
    observed = {"min_alpha_tstat": [0.1, 0.3, -0.2, 0.5, 0.4, 0.2, 0.6, 0.1, 0.3, 0.2, 0.4]}
    unreachable = reward.check_mandatory_gate_reachability_live(tcfg, observed, n_evaluated=11)
    assert unreachable == ["min_alpha_tstat"]


def test_mandatory_gate_reachability_live_passes_when_reachable():
    tcfg = {"oos_min_alpha_tstat": 2.0, "eligible_requires_all": ["min_alpha_tstat"]}
    observed = {"min_alpha_tstat": [0.1, 3.0, 2.5, 0.5, 4.0, 0.2, 2.1, 0.1, 3.3, 0.2, 2.9]}
    unreachable = reward.check_mandatory_gate_reachability_live(tcfg, observed, n_evaluated=11)
    assert unreachable == []


def test_mandatory_gate_reachability_live_insufficient_data_is_silent():
    tcfg = {"oos_min_alpha_tstat": 2.0, "eligible_requires_all": ["min_alpha_tstat"]}
    observed = {"min_alpha_tstat": [0.1, 0.2]}  # < any_arm_min_observations (Default 10)
    unreachable = reward.check_mandatory_gate_reachability_live(tcfg, observed, n_evaluated=2)
    assert unreachable == []


def test_mandatory_gate_reachability_live_does_not_affect_any_arm_policy():
    """Die neue Diagnose ist READ-ONLY: sie darf resolve_any_arm_policy (die den OR-Arm 'droppen'
    kann) nicht beeinflussen, da min_alpha_tstat kein eligible_requires_any-Mitglied ist."""
    tcfg = {"oos_min_alpha_tstat": 2.0, "eligible_requires_all": ["min_alpha_tstat"],
           "eligible_requires_any": [], "any_arm_unreachable_policy": "drop_arm"}
    decision = reward.resolve_any_arm_policy(
        tcfg, {"min_alpha_tstat": [0.1] * 11}, n_evaluated=11)
    assert decision["dropped_clauses"] == []
