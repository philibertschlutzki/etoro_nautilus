"""Issue #679 — Gate-Kollinearitäts-Diagnose (#667) wird zu einem STRUKTURIERTEN Redundanz-Alarm
(nicht nur geloggt): kollineare eligible-Gates addieren keine False-Positive-Kontrolle, senken aber
die Passrate multiplikativ. ``gate_collinearity_redundancy_alarm`` markiert das niedriger
priorisierte Gate (PSR hat höchste Priorität) als Konsolidierungs-Kandidat.
"""
from automation.optimizer.reward import gate_collinearity_redundancy_alarm

# Issue #760 — gate_collinearity_redundancy_alarm leitet die geprüften Keys jetzt zur Laufzeit aus
# tournament_cfg ab (keine eingefrorene Code-Konstante mehr); dieses Fixture reproduziert exakt den
# #667/#679-4-Key-Satz (oos_min_expectancy, oos_min_profitable_folds_frac, any_condition, oos_min_psr).
_TCFG = {
    "eligible_requires_all": ["oos_min_expectancy", "oos_min_profitable_folds_frac", "oos_min_psr"],
    "eligible_requires_any": ["min_profit_factor"],
}


def _deltas(expectancy, psr, profitable_folds_frac=None, any_condition=None):
    # ``any_condition`` wird von gate_rank_correlation_matrix NICHT aus einem gleichnamigen Key
    # gelesen, sondern aus dem BESTEN Arm-Delta (_any_condition_delta_from_gate_deltas) — hier via
    # ``oos_min_profit_factor`` als einzigen konfigurierten Arm bereitgestellt.
    return {
        "oos_min_expectancy": expectancy,
        "oos_min_psr": psr,
        "oos_min_profitable_folds_frac": profitable_folds_frac if profitable_folds_frac is not None else expectancy,
        "oos_min_profit_factor": any_condition if any_condition is not None else expectancy,
    }


def test_no_alarm_below_threshold():
    # Nahezu unabhängige Serien (keine perfekte Korrelation).
    cohort = [
        _deltas(0.001, 0.9, profitable_folds_frac=0.5, any_condition=-0.2),
        _deltas(-0.001, 0.5, profitable_folds_frac=-0.5, any_condition=0.3),
        _deltas(0.002, 0.1, profitable_folds_frac=0.1, any_condition=-0.1),
        _deltas(-0.003, 0.7, profitable_folds_frac=-0.2, any_condition=0.4),
    ]
    result = gate_collinearity_redundancy_alarm(cohort, _TCFG, threshold=0.9)
    assert result["alarms"] == []
    assert result["redundant_candidates"] == {}


def test_alarm_fires_and_recommends_consolidating_lower_priority_gate():
    """Perfekt kollineare expectancy/psr-Serie (rho=1.0) ⇒ Alarm empfiehlt PSR zu behalten,
    Expectancy als Konsolidierungs-Kandidat (PSR hat die höhere Prioritaet, #679)."""
    cohort = [
        _deltas(e, e, profitable_folds_frac=100 - e, any_condition=-e)
        for e in [0.001, 0.002, 0.003, 0.004, -0.001]
    ]
    result = gate_collinearity_redundancy_alarm(cohort, _TCFG, threshold=0.9)
    assert result["n_samples"] == 5
    pairs = {(a["keep"], a["redundant_candidate"]) for a in result["alarms"]}
    assert ("oos_min_psr", "oos_min_expectancy") in pairs
    assert "oos_min_expectancy" in result["redundant_candidates"]
    # PSR wird NIE als redundanter Kandidat markiert (hoechste Prioritaet).
    assert "oos_min_psr" not in result["redundant_candidates"]


def test_psr_never_marked_as_redundant_candidate():
    """Auch bei mehreren gleichzeitig kollinearen Paaren bleibt PSR immer das 'keep'-Gate."""
    cohort = [
        _deltas(e, e, profitable_folds_frac=e, any_condition=e)
        for e in [0.001, 0.002, 0.003, 0.004, 0.005, -0.001]
    ]
    result = gate_collinearity_redundancy_alarm(cohort, _TCFG, threshold=0.9)
    for alarm in result["alarms"]:
        assert alarm["redundant_candidate"] != "oos_min_psr"


def test_empty_cohort_yields_no_alarm():
    result = gate_collinearity_redundancy_alarm([], _TCFG, threshold=0.9)
    assert result["n_samples"] == 0
    assert result["alarms"] == []
    assert result["redundant_candidates"] == {}
