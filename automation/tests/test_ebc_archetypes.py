"""EBC-H1 — Archetypen T/M/V (ebc_trend/ebc_pullback/ebc_squeeze): Indikatoren, Seitenregel, Engine-Smoke auf synthetischen Tagesbars."""
import numpy as np
import pytest

from automation.strategies.ebc_common import Ema, RollingWindow, SideRuleMixin, WilderAtr, WilderRsi

DAY = 86_400_000_000_000


def test_wilder_atr_recursion_and_init():
    atr = WilderAtr(3)
    assert atr.update(11.0, 9.0, 10.0) == pytest.approx(2.0)          # erste True Range = H-L
    assert atr.update(12.0, 10.0, 11.0) == pytest.approx(2.0 + (2.0 - 2.0) / 3)
    assert not atr.initialized
    atr.update(13.0, 10.0, 12.0)                                       # TR = 3
    assert atr.initialized
    assert atr.value == pytest.approx(2.0 + (3.0 - 2.0) / 3)


def test_wilder_rsi_monotone_series_hits_extremes():
    up, down = WilderRsi(2), WilderRsi(2)
    for k in range(10):
        up.update(100.0 + k)
        down.update(100.0 - k)
    assert up.initialized and up.value == pytest.approx(100.0)
    assert down.value == pytest.approx(0.0)


def test_ema_seed_and_rolling_window():
    ema = Ema(3)
    assert ema.update(10.0) == 10.0
    assert ema.update(14.0) == pytest.approx(12.0)                     # alpha = 0.5
    win = RollingWindow(3)
    for x in (1.0, 2.0, 3.0, 4.0):
        win.push(x)
    assert win.full and win.mean() == pytest.approx(3.0)
    assert win.std() == pytest.approx(np.std([2.0, 3.0, 4.0]))


class _Cfg:
    def __init__(self, allow_short, gate):
        self.allow_short, self.short_regime_gate = allow_short, gate


class _Side(SideRuleMixin):
    def __init__(self, allow_short, gate):
        self.config = _Cfg(allow_short, gate)


def test_short_rule_defaults_long_only_and_regime_gate():
    assert not _Side(False, False)._short_allowed(90.0, 100.0, 101.0)
    assert _Side(True, False)._short_allowed(110.0, None, None)         # ohne Gate bedingungslos
    gated = _Side(True, True)
    assert gated._short_allowed(90.0, 100.0, 101.0)                     # unter SMA200 und SMA200 fallend
    assert not gated._short_allowed(110.0, 100.0, 101.0)                # ueber SMA200
    assert not gated._short_allowed(90.0, 100.0, 99.0)                  # SMA200 steigt
    assert not gated._short_allowed(90.0, None, None)                   # Historie fehlt


def _synthetic_daily(seed=3, n=900):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.012, n)
    for k in rng.choice(np.arange(120, n - 40), size=12, replace=False):
        r[k:k + 4] -= 0.02                                              # Ruecksetzer
        r[k + 4:k + 12] += 0.01
    for k in range(150, n - 60, 150):                                   # Kompression (ruhige Phase) gefolgt von Ausbruch
        r[k:k + 40] = rng.normal(0.0, 0.0012, 40)
        r[k + 40:k + 43] += 0.03
    c = 100.0 * np.exp(np.cumsum(r))
    o = np.concatenate([[c[0]], c[:-1]]) * (1.0 + rng.normal(0, 0.001, n))
    h = np.maximum(o, c) * (1.0 + np.abs(rng.normal(0, 0.003, n)))
    l = np.minimum(o, c) * (1.0 - np.abs(rng.normal(0, 0.003, n)))
    ts = (1_700_000_000_000_000_000 // DAY) * DAY + np.arange(n) * DAY
    return ts, o, h, l, c


def _run(module, config_cls, strat_cls, **overrides):
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.data.config import DataEngineConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.data import Bar, BarType, QuoteTick
    from nautilus_trader.model.enums import AccountType, AssetClass, OmsType
    from nautilus_trader.model.identifiers import InstrumentId, Venue
    from nautilus_trader.model.instruments import Cfd
    from nautilus_trader.model.objects import Money, Price, Quantity
    import importlib

    ts, o, h, l, c = _synthetic_daily()
    iid = InstrumentId.from_str("EBCTEST.ETORO")
    inst = Cfd(instrument_id=iid, raw_symbol=iid.symbol, asset_class=AssetClass.EQUITY, quote_currency=USD, price_precision=4,
               size_precision=8, price_increment=Price(1e-4, 4), size_increment=Quantity(1e-8, 8), ts_event=0, ts_init=0)
    mod = importlib.import_module(module)
    bar_type = BarType.from_str(f"{iid}-1-DAY-MID-EXTERNAL")
    cfg = getattr(mod, config_cls)(instrument_id=str(iid), bar_type=str(bar_type), trade_amount_pct=2.0, disaster_stop_mode="simulated_order",
                                   session_window=None, max_daily_trades=None, max_aggregate_open_positions=10**6, max_order_notional=1e12,
                                   max_risk_per_trade_frac=None, stop_out_equity_frac=0.0, sizing_cap_tolerance=1000.0, **overrides)
    eng = BacktestEngine(config=BacktestEngineConfig(trader_id="EBC-T", logging=LoggingConfig(log_level="ERROR"),
                                                    data_engine=DataEngineConfig(time_bars_build_with_no_updates=False)))
    eng.add_venue(venue=Venue("ETORO"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN, base_currency=USD,
                  starting_balances=[Money(1_000_000, USD)], bar_execution=False)
    eng.add_instrument(inst)
    sz = Quantity(1e6, 8)
    ticks, bars = [], []
    for i in range(len(ts)):
        for off, px in ((0, o[i]), (DAY // 3, l[i]), (2 * DAY // 3, h[i]), (DAY - 1, c[i])):
            p = Price(round(px, 4), 4)
            ticks.append(QuoteTick(instrument_id=iid, bid_price=p, ask_price=p, bid_size=sz, ask_size=sz, ts_event=int(ts[i]) + off, ts_init=int(ts[i]) + off))
        t = int(ts[i]) + DAY + 1
        bars.append(Bar(bar_type, Price(round(o[i], 4), 4), Price(round(h[i], 4), 4), Price(round(l[i], 4), 4), Price(round(c[i], 4), 4),
                        Quantity(1.0, 8), t, t))
    eng.add_data(ticks); eng.add_data(bars)
    eng.add_strategy(getattr(mod, strat_cls)(config=cfg))
    eng.run()
    positions = eng.trader.generate_positions_report()
    eng.dispose()
    return positions


@pytest.mark.parametrize("module,config_cls,strat_cls", [
    ("automation.strategies.ebc_trend", "EbcTrendConfig", "EbcTrendStrategy"),
    ("automation.strategies.ebc_pullback", "EbcPullbackConfig", "EbcPullbackStrategy"),
    ("automation.strategies.ebc_squeeze", "EbcSqueezeConfig", "EbcSqueezeStrategy"),
])
def test_archetype_engine_smoke_long_only(module, config_cls, strat_cls):
    pos = _run(module, config_cls, strat_cls)
    assert len(pos) >= 1, "der Archetyp muss auf synthetischen Tagesbars handeln"
    assert set(pos["entry"].astype(str)) == {"BUY"}, "allow_short=False => nie ein Short"


@pytest.mark.parametrize("module,config_cls,strat_cls", [
    ("automation.strategies.ebc_trend", "EbcTrendConfig", "EbcTrendStrategy"),
    ("automation.strategies.ebc_pullback", "EbcPullbackConfig", "EbcPullbackStrategy"),
])
def test_archetype_short_gate_blocks_shorts_in_uptrend(module, config_cls, strat_cls):
    pos = _run(module, config_cls, strat_cls, allow_short=True, short_regime_gate=True)
    # Die synthetische Reihe steigt: Das Regime-Gate (Schluss < SMA200 und SMA200 fallend) darf kaum Shorts zulassen.
    shorts = int((pos["entry"].astype(str) == "SELL").sum()) if len(pos) else 0
    assert shorts <= max(1, len(pos) // 10)
