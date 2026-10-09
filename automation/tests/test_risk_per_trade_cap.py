"""Risiko-Deckel je Trade: Verlust am Katastrophen-Stop <= max_risk_per_trade_frac der Equity."""
import math

import pytest

from automation.live_risk import risk_capped_notional
from automation.strategies.hourly_strategy_base import HourlyStrategyConfig


def test_cap_binds_only_for_wide_stops():
    # 1 % Risiko, 10 000 USD: Stop 15 % -> 666,67 USD; Stop 2 % -> 5 000 USD
    assert risk_capped_notional(equity=10_000, stop_pct=0.15, max_risk_fraction=0.01) == pytest.approx(666.6667, rel=1e-4)
    assert risk_capped_notional(equity=10_000, stop_pct=0.02, max_risk_fraction=0.01) == pytest.approx(5_000.0)
    # 15-%-Position (1 500 USD) bleibt bei 2-%-Stop unberuehrt
    assert 1_500 < risk_capped_notional(equity=10_000, stop_pct=0.02, max_risk_fraction=0.01)


@pytest.mark.parametrize("equity,stop,frac", [
    (None, 0.05, 0.01), (10_000, None, 0.01), (10_000, 0.05, None), (0, 0.05, 0.01),
    (10_000, 0.0, 0.01), (10_000, 0.05, 0.0), (10_000, math.nan, 0.01), (10_000, math.inf, 0.01),
    ("x", 0.05, 0.01),
])
def test_fail_open_without_valid_basis(equity, stop, frac):
    assert risk_capped_notional(equity=equity, stop_pct=stop, max_risk_fraction=frac) is None


def test_config_default_is_one_percent():
    assert HourlyStrategyConfig.__struct_fields__ and HourlyStrategyConfig(
        instrument_id="X.Y", bar_type="X.Y-1-HOUR-LAST-EXTERNAL").max_risk_per_trade_frac == 0.01
