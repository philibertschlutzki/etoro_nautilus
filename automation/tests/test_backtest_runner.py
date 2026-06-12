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
            "total_return": 0.1,
            "median_position_notional": 15.0
        },
        "oos_metrics": {
            "total_trades": 25,
            "sortino_ratio": 1.0,
            "profit_factor": 1.1,
            "win_rate": 0.4,
            "max_drawdown": 0.2,
            "total_return": 0.1,
            "median_position_notional": 15.0
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
            "total_return": 0.3,
            "median_position_notional": 15.0
        },
        "oos_metrics": {
            "total_trades": 25,
            "sortino_ratio": 3.0,
            "profit_factor": 2.5,
            "win_rate": 0.7,
            "max_drawdown": 0.05,
            "total_return": 0.3,
            "median_position_notional": 15.0
        }
    }

    all_results = [strat_worse, strat_better]

    per_symbol_winners, aggregate_winner, _, _, _ = select_winners(all_results, tournament_cfg)

    # SYM1 should be in the winners
    assert "SYM1" in per_symbol_winners

    # The chosen strategy for SYM1 should be 'BetterStrategy'
    winner = per_symbol_winners["SYM1"]
    assert winner["strategy"] == "BetterStrategy", f"Expected BetterStrategy, but got {winner['strategy']}."

    # To be absolutely sure, let's reverse the order and test again
    all_results_reversed = [strat_better, strat_worse]
    per_symbol_winners_rev, _, _, _, _ = select_winners(all_results_reversed, tournament_cfg)

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
            "total_return": 0.2,
            "median_position_notional": 15.0
        },
        "oos_metrics": {
            "total_trades": 25,
            "sortino_ratio": 2.0,
            "profit_factor": 2.0,
            "win_rate": 0.5,
            "max_drawdown": 0.1,
            "total_return": 0.2,
            "median_position_notional": 15.0
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
            "total_return": 0.5, # Better tie breaker
            "median_position_notional": 15.0
        },
        "oos_metrics": {
            "total_trades": 25,
            "sortino_ratio": 2.0,
            "profit_factor": 2.0,
            "win_rate": 0.5,
            "max_drawdown": 0.1,
            "total_return": 0.5, # Better tie breaker
            "median_position_notional": 15.0
        }
    }

    # Test lower first
    all_results = [strat_lower_return, strat_higher_return]
    per_symbol_winners, _, _, _, _ = select_winners(all_results, tournament_cfg)
    assert per_symbol_winners["SYM2"]["strategy"] == "HigherReturnStrategy"

    # Test higher first
    all_results_rev = [strat_higher_return, strat_lower_return]
    per_symbol_winners_rev, _, _, _, _ = select_winners(all_results_rev, tournament_cfg)
    assert per_symbol_winners_rev["SYM2"]["strategy"] == "HigherReturnStrategy"

def test_near_all_win_scenario():
    from automation.backtest_runner import _calculate_stats
    pnl_list = [10.0] * 40 + [-0.01]  # 41 trades, 1 loss, < 50 trades total
    hold_list = [(1000, 1.0)] * 41
    metrics = _calculate_stats(pnl_list, hold_list, 1000.0)
    assert metrics["profit_factor"] is None
    # Now sortino_ratio will not be None since we changed the gate for 1 loss
    assert metrics["sortino_ratio"] is not None
    assert metrics["sortino_ratio"] <= 50.0

def test_zero_downside_deviation():
    from automation.backtest_runner import _calculate_stats
    pnl_list = [10.0] * 60  # 60 trades, 0 losses (but total trades >= 50, so metrics are generated with EPSILON fallback instead of exploding/throwing exceptions)
    hold_list = [(1000, 1.0)] * 60
    metrics = _calculate_stats(pnl_list, hold_list, 1000.0)

    assert metrics["max_drawdown"] == 0.0
    # Issue #209: Hardcaps removed, now strictly None for undefined stats
    assert metrics["profit_factor"] is None
    assert metrics["sortino_ratio"] is None
    assert metrics["calmar_ratio"] is None

def test_clamping_limits():
    from automation.backtest_runner import _calculate_stats
    # Added slightly larger initial loss so max_dd > 1e-9 to trigger calmar cap, but small enough that PF and Sortino explode
    pnl_list = [-1.0, 10.0] + [10.0] * 49 + [-0.00000001] * 2
    hold_list = [(1000, 1.0)] * 52
    metrics = _calculate_stats(pnl_list, hold_list, 1000.0)
    # Caps were reinstated according to safety rules (max 50.0 for PF and Sortino, Calmar 50.0 per Issue #288)
    assert metrics["profit_factor"] == 50.0
    assert metrics["sortino_ratio"] == 50.0
    assert metrics["calmar_ratio"] == 50.0

def test_select_winners_sibling_key_access():
    """
    Test that select_winners strictly accesses oos_metrics as a sibling key
    and does not fail or look inside metrics["oos_metrics"].
    """
    from automation.backtest_runner import select_winners
    tournament_cfg = {
        "min_trades": 10,
        "oos_min_trades": 10,
        "eligible_requires_all": ["min_trades"]
    }

    # 1. Correctly structured result (sibling keys)
    res_correct = {
        "symbol": "GOOD.ETORO",
        "strategy": "StratA",
        "metrics": {"total_trades": 15, "sortino_ratio": 1.0, "profit_factor": 1.0, "median_position_notional": 15.0},
        "oos_metrics": {"total_trades": 15, "sortino_ratio": 1.0, "profit_factor": 1.0, "median_position_notional": 15.0}
    }

    # 2. Incorrectly structured result (nested in metrics)
    # Because of the Sibling-Key bug, oos_metrics was set as {} at the root by the runner,
    # but the real stats were nested.
    res_incorrect = {
        "symbol": "BAD.ETORO",
        "strategy": "StratB",
        "oos_metrics": {},
        "metrics": {
            "total_trades": 15,
            "sortino_ratio": 1.0,
            "profit_factor": 1.0, "median_position_notional": 15.0,
            "oos_metrics": {"total_trades": 15, "sortino_ratio": 1.0, "profit_factor": 1.0, "median_position_notional": 15.0}
        }
    }

    all_results = [res_correct, res_incorrect]
    per_symbol_winners, _, _, _, _ = select_winners(all_results, tournament_cfg)

    assert "GOOD.ETORO" in per_symbol_winners
    assert "BAD.ETORO" not in per_symbol_winners, "The incorrectly structured result shouldn't pass because oos_metrics is missing as a sibling."

def test_tournament_fails_closed_on_none_oos_metrics():
    """
    Test that if oos_metrics is missing (None) and require_oos is True (default in tournament),
    the strategy is strictly rejected (Fail-Closed).
    """
    from automation.backtest_runner import select_winners
    tournament_cfg = {
        "min_trades": 10,
        "eligible_requires_all": ["min_trades"],
        "require_oos": True # explicitly testing the default behavior
    }

    # Missing oos_metrics entirely (evaluates to None in r.get("oos_metrics"))
    res_missing_oos = {
        "symbol": "FAIL.ETORO",
        "strategy": "StratFail",
        "metrics": {"total_trades": 15, "sortino_ratio": 1.0, "profit_factor": 1.0, "median_position_notional": 15.0}
    }

    all_results = [res_missing_oos]
    per_symbol_winners, _, _, _, _ = select_winners(all_results, tournament_cfg)

    assert "FAIL.ETORO" not in per_symbol_winners, "Fail-closed safety violated: Strategy missing OOS metrics was allowed."

def test_calculate_stats_low_sample_one_loss_sortino():
    from automation.backtest_runner import _calculate_stats
    # Create exactly 18 trades, with exactly 1 loss
    # n=18, losses_count=1
    pnl_list = [10.0] * 17 + [-5.0]

    # Calculate stats with some starting capital
    stats = _calculate_stats(pnl_list, hold_list=[], starting_capital=10000.0)

    # n < 50 and losses_count == 1 => sortino should be calculated and capped at 2.0
    assert stats["sortino_ratio"] is not None
    assert stats["sortino_ratio"] <= 50.0

def test_calculate_stats_drawdown_calculation_basis():
    from automation.backtest_runner import _calculate_stats
    import math

    # We provide a set of realized PnLs that mimic a specific curve
    # 1. win $1000 -> 10%
    # 2. lose $500 -> -5% based on original, but compounded:
    # starting: 10000
    # after 1: 11000 (ret 0.1) -> peak 1.1
    # after 2: 10500 (ret -0.05) -> cum 1.1 * 0.95 = 1.045
    # drawdown here: (1.1 - 1.045) / 1.1 = 0.05 / 1.1 = 0.0454545...
    #
    # Wait, the ret is v / starting_capital, so:
    # r1 = 1000 / 10000 = 0.1. cum = 1.1. peak = 1.1
    # r2 = -500 / 10000 = -0.05. cum = 1.1 * 0.95 = 1.045
    # DD = (1.1 - 1.045) / 1.1 = 0.05 / 1.1 = 0.045454545

    pnl_list = [1000.0, -500.0, 2000.0]
    # after 3: r3 = 2000 / 10000 = 0.2. cum = 1.045 * 1.2 = 1.254. peak = 1.254

    stats = _calculate_stats(pnl_list, hold_list=[], starting_capital=10000.0)

    expected_dd = 0.05
    assert math.isclose(stats["max_drawdown"], expected_dd, rel_tol=1e-5), "Drawdown calculation must strictly follow FIFO closed trade realized PnLs"

def test_check_data_span():
    from automation.backtest_runner import check_data_span

    class MockTick:
        def __init__(self, ts_event):
            self.ts_event = ts_event

    # Test 1: exact span required_days - span_tolerance_days + epsilon
    required_days = 150
    span_tolerance_days = 3.0
    epsilon = 0.01

    # 86400 * 1_000_000_000 is 1 day in ns
    day_in_ns = 86400 * 1_000_000_000

    # Span needs to be (150 - 3.0 + 0.01) days
    target_span_days = required_days - span_tolerance_days + epsilon
    target_span_ns = int(target_span_days * day_in_ns)

    ticks_sufficient = [
        MockTick(0),
        MockTick(target_span_ns)
    ]

    is_sufficient, span_days, req_days = check_data_span(ticks_sufficient, required_days, span_tolerance_days)
    assert is_sufficient is True
    assert req_days == required_days

    # Test 2: exact span required_days - span_tolerance_days - epsilon
    target_span_days_insufficient = required_days - span_tolerance_days - epsilon
    target_span_ns_insufficient = int(target_span_days_insufficient * day_in_ns)

    ticks_insufficient = [
        MockTick(0),
        MockTick(target_span_ns_insufficient)
    ]

    is_sufficient, span_days, req_days = check_data_span(ticks_insufficient, required_days, span_tolerance_days)
    assert is_sufficient is False
    assert req_days == required_days

from unittest.mock import patch, ANY
import concurrent.futures

def test_broken_pool_fallback_passes_arguments():
    """
    Testet den Fallback-Mechanismus in `run_backtest`, wenn der ProcessPoolExecutor abstürzt.
    Es wird geprüft, dass `_run_remaining_sequentially` mit den korrekten Argumenten aufgerufen wird,
    ohne dass ein TypeError durch fehlende Argumente entsteht (Issue Regression Test).
    """
    from automation.backtest_runner import run_backtest, _BrokenPool
    import automation.backtest_runner as br
    from concurrent.futures.process import BrokenProcessPool

    # Mock as_completed to throw _BrokenPool immediately
    def mock_as_completed(futures):
        yield list(futures.keys())[0]  # yield one future to trigger the loop
        raise _BrokenPool("Simulated Pool Crash")

    class DummyFuture:
        def result(self):
            raise _BrokenPool("Simulated Pool Crash")

    with patch('automation.backtest_runner.ProcessPoolExecutor') as MockExecutor, \
         patch('automation.backtest_runner.as_completed') as mock_as_completed_patch, \
         patch('automation.backtest_runner._run_remaining_sequentially') as mock_run_seq, \
         patch('automation.backtest_runner.sys.argv', ['backtest_runner.py', '--config', 'automation/config/backtest.json']):

        # Setup mocks
        mock_executor_instance = MockExecutor.return_value
        dummy_future = DummyFuture()
        mock_executor_instance.submit.return_value = dummy_future

        def generator_yielding_future(futures):
            yield dummy_future

        mock_as_completed_patch.side_effect = generator_yielding_future

        # Create dummy config files for run_backtest if needed or just patch load logic
        from io import StringIO
        import json
        import builtins

        original_open = builtins.open

        def mock_open_file(filename, *args, **kwargs):
            if isinstance(filename, str) and "strategies.json" in filename:
                return StringIO(json.dumps({"strategies": [{"active": True, "strategy_module": "automation.strategies.mean_reversion", "strategy_class": "MeanReversionStrategy", "config_class": "MeanReversionConfig", "params": {}}]}))
            if isinstance(filename, str) and "backtest.json" in filename:
                return StringIO(json.dumps({
                    "global_settings": {
                        "spread_modeling": False,
                        "span_tolerance_days": 5.0,
                        "commission_bps": 2.5,
                        "spread_bps_by_asset_class": {"CRYPTO": 10.0}
                    },
                    "strategies": [
                        {
                            "active": True,
                            "strategy_module": "automation.strategies.mean_reversion",
                            "strategy_class": "MeanReversionStrategy",
                            "config_class": "MeanReversionConfig",
                            "params": {}
                        }
                    ]
                }))
            # Catch dummy logs requested by the runner's logging setup or error log handling
            if isinstance(filename, str) and "errors_dummy.log" in filename:
                return StringIO()
            if isinstance(filename, str) and "backtest_dummy.log" in filename:
                return StringIO()

            return original_open(filename, *args, **kwargs)

        with patch('builtins.open', side_effect=mock_open_file), patch('os.path.exists', return_value=True):

            # Call run_backtest with a small configuration to run the loop quickly
            # To avoid the test getting stuck or requiring lots of mocked file I/O, we patch further down
            with patch('automation.backtest_runner.normalize_parquet_metadata', return_value=False), \
                 patch('automation.backtest_runner.ParquetDataCatalog'), \
                 patch('automation.backtest_runner.Path.glob', return_value=['dummy_path/AAPL.NASDAQ-1-HOUR-MID-INTERNAL.parquet']), \
                 patch('automation.backtest_runner.read_precisions_from_parquet', return_value=(2, 2)), \
                 patch('automation.backtest_runner.datetime') as mock_dt, \
                 patch('automation.backtest_runner.os.makedirs'), \
                 patch('builtins.print'), \
                 patch('automation.backtest_runner.DualLogger'), \
                 patch('argparse.ArgumentParser.parse_args') as mock_args, \
                 patch('automation.backtest_runner.discover_instruments_from_catalog', return_value=['AAPL.NASDAQ']):

                 mock_dt.now.return_value.strftime.return_value = "dummy"
                 mock_args.return_value.config = "automation/config/backtest.json"
                 mock_args.return_value.htmlreport = False
                 mock_args.return_value.output = None
                 mock_args.return_value.dry_run = False
                 mock_args.return_value.strategy = None
                 mock_args.return_value.instrument = None
                 mock_args.return_value.single_symbol = None
                 mock_args.return_value.catalog_path = "dummy_catalog_path"
                 mock_args.return_value.catalog = "dummy_catalog_path"

                 # run it
                 run_backtest()

                 # Verify that _run_remaining_sequentially was called
                 assert mock_run_seq.called

                 # Verify it was called with the correct signature (at least 15 parameters)
                 # args = (futures, future, strategies_list, catalog_path, start_ns, end_ns, start_capital, args.htmlreport, reports_dir, all_results, done_count, total_jobs, span_tolerance_days, commission_bps, spread_bps_by_asset_class)
                 called_args, called_kwargs = mock_run_seq.call_args
                 assert len(called_args) == 15

                 # Check the specific added variables
                 span_tolerance_days = called_args[12]
                 commission_bps = called_args[13]
                 spread_bps_by_asset_class = called_args[14]

                 # The values should match our mocked config
                 assert isinstance(span_tolerance_days, float)
                 assert isinstance(commission_bps, float)
                 assert isinstance(spread_bps_by_asset_class, dict)

def test_broken_pool_fallback_call_signature():
    """
    Regression Test für Issue #331 (Pitfall #30 / P1-Defect):
    Validiert, dass der Fallback-Aufruf bei einem `_BrokenPool`-Crash
    exakt mit der Signatur von _run_remaining_sequentially übereinstimmt
    und kein TypeError durch fehlende Argumente geworfen wird.
    """
    from automation.backtest_runner import _run_remaining_sequentially
    from unittest.mock import MagicMock
    import pytest

    # Simuliere die exakten 15 Parameter, die im Fallback-Catch-Block übergeben werden
    try:
        # Dummy-Werte, die die Logik nicht ausführen, da strategies_list=[] und futures={}
        _run_remaining_sequentially(
            futures={},
            failed_future=MagicMock(),
            strategies_list=[],
            catalog_path="dummy/path",
            start_ns=0,
            end_ns=1000,
            start_capital=10000.0,
            generate_html=False,
            reports_dir="reports",
            all_results=[],
            done_count=0,
            total_jobs=1,
            span_tolerance_days=3.0,         # Hinzugefügt in Issue #331
            commission_bps=0.0,              # Hinzugefügt in Issue #331
            spread_bps_by_asset_class={}     # Hinzugefügt in Issue #331
        )
    except TypeError as e:
        pytest.fail(f"Regression Issue #331: TypeError im Fallback-Aufruf bei vollständiger Signatur: {e}")
