"""Mehr Paare im Paper-Bot: Plätze aus dem Paper-Overlay, Budget-Aufteilung, Positionen je Instrument,
volatile Zusatz-Symbole nur über eToro-Metadaten."""
import re
from datetime import datetime, timezone
from pathlib import Path

from automation import incubation as inc
from automation import universe_fetcher as uf
from automation.momentum_ls_run import incubation_sizing

_T0 = datetime(2026, 10, 10, tzinfo=timezone.utc)
_STRATEGIES = Path(__file__).resolve().parents[1] / "strategies"


def _winners(n):
    return {f"S{i:02d}.ETORO": {"strategy": "Strat", "oos_eligible": True, "oos_metrics": {"psr": 0.5 + i / 100}}
            for i in range(n)}


def test_cycle_uses_paper_inc_cfg_slots(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    cfg = inc.incubation_config({"incubation": {"enabled": True}})
    cfg["max_concurrent"] = 20
    res = inc.run_incubation_cycle(stages, winners=_winners(25), tournament_cfg={"incubation": {"enabled": True}},
                                   resolve_params=lambda s, y: {"p": y}, now=_T0, ledger_dir=tmp_path / "inc",
                                   inc_cfg=cfg)
    assert len(res.started) == 20


def test_cycle_without_inc_cfg_keeps_production_slots(tmp_path):
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    res = inc.run_incubation_cycle(stages, winners=_winners(25), tournament_cfg={"incubation": {"enabled": True}},
                                   resolve_params=lambda s, y: {"p": y}, now=_T0, ledger_dir=tmp_path / "inc")
    assert len(res.started) == inc.incubation_config({})["max_concurrent"]


def test_incubation_sizing_splits_budget():
    assert incubation_sizing(3, 0.6, 0.10) == (0.10, 5)
    frac, n = incubation_sizing(20, 0.6, 0.10)
    assert abs(frac - 0.03) < 1e-12 and n == 20
    frac, _ = incubation_sizing(100, 0.6, 0.10)
    assert frac == 0.02                                   # Untergrenze, Allocator deckelt die Summe


def test_strategies_count_positions_per_instrument():
    # Mehrere Paare in einem Bot: eine offene Position auf A darf B nicht blockieren.
    for path in _STRATEGIES.glob("*.py"):
        text = path.read_text("utf-8")
        assert not re.search(r"len\(self\.cache\.positions_open\(\)\)\s*>=\s*self\.config\.max_open_positions", text), path


def test_resolve_extra_symbols_only_via_metadata():
    existing = {"1111": {"symbol": "TSLA.ETORO"}}
    meta = {
        "9001": {"SymbolFull": "MARA", "InstrumentTypeID": 5},
        "9002": {"SymbolFull": "LINK", "InstrumentTypeID": 10},
        "9003": {"SymbolFull": "SPX500", "InstrumentTypeID": 4},
        "1111": {"SymbolFull": "TSLA", "InstrumentTypeID": 5},
    }
    out = uf.resolve_extra_symbols(existing, meta, ["MARA", "LINK", "SPX500", "TSLA", "NOPE"])
    assert {k: v["symbol"] for k, v in out.items()} == {"9001": "MARA.ETORO", "9002": "LINK.ETORO"}
    assert out["9002"]["asset_class"] == "crypto"


def test_volatile_universe_config_loads():
    syms = uf.load_extra_symbols()
    assert "MARA" in syms and len(syms) == len(set(syms))


def test_incubation_bots_dedupe_duplicate_universe_entries():
    from automation.momentum_ls_run import _build_incubation_bots_config
    params = {"sma_period": 5}
    universe = {"universe": [{"symbol": "AAA.ETORO"}, {"symbol": "AAA.ETORO"}, {"symbol": "BBB.ETORO"}]}
    winners = {"per_symbol_winners": {
        s: {"stage": inc.INCUBATING, "strategy": "SmaCrossoverStrategy", "params": dict(params),
            "params_sha256": inc.params_fingerprint(params)} for s in ("AAA.ETORO", "BBB.ETORO")}}
    registry = {"SmaCrossoverStrategy": ("m", "C", "Cfg")}
    syms, bots = _build_incubation_bots_config(universe, winners, registry, {"AAA.ETORO": "1", "BBB.ETORO": "2"})
    assert syms == ["AAA.ETORO", "BBB.ETORO"] and len(bots) == 2


def test_extras_fetch_due_respects_min_age(tmp_path):
    from automation.daily_orchestrator import _extras_fetch_due
    f = tmp_path / "u.json"
    assert _extras_fetch_due(f) is True                   # fehlt ⇒ fällig
    f.write_text("{}")
    assert _extras_fetch_due(f) is False                  # gerade geschrieben


def _write_oneday(qt, symbol, days):
    import pyarrow as pa
    import pyarrow.parquet as pq
    d = qt / symbol / "OneDay"
    d.mkdir(parents=True)
    pq.write_table(pa.table({"ts_event": [int(x) * 86_400_000_000_000 for x in days]}), str(d / "data.parquet"))


def test_oneday_full_window_due(tmp_path, monkeypatch):
    import json as _json
    from automation import historical_fetcher as hf
    bounds = tmp_path / "bounds.json"
    monkeypatch.setattr(hf, "INCEPTION_CACHE_PATH", bounds)
    qt = tmp_path / "qt"
    _write_oneday(qt, "OLD.ETORO", range(19000, 20000))   # 2022-01-08 … volles Fenster
    _write_oneday(qt, "CUT.ETORO", range(19950, 20000))   # von Phase 2d gekürzt
    win = {"window": {"OneDay": {"window_oldest_utc": "2022-01-08T00:00:00Z"}}}
    bounds.write_text(_json.dumps({"OLD.ETORO": win, "CUT.ETORO": win}))
    assert hf.oneday_full_window_due("OLD.ETORO", qt) is False
    assert hf.oneday_full_window_due("CUT.ETORO", qt) is True
    assert hf.oneday_full_window_due("NEW.ETORO", qt) is True     # weder Datei noch Fenster


def test_phase2e_fetches_only_due_symbols(monkeypatch):
    import logging
    from automation import daily_orchestrator as do
    from automation import historical_fetcher as hf
    from automation import api_backfiller as ab
    monkeypatch.setattr(hf, "oneday_full_window_due", lambda s: s != "OLD.ETORO")
    monkeypatch.setattr(ab, "_load_etoro_id_map", lambda p: {"1": "OLD.ETORO", "2": "MARA.ETORO"})
    seen = {}

    async def fake_run(api_key, user_key, id_map, symbols):
        seen["symbols"] = symbols
        return {s: {} for s in symbols}

    monkeypatch.setattr(hf, "run_oneday_full_window", fake_run)
    result = {}
    universe = {"universe": [{"symbol": "OLD.ETORO"}, {"symbol": "MARA.ETORO"}, {"symbol": "MARA.ETORO"}]}
    do._phase2e_oneday_full_window(logging.getLogger("t"), universe, "k", "u", result)
    assert seen["symbols"] == ["MARA.ETORO"] and result["oneday_filled"] == ["MARA.ETORO"]


def test_paper_selection_has_room_and_no_deflated_winner_filter():
    from automation import papertrading as pt
    assert pt.PAPER_MAX_CONCURRENT >= 40
    assert pt.PAPER_SELECTION_GATES["deflated_selection"] is False
    assert pt.daily_profile_spec(1400.0)["tournament.json"]["deflated_selection"] is False


def test_trailing_tp_lock_long_and_short():
    from automation.strategies.hourly_strategy_base import compute_trailing_tp_lock as lock
    # nicht aktiv: Gewinn 0,4 ATR < 0,5 ATR
    assert lock(100.0, 100.4, 1.0, "LONG", 0.5, 0.3, 6.0) is None
    # aktiv: Bestkurs 101, Sicherung max(101 - 0,3, 100 + 0,06) = 100,7
    assert abs(lock(100.0, 101.0, 1.0, "LONG", 0.5, 0.3, 6.0) - 100.7) < 1e-9
    # Mindest-Sicherung über dem Einstieg greift, wenn der Rücklauf grösser wäre als der Gewinn
    assert abs(lock(100.0, 100.6, 1.0, "LONG", 0.5, 2.0, 6.0) - 100.06) < 1e-9
    # Spread zu gross für den Gewinn ⇒ nicht aktiv
    assert lock(100.0, 100.6, 1.0, "LONG", 0.5, 0.3, 40.0) is None
    assert abs(lock(100.0, 99.0, 1.0, "SHORT", 0.5, 0.3, 6.0) - 99.3) < 1e-9


def test_trailing_tp_config_off_by_default_on_in_paper():
    from automation import papertrading as pt
    from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
    assert "trailing_tp_activation_atr" in HourlyStrategyConfig.__struct_fields__
    assert HourlyStrategyConfig(instrument_id="X.ETORO", bar_type="b").trailing_tp_activation_atr is None
    sd = pt.daily_profile_spec(1400.0)["strategy_defaults.json"]
    assert sd and all(v == pt.PAPER_TRAILING_TP for v in sd.values())
    assert "SmaCrossoverStrategy" in sd


def test_round_the_clock_resolution():
    existing = {"1": {"symbol": "BTC.ETORO", "asset_class": "crypto"}}
    meta = {
        "1": {"SymbolFull": "BTC", "InstrumentTypeID": 10},
        "2": {"SymbolFull": "ETH", "InstrumentTypeID": 10},
        "3": {"SymbolFull": "EURUSD", "InstrumentTypeID": 1},
        "4": {"SymbolFull": "GOLD", "InstrumentTypeID": 2},
        "5": {"SymbolFull": "AAPL", "InstrumentTypeID": 5},
        "6": {"SymbolFull": "SPX500", "InstrumentTypeID": 4},
        "7": {"SymbolFull": "OLDCOIN", "InstrumentTypeID": 10, "IsDelisted": True},
        "8": {"SymbolFull": "BTC", "InstrumentTypeID": 10},
    }
    out = uf.resolve_round_the_clock_symbols(existing, meta, ("crypto", "forex", "commodity"))
    assert {v["symbol"]: v["asset_class"] for v in out.values()} == {
        "ETH.ETORO": "crypto", "EURUSD.ETORO": "forex", "GOLD.ETORO": "commodity"}
    assert out["3"]["price_precision"] == 5


def test_round_the_clock_config(tmp_path):
    f = tmp_path / "rtc.json"
    f.write_text('{"enabled": true, "asset_classes": ["crypto", "index", "forex"]}')
    assert uf.load_round_the_clock_classes(f) == ("crypto", "forex")
    f.write_text('{"enabled": false, "asset_classes": ["crypto"]}')
    assert uf.load_round_the_clock_classes(f) == ()
    assert uf.load_round_the_clock_classes() == ("crypto", "forex", "commodity")


def test_trailing_tp_exit_over_a_price_path():
    from types import SimpleNamespace
    from nautilus_trader.model.enums import PositionSide
    from automation.strategies.hourly_strategy_base import HourlyStrategyBase
    cfg = SimpleNamespace(trailing_tp_activation_atr=0.5, trailing_tp_trail_atr=0.3,
                          trailing_tp_min_lock_spread_mult=2.0)
    fake = SimpleNamespace(config=cfg, _exit_atr=SimpleNamespace(initialized=True, value=1.0), _ttp_peak=None,
                           instrument_id="NOPE.ETORO", _effective_atr_value=lambda atr, price: atr)
    pos = SimpleNamespace(side=PositionSide.LONG, avg_px_open=100.0)
    fired = [HourlyStrategyBase._trailing_tp_lock(fake, pos, c) for c in (100.2, 100.6, 101.2, 101.0, 100.85)]
    assert fired[:4] == [None, None, None, None]          # Bestkurs 101,2 ⇒ Sicherung 100,9
    assert fired[4] and "Trailing Take-Profit LONG" in fired[4]
    off = SimpleNamespace(**{**vars(fake), "config": SimpleNamespace(trailing_tp_activation_atr=None)})
    assert HourlyStrategyBase._trailing_tp_lock(off, pos, 90.0) is None


def _phase1_fetch_called(tmp_path, monkeypatch, imap: dict) -> bool:
    import json
    import logging
    from unittest.mock import AsyncMock

    from automation import daily_orchestrator as do
    imap_path = tmp_path / "instrument_map.json"
    imap_path.write_text(json.dumps(imap), "utf-8")
    universe_path = tmp_path / "universe.json"
    universe_path.write_text("{}", "utf-8")  # frische mtime: die 6-h-Sperre der Zusatz-Symbole griffe
    fresh = {"fetched_at": datetime.now(timezone.utc).isoformat(), "universe": [{"symbol": "AAPL.ETORO", "etoro_id": "1"}]}
    fetch = AsyncMock(return_value=True)
    monkeypatch.setattr(do, "INSTRUMENT_MAP_PATH", imap_path)
    monkeypatch.setattr(do, "UNIVERSE_PATH", universe_path)
    monkeypatch.setattr(do, "_load_universe_file", lambda log: fresh)
    monkeypatch.setattr(uf, "run_fetch", fetch)
    monkeypatch.setattr(uf, "load_extra_symbols", lambda *a, **k: [])
    monkeypatch.setattr(uf, "load_instrument_map", lambda *a, **k: {})
    do.phase1_universe_and_mapping(logging.getLogger("t"), api_key="k", user_key="u")
    return fetch.called


def test_phase1_fetches_round_the_clock_even_when_universe_is_fresh(tmp_path, monkeypatch):
    assert _phase1_fetch_called(tmp_path, monkeypatch, {"instruments": {}})
    assert not _phase1_fetch_called(tmp_path, monkeypatch,
                                    {"instruments": {}, uf.ROUND_THE_CLOCK_STAMP: "2026-10-10T18:00:00+00:00",
                                     uf.ROUND_THE_CLOCK_FILTER_KEY: uf.ROUND_THE_CLOCK_FILTER_VERSION})
    # Stempel aus PR 1316 ohne Filterversion: erneuter Abgleich, damit Terminkontrakte wieder herausfallen.
    assert _phase1_fetch_called(tmp_path, monkeypatch,
                                {"instruments": {}, uf.ROUND_THE_CLOCK_STAMP: "2026-10-10T16:16:57+00:00"})


def _spot(sym, type_id=10, name=None, **extra):
    return {"SymbolFull": sym, "InstrumentTypeID": type_id, "InstrumentDisplayName": name or sym,
            "IsInternalInstrument": False, "HasExpirationDate": False, "PriceSource": "eToro", **extra}


def test_round_the_clock_keeps_only_tradable_spot_instruments():
    meta = {
        "1": _spot("SOL", name="Solana"),
        "2": _spot("BTC.JAN26", name="Micro Bitcoin Jan 26 Future", PriceSource="CME"),
        "3": _spot("GIGA.old", IsInternalInstrument=True),
        "4": _spot("BTCEUR", name="Bitcoin/Euro"),
        "5": _spot("USDT", name="Tether"),
        "6": _spot("CL.JUL20", 2, "Crude Oil Future July 20", HasExpirationDate=True),
        "7": _spot("OIL", 2, "Crude Oil"),
        "8": _spot("GOLDEUR", 2, "Gold/Euro"),
        "9": _spot("EURJPY", 1, "EUR/JPY"),
        "10": _spot("EURUSD.MAR27", 1, "EUR/USD Mar 27", PriceSource="CME"),
        "11": _spot("DOGE", name="Dogecoin", IsInternalInstrument=True),
    }
    out = uf.resolve_round_the_clock_symbols({}, meta, ("crypto", "forex", "commodity"))
    assert sorted(v["symbol"] for v in out.values()) == ["EURJPY.ETORO", "OIL.ETORO", "SOL.ETORO"]


def test_prune_removes_earlier_non_spot_entries_but_keeps_unknown_ones():
    existing = {
        "1": {"symbol": "SOL.ETORO", "asset_class": "crypto"},
        "2": {"symbol": "BTC.JAN26.ETORO", "asset_class": "crypto"},
        "6": {"symbol": "CL.JUL20.ETORO", "asset_class": "commodity"},
        "99": {"symbol": "XYZ.ETORO", "asset_class": "crypto"},
        "50": {"symbol": "PSN.US.ETORO", "asset_class": "equity"},
    }
    meta = {"1": _spot("SOL"), "2": _spot("BTC.JAN26", PriceSource="CME"),
            "6": _spot("CL.JUL20", 2, HasExpirationDate=True), "50": _spot("PSN.US", 5)}
    removed = uf.prune_round_the_clock_entries(existing, meta, ("crypto", "forex", "commodity"))
    assert set(removed) == {"2", "6"}
    assert set(existing) == {"1", "99", "50"}
