"""EBC-H3 Anhang B — VolatilityBreakoutPump / FlashCrashReversal eröffnen nie ungewollt einen Short (allow_short=False)."""
import collections

import numpy as np
import pytest

HOUR_NS = 3_600_000_000_000


def _ticks(iid, seed, shock):
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.objects import Price, Quantity
    rng = np.random.default_rng(seed)
    hours = 24 * 90
    r = rng.normal(0.0, 0.006, hours)
    for k in rng.choice(np.arange(50, hours - 30), size=20, replace=False):
        r[k:k + 3] += shock
        r[k + 3:k + 15] -= shock / 3.0
    mid = 100.0 * np.exp(np.cumsum(r))
    t0 = 1_700_000_000_000_000_000
    out = []
    for h in range(hours):
        for q in range(4):
            ts = t0 + h * HOUR_NS + q * (HOUR_NS // 4)
            m = mid[h] * (1.0 + rng.normal(0.0, 0.0008))
            out.append(QuoteTick(instrument_id=iid, bid_price=Price(round(m * 0.9998, 2), 2), ask_price=Price(round(m * 1.0002, 2), 2),
                                 bid_size=Quantity(1e6, 2), ask_size=Quantity(1e6, 2), ts_event=ts, ts_init=ts))
    return out


def _entries(strategy, seed, **overrides):
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.enums import AccountType, AssetClass, OmsType
    from nautilus_trader.model.identifiers import InstrumentId, Venue
    from nautilus_trader.model.instruments import Cfd
    from nautilus_trader.model.objects import Money, Price, Quantity
    iid = InstrumentId.from_str("SIMLEAK.ETORO")
    inst = Cfd(instrument_id=iid, raw_symbol=iid.symbol, asset_class=AssetClass.EQUITY, quote_currency=USD, price_precision=2,
               size_precision=2, price_increment=Price(0.01, 2), size_increment=Quantity(0.01, 2), ts_event=0, ts_init=0)
    common = dict(instrument_id=str(iid), bar_type=f"{iid}-1-HOUR-MID-INTERNAL", bb_period=10, bb_std_dev=2.0, trade_amount_pct=15.0,
                  disaster_stop_mode="simulated_order", session_window=None, max_daily_trades=overrides.pop("max_daily_trades", 5))
    if strategy == "vbp":
        from automation.strategies.volatility_breakout import VolatilityBreakoutConfig as Cfg, VolatilityBreakoutPumpStrategy as Strat
        cfg = Cfg(**common, atr_trailing_multiplier=1.5, max_bars_in_trade=7, cooldown_bars=0, **overrides); shock = +0.012
    else:
        from automation.strategies.flash_crash_reversal import FlashCrashReversalConfig as Cfg, FlashCrashReversalStrategy as Strat
        cfg = Cfg(**common, rsi_period=7, rsi_oversold=30.0, atr_trailing_multiplier=0.75, max_bars_in_trade=4, **overrides); shock = -0.012
    eng = BacktestEngine(config=BacktestEngineConfig(trader_id="LEAK-T", logging=LoggingConfig(log_level="ERROR")))
    eng.add_venue(venue=Venue("ETORO"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN, base_currency=USD,
                  starting_balances=[Money(10_000, USD)])
    eng.add_instrument(inst)
    eng.add_data(_ticks(iid, seed, shock))
    eng.add_strategy(Strat(config=cfg))
    eng.run()
    positions = eng.trader.generate_positions_report()
    eng.dispose()
    return collections.Counter(positions["entry"].astype(str)) if len(positions) else collections.Counter()


@pytest.mark.parametrize("seed", [1, 2])
def test_vbp_never_shorts_by_default(seed):
    c = _entries("vbp", seed)
    assert c["BUY"] > 0 and c["SELL"] == 0


@pytest.mark.parametrize("seed", [1, 2])
def test_fcr_blocked_entry_does_not_open_short(seed):
    c = _entries("fcr", seed, max_daily_trades=1)      # blockierter Entry (Tageslimit) war die Quelle des Short-Leaks
    assert c["BUY"] > 0 and c["SELL"] == 0
