"""Issue #1065/#1215 (P2, Katalog #1196-1221) — Budget-Median 100 % bei fehlenden Trials.

Symptom: `f48a3f77`: "Median Budgetausführung: 100,0 % (p10: 100,0 %, n=14)" bei "Trials gesamt:
1921 von 1940". `c429c992`: p10 100,0 % bei 1836 von 1940 (−104, 5,4 % fehlend). `0894e170`: p10
89,0 % bei 1915 von 1940.

Root-Cause: Median und p10 werden über Studies gebildet, die Summe über Trials; einzelne Studies
mit Ausfall verschwinden im Median.

Fix: `min` zusätzlich zu `p10`; bei `Σ trials < Σ budget` die Studies mit Defizit namentlich
listen (Study, ist, soll, Grund aus `stop_reason`).

Akzeptanzkriterium: jeder Lauf mit `Σ trials < Σ budget` nennt die verantwortlichen Studies.
"""
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sd


# --- report._budget_execution_summary: min ergaenzt Median/p10 -------------------------------------

def test_min_is_a_new_key_alongside_median_and_p10():
    studies = [
        {"budget_executed_fraction": 1.0}, {"budget_executed_fraction": 1.0},
        {"budget_executed_fraction": 1.0}, {"budget_executed_fraction": 1.0},
        {"budget_executed_fraction": 0.0},  # eine vollstaendig ausgefallene Study
    ]
    result = rpt._budget_execution_summary(studies)
    assert result["median"] == 1.0
    assert result["min"] == 0.0
    assert result["n"] == 5


def test_min_is_none_for_empty_cohort():
    result = rpt._budget_execution_summary([])
    assert result == {"median": None, "p10": None, "min": None, "n": 0}


def test_reproduces_c429c992_p10_hides_the_5_4_percent_gap():
    """13 Studies bei 100%, 1 Study bei 45% (die c429c992-Gesamtluecke von 5,4% konzentriert auf
    eine einzelne Study) -- p10 zeigt immer noch 100%, min deckt den Ausfall auf."""
    studies = [{"budget_executed_fraction": 1.0} for _ in range(13)] + [
        {"budget_executed_fraction": 0.45}]
    result = rpt._budget_execution_summary(studies)
    assert result["p10"] == 1.0
    assert result["min"] == 0.45


# --- report._budget_deficit_studies -----------------------------------------------------------------

def _study(strategy, symbol, *, completed, budgeted, stop_reason=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "n_trials_completed": completed, "n_trials_budgeted": budgeted,
        "stop_reason": stop_reason,
    }


def test_lists_only_studies_with_a_deficit():
    studies = [
        _study("A", "X.ETORO", completed=100, budgeted=100),  # kein Defizit
        _study("B", "Y.ETORO", completed=45, budgeted=140, stop_reason="WALLCLOCK_BUDGET_EXCEEDED"),
    ]
    deficits = rpt._budget_deficit_studies(studies)
    assert len(deficits) == 1
    assert deficits[0]["strategy"] == "B"
    assert deficits[0]["symbol"] == "Y.ETORO"
    assert deficits[0]["n_trials_completed"] == 45
    assert deficits[0]["n_trials_budgeted"] == 140
    assert deficits[0]["deficit"] == 95
    assert deficits[0]["stop_reason"] == "WALLCLOCK_BUDGET_EXCEEDED"


def test_missing_fields_are_excluded_not_falsely_flagged():
    studies = [_study("A", "X.ETORO", completed=None, budgeted=100)]
    assert rpt._budget_deficit_studies(studies) == []


def test_reason_falls_back_gracefully_when_stop_reason_absent():
    studies = [_study("A", "X.ETORO", completed=50, budgeted=100, stop_reason=None)]
    deficits = rpt._budget_deficit_studies(studies)
    assert deficits[0]["stop_reason"] is None


# --- summary_de.py §3.3: die verantwortlichen Studies erscheinen namentlich ------------------------

def _report(studies, deficit_studies, budget_summary):
    return {
        "run_id": "r", "run_status": "complete",
        "started_at_utc": "2026-08-19T00:00:00Z", "wallclock_s": 10.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "symbols_completed": 1, "symbols_planned": 1,
        "studies": studies,
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": budget_summary,
            "budget_deficit_studies": deficit_studies,
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }


def test_deficit_studies_appear_by_name_in_section_3_3():
    report = _report(
        studies=[
            {"strategy": "A", "symbol": "X.ETORO", "n_trials_completed": 100, "n_trials_budgeted": 100},
            {"strategy": "B", "symbol": "Y.ETORO", "n_trials_completed": 45, "n_trials_budgeted": 140},
        ],
        deficit_studies=[{
            "strategy": "B", "symbol": "Y.ETORO", "n_trials_completed": 45, "n_trials_budgeted": 140,
            "deficit": 95, "stop_reason": "WALLCLOCK_BUDGET_EXCEEDED",
        }],
        budget_summary={"median": 1.0, "p10": 1.0, "min": 0.32, "n": 2},
    )
    text = sd.generate_german_summary(report)
    assert "min:" in text
    assert "B/Y.ETORO" in text
    assert "WALLCLOCK_BUDGET_EXCEEDED" in text
    assert "45" in text and "140" in text


def test_no_deficit_line_when_totals_match():
    report = _report(
        studies=[{"strategy": "A", "symbol": "X.ETORO", "n_trials_completed": 100, "n_trials_budgeted": 100}],
        deficit_studies=[],
        budget_summary={"median": 1.0, "p10": 1.0, "min": 1.0, "n": 1},
    )
    text = sd.generate_german_summary(report)
    assert "verantwortliche" not in text
