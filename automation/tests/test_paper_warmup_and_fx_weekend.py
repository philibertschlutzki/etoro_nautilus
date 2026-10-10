"""Demo-Bot: Indikator-Vorwärmung aus dem Katalog beim Start (sonst wartet jede Strategie nach jedem Neustart bis
zu 100 Stunden auf ihr erstes Signal) und keine Devisen-Einstiege am Wochenende (eToro lehnt sie ab)."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import multiprocessing

from automation import api_backfiller as ab
from automation.strategies import hourly_strategy_base as hsb

_UTC = dt.timezone.utc
_H = 3_600_000_000_000


def test_forex_weekend_window():
    def ts(y, m, d, h):  # New-York-Ortszeit (EDT, UTC-4)
        return int(dt.datetime(y, m, d, h + 4, tzinfo=_UTC).timestamp() * 1e9)
    assert not hsb.forex_weekend_closed(ts(2026, 10, 9, 16))     # Freitag 16:00
    assert hsb.forex_weekend_closed(ts(2026, 10, 9, 17))         # Freitag 17:00
    assert hsb.forex_weekend_closed(ts(2026, 10, 10, 12))        # Samstag
    assert hsb.forex_weekend_closed(ts(2026, 10, 11, 16))        # Sonntag 16:00
    assert not hsb.forex_weekend_closed(ts(2026, 10, 11, 17))    # Sonntag 17:00
    assert not hsb.forex_weekend_closed(ts(2026, 10, 12, 3))     # Montag


def test_load_catalog_hourly_bars_rebuilds_ohlc_from_the_tick_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "CATALOG_PATH", tmp_path, raising=False)
    candles = [{"fromDate": f"2026-10-0{d}T{h:02d}:00:00Z", "open": 10.0 + h, "low": 9.0 + h,
                "high": 12.0 + h, "close": 11.0 + h} for d in (5, 6) for h in (13, 14)]
    table = ab._candles_to_arrow_table(candles, "BTC.ETORO", 2, 8, dt.datetime(2026, 1, 1, tzinfo=_UTC),
                                       interval="OneHour")
    out_dir = tmp_path / "data" / "quote_tick" / "BTC.ETORO" / "OneHour"
    out_dir.mkdir(parents=True)
    import pyarrow.parquet as pq
    pq.write_table(table, out_dir / "data.parquet")
    rows = hsb.load_catalog_hourly_bars("BTC.ETORO", 3, catalog_root=tmp_path)
    assert len(rows) == 3
    ts, o, h, low, c = rows[-1]
    assert ts == int(dt.datetime(2026, 10, 6, 15, tzinfo=_UTC).timestamp() * 1e9)
    assert (o, h, low, c) == (24.0, 26.0, 23.0, 25.0)
    cutoff = int(dt.datetime(2026, 10, 6, 13, 59, tzinfo=_UTC).timestamp() * 1e9)
    assert len(hsb.load_catalog_hourly_bars("BTC.ETORO", 10, catalog_root=tmp_path, now_ns=cutoff)) == 2


def _run_engine(warmup_bars: int) -> dict:
    """Engine mit SmaCrossover (SMA 20); der Katalog liefert 30 steigende Stundenkerzen vor dem ersten Live-Tick.
    Läuft in einem frischen Prozess (andere Testmodule installieren nautilus-Mocks)."""
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.identifiers import InstrumentId, Venue
    from nautilus_trader.model.objects import Money, Price, Quantity

    from automation.backtest_runner import create_mock_instrument
    from automation.strategies import hourly_strategy_base as base
    from automation.strategies.sma_crossover import SmaCrossoverConfig, SmaCrossoverStrategy

    sym = "TESTCOIN.ETORO"
    inst = InstrumentId.from_str(sym)
    t0 = int(dt.datetime(2026, 10, 6, 0, tzinfo=_UTC).timestamp() * 1e9)
    base.load_catalog_hourly_bars = lambda *a, **k: [  # noqa: E731
        (t0 - (30 - i) * _H, 100.0 + i, 100.5 + i, 99.5 + i, 100.0 + i) for i in range(30)]
    base._read_instrument_asset_class = lambda s: "CRYPTO"  # noqa: E731

    engine = BacktestEngine(config=BacktestEngineConfig(trader_id="BT-WARM-001"))
    engine.add_venue(venue=Venue("ETORO"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                     base_currency=USD, starting_balances=[Money(100_000, USD)])
    engine.add_instrument(create_mock_instrument(sym, 2, size_precision=2))
    ticks = [QuoteTick(inst, Price(140.0, 2), Price(140.02, 2), Quantity(1000, 2), Quantity(1000, 2),
                       t0 + m * 60_000_000_000, t0 + m * 60_000_000_000) for m in range(30, 155, 5)]
    engine.add_data(ticks)
    strat = SmaCrossoverStrategy(SmaCrossoverConfig(
        instrument_id=sym, bar_type=f"{sym}-1-HOUR-MID-INTERNAL", sma_period=20, trade_amount_usd=1000.0,
        disaster_stop_mode="simulated_order", catalog_warmup_bars=warmup_bars))
    engine.add_strategy(strat)
    engine.run()
    fills = engine.trader.generate_order_fills_report()
    out = {"sides": [str(s) for s in fills["side"]] if len(fills) else [],
           "first_fill": str(fills["ts_last"].iloc[0]) if len(fills) else None,
           "signal_after": strat.current_signal}
    engine.dispose()
    return out


def _isolated(warmup_bars: int) -> dict:
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as ex:
        return ex.submit(_run_engine, warmup_bars).result()


def test_warmup_lets_the_first_live_bar_trade_and_sends_no_orders_during_replay():
    warm = _isolated(30)
    cold = _isolated(0)
    assert cold["sides"] == []                       # SMA 20 nach zwei Live-Stunden nicht bereit
    assert warm["sides"][:1] == ["BUY"]              # erste Live-Stunde: Kurs über der vorgewärmten SMA
    # Erster Live-Tick 00:30, erste Live-Stunde schliesst 01:00: eine Order aus der Vorwärmung wäre schon beim
    # ersten Tick gefüllt worden.
    assert warm["first_fill"].startswith("2026-10-06 01:00")
