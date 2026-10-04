"""Issue #1361 (GH #1257, P1) — Live-Bars ≠ Backtest-Bars: der Live-Pfad wendete keinen Session-Filter an.

Fix: ``HourlyStrategyBase`` umhüllt das ``on_bar`` jeder Strategie-Unterklasse mit einem Session-Gate
(``_bar_in_session``: Kerze ``[ts_event − Intervall, ts_event)`` gegen ``session_windows``, Börsen-Lokalzeit);
das Fenster ist das Config-Feld ``session_window``, das Backtest-Runner und Live-Bot über dieselbe Funktion
aus ``backtest.json`` auflösen.

Abnahme: Bars 08-23 UTC an einem EDT-Tag ⇒ die Strategie verarbeitet genau 7; der Bar-Zähler einer offenen
Position steigt nur in der Session.

Befund (abweichend von der Issue-Prämisse "im Backtest ein No-Op"): NautilusTraders Zeitbar-Aggregator gibt
mit dem Default ``time_bars_build_with_no_updates=True`` auch für Stunden OHNE Ticks flache Füllbars aus —
der Tick-Filter (#1275) entfernt Off-Session-TICKS, nicht Off-Session-BARS. Der Gate entfernt diese Füllbars
im Backtest genauso wie Extended-Hours-Bars live; erst damit sind Backtest-Bars == Live-Bars (Test unten).
"""
from __future__ import annotations

import os

import json
import logging
import types
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from automation import session_windows as sw

_H = 3_600_000_000_000
_NY = {"tz": "America/New_York", "open": "09:30", "close": "16:00"}
_EDT_DAY = date(2026, 10, 30)
_SYM = "XOM.ETORO"


def _ns(day: date, hour: int, minute: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def _nautilus_is_real() -> bool:
    """Ältere Testmodule installieren ``nautilus_trader``-Mocks in ``sys.modules`` (vgl. #1354-Tests)."""
    import sys
    mod = sys.modules.get("nautilus_trader")
    strat_mod = sys.modules.get("nautilus_trader.trading.strategy")

    def _has_file(m) -> bool:
        return isinstance(m, types.ModuleType) and vars(m).get("__file__") is not None
    return (mod is None or _has_file(mod)) and (strat_mod is None or _has_file(strat_mod))


# Die Bibliotheks-Vertragstests laufen IMMER in einem sauberen Interpreter (Subprozess-Test unten setzt
# ``_CLEAN_SUBPROCESS_ENV``): im Suite-Prozess (insbesondere unter pytest-xdist) installieren andere Module
# ``nautilus_trader``-Mocks zur Import- UND Laufzeit — ein Prüfergebnis zur Sammelzeit ist nicht belastbar.
_CLEAN_SUBPROCESS_ENV = "ETORO_REAL_NAUTILUS_SUBPROCESS"
_NAUTILUS_REAL = os.environ.get(_CLEAN_SUBPROCESS_ENV) == "1" and _nautilus_is_real()
real_nautilus = pytest.mark.skipif(
    not _NAUTILUS_REAL,
    reason="läuft im sauberen Subprozess (Subprozess-Test unten), nie im Suite-Prozess.")


# ─── reine Auflösung (ohne nautilus) ──────────────────────────────────────────────────

def test_bot_and_runner_resolve_the_same_window():
    from automation.optimizer.trial_config import config_dir

    cfg = json.loads((config_dir() / "backtest.json").read_text("utf-8"))
    imap = json.loads((config_dir() / "instrument_map.json").read_text("utf-8"))["instruments"]
    equity = next(v["symbol"] for v in imap.values() if (v.get("asset_class") or "").upper() == "EQUITY")
    crypto = next(v["symbol"] for v in imap.values() if (v.get("asset_class") or "").upper() == "CRYPTO")
    assert sw.load_session_window_for_symbol(equity) == sw.resolve_session_window(
        "EQUITY", cfg["session_hours_by_asset_class"]) == sw.parse_session_window(_NY)
    assert sw.load_session_window_for_symbol(crypto) is None
    assert sw.load_session_window_for_symbol("DOES_NOT_EXIST.ETORO") is None


def test_live_bot_injects_the_session_window_into_every_strategy_config():
    src = Path("automation/momentum_ls_run.py").read_text("utf-8")
    body = src[src.index("def _instantiate_strategy"):src.index("def _reset_hwm")]
    assert 'cfg_kwargs["session_window"] = session_window_to_param(' in body
    assert "load_session_window_for_symbol(bot_spec[\"symbol\"])" in body


# ─── Gate an der echten Strategie-Basisklasse ─────────────────────────────────────────

def _probe_strategy(window: dict | None, *, live: bool | None = None):
    from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig

    class _Cfg(HourlyStrategyConfig, kw_only=True, frozen=True):
        pass

    class _Probe(HourlyStrategyBase):
        def __init__(self, config):
            super().__init__(config)
            self.seen: list[tuple[int, int]] = []

        def on_bar(self, bar):
            # Stellvertretend für _check_exits_and_update: der Positions-Bar-Zähler steigt je verarbeiteter Bar.
            if self._in_position:
                self._bars_in_position += 1
            self.seen.append((int(bar.ts_event), self._bars_in_position))

    cfg = _Cfg(instrument_id=_SYM, bar_type=f"{_SYM}-1-HOUR-MID-INTERNAL",
               session_window=sw.session_window_to_param(sw.parse_session_window(window)))
    strat = _Probe(cfg)
    strat._session_telemetry_live = live
    return strat


def _bar(ts_ns: int):
    return types.SimpleNamespace(ts_event=ts_ns)


@real_nautilus
def test_bars_08_to_23_utc_on_an_edt_day_reach_the_strategy_exactly_seven_times():
    strat = _probe_strategy(_NY)
    strat._in_position = True
    for h in range(8, 24):
        strat.on_bar(_bar(_ns(_EDT_DAY, h)))
    hours = [datetime.fromtimestamp(ts // 1_000_000_000, tz=timezone.utc).hour for ts, _ in strat.seen]
    assert hours == [14, 15, 16, 17, 18, 19, 20]          # Kerzen 13:00-19:00 UTC (Schluss 14-20)
    assert [n for _, n in strat.seen] == [1, 2, 3, 4, 5, 6, 7]   # Zähler steigt nur in der Session
    assert strat._out_of_session_bars == 16 - 7


@real_nautilus
def test_without_a_window_every_bar_passes_bit_identically():
    strat = _probe_strategy(None)
    for h in range(24):
        strat.on_bar(_bar(_ns(_EDT_DAY, h)))
    assert len(strat.seen) == 24 and strat._out_of_session_bars == 0


@real_nautilus
def test_every_strategy_subclass_is_gated():
    import importlib

    from automation.strategies.hourly_strategy_base import HourlyStrategyBase

    registry = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    entries = registry.get("strategies", registry) if isinstance(registry, dict) else registry
    checked = 0
    for entry in entries:
        module = importlib.import_module(entry["strategy_module"])
        cls = getattr(module, entry["strategy_class"])
        if isinstance(cls, type) and issubclass(cls, HourlyStrategyBase):
            assert getattr(cls.on_bar, "_session_gated", False), entry["strategy_class"]
            checked += 1
    assert checked >= 10


@real_nautilus
def test_out_of_session_telemetry_is_hourly_and_live_only(monkeypatch):
    import automation.strategies.hourly_strategy_base as hsb

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(hsb, "emit_execution_event",
                        lambda _log, name, payload, **_kw: events.append((name, payload)))
    backtest = _probe_strategy(_NY, live=False)
    for h in range(0, 13):
        backtest.on_bar(_bar(_ns(_EDT_DAY, h)))
    assert events == []
    live = _probe_strategy(_NY, live=True)
    for h in range(0, 13):
        live.on_bar(_bar(_ns(_EDT_DAY, h)))
        live.on_bar(_bar(_ns(_EDT_DAY, h) + 60_000_000_000))     # zweite Bar derselben Stunde
    skipped = [p for n, p in events if n == "LIVE_BAR_SKIPPED_OUT_OF_SESSION"]
    assert len(skipped) == 13
    assert skipped[0]["skipped_since_last_event"] == 1
    assert [p["skipped_since_last_event"] for p in skipped[1:]] == [2] * 12
    assert skipped[-1]["skipped_total"] == 25
    assert live._out_of_session_bars == 26       # die letzte Bar wartet auf das nächste Stunden-Event


@real_nautilus
def test_session_bar_count_invariant_per_trading_day(monkeypatch):
    import automation.strategies.hourly_strategy_base as hsb

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(hsb, "emit_execution_event",
                        lambda _log, name, payload, **_kw: events.append((name, payload)))
    strat = _probe_strategy(_NY, live=True)
    # Do 29.10. (Starttag, nicht bewertet), Fr 30.10. (voll, EDT), Mo 2.11. (EST, 1 Bar fehlt), Di 3.11.
    days = [(date(2026, 10, 29), range(24)), (date(2026, 10, 30), range(24)),
            (date(2026, 11, 2), [h for h in range(24) if h != 17]), (date(2026, 11, 3), range(24))]
    for day, hours in days:
        for h in hours:
            strat.on_bar(_bar(_ns(day, h)))
    counts = [p for n, p in events if n == "LIVE_SESSION_BAR_COUNT"]
    assert [(p["trading_day"], p["n_bars_in_session"], p["expected"], p["passed"]) for p in counts] == [
        ("2026-10-30", 7, 7, True), ("2026-11-02", 6, 7, False)]


@real_nautilus
def test_holiday_bars_are_out_of_session():
    strat = _probe_strategy(_NY)
    for h in range(24):
        strat.on_bar(_bar(_ns(date(2026, 11, 26), h)))     # Thanksgiving
    assert strat.seen == []


# ─── echte Engine: Backtest-Bars == Live-Bars ─────────────────────────────────────────

def _run_engine(window: dict | None, *, hours=range(8, 24), days=(29, 30)) -> dict:
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.data import BarType, QuoteTick
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, TimeInForce
    from nautilus_trader.model.identifiers import InstrumentId, Venue
    from nautilus_trader.model.objects import Money, Price, Quantity

    from automation.backtest_runner import create_mock_instrument
    from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig

    inst = InstrumentId.from_str(_SYM)

    class _Cfg(HourlyStrategyConfig, kw_only=True, frozen=True):
        pass

    class _Probe(HourlyStrategyBase):
        def __init__(self, config):
            super().__init__(config)
            self.instrument_id = inst
            self.bar_type = BarType.from_str(config.bar_type)
            self.entered = False
            self.seen: list[tuple[int, int]] = []

        def on_start(self):
            super().on_start()
            self.subscribe_bars(self.bar_type)

        def on_bar(self, bar):
            exited = self._check_exits_and_update(bar)
            self.seen.append((int(bar.ts_event), self._bars_in_position))
            if exited or self.entered:
                return
            self.entered = True
            self.submit_order(self.order_factory.market(
                instrument_id=inst, order_side=OrderSide.BUY,
                quantity=self.cache.instrument(inst).make_qty(10), time_in_force=TimeInForce.GTC,
                tags=self._entry_order_tags(bar)))

    engine = BacktestEngine(config=BacktestEngineConfig(trader_id="BT-GATE-001"))
    engine.add_venue(venue=Venue("ETORO"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                     base_currency=USD, starting_balances=[Money(100_000, USD)])
    engine.add_instrument(create_mock_instrument(_SYM, 2, size_precision=2))
    ticks = []
    for d in days:
        for h in hours:
            for m in (0, 15, 30, 45):
                ts = _ns(date(2026, 10, d), h, m)
                px = 100.0 + (h % 3) * 0.1
                ticks.append(QuoteTick(inst, Price(px - 0.01, 2), Price(px + 0.01, 2),
                                       Quantity(1000, 2), Quantity(1000, 2), ts, ts))
    engine.add_data(ticks)
    strat = _Probe(_Cfg(instrument_id=_SYM, bar_type=f"{_SYM}-1-HOUR-MID-INTERNAL",
                        session_window=sw.session_window_to_param(sw.parse_session_window(window)),
                        disaster_stop_mode="simulated_order", atr_trailing_multiplier=50.0,
                        max_bars_in_trade=7))
    engine.add_strategy(strat)
    engine.run()
    fills = engine.trader.generate_order_fills_report().reset_index()
    out = {"seen": strat.seen, "fills": [(str(r["side"]), str(r["ts_last"])) for _, r in fills.iterrows()]}
    engine.dispose()
    return out


@real_nautilus
def test_engine_with_unfiltered_ticks_strategy_sees_seven_session_bars_per_day():
    """Live-Szenario (Extended-Hours-Quotes 08-23 UTC): 7 Bars je Handelstag, Zeit-Exit nach 7 SESSION-Bars."""
    out = _run_engine(_NY)
    hours = [datetime.fromtimestamp(ts // 1_000_000_000, tz=timezone.utc).strftime("%d %H") for ts, _ in out["seen"]]
    assert hours == [f"{d} {h}" for d in (29, 30) for h in range(14, 21)]
    counters = [n for _, n in out["seen"]]
    assert counters[:8] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert [side for side, _ in out["fills"]] == ["BUY", "SELL"]
    assert out["fills"][1][1].startswith("2026-10-30 14:00")   # 7 Session-Bars, nicht 7 Wanduhrstunden


@real_nautilus
def test_engine_with_session_filtered_ticks_drops_the_aggregator_filler_bars():
    """Backtest-Szenario (Ticks bereits auf die Session gefiltert): der Aggregator gibt trotzdem Füllbars für
    die Nacht aus (24 je Tag); der Gate reicht dieselben 7 Bars je Tag durch wie live."""
    session_hours = range(13, 20)
    ungated = _run_engine(None, hours=session_hours)
    gated = _run_engine(_NY, hours=session_hours)
    assert len(ungated["seen"]) > 2 * 7          # Füllbars 20:00-12:00 erreichen die Strategie ohne Gate
    hours = [datetime.fromtimestamp(ts // 1_000_000_000, tz=timezone.utc).strftime("%d %H") for ts, _ in gated["seen"]]
    # Die 20:00-Bar des letzten Tages fehlt nur, weil die Daten um 19:45 enden (kein Timer danach).
    assert hours == [f"29 {h}" for h in range(14, 21)] + [f"30 {h}" for h in range(14, 20)]
    live = _run_engine(_NY)                      # unfiltrierte Ticks wie live
    assert [ts for ts, _ in live["seen"]][:len(gated["seen"])] == [ts for ts, _ in gated["seen"]]


@pytest.mark.skipif(os.environ.get(_CLEAN_SUBPROCESS_ENV) == "1", reason="bereits der saubere Subprozess.")
def test_real_nautilus_tests_in_a_clean_subprocess_when_this_process_is_polluted():
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(Path(__file__)), "-q", "-p", "no:cacheprovider",
         "-k", "not subprocess"],
        cwd=str(repo), capture_output=True, text=True, timeout=600,
        env={**os.environ, _CLEAN_SUBPROCESS_ENV: "1"})
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]
    assert "skipped" not in proc.stdout.splitlines()[-1], proc.stdout[-500:]


def test_unknown_symbol_gets_no_gate_and_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="automation.session_windows"):
        assert sw.load_session_window_for_symbol("NOPE.ETORO") is None
    assert any("NOPE.ETORO" in r.getMessage() for r in caplog.records)
