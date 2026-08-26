"""Issue #1284 (GH #1157, Katalog #1272-1297, P3) — ``alpha_tstat_df`` ist inkonsistent zur
Regressions-Stichprobe.

Symptom. ``holdout_alpha_tstat_df = n_informative - 2`` (AdxAtr/TSLA: 735), waehrend beide
t-Statistiken (klassisch und HC3) ueber ``n_total = 1079`` Beobachtungen gerechnet werden. Aktuell
folgenlos (``oos_min_alpha_tstat_mode='static'`` nutzt keine t-Verteilungs-Nachschlagestelle), wird
aber mit #1282 (``'multiplicity_adjusted'``) entscheidungsrelevant.

Fix. ``alpha_tstat_df = n_used - 2`` mit ``n_used = n`` (ALLE Bars — dieselbe Grundgesamtheit wie
die Regressions-Schaetzung selbst, siehe backtest_runner._alpha_regression_diagnostics-Docstring
fuer die Begruendung gegenueber einer Restriktion der Regression auf die informativen Bars). Neue
Invariante ``invariants.check_alpha_df_consistency`` (``alpha_tstat_df == n_used - 2``);
``holdout_alpha_n_used`` benennt die tatsaechlich verwendete Stichprobe explizit.
"""
import numpy as np
import pytest

from automation.backtest_runner import _alpha_regression_diagnostics
from automation.optimizer import invariants as inv, report as rpt


# ---------------------------------------------------------------------------------------------
# backtest_runner._alpha_regression_diagnostics
# ---------------------------------------------------------------------------------------------

def test_alpha_tstat_df_equals_n_used_minus_2():
    rng = np.random.default_rng(1284)
    n = 200
    x = rng.normal(0.0, 0.001, n)
    y = np.where(rng.random(n) < 0.5, 0.0, x * 0.1 + rng.normal(0.0, 0.0005, n))
    diag = _alpha_regression_diagnostics(y, x)
    assert diag is not None
    assert diag["alpha_tstat_df"] == diag["n_used"] - 2


def test_n_used_equals_n_total_not_n_informative():
    """Referenzfall-Struktur (AdxAtr/TSLA): n_informative < n_total -- n_used muss trotzdem
    n_total folgen, nicht n_informative."""
    rng = np.random.default_rng(7)
    n = 300
    x = np.zeros(n)
    y = np.zeros(n)
    x[:50] = rng.normal(0.0, 0.001, 50)
    y[:50] = x[:50] * 0.1 + rng.normal(0.0, 0.0005, 50)  # nur 50/300 Bars informativ (x UND y != 0)
    diag = _alpha_regression_diagnostics(y, x)
    assert diag is not None
    assert diag["n_informative"] < diag["n_total"]
    assert diag["n_used"] == diag["n_total"] == n
    assert diag["alpha_tstat_df"] == n - 2


def test_alpha_tstat_df_never_negative_for_minimal_sample():
    x = np.array([0.001, -0.002, 0.0015])
    y = np.array([0.0002, -0.0003, 0.0001])
    diag = _alpha_regression_diagnostics(y, x)
    assert diag is not None
    assert diag["alpha_tstat_df"] == max(0, diag["n_used"] - 2)
    assert diag["alpha_tstat_df"] >= 0


# ---------------------------------------------------------------------------------------------
# invariants.check_alpha_df_consistency
# ---------------------------------------------------------------------------------------------

def test_reference_case_adxatr_tsla_style_passes_with_the_fixed_df():
    """Katalog-Referenzfall-Struktur: n_used=1079 -> df=1077 (NICHT 735 == n_informative-2)."""
    records = [{"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
                "holdout_alpha_tstat_df": 1077, "holdout_alpha_n_used": 1079}]
    r = inv.check_alpha_df_consistency(records)
    assert r.passed is True


def test_the_pre_fix_735_value_would_have_failed():
    """Demonstriert das behobene Symptom: df=735 bei n_used=1079 ist INKONSISTENT."""
    records = [{"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
                "holdout_alpha_tstat_df": 735, "holdout_alpha_n_used": 1079}]
    r = inv.check_alpha_df_consistency(records)
    assert r.passed is False
    assert r.severity == "high"
    offender = r.actual["AdxAtrMomentumStrategy/TSLA.ETORO"]
    assert offender["expected_df"] == 1077


def test_inconclusive_without_both_fields():
    r = inv.check_alpha_df_consistency([{"strategy": "S", "symbol": "X"}])
    assert r.passed is True
    assert r.inconclusive is True
    assert r.severity == "high"


def test_partial_fields_skipped_not_crashed():
    records = [{"strategy": "S", "symbol": "X", "holdout_alpha_tstat_df": 5}]
    r = inv.check_alpha_df_consistency(records)
    assert r.passed is True
    assert r.inconclusive is True


def test_mixed_cohort_flags_only_the_offender():
    records = [
        {"strategy": "S1", "symbol": "X", "holdout_alpha_tstat_df": 998, "holdout_alpha_n_used": 1000},
        {"strategy": "S2", "symbol": "Y", "holdout_alpha_tstat_df": 500, "holdout_alpha_n_used": 1000},
    ]
    r = inv.check_alpha_df_consistency(records)
    assert r.passed is False
    assert "S2/Y" in r.actual
    assert "S1/X" not in r.actual


def test_wired_in_build_report():
    import inspect
    source = inspect.getsource(rpt._build_report)
    assert "check_alpha_df_consistency" in source


def test_check_alpha_df_consistency_appears_in_stream(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1284-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_alpha_df_consistency" in names


# ---------------------------------------------------------------------------------------------
# Feld-Durchreichung: backtest_runner -> parsing -> report
# ---------------------------------------------------------------------------------------------

def test_tournament_metrics_carries_n_used_field():
    from automation.optimizer.parsing import TournamentMetrics
    assert "oos_alpha_n_used" in TournamentMetrics.__dataclass_fields__


def test_study_record_stamps_holdout_alpha_n_used():
    import inspect
    source = inspect.getsource(rpt._study_record)
    assert "holdout_alpha_n_used" in source


def test_alpha_n_used_is_allowlisted_holdout_only():
    from automation.optimizer import run_optimization as ro
    assert "oos_alpha_n_used" in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
