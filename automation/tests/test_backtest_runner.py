import pytest
from automation.backtest_runner import create_mock_instrument

def test_create_mock_instrument_size_precision_0():
    inst = create_mock_instrument("AAPL.NASDAQ", size_precision=0)
    assert inst.size_precision == 2
    assert inst.size_increment.as_double() < 1.0

def test_create_mock_instrument_size_precision_none():
    inst = create_mock_instrument("AAPL.NASDAQ", size_precision=None)
    assert inst.size_precision == 2

def test_create_mock_instrument_size_precision_8():
    inst = create_mock_instrument("AAPL.NASDAQ", size_precision=8)
    assert inst.size_precision == 8

def test_create_mock_instrument_size_precision_5():
    inst = create_mock_instrument("EURUSD.FOREX", size_precision=5)
    assert inst.size_precision == 5

from unittest.mock import MagicMock
from automation.backtest_runner import extract_metrics
import pandas as pd

def test_extract_metrics_holding_time():
    engine_mock = MagicMock()
    # Create mock df_fills with specific timestamps
    # For a hold time of 1 hour = 3600 seconds = 3600 * 1e9 ns
    # ts_event expects ns. Let's make an entry, then exit 3600 * 1e9 ns later

    # Let's say entry is at ts_event = 1000 * 1e9
    # Exit is at ts_event = 4600 * 1e9

    records = [
        # Buy 1.0 @ 100.0
        {
            "instrument_id": "AAPL.NASDAQ",
            "last_qty": 1.0,
            "last_px": 100.0,
            "order_side": "BUY",
            "ts_event": 1000 * 10**9,
        },
        # Sell 1.0 @ 110.0 (Profit = 10.0, holding time = 3600s)
        {
            "instrument_id": "AAPL.NASDAQ",
            "last_qty": 1.0,
            "last_px": 110.0,
            "order_side": "SELL",
            "ts_event": 4600 * 10**9,
        }
    ]
    df_fills = pd.DataFrame.from_records(records)
    engine_mock.trader.generate_fills_report.return_value = df_fills

    metrics_result = extract_metrics(engine_mock, starting_capital=10000.0)

    assert metrics_result is not None
    m = metrics_result["metrics"] if "metrics" in metrics_result else metrics_result
    assert m.get("total_trades", 0) > 0, (
        f"Regressions-Fehler: total_trades ist 0. FIFO-Pfad/Entpackung fehlgeschlagen. "
        f"Metriken-Snapshot: {m}"
    )

    # 1 trade closed
    assert m["total_trades"] == 1
    # Average holding time should be 3600.0 s
    assert m["avg_holding_time_s"] == 3600.0
    assert m["median_holding_time_s"] == 3600.0

def test_extract_metrics_holding_time_short():
    engine_mock = MagicMock()
    # Let's say entry is SELL at ts_event = 2000 * 1e9
    # Exit is BUY at ts_event = 2100 * 1e9 (Hold = 100s)

    records = [
        {
            "instrument_id": "TSLA.NASDAQ",
            "last_qty": 0.5,
            "last_px": 200.0,
            "order_side": "SELL",
            "ts_event": 2000 * 10**9,
        },
        {
            "instrument_id": "TSLA.NASDAQ",
            "last_qty": 0.5,
            "last_px": 190.0,
            "order_side": "BUY",
            "ts_event": 2100 * 10**9,
        }
    ]
    df_fills = pd.DataFrame.from_records(records)
    engine_mock.trader.generate_fills_report.return_value = df_fills

    metrics_result = extract_metrics(engine_mock, starting_capital=10000.0)
    m = metrics_result["metrics"] if "metrics" in metrics_result else metrics_result
    assert m.get("total_trades", 0) > 0, (
        f"Regressions-Fehler: total_trades ist 0. FIFO-Pfad/Entpackung fehlgeschlagen. "
        f"Metriken-Snapshot: {m}"
    )

    assert m["total_trades"] == 1
    assert m["avg_holding_time_s"] == 100.0
    assert m["median_holding_time_s"] == 100.0


def test_extract_metrics_weighted_holding_time():
    engine_mock = MagicMock()
    # Buy 10 units at ts=0
    # Sell 2 units at ts=10 (hold = 10s)
    # Sell 8 units at ts=20 (hold = 20s)

    # Expected weighted avg: (2*10 + 8*20) / 10 = (20 + 160) / 10 = 180 / 10 = 18.0 s

    records = [
        {
            "instrument_id": "MSFT.NASDAQ",
            "last_qty": 10.0,
            "last_px": 100.0,
            "order_side": "BUY",
            "ts_event": 0,
        },
        {
            "instrument_id": "MSFT.NASDAQ",
            "last_qty": 2.0,
            "last_px": 110.0,
            "order_side": "SELL",
            "ts_event": 10 * 10**9,
        },
        {
            "instrument_id": "MSFT.NASDAQ",
            "last_qty": 8.0,
            "last_px": 120.0,
            "order_side": "SELL",
            "ts_event": 20 * 10**9,
        }
    ]
    df_fills = pd.DataFrame.from_records(records)
    engine_mock.trader.generate_fills_report.return_value = df_fills

    metrics_result = extract_metrics(engine_mock, starting_capital=10000.0)
    m = metrics_result["metrics"] if "metrics" in metrics_result else metrics_result
    assert m.get("total_trades", 0) > 0, (
        f"Regressions-Fehler: total_trades ist 0. FIFO-Pfad/Entpackung fehlgeschlagen. "
        f"Metriken-Snapshot: {m}"
    )

    assert m["total_trades"] == 2
    assert m["avg_holding_time_s"] == 18.0

from automation.backtest_runner import select_winners

def test_select_winners_order_independence():
    """
    Tests that select_winners selects the best strategy based on the composite score,
    regardless of the iteration order.
    A regression test for Pitfall #35 (Issue #134) where the winner was always the first
    eligible strategy because compute_tournament_score returned 0.0.
    """
    tournament_cfg = {
        "min_trades": 20,
        "min_sortino": 0.0,
        "min_profit_factor": 1.0,
        "max_drawdown": 1.0,
        "min_win_rate": 0.0,
        "min_total_return": 0.0,
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": [],
        "scoring": {
            "sortino_weight": 0.4,
            "profit_factor_weight": 0.3,
            "win_rate_weight": 0.2,
            "drawdown_penalty_weight": 0.1
        }
    }

    # We provide two strategies for SYM1.
    # We pass the worse strategy first, and the better strategy second.
    # The scoring is: Sortino (0.4) + PF (0.3) + WinRate (0.2) - MaxDD (0.1)

    # Worse strategy
    strat_worse = {
        "symbol": "SYM1",
        "strategy": "WorseStrategy",
        "metrics": {
            "total_trades": 25,
            "sortino_ratio": 1.0,
            "profit_factor": 1.1,
            "win_rate": 0.4,
            "max_drawdown": 0.2,
            "total_return": 0.1
        }
    }

    # Better strategy
    strat_better = {
        "symbol": "SYM1",
        "strategy": "BetterStrategy",
        "metrics": {
            "total_trades": 25,
            "sortino_ratio": 3.0,
            "profit_factor": 2.5,
            "win_rate": 0.7,
            "max_drawdown": 0.05,
            "total_return": 0.3
        }
    }

    all_results = [strat_worse, strat_better]

    per_symbol_winners, aggregate_winner, _ = select_winners(all_results, tournament_cfg)

    # SYM1 should be in the winners
    assert "SYM1" in per_symbol_winners

    # The chosen strategy for SYM1 should be 'BetterStrategy'
    winner = per_symbol_winners["SYM1"]
    assert winner["strategy"] == "BetterStrategy", f"Expected BetterStrategy, but got {winner['strategy']}."

    # To be absolutely sure, let's reverse the order and test again
    all_results_reversed = [strat_better, strat_worse]
    per_symbol_winners_rev, _, _ = select_winners(all_results_reversed, tournament_cfg)

    winner_rev = per_symbol_winners_rev["SYM1"]
    assert winner_rev["strategy"] == "BetterStrategy", f"Expected BetterStrategy, but got {winner_rev['strategy']} when list was reversed."


def test_select_winners_tie_breaker():
    """
    Tests that select_winners uses total_return as a determinisitic tie-breaker
    when composite scores are exactly equal.
    """
    tournament_cfg = {
        "min_trades": 20,
        "min_sortino": 0.0,
        "min_profit_factor": 1.0,
        "max_drawdown": 1.0,
        "min_win_rate": 0.0,
        "min_total_return": 0.0,
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": [],
        "scoring": {
            "sortino_weight": 0.4,
            "profit_factor_weight": 0.3,
            "win_rate_weight": 0.2,
            "drawdown_penalty_weight": 0.1
        }
    }

    # Same base metrics so the rank normalizer will give them identical ranks (1.0 for all)
    # Therefore the composite score will be exactly the same.
    # We differentiate only by raw `total_return`.
    strat_lower_return = {
        "symbol": "SYM2",
        "strategy": "LowerReturnStrategy",
        "metrics": {
            "total_trades": 25,
            "sortino_ratio": 2.0,
            "profit_factor": 2.0,
            "win_rate": 0.5,
            "max_drawdown": 0.1,
            "total_return": 0.2
        }
    }

    strat_higher_return = {
        "symbol": "SYM2",
        "strategy": "HigherReturnStrategy",
        "metrics": {
            "total_trades": 25,
            "sortino_ratio": 2.0,
            "profit_factor": 2.0,
            "win_rate": 0.5,
            "max_drawdown": 0.1,
            "total_return": 0.5 # Better tie breaker
        }
    }

    # Test lower first
    all_results = [strat_lower_return, strat_higher_return]
    per_symbol_winners, _, _ = select_winners(all_results, tournament_cfg)
    assert per_symbol_winners["SYM2"]["strategy"] == "HigherReturnStrategy"

    # Test higher first
    all_results_rev = [strat_higher_return, strat_lower_return]
    per_symbol_winners_rev, _, _ = select_winners(all_results_rev, tournament_cfg)
    assert per_symbol_winners_rev["SYM2"]["strategy"] == "HigherReturnStrategy"

from automation.backtest_runner import check_data_span
import collections

def test_check_data_span_sufficient():
    Tick = collections.namedtuple("Tick", ["ts_event"])
    # 150 days exact
    start_ts = 0
    end_ts = 150 * 86400 * 1_000_000_000
    ticks = [Tick(ts_event=start_ts), Tick(ts_event=end_ts)]

    is_sufficient, span_days, req_days = check_data_span(ticks, 150, 1.0)
    assert is_sufficient is True
    assert span_days == 150.0

def test_check_data_span_within_tolerance():
    Tick = collections.namedtuple("Tick", ["ts_event"])
    # 149.8 days span, tolerance is 1.0, required is 150
    start_ts = 0
    end_ts = int(149.8 * 86400 * 1_000_000_000)
    ticks = [Tick(ts_event=start_ts), Tick(ts_event=end_ts)]

    is_sufficient, span_days, req_days = check_data_span(ticks, 150, 1.0)
    assert is_sufficient is True
    assert round(span_days, 1) == 149.8

def test_check_data_span_insufficient():
    Tick = collections.namedtuple("Tick", ["ts_event"])
    # 100 days span, tolerance is 1.0, required is 150
    start_ts = 0
    end_ts = 100 * 86400 * 1_000_000_000
    ticks = [Tick(ts_event=start_ts), Tick(ts_event=end_ts)]

    is_sufficient, span_days, req_days = check_data_span(ticks, 150, 1.0)
    assert is_sufficient is False
    assert span_days == 100.0

import collections
from unittest.mock import MagicMock, patch, mock_open

def test_run_single_backtest_worker_logging_warning_within_tolerance():
    Tick = collections.namedtuple("Tick", ["ts_event"])
    start_ts = type("TS", (), {"value": 0, "__sub__": lambda self, other: type("TD", (), {"value": self.value - other.value})()})()
    end_ts = type("TS", (), {"value": int(149.8 * 86400 * 1_000_000_000), "__sub__": lambda self, other: type("TD", (), {"value": self.value - other.value})()})()
    ticks = [Tick(ts_event=start_ts), Tick(ts_event=end_ts)]

    strat = {"_walk_forward_days": 150, "strategy_class": "MockStrategy", "strategy_module": "automation.strategies.mock", "config_class": "MockConfig"}

    with patch("automation.backtest_runner.load_ticks_from_catalog", return_value=ticks), \
         patch("automation.backtest_runner.pd.Timestamp", return_value=MagicMock()), \
         patch("automation.backtest_runner._empty_result", return_value={}), \
         patch("builtins.open", mock_open()) as m_open:

        # We will catch the Exception raised by the next step to stop the function early
        try:
            from automation.backtest_runner import run_single_backtest_worker
            run_single_backtest_worker(
                "MOCK.SYM", "1H", strat, "path", None, None, 10000.0, False, "dir", "log.txt", 1.0
            )
        except Exception as e:
            print(e)
            pass

        # Check that wlog was called with the correct warning message
        written_lines = [call.args[0] for call in m_open().write.call_args_list]
        assert any("Knappe Datenspanne, fahre fort" in line for line in written_lines)
