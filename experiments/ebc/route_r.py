"""Route R: Nautilus-BacktestEngine auf Tageskerzen (O/L/H/C-Ticks je Kerze), Harness nach EBC-H3 Anhang A.
Setzt ETORO_CONFIG_DIR (cfg_A = Status-quo-Overlay max_handelstage 5 + PAPER_TRAILING_TP, cfg_B = Archetypen max_handelstage 60)
VOR dem Import der Strategien. Spread im Tick = 0 (bid = ask = mid); alle Kosten werden explizit in costs.py verbucht."""
import csv, importlib, json, math, os, sys
import numpy as np
import pandas as pd

HOUR = 3_600_000_000_000
DAY = 24 * HOUR
UNIT = {'DAY': DAY, 'HOUR': HOUR}

def load_daily(iid, base="data/ebc/daily", hourly=False):
    rows = list(csv.DictReader(open(f"{base}/{iid}.csv")))
    import datetime as dt
    ts = np.array([int(dt.datetime.fromisoformat(r["date"]).replace(tzinfo=dt.timezone.utc).timestamp()) * 10**9 for r in rows], dtype=np.int64)
    o, h, l, c = (np.array([float(r[k]) for r in rows]) for k in ("open", "high", "low", "close"))
    h = np.maximum.reduce([h, o, c]); l = np.minimum.reduce([l, o, c])   # Rundungsfehler der API-Kerzen bereinigen
    return ts, o, h, l, c

def price_precision(px):
    return int(min(8, max(2, 6 - math.floor(math.log10(max(px, 1e-9))))))

def make_ticks(iid_obj, ts, o, h, l, c, pp, unit=DAY):
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.objects import Price, Quantity
    out = []
    sz = Quantity(1e6, 8)
    for i in range(len(ts)):
        for off, px in ((0, o[i]), (unit // 3, l[i]), (2 * unit // 3, h[i]), (unit - 1, c[i])):
            t = int(ts[i]) + off
            p = Price(round(px, pp), pp)
            out.append(QuoteTick(instrument_id=iid_obj, bid_price=p, ask_price=p, bid_size=sz, ask_size=sz, ts_event=t, ts_init=t))
    return out

def make_bars(iid_obj, ts, o, h, l, c, pp, unit=DAY, spec='1-DAY'):
    """EXTERNAL-Tagesbars; ts_event = Start der NAECHSTEN Kerze + 1 ns, damit der Open-Tick der Folgekerze zuerst verarbeitet
    wird und die Market-Order zum Eroeffnungskurs der Folgekerze fuellt (keine Wochenend-/Feiertagsbars)."""
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.objects import Price, Quantity
    bt = BarType.from_str(f"{iid_obj}-{spec}-MID-EXTERNAL")
    out = []
    for i in range(len(ts)):
        t_next = int(ts[i + 1]) if i + 1 < len(ts) else int(ts[i]) + unit
        t = t_next + 1
        out.append(Bar(bt, Price(round(o[i], pp), pp), Price(round(h[i], pp), pp), Price(round(l[i], pp), pp),
                       Price(round(c[i], pp), pp), Quantity(1.0, 8), t, t))
    return out

STRATS = json.load(open("automation/config/strategies.json"))["strategies"]
BY_CLASS = {s["strategy_class"]: s for s in STRATS}

def run(strategy_class, iid_str, series, cfg_overrides=None, module=None, config_class=None, defaults=None, min_gap=None, axis='DAY', want_positions=False):
    """Gibt Liste von Trades zurueck: dict(side, entry_ts, exit_ts, px_open, px_close, exit_reason)."""
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.enums import AccountType, AssetClass, OmsType
    from nautilus_trader.model.identifiers import InstrumentId, Venue
    from nautilus_trader.model.instruments import Cfd
    from nautilus_trader.model.objects import Money, Price, Quantity
    ts, o, h, l, c = series
    iid = InstrumentId.from_str(iid_str)
    pp = price_precision(float(np.median(c)))
    inst = Cfd(instrument_id=iid, raw_symbol=iid.symbol, asset_class=AssetClass.EQUITY, quote_currency=USD,
               price_precision=pp, size_precision=8, price_increment=Price(10.0 ** -pp, pp),
               size_increment=Quantity(1e-8, 8), ts_event=0, ts_init=0)
    meta = BY_CLASS.get(strategy_class, {})
    module = module or meta["strategy_module"]
    config_class = config_class or meta["config_class"]
    mod = importlib.import_module(module)
    Cfg = getattr(mod, config_class); Strat = getattr(mod, strategy_class)
    kwargs = dict(instrument_id=iid_str, bar_type=f"{iid_str}-1-{axis}-MID-EXTERNAL", trade_amount_pct=2.0,
                  disaster_stop_mode="simulated_order", session_window=None, max_daily_trades=None,
                  max_aggregate_open_positions=10**6, max_order_notional=1e12, max_risk_per_trade_frac=None,
                  stop_out_equity_frac=0.0, sizing_cap_tolerance=1000.0)
    kwargs.update(defaults or {})
    kwargs.update(cfg_overrides or {})
    kwargs.pop("trade_amount_pct", None) if False else None
    cfg = Cfg(**kwargs)
    from nautilus_trader.data.config import DataEngineConfig
    eng = BacktestEngine(config=BacktestEngineConfig(trader_id="EBC-001", logging=LoggingConfig(log_level="ERROR"),
                                                    data_engine=DataEngineConfig(time_bars_build_with_no_updates=False)))
    eng.add_venue(venue=Venue(iid.venue.value), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                  base_currency=USD, starting_balances=[Money(1_000_000, USD)], bar_execution=False)
    eng.add_instrument(inst)
    eng.add_data(make_ticks(iid, ts, o, h, l, c, pp, UNIT[axis]))
    eng.add_data(make_bars(iid, ts, o, h, l, c, pp, UNIT[axis], '1-' + axis))
    eng.add_strategy(Strat(config=cfg))
    eng.run()
    fills = eng.trader.generate_order_fills_report()
    positions = eng.trader.generate_positions_report()
    eng.dispose()
    if want_positions:
        pos = []
        for _, r_ in positions.iterrows():
            def _ns(x):
                try: return int(x.value)
                except AttributeError: return int(x)
            pos.append(dict(side=str(r_["entry"]), entry_ts=_ns(r_["ts_opened"]),
                            exit_ts=_ns(r_["ts_closed"]) if not pd.isna(r_["ts_closed"]) else None,
                            px_open=float(r_["avg_px_open"]), px_close=float(r_["avg_px_close"]) if r_["avg_px_close"] == r_["avg_px_close"] else None))
        return _trades_from_fills(fills, series, UNIT[axis]), pos
    return _trades_from_fills(fills, series, UNIT[axis])


def _trades_from_fills(fills, series, unit=DAY):
    """Fills (nach Zeit) -> Trades. Entry-Fill traegt den SL:-Tag; Exit-Reason aus EXIT_REASON:-Tag.
    Katastrophen-Stop: Fill wird auf das Stop-Niveau min(Open, Stop) bzw. max(Open, Stop) korrigiert (die O/L/H/C-Tickfolge
    liesse die Stop-Order sonst am Extremtick fuellen)."""
    import re
    ts, o, h, l, c = series
    trades = []
    if fills is None or not len(fills):
        return trades
    f = fills.sort_values("ts_last")
    cur = None
    for _, r in f.iterrows():
        tags = " ".join(str(t) for t in r["tags"]) if hasattr(r["tags"], "__iter__") and not isinstance(r["tags"], str) else str(r["tags"])
        t_ns = int(r["ts_last"].value) if hasattr(r["ts_last"], "value") else int(r["ts_last"])
        side = str(r["side"])
        px = float(r["avg_px"])
        if cur is None:
            m = re.search(r"SL:([0-9.]+)", tags)
            cur = dict(side=side, entry_ts=t_ns, px_open=px, sl_pct=float(m.group(1)) if m else None)
        else:
            m = re.search(r"EXIT_REASON:([A-Z_]+)", tags)
            reason = m.group(1) if m else "UNKNOWN"
            if reason == "DISASTER_STOP" and cur["sl_pct"]:
                lvl = cur["px_open"] * (1 - cur["sl_pct"]) if cur["side"] == "BUY" else cur["px_open"] * (1 + cur["sl_pct"])
                d0 = (t_ns // unit) * unit
                j = int(np.searchsorted(ts, d0))
                op = float(o[j]) if j < len(ts) and ts[j] == d0 else px
                if cur["side"] == "BUY":
                    px = min(op, lvl) if op < lvl else lvl
                else:
                    px = max(op, lvl) if op > lvl else lvl
            cur.update(exit_ts=t_ns, px_close=px, exit_reason=reason)
            trades.append(cur)
            cur = None
    if cur is not None:   # am Datenende offen
        cur.update(exit_ts=int(ts[-1]) + unit - 1, px_close=float(c[-1]), exit_reason="DATA_END")
        trades.append(cur)
    return trades
