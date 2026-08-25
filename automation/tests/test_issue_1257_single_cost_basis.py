"""Issue #1257 (GH #1127), Pitfall #454 in AGENTS.md — eine Kostenbasis fuer Return, Expectancy und
Alpha.

Symptom (ComboTrendVwap): ``holdout_total_return = +0,57 %`` (positiv) bei
``holdout_expectancy_capital_weighted = -0,52 %/Trade`` (negativ) — DIESELBEN 62 Trades, exakt um
die kalibrierte Slippage (78,4052 bps/Trade) auseinander.

Root-Cause: ``backtest_runner._apply_calibrated_slippage_deduction`` (#1078/#1226) korrigiert
AUSSCHLIESSLICH ``rt_pnls_with_ts`` (⇒ ``expectancy``). ``total_return``/``sortino_ratio``/PSR/die
α/β-Regression entstehen dagegen aus der SEPARATEN ``mtm_series``/``mtm_frames``-Equity-Kurve
(#465/#771-Prioritaet in ``_calculate_stats``) — diese blieb bis #1257 unkorrigiert.

Fix: ``backtest_runner._apply_calibrated_slippage_to_mtm_series`` zieht DIESELBE kalibrierte
p50-Slippage AN DER QUELLE von der MtM-Equity-Kurve ab (Stufenfunktion am Exit-Bar jedes
TRAILING_STOP-Round-Trips, kumulativ) — ``total_return``/``sortino_ratio``/PSR/α(t) teilen sich
damit dieselbe Kostenbasis wie ``expectancy``. ``_calculate_stats`` traegt zusaetzlich explizite
``_net``/``_gross``-Alias-/Traceability-Felder (``total_return_net``/``_gross``,
``expectancy_capital_weighted_net``/``_gross``); ``invariants.check_cost_basis_coherence``
(severity ``blocking``) prueft ``sign(total_return_net) == sign(expectancy_capital_weighted_net)``.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from automation import backtest_runner
from automation.backtest_runner import (
    _apply_calibrated_slippage_to_mtm_series, _calculate_stats,
)
from automation.optimizer import confirm, invariants as inv, parsing, run_optimization as ro


def _mtm(values: list[float], *, start: str = "2026-01-01", freq: str = "h") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx)


def _exit_ts_ns(mtm: pd.Series, bar_idx: int) -> int:
    return int(mtm.index[bar_idx].value)


# ---------------------------------------------------------------------------------------------
# _apply_calibrated_slippage_to_mtm_series
# ---------------------------------------------------------------------------------------------

def test_noop_when_slippage_bps_p50_is_zero():
    mtm = _mtm([1000.0, 1010.0, 1020.0])
    notionals = [(2000.0, _exit_ts_ns(mtm, 1))]
    meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        mtm, notionals, meta, slippage_bps_p50=0.0)
    assert adjusted is mtm
    assert n == 0


def test_noop_when_slippage_bps_p50_is_negative():
    mtm = _mtm([1000.0, 1010.0, 1020.0])
    notionals = [(2000.0, _exit_ts_ns(mtm, 1))]
    meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        mtm, notionals, meta, slippage_bps_p50=-5.0)
    assert adjusted is mtm
    assert n == 0


def test_noop_when_mtm_series_is_none_or_empty():
    notionals = [(2000.0, 0)]
    meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        None, notionals, meta, slippage_bps_p50=10.0)
    assert adjusted is None and n == 0
    empty = pd.Series([], dtype=float)
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        empty, notionals, meta, slippage_bps_p50=10.0)
    assert adjusted is empty and n == 0


def test_shift_applies_cumulatively_from_exit_bar_onward():
    """Ein einzelner TRAILING_STOP-Round-Trip am Bar-Index 2 senkt AB diesem Bar die Kurve DAUERHAFT
    (kumulativ) — vorherige Bars bleiben unveraendert (die Equity-Kurve ist ein LEVEL, kein
    periodischer Return)."""
    mtm = _mtm([1000.0, 1010.0, 1020.0, 1030.0, 1040.0])
    notionals = [(2000.0, _exit_ts_ns(mtm, 2))]
    meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        mtm, notionals, meta, slippage_bps_p50=50.0)  # 0.5% * 2000 = 10.0
    assert n == 1
    assert adjusted.iloc[0] == pytest.approx(1000.0)
    assert adjusted.iloc[1] == pytest.approx(1010.0)
    assert adjusted.iloc[2] == pytest.approx(1010.0)  # 1020 - 10
    assert adjusted.iloc[3] == pytest.approx(1020.0)  # 1030 - 10
    assert adjusted.iloc[4] == pytest.approx(1030.0)  # 1040 - 10
    # Original bleibt unveraendert (kein In-Place-Mutieren).
    assert mtm.iloc[2] == 1020.0


def test_non_trailing_stop_exits_are_ignored():
    mtm = _mtm([1000.0, 1010.0, 1020.0])
    notionals = [(2000.0, _exit_ts_ns(mtm, 1))]
    meta = [{"exit_reason": "TIME_BOX"}]
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        mtm, notionals, meta, slippage_bps_p50=50.0)
    assert n == 0
    assert adjusted is mtm


def test_multiple_round_trips_accumulate():
    mtm = _mtm([1000.0, 1010.0, 1020.0, 1030.0, 1040.0])
    notionals = [(2000.0, _exit_ts_ns(mtm, 1)), (1000.0, _exit_ts_ns(mtm, 3))]
    meta = [{"exit_reason": "TRAILING_STOP"}, {"exit_reason": "TRAILING_STOP"}]
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        mtm, notionals, meta, slippage_bps_p50=50.0)  # deltas: 10.0, 5.0
    assert n == 2
    assert adjusted.iloc[0] == pytest.approx(1000.0)
    assert adjusted.iloc[1] == pytest.approx(1000.0)   # 1010 - 10
    assert adjusted.iloc[2] == pytest.approx(1010.0)   # 1020 - 10
    assert adjusted.iloc[3] == pytest.approx(1015.0)   # 1030 - 10 - 5
    assert adjusted.iloc[4] == pytest.approx(1025.0)   # 1040 - 10 - 5


def test_exit_after_last_bar_is_skipped_safely():
    mtm = _mtm([1000.0, 1010.0])
    far_future_ts = int(pd.Timestamp("2030-01-01").value)
    notionals = [(2000.0, far_future_ts)]
    meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        mtm, notionals, meta, slippage_bps_p50=50.0)
    assert n == 0
    assert adjusted is mtm


def test_missing_or_zero_notional_is_skipped_safely():
    mtm = _mtm([1000.0, 1010.0, 1020.0])
    notionals = [(0.0, _exit_ts_ns(mtm, 1)), (None, _exit_ts_ns(mtm, 2))]
    meta = [{"exit_reason": "TRAILING_STOP"}, {"exit_reason": "TRAILING_STOP"}]
    adjusted, n = _apply_calibrated_slippage_to_mtm_series(
        mtm, notionals, meta, slippage_bps_p50=50.0)
    assert n == 0
    assert adjusted is mtm


# ---------------------------------------------------------------------------------------------
# _calculate_stats — gross/net wiring
# ---------------------------------------------------------------------------------------------

def test_total_return_net_is_alias_of_total_return():
    mtm = _mtm([1000.0, 1010.0, 1020.0])
    m = _calculate_stats([10.0], [], 1000.0, mtm_series=mtm)
    assert m["total_return_net"] == m["total_return"]


def test_total_return_gross_is_none_without_gross_series():
    """Rueckwaertskompatibel: kein ``mtm_series_gross`` uebergeben ⇒ ``total_return_gross`` bleibt
    ``None`` (kein Raten) — bestehende Call-Sites, die den neuen Parameter nicht kennen, bleiben
    unveraendert funktionsfaehig."""
    mtm = _mtm([1000.0, 1010.0, 1020.0])
    m = _calculate_stats([10.0], [], 1000.0, mtm_series=mtm)
    assert m["total_return_gross"] is None
    assert m["expectancy_capital_weighted_gross"] is None


def test_total_return_gross_worse_than_net_when_slippage_applied():
    """Reproduziert die ComboTrendVwap-Symptomatik: die NET-Kurve (nach kalibrierter Slippage) hat
    einen niedrigeren Endwert als die GROSS-Kurve (davor) — total_return_net < total_return_gross."""
    mtm_gross = _mtm([1000.0, 1010.0, 1020.0, 1005.7])
    notionals = [(2000.0, _exit_ts_ns(mtm_gross, 3))]
    meta = [{"exit_reason": "TRAILING_STOP"}]
    mtm_net, n = _apply_calibrated_slippage_to_mtm_series(
        mtm_gross, notionals, meta, slippage_bps_p50=100.0)  # 1% * 2000 = 20.0
    assert n == 1
    m = _calculate_stats([10.0], [], 1000.0, mtm_series=mtm_net, mtm_series_gross=mtm_gross)
    assert m["total_return_gross"] == pytest.approx(1005.7 / 1000.0 - 1.0)
    assert m["total_return_net"] == pytest.approx((1005.7 - 20.0) / 1000.0 - 1.0)
    assert m["total_return_net"] < m["total_return_gross"]


def test_expectancy_capital_weighted_gross_vs_net():
    """Der Nennerboden (Notional) ist zwischen Gross/Net IDENTISCH — nur der Zaehler (Σpnl)
    unterscheidet sich. 3 Trades, alle oberhalb des 5%-Notional-Bodens."""
    pnl_net = [10.0, -5.0, 20.0]
    pnl_gross = [15.0, 0.0, 25.0]  # je 5.0 hoeher (Slippage-Betrag)
    notional_list = [1000.0, 1000.0, 1000.0]
    m = _calculate_stats(
        pnl_net, [], 10_000.0, notional_list=notional_list, pnl_list_gross=pnl_gross)
    assert m["expectancy_capital_weighted_net"] == pytest.approx(sum(pnl_net) / sum(notional_list))
    assert m["expectancy_capital_weighted_gross"] == pytest.approx(
        sum(pnl_gross) / sum(notional_list))
    assert m["expectancy_capital_weighted_net"] == m["expectancy_capital_weighted"]
    assert m["expectancy_capital_weighted_gross"] > m["expectancy_capital_weighted_net"]


def test_pnl_list_gross_length_mismatch_is_ignored_safely():
    """Ein laengenabweichendes ``pnl_list_gross`` (strukturell inkonsistenter Aufrufer) darf nicht
    crashen — die Gross-Felder bleiben dann ``None`` (fail-open, analog anderen optionalen
    Parallel-Listen in diesem Modul)."""
    m = _calculate_stats(
        [10.0, -5.0], [], 10_000.0, notional_list=[1000.0, 1000.0],
        pnl_list_gross=[15.0])  # zu kurz
    assert m["expectancy_capital_weighted_gross"] is None


# ---------------------------------------------------------------------------------------------
# invariants.check_cost_basis_coherence
# ---------------------------------------------------------------------------------------------

def _record(*, total_return_net, expectancy_net, trades=10, strategy="S", symbol="SYM.ETORO"):
    return {
        "strategy": strategy, "symbol": symbol, "holdout_total_trades": trades,
        "holdout_total_return_net": total_return_net,
        "holdout_expectancy_capital_weighted_net": expectancy_net,
    }


def test_inconclusive_without_any_net_fields():
    result = inv.check_cost_basis_coherence([{"strategy": "S", "symbol": "X.ETORO"}])
    assert result.passed is True
    assert result.inconclusive is True
    assert result.severity == "blocking"


def test_not_applicable_without_holdout_trades():
    result = inv.check_cost_basis_coherence(
        [{"strategy": "S", "symbol": "X.ETORO", "holdout_total_return_net": 0.01,
          "holdout_expectancy_capital_weighted_net": 0.001}])
    assert result.passed is True
    assert result.inconclusive is True


def test_passes_when_both_positive():
    result = inv.check_cost_basis_coherence(
        [_record(total_return_net=0.02, expectancy_net=0.001)])
    assert result.passed is True


def test_passes_when_both_negative():
    result = inv.check_cost_basis_coherence(
        [_record(total_return_net=-0.02, expectancy_net=-0.001)])
    assert result.passed is True


def test_passes_when_one_is_exact_zero():
    result = inv.check_cost_basis_coherence(
        [_record(total_return_net=0.0, expectancy_net=-0.001)])
    assert result.passed is True
    result2 = inv.check_cost_basis_coherence(
        [_record(total_return_net=0.02, expectancy_net=0.0)])
    assert result2.passed is True


def test_fails_on_combotrendvwap_like_sign_mismatch():
    """Reproduziert das #1127-Symptom: total_return_net positiv, expectancy_capital_weighted_net
    negativ, dieselben Trades."""
    result = inv.check_cost_basis_coherence(
        [_record(total_return_net=0.0057, expectancy_net=-0.0052)])
    assert result.passed is False
    assert result.severity == "blocking"
    assert "S/SYM.ETORO" in result.actual


def test_fails_on_opposite_sign_mismatch():
    result = inv.check_cost_basis_coherence(
        [_record(total_return_net=-0.01, expectancy_net=0.002)])
    assert result.passed is False


# ---------------------------------------------------------------------------------------------
# parsing.py / confirm.py / report.py bridging
# ---------------------------------------------------------------------------------------------

def test_parse_tournament_reads_the_four_new_fields(tmp_path):
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1, "median_is_sortino": 0.4,
            "oos_metrics": {
                "sortino_ratio": 1.1, "total_trades": 5,
                "total_return_net": 0.02, "total_return_gross": 0.03,
                "expectancy_capital_weighted_net": -0.001,
                "expectancy_capital_weighted_gross": 0.001,
            },
        },
        "full_results": [],
    }
    path = tmp_path / "tournament_result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = parsing.parse_tournament(path)
    assert metrics.oos_total_return_net == pytest.approx(0.02)
    assert metrics.oos_total_return_gross == pytest.approx(0.03)
    assert metrics.oos_expectancy_capital_weighted_net == pytest.approx(-0.001)
    assert metrics.oos_expectancy_capital_weighted_gross == pytest.approx(0.001)


def test_parse_tournament_missing_fields_is_none(tmp_path):
    """Legacy-JSON ohne die Felder (Pre-#1257) laedt fehlerfrei mit None."""
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1, "median_is_sortino": 0.4,
            "oos_metrics": {"sortino_ratio": 1.1, "total_trades": 5},
        },
        "full_results": [],
    }
    path = tmp_path / "tournament_result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = parsing.parse_tournament(path)
    assert metrics.oos_total_return_net is None
    assert metrics.oos_total_return_gross is None
    assert metrics.oos_expectancy_capital_weighted_net is None
    assert metrics.oos_expectancy_capital_weighted_gross is None


class _M:
    def __getattr__(self, name):
        return None


def test_metrics_dict_carries_all_four_fields():
    m = _M()
    m.oos_total_return_net = 0.02
    m.oos_total_return_gross = 0.03
    m.oos_expectancy_capital_weighted_net = -0.001
    m.oos_expectancy_capital_weighted_gross = 0.001
    d = confirm._metrics_dict(m)
    assert d["oos_total_return_net"] == 0.02
    assert d["oos_total_return_gross"] == 0.03
    assert d["oos_expectancy_capital_weighted_net"] == -0.001
    assert d["oos_expectancy_capital_weighted_gross"] == 0.001


def test_allowlist_entries_name_the_report_field():
    for field, report_field in (
        ("oos_total_return_net", "holdout_total_return_net"),
        ("oos_total_return_gross", "holdout_total_return_gross"),
        ("oos_expectancy_capital_weighted_net", "holdout_expectancy_capital_weighted_net"),
        ("oos_expectancy_capital_weighted_gross", "holdout_expectancy_capital_weighted_gross"),
    ):
        assert field in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
        reason = ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS[field]
        assert "confirm.py-Re-Evaluation" in reason
        assert report_field in reason


def test_check_cost_basis_coherence_is_wired_in_report():
    import inspect as _inspect
    from automation.optimizer import report as _report
    source = _inspect.getsource(_report._build_report)
    assert "check_cost_basis_coherence" in source


# ---------------------------------------------------------------------------------------------
# extract_metrics — structural Zero-Regression assertions (analog #1078/#1226 pattern)
# ---------------------------------------------------------------------------------------------

def test_extract_metrics_gates_the_mtm_deduction_behind_the_same_switch():
    import inspect
    source = inspect.getsource(backtest_runner.extract_metrics)
    idx_gate = source.rindex("if _read_apply_calibrated_slippage_in_selection():")
    idx_call = source.index("_apply_calibrated_slippage_to_mtm_series(")
    assert idx_gate < idx_call


def test_extract_metrics_applies_mtm_deduction_before_fold_slicing():
    """Reihenfolge-Kontrakt: der mtm-Abzug muss VOR der is_mtm/oos_mtm-Fold-Slicing-Berechnung
    laufen, sonst erben die abgeleiteten Slices nicht die korrigierte Kurve."""
    import inspect
    source = inspect.getsource(backtest_runner.extract_metrics)
    idx_mtm_fix = source.index("_apply_calibrated_slippage_to_mtm_series(")
    idx_fold_slice = source.index("is_mtm = _slice_half_open(mtm_series")
    assert idx_mtm_fix < idx_fold_slice


def test_production_config_reward_semantics_version_at_least_26():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["reward_semantics_version"] >= 26
