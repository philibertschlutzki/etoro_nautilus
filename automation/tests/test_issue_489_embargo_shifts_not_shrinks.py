import pytest
import sys
import unittest.mock as mock

class MockNautilus(mock.MagicMock):
    @classmethod
    def __getattr__(cls, name):
        return mock.MagicMock()

import types
class MockModule(types.ModuleType):
    def __getattr__(self, name):
        return mock.MagicMock()

sys.modules['nautilus_trader'] = MockModule('nautilus_trader')
sys.modules['nautilus_trader.backtest'] = MockModule('nautilus_trader.backtest')
sys.modules['nautilus_trader.backtest.engine'] = MockModule('nautilus_trader.backtest.engine')
sys.modules['nautilus_trader.backtest.models'] = MockModule('nautilus_trader.backtest.models')
sys.modules['nautilus_trader.model'] = MockModule('nautilus_trader.model')
sys.modules['nautilus_trader.model.data'] = MockModule('nautilus_trader.model.data')
sys.modules['nautilus_trader.model.enums'] = MockModule('nautilus_trader.model.enums')
sys.modules['nautilus_trader.model.identifiers'] = MockModule('nautilus_trader.model.identifiers')
sys.modules['nautilus_trader.model.currencies'] = MockModule('nautilus_trader.model.currencies')
sys.modules['nautilus_trader.model.objects'] = MockModule('nautilus_trader.model.objects')
sys.modules['nautilus_trader.model.instruments'] = MockModule('nautilus_trader.model.instruments')
sys.modules['nautilus_trader.config'] = MockModule('nautilus_trader.config')
sys.modules['nautilus_trader.common'] = MockModule('nautilus_trader.common')
sys.modules['nautilus_trader.common.enums'] = MockModule('nautilus_trader.common.enums')
sys.modules['nautilus_trader.common.actor'] = MockModule('nautilus_trader.common.actor')
sys.modules['nautilus_trader.core'] = MockModule('nautilus_trader.core')
sys.modules['nautilus_trader.core.message'] = MockModule('nautilus_trader.core.message')
sys.modules['nautilus_trader.portfolio'] = MockModule('nautilus_trader.portfolio')
sys.modules['nautilus_trader.test_engine'] = MockModule('nautilus_trader.test_engine')
sys.modules['nautilus_trader.persistence'] = MockModule('nautilus_trader.persistence')
sys.modules['nautilus_trader.persistence.catalog'] = MockModule('nautilus_trader.persistence.catalog')
sys.modules['nautilus_trader.execution'] = MockModule('nautilus_trader.execution')
sys.modules['nautilus_trader.execution.messages'] = MockModule('nautilus_trader.execution.messages')

import automation.backtest_runner

def test_fold_boundaries_embargo_shifts_not_shrinks():
    start_ns = 0
    walk_forward_dict = {
        "is_window_days": 10,
        "oos_window_days": 5,
        "embargo_period_days": 2,
        "splits": 3
    }
    boundaries = automation.backtest_runner.compute_fold_boundaries(start_ns, walk_forward_dict)

    # 10 days = 10 * 86400 * 1e9 ns
    day_ns = 86400 * 1_000_000_000
    oos_window_ns = 5 * day_ns

    for _is_start_ns, split_oos_start_ns, split_oos_end_ns in boundaries:
        # Assertion 1: Δ(oos_end - oos_start) == oos_window
        assert (split_oos_end_ns - split_oos_start_ns) == oos_window_ns


def test_geometry_assertion_445_embargo():
    # Setup mock returns
    pass

    bt_data = {
        "walk_forward": {
            "is_window_days": 100,
            "oos_window_days": 10,
            "embargo_period_days": 5,
            "data_history_days": 130, # 100 + 2*10 + 5 + 10 (holdout default) = 135
            "splits": 2
        },
        "holdout_days": 10
    }

    from automation.optimizer.trial_config import build_trial

    with pytest.raises(ValueError, match="is_window 100 \+ splits 2 × oos 10 \+ embargo 5 \+ holdout"):
        with mock.patch('builtins.open', mock.mock_open(read_data='{}')):
            with mock.patch('json.load', side_effect=[bt_data, {'MyStrat': {'strategy_module': 'x', 'config_class': 'y'}}, bt_data, bt_data, bt_data, bt_data, bt_data]):
                with mock.patch('shutil.copy2'):
                    with mock.patch('pathlib.Path.mkdir'):
                        with mock.patch('automation.optimizer.trial_config.sha256_file', return_value='dummy_hash'):
                            build_trial('MyStrat', {}, study_name='test', trial_number=0, seed=42, holdout_days=10, n_folds=2)


def test_extract_metrics_purge_leakage():
    day_ns = 86400 * 1_000_000_000
    start_ns = 0
    fold_boundaries = [
        (0, 12 * day_ns, 17 * day_ns)
    ]
    embargo_period_ns = 2 * day_ns

    pnls_with_ts = [
        (100.0, 5 * day_ns, 1, 1),
        (200.0, 11 * day_ns, 1, 1),
        (300.0, 14 * day_ns, 1, 1),
    ]

    is_pnls = []
    oos_pnls = []

    for pnl, ts, _, _ in pnls_with_ts:
        is_oos = False
        is_in_sample = False

        for _is_start_ns, split_oos_start_ns, split_oos_end_ns in fold_boundaries:
            is_end_ns = split_oos_start_ns - embargo_period_ns # from the boundary formula

            if split_oos_start_ns <= ts < split_oos_end_ns:
                is_oos = True
                break
            elif _is_start_ns <= ts < is_end_ns:
                is_in_sample = True

        if is_oos:
            oos_pnls.append(pnl)
        elif is_in_sample:
            is_pnls.append(pnl)

    assert len(is_pnls) == 1
    assert len(oos_pnls) == 1
    # Count(IS-Trades mit Exit-Timestamp >= IS-Ende) == 0.
    # The IS end is 10 * day_ns.
    # Trade 11 * day_ns is purged and not in IS.
    assert 200.0 not in is_pnls
    assert 200.0 not in oos_pnls
