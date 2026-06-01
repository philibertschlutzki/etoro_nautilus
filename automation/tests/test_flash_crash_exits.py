import pytest
import os
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from automation.strategies.flash_crash_reversal import FlashCrashReversalStrategy, FlashCrashReversalConfig

def test_config_initialization():
    config = FlashCrashReversalConfig(
        instrument_id="VRT.ETORO",
        bar_type="VRT.ETORO-1-HOUR-MID-INTERNAL",
        bb_period=5,
        bb_std_dev=2.0,
        rsi_period=3,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        trade_amount_usd=100.0,
        max_bars_in_trade=12,
        atr_trailing_multiplier=1.25,
        profit_target_pct=2.0
    )
    assert config.max_bars_in_trade == 12
    assert config.atr_trailing_multiplier == 1.25
    assert config.profit_target_pct == 2.0

def test_flash_crash_has_mean_reversion_logic():
    with open("automation/strategies/flash_crash_reversal.py", "r") as f:
        content = f.read()
    assert "is_mean_reversion = close_price >= self.bb.middle" in content
    assert "is_recovery or is_overbought or is_mean_reversion" in content
    assert "open_orders = self.cache.orders_open(instrument_id=self.instrument_id)" in content
    assert "self.cancel_order(order)" in content
    assert "pos.is_closing" not in content

def test_profit_target_logic_present():
    with open("automation/strategies/hourly_strategy_base.py", "r") as f:
        content = f.read()
    assert "Profit Target LONG hit" in content
    assert "Profit Target SHORT hit" in content
