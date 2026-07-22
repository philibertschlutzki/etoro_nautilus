"""Issue #760 (P1) — Der Kollinearitäts-Check läuft gegen ein eingefrorenes Key-Tupel statt gegen
die aktive Config.

Root-Cause: `_GATE_COLLINEARITY_KEYS` war der Gate-Satz vom Stand #667 (2026-07-17):
`("oos_min_expectancy", "oos_min_profitable_folds_frac", "any_condition", "oos_min_psr")`.
`oos_min_expectancy` wurde per #697 aus `eligible_requires_all` entfernt, `oos_min_profitable_
folds_frac` per #676 — die Warnung meldete trotzdem 426× eine Kollinearität mit einem Gate, das
längst nicht mehr Teil der Konjunktion ist, während die TATSÄCHLICH aktiven Gates
(`oos_min_psr`, `oos_min_excess_return`, `min_trades`, `max_drawdown`) nie gegeneinander geprüft
wurden.

Fix: `gate_rank_correlation_matrix` leitet die zu prüfenden Keys ZUR LAUFZEIT aus
`tournament_cfg['eligible_requires_all']` (+ `'any_condition'` als ANY-Disjunktions-Proxy) ab.
"""
import json
from pathlib import Path

from automation.optimizer.reward import (
    gate_rank_correlation_matrix, _active_gate_collinearity_keys,
    assert_gate_collinearity_guard,
)
from automation.optimizer.invariants import check_config_key_registry


# ── _active_gate_collinearity_keys: reine Ableitung ─────────────────────────────────────────────
def test_keys_derived_from_eligible_requires_all_with_oos_prefix_normalization():
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr",
                                      "oos_min_excess_return"]}
    keys = _active_gate_collinearity_keys(tcfg)
    assert keys == ["oos_min_trades", "oos_max_drawdown", "oos_min_psr", "oos_min_excess_return"]


def test_any_condition_appended_only_when_any_arm_configured():
    with_any = _active_gate_collinearity_keys(
        {"eligible_requires_all": ["min_trades"], "eligible_requires_any": ["min_win_rate"]})
    assert "any_condition" in with_any

    without_any = _active_gate_collinearity_keys({"eligible_requires_all": ["min_trades"]})
    assert "any_condition" not in without_any


def test_empty_config_yields_empty_key_set_no_hardcoded_fallback():
    assert _active_gate_collinearity_keys(None) == []
    assert _active_gate_collinearity_keys({}) == []


# ── Akzeptanzkriterium: Änderung von eligible_requires_all ändert die geprüfte Key-Menge ──────────
def test_changing_eligible_requires_all_changes_checked_keys_without_code_change():
    cohort = [{"oos_min_psr": 0.1 * i, "oos_min_expectancy": 0.1 * i,
              "oos_min_excess_return": 0.1 * i} for i in range(10)]

    tcfg_a = {"eligible_requires_all": ["oos_min_psr", "oos_min_expectancy"]}
    result_a = gate_rank_correlation_matrix(cohort, tcfg_a)
    assert set(result_a["keys"]) == {"oos_min_psr", "oos_min_expectancy"}

    tcfg_b = {"eligible_requires_all": ["oos_min_psr", "oos_min_excess_return"]}
    result_b = gate_rank_correlation_matrix(cohort, tcfg_b)
    assert set(result_b["keys"]) == {"oos_min_psr", "oos_min_excess_return"}
    # Dieselbe Kohorte, ANDERE Config ⇒ andere geprüfte Paare — ohne jede Code-Änderung.
    assert result_a["keys"] != result_b["keys"]


def test_production_config_no_longer_flags_stale_667_gates():
    """Regressionsbeleg: die AKTIVE tournament.json enthält weder oos_min_expectancy (seit #697)
    noch oos_min_profitable_folds_frac (seit #676) mehr in eligible_requires_all — die Kollinearitäts-
    Diagnose darf sich also nie mehr auf diese beiden Gates beziehen."""
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    keys = _active_gate_collinearity_keys(cfg)
    assert "oos_min_expectancy" not in keys
    assert "oos_min_profitable_folds_frac" not in keys
    # Die tatsächlich aktiven Gates werden dagegen geprüft.
    assert "oos_min_psr" in keys
    assert "oos_min_excess_return" in keys
    assert "oos_min_trades" in keys
    assert "oos_max_drawdown" in keys


# ── non_correlable_keys: strukturell fehlende Delta-Spalte wird explizit telemetriert ─────────────
def test_non_correlable_keys_are_explicitly_reported_not_silently_missing():
    tcfg = {"eligible_requires_all": ["min_trades", "min_evaluable_folds"]}
    # Kohorte stempelt NIE ein Delta fuer min_evaluable_folds (strukturell delta-frei, #760).
    cohort = [{"oos_min_trades": float(i)} for i in range(10)]
    result = gate_rank_correlation_matrix(cohort, tcfg)
    assert "oos_min_evaluable_folds" in result["non_correlable_keys"]


def test_no_hardcoded_gate_name_list_remains_in_reward_module():
    """Akzeptanzkriterium (#760): der Code enthält keine hartcodierte Liste von Gate-Namen mehr."""
    import automation.optimizer.reward as reward_mod
    assert not hasattr(reward_mod, "_GATE_COLLINEARITY_KEYS")
    assert not hasattr(reward_mod, "_GATE_COLLINEARITY_TO_CONJUNCTION_KEY")


# ── check_config_key_registry: Handler UND Delta-Spalte ────────────────────────────────────────────
def test_config_key_registry_flags_handler_without_delta_column():
    """min_evaluable_folds hat einen condition_map-Handler, aber KEINE oos_gate_deltas-Spalte —
    als eligible_requires_all-Mitglied muss das jetzt fail-loud auffallen (#760)."""
    result = check_config_key_registry({
        "eligible_requires_all": ["min_trades", "min_evaluable_folds"],
        "eligible_requires_any": [],
    })
    assert result.passed is False
    assert any("min_evaluable_folds" in a for a in result.actual)


def test_config_key_registry_passes_for_known_delta_bearing_gates():
    result = check_config_key_registry({
        "eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr",
                                  "oos_min_excess_return"],
        "eligible_requires_any": ["min_profit_factor", "min_win_rate"],
    })
    assert result.passed is True
    assert result.actual == []


def test_production_tournament_config_passes_registry_check():
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    result = check_config_key_registry(cfg)
    assert result.passed is True, result.detail


# ── assert_gate_collinearity_guard warns using the LIVE key set ───────────────────────────────────
def test_guard_no_longer_warns_about_expectancy_when_not_in_conjunction():
    import logging
    cohort = [{"oos_min_expectancy": float(i), "oos_min_psr": float(i)} for i in range(10)]
    tcfg = {"eligible_requires_all": ["oos_min_psr"]}  # expectancy NICHT Teil der Konjunktion
    logger = logging.getLogger("optimizer")
    recs = []

    class _H(logging.Handler):
        def emit(self, r):
            recs.append(r)

    h = _H()
    logger.addHandler(h)
    try:
        assert_gate_collinearity_guard(cohort, tcfg, threshold=0.95)
    finally:
        logger.removeHandler(h)
    assert not any("expectancy" in r.getMessage() for r in recs)
