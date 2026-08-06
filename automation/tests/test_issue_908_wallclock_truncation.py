"""Issue #908 Fix 2 (P1) — die #842-Vorab-Prognose hatte KEINE Konsequenz (nur eine WARNUNG-
Log-Zeile): überschritt die Hochrechnung ``sweep_max_wallclock_h``, lief der Sweep trotzdem bis
zum Timeout durch. Fix: die Symbolliste wird gekürzt (``least_recently_covered`` ist bereits
gesetzt, #841), sobald die Prognose einen Shortfall meldet.
"""
from automation.optimizer import sweep

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _patch_common(monkeypatch, tmp_path, pairs, opt_data_extra=None):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    opt_data = {"wallclock_forecast_after_symbols": 1}
    if opt_data_extra:
        opt_data.update(opt_data_extra)
    monkeypatch.setattr(sweep, "_load_optimizer_config", lambda: opt_data)

    def fake_opt(strategy, symbol, **k):
        return object()

    def fake_confirm(study, s, sym, gp, **k):
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}
    return fake_opt, fake_confirm


def test_shortfall_forecast_truncates_the_symbol_list(monkeypatch, tmp_path):
    """Vier Symbole geplant; die (gemockte) Prognose meldet nach dem ersten Symbol einen
    Shortfall mit reachable_symbols=1 ⇒ nur das erste Symbol wird tatsächlich verarbeitet, die
    übrigen drei werden NICHT gestartet (Symbolliste gekürzt statt Timeout)."""
    symbols = ["A.ETORO", "B.ETORO", "C.ETORO", "D.ETORO"]
    pairs = [("S1", sym, "OK") for sym in symbols]
    fake_opt, fake_confirm = _patch_common(monkeypatch, tmp_path, pairs,
                                           opt_data_extra={"sweep_max_wallclock_h": 1.0})
    monkeypatch.setattr(sweep, "_wallclock_forecast", lambda *a, **k: {
        "reachable_symbols": 1, "shortfall": 3,
    })

    out = sweep.run_per_symbol_sweep(
        ["S1"], symbols, tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )
    # Nur das erste Symbol wurde tatsaechlich confirmt/exportiert.
    assert out == [tmp_path / "proposal_S1_A.ETORO.json"]


def test_forecast_within_budget_does_not_truncate(monkeypatch, tmp_path):
    """Meldet die Prognose KEINEN Shortfall, laeuft der Sweep unveraendert ueber alle Symbole."""
    symbols = ["A.ETORO", "B.ETORO", "C.ETORO"]
    pairs = [("S1", sym, "OK") for sym in symbols]
    fake_opt, fake_confirm = _patch_common(monkeypatch, tmp_path, pairs,
                                           opt_data_extra={"sweep_max_wallclock_h": 1000.0})
    monkeypatch.setattr(sweep, "_wallclock_forecast", lambda *a, **k: {
        "reachable_symbols": 3, "shortfall": 0,
    })

    out = sweep.run_per_symbol_sweep(
        ["S1"], symbols, tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )
    assert len(out) == 3
