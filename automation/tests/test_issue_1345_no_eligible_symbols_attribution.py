"""Issue #1345 (GH #1239) — ``[#595]`` (Strategie-Registry-Paritätslücke) feuert nicht mehr für
``NO_ELIGIBLE_SYMBOLS`` (Folge einer Symbol-Preflight-Ablehnung). Letztere erzeugt stattdessen
GENAU EINE Lauf-Ebene-Meldung mit der Symbolursache, nicht eine Aufzählung je Strategie.
"""
import logging

from automation.optimizer import sweep


def test_no_eligible_symbols_produces_single_run_level_message_not_595(tmp_path, monkeypatch, caplog):
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sweep, "emit_execution_event", lambda *a, **kw: None)
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: ["ONLY.ETORO"])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})

    caplog.set_level(logging.WARNING, logger="optimizer")
    try:
        sweep.run_per_symbol_sweep(
            ["SmaCrossoverStrategy", "MeanReversionStrategy"], ["ONLY.ETORO"],
            optimize_symbol=lambda pair: None, confirm=lambda *a, **kw: None,
            tick_population_fn=lambda symbol: {"n_ticks_raw": 0, "n_ticks_after_session_filter": 0},
            bar_quality_fn=lambda symbol: None,
            run_id="test-1345-attribution",
        )
    except Exception:
        pass

    messages = [r.getMessage() for r in caplog.records]
    n_595 = sum(1 for m in messages if m.startswith("[#595]"))
    n_1345 = sum(1 for m in messages if m.startswith("[#1345]"))
    assert n_595 == 0, "NO_ELIGIBLE_SYMBOLS darf keine [#595]-Zeile erzeugen"
    assert n_1345 == 1, "genau EINE Lauf-Ebene-Meldung statt einer je Strategie"
    assert "ONLY.ETORO" in messages[[m.startswith("[#1345]") for m in messages].index(True)]


def test_real_search_space_gap_still_emits_595(tmp_path, monkeypatch, caplog):
    """Eine echte Registry-Paritätslücke (NO_SEARCH_SPACE) erzeugt weiterhin [#595]."""
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sweep, "emit_execution_event", lambda *a, **kw: None)
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: ["SOME.ETORO"])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})
    monkeypatch.setattr(sweep, "strategy_has_search_space", lambda s: False)

    caplog.set_level(logging.WARNING, logger="optimizer")
    try:
        sweep.run_per_symbol_sweep(
            ["StrategyWithoutSearchSpace"], ["SOME.ETORO"],
            optimize_symbol=lambda pair: None, confirm=lambda *a, **kw: None,
            tick_population_fn=lambda symbol: {"n_ticks_raw": 100, "n_ticks_after_session_filter": 100},
            bar_quality_fn=lambda symbol: {
                "highs": [100.0 + i * 0.3 for i in range(200)],
                "lows": [99.0 + i * 0.3 for i in range(200)],
                "closes": [99.5 + i * 0.31 for i in range(200)],
                "bar_coverage_ratio": 1.0,
            },
            run_id="test-1345-registry-gap",
        )
    except Exception:
        pass

    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[#595]") and "NO_SEARCH_SPACE" in m for m in messages)
