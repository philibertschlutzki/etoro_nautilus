"""Issue #776 (P1) — `eligible_requires_all` enthält weiterhin drei ~0.98-kollineare Renditegates
(`oos_max_drawdown`, `oos_min_psr`, `oos_min_excess_return`); der #679-Redundanz-Alarm ist reine
Telemetrie ohne sweep-weiten Konsumenten.

Root-Cause: der aktive Kollinearitäts-Check (`reward._active_gate_collinearity_keys`, #760) belegte
Median |ρ(oos_max_drawdown, oos_min_excess_return)|=0.98 (327 Studies) UND
|ρ(oos_min_psr, oos_min_excess_return)|=0.98 (264 Studies) — eine Konjunktion aus drei ~0.98-
kollinearen Bedingungen ist statistisch EIN Gate mit der schärfsten der drei Schwellen. Teilursache
ist ANALYTISCH: seit #666 verlangt `oos_min_excess_return` im Bärenmarkt `sortino_period > 0`, und
`oos_min_psr` ist eine monotone Transformation von `sortino_period` — die beiden Gates sind PER
SPEZIFIKATION funktional abhängig.

Fix: `oos_min_excess_return` aus `eligible_requires_all` entfernt (bleibt als weiche Near-Miss-
Distanz über `reward._normalized_gate_distances` erhalten, oos_min_psr bleibt HART);
`report._study_record` stempelt `gate_collinearity_unconsolidated` je Study
(`reward.assert_eligible_requires_all_not_redundant` gegen die LIVE-Trial-Kohorte);
`invariants.check_gate_collinearity_consolidation` konsumiert den #679-Alarm sweep-weit (FAIL ab
>= 20 % betroffener Studies, `optimizer.json['max_gate_collinearity_affected_fraction']`).
"""
import json
from pathlib import Path

from automation.optimizer.invariants import check_gate_collinearity_consolidation
from automation.optimizer.reward import assert_eligible_requires_all_not_redundant
from automation.optimizer.report import _study_record

CFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ── Akzeptanzkriterium #776/1: genau ein risikoadjustiertes Rendite-Gate nach dem Merge ───────────
def test_conjunction_contains_exactly_one_risk_adjusted_return_gate():
    requires_all = CFG["eligible_requires_all"]
    assert requires_all == ["min_trades", "max_drawdown", "oos_min_psr"]
    assert "oos_min_excess_return" not in requires_all


def test_oos_min_excess_return_still_present_as_schema_soft_distance():
    """Der Key bleibt als Schwelle/Telemetrie/Near-Miss-Distanz erhalten — nur die harte
    Konjunktions-Mitgliedschaft entfällt (analog #697/min_expectancy)."""
    assert "oos_min_excess_return" in CFG
    assert "oos_min_excess_return" in CFG["_schema"]["fields"]


# ── Akzeptanzkriterium #776/2: assert_eligible_requires_all_not_redundant liefert auf der neuen
# Config eine leere Liste für das Rendite-Paar ─────────────────────────────────────────────────────
def test_assert_not_redundant_returns_empty_list_on_new_config():
    # Kohorte reproduziert die dokumentierte Kollinearität (|ρ|>=0.98) zwischen drawdown/psr/excess.
    cohort = [
        {"oos_max_drawdown": -e, "oos_min_psr": e, "oos_min_excess_return": e}
        for e in [0.01, 0.02, 0.03, 0.04, 0.05, -0.01]
    ]
    result = assert_eligible_requires_all_not_redundant(
        cohort, CFG["eligible_requires_all"], CFG)
    assert result == []


def test_assert_not_redundant_still_fires_if_excess_return_is_reintroduced():
    """Regressionswächter: reaktiviert ein Operator 'oos_min_excess_return' versehentlich wieder,
    UND die Live-Kohorte zeigt weiterhin hohe Kollinearität, muss die Prüfung das melden."""
    cohort = [
        {"oos_min_trades": 100.0, "oos_max_drawdown": -e, "oos_min_psr": e,
         "oos_min_excess_return": e}
        for e in [0.01, 0.02, 0.03, 0.04, 0.05, -0.01]
    ]
    reintroduced = ["min_trades", "max_drawdown", "oos_min_psr", "oos_min_excess_return"]
    tcfg = {"eligible_requires_all": reintroduced}
    result = assert_eligible_requires_all_not_redundant(cohort, reintroduced, tcfg)
    assert "oos_min_excess_return" in result


# ── report._study_record: gate_collinearity_unconsolidated-Feld ───────────────────────────────────
def _study(deltas_by_trial):
    class _T:
        def __init__(self, deltas):
            self.value = 1.0
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True, "oos_gate_deltas": deltas}

    class _S:
        trials = [_T(d) for d in deltas_by_trial]
        best_value = 1.0
        user_attrs = {}

    return _S()


def test_study_record_reports_unconsolidated_field_empty_on_current_config():
    proposal = {"symbol": "BTC.ETORO", "strategy": "TrendFollow"}
    study = _study([{"oos_min_trades": 0.2, "oos_max_drawdown": 0.1, "oos_min_psr": 0.3}])
    record, _checks = _study_record(proposal, study, CFG)
    assert record["gate_collinearity_unconsolidated"] == []


def test_study_record_flags_reintroduced_redundant_gate():
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr",
                                      "oos_min_excess_return"]}
    deltas = [{"oos_min_trades": 100.0, "oos_max_drawdown": -e, "oos_min_psr": e,
              "oos_min_excess_return": e}
              for e in [0.01, 0.02, 0.03, 0.04, 0.05, -0.01]]
    proposal = {"symbol": "ETH.ETORO", "strategy": "TrendFollow"}
    study = _study(deltas)
    record, _checks = _study_record(proposal, study, tcfg)
    assert "oos_min_excess_return" in record["gate_collinearity_unconsolidated"]


# ── invariants.check_gate_collinearity_consolidation: sweep-weiter Konsument ──────────────────────
def test_consolidation_check_passes_when_no_study_reports_unconsolidated_gate():
    studies = [{"gate_collinearity_unconsolidated": []} for _ in range(10)]
    result = check_gate_collinearity_consolidation(studies)
    assert result.passed is True


def test_consolidation_check_fails_at_or_above_20_percent_affected():
    studies = [{"gate_collinearity_unconsolidated": ["oos_min_excess_return"]}] * 2 + \
              [{"gate_collinearity_unconsolidated": []}] * 8
    result = check_gate_collinearity_consolidation(studies)
    assert result.passed is False
    assert result.actual == 0.2


def test_consolidation_check_passes_just_below_20_percent_affected():
    studies = [{"gate_collinearity_unconsolidated": ["oos_min_excess_return"]}] * 1 + \
              [{"gate_collinearity_unconsolidated": []}] * 9
    result = check_gate_collinearity_consolidation(studies)
    assert result.passed is True


def test_consolidation_check_is_a_noop_when_no_study_has_the_field():
    result = check_gate_collinearity_consolidation([{"symbol": "X"}])
    assert result.passed is True
