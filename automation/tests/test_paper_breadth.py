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
