"""EBC-H3 Anhang A — Short-Leak-Reproduktion mit unverändertem Repo-Code.

Aufruf im Root des Experiment-Klons:
  python experiments/ebc_h3/short_leak_harness.py --strategy vbp --seeds 1 2 3
  python experiments/ebc_h3/short_leak_harness.py --strategy fcr --seeds 1 2 --max-daily-trades 1
"""
import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, os.getcwd())
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig  # noqa: E402
from nautilus_trader.config import LoggingConfig  # noqa: E402
from nautilus_trader.model.currencies import USD  # noqa: E402
from nautilus_trader.model.data import QuoteTick  # noqa: E402
from nautilus_trader.model.enums import AccountType, AssetClass, OmsType  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId, Venue  # noqa: E402
from nautilus_trader.model.instruments import Cfd  # noqa: E402
from nautilus_trader.model.objects import Money, Price, Quantity  # noqa: E402

HOUR_NS = 3_600_000_000_000


def synthetic_ticks(iid, seed: int, shock: float):
    """24/7-Stundenpfad: Random Walk, 25 Schocks über 3 Stunden (+ Pump / − Crash), danach Gegenbewegung."""
    rng = np.random.default_rng(seed)
    hours = 24 * 120
    r = rng.normal(0.0, 0.006, hours)
    for k in rng.choice(np.arange(50, hours - 30), size=25, replace=False):
        r[k:k + 3] += shock
        r[k + 3:k + 15] -= shock / 3.0
    mid = 100.0 * np.exp(np.cumsum(r))
    t0 = 1_700_000_000_000_000_000
    out = []
    for h in range(hours):
        for q in range(4):  # 4 Quotes je Stunde, wie die O/H/L/C-Ticks des Katalogs
            ts = t0 + h * HOUR_NS + q * (HOUR_NS // 4)
            m = mid[h] * (1.0 + rng.normal(0.0, 0.0008))
            out.append(QuoteTick(instrument_id=iid, bid_price=Price(round(m * 0.9998, 2), 2),
                                 ask_price=Price(round(m * 1.0002, 2), 2), bid_size=Quantity(1e6, 2),
                                 ask_size=Quantity(1e6, 2), ts_event=ts, ts_init=ts))
    return out


def run(strategy: str, seed: int, max_daily_trades: int) -> dict:
    iid = InstrumentId.from_str("SIMLEAK.ETORO")
    inst = Cfd(instrument_id=iid, raw_symbol=iid.symbol, asset_class=AssetClass.EQUITY, quote_currency=USD,
               price_precision=2, size_precision=2, price_increment=Price(0.01, 2),
               size_increment=Quantity(0.01, 2), ts_event=0, ts_init=0)
    common = dict(instrument_id=str(iid), bar_type=f"{iid}-1-HOUR-MID-INTERNAL", bb_period=10, bb_std_dev=2.0,
                  trade_amount_pct=15.0, disaster_stop_mode="simulated_order", session_window=None,
                  max_daily_trades=max_daily_trades)
    if strategy == "vbp":
        from automation.strategies.volatility_breakout import (VolatilityBreakoutConfig as Cfg,
                                                               VolatilityBreakoutPumpStrategy as Strat)
        cfg = Cfg(**common, atr_trailing_multiplier=1.5, max_bars_in_trade=7, cooldown_bars=0)
        shock = +0.012
    else:
        from automation.strategies.flash_crash_reversal import (FlashCrashReversalConfig as Cfg,
                                                                FlashCrashReversalStrategy as Strat)
        cfg = Cfg(**common, rsi_period=7, rsi_oversold=30.0, atr_trailing_multiplier=0.75, max_bars_in_trade=4)
        shock = -0.012
    eng = BacktestEngine(config=BacktestEngineConfig(trader_id="LEAK-001",
                                                     logging=LoggingConfig(log_level="ERROR")))
    eng.add_venue(venue=Venue("ETORO"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                  base_currency=USD, starting_balances=[Money(10_000, USD)])
    eng.add_instrument(inst)
    eng.add_data(synthetic_ticks(iid, seed, shock))
    eng.add_strategy(Strat(config=cfg))
    eng.run()
    positions = eng.trader.generate_positions_report()
    fills = eng.trader.generate_order_fills_report()
    eng.dispose()
    sides = collections.Counter(positions["entry"].astype(str)) if len(positions) else collections.Counter()
    reasons = collections.Counter()
    for tag in (fills["tags"] if len(fills) and "tags" in fills else []):
        text = str(tag)
        for key in ("TRAILING_STOP", "TIME_BOX", "SIGNAL_REVERSAL", "DISASTER_STOP", "PROFIT_TARGET"):
            if f"EXIT_REASON:{key}" in text:
                reasons[key] += 1
    return {"strategy": strategy, "seed": seed, "max_daily_trades": max_daily_trades,
            "positions": int(len(positions)), "long_entries": sides.get("BUY", 0),
            "short_entries": sides.get("SELL", 0), "exit_reasons": dict(reasons)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=["vbp", "fcr"], required=True)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--max-daily-trades", type=int, default=5)
    args = ap.parse_args()
    for s in args.seeds:
        print(run(args.strategy, s, args.max_daily_trades))
