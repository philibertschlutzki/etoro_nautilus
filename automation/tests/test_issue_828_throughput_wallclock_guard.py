"""Issue #828 (P0, Katalog #828-#835, GitHub-Issue #751) — 3,6x Laufzeit-Regression: 8 von 22
Workern werden nie benutzt, die Per-Symbol-Barriere wartet auf den Nachzügler, und es existiert
kein Optuna-Pruner.

Scope-Entscheidung (siehe run_optimization.run_per_symbol_sweep's Docstring für die vollständige
Begründung): implementiert sind Fix Punkt 3 (max_workers nicht mehr an len(symbol_pairs) gedeckelt)
und Fix Punkt 5 (wallclock_guard, strukturgleich zu disk_guard #795). BEWUSST NICHT implementiert:
Fix Punkt 1/2 (echtes Pipelining über Symbolgrenzen + Largest-First-Scheduling — eine Restrukturierung
der Dispatch-Schleife, deren Akzeptanzkriterien [Worker-Auslastung, Wallclock-Hochrechnung] rein
empirisch sind und ohne einen echten Mehrsymbol-Produktionslauf in diesem Environment nicht
verifizierbar wären, bei hohem Risiko einer stillen Korrektheitsregression in den > 15 bestehenden
Dispatch-Tests) und Fix Punkt 4 (SuccessiveHalvingPruner — der fold-weise Zwischenwert für
trial.report() wird erst NACH dem vollständigen Rückkehren des Backtest-Subprozesses bekannt; ein
Pruner an dieser Stelle könnte keine Rechenzeit sparen, nur nachträglich die TPE-Stichprobenwahl
beeinflussen — das entspricht nicht dem im Issue behaupteten Durchsatzgewinn).
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import wallclock_guard


@pytest.fixture(autouse=True)
def _reset_wallclock_guard():
    wallclock_guard.reset_for_tests()
    yield
    wallclock_guard.reset_for_tests()


# ── wallclock_guard.check_wallclock_budget: reine Arithmetik ────────────────────────────────────────
def test_check_wallclock_budget_false_when_under_budget():
    assert wallclock_guard.check_wallclock_budget(3600.0, max_hours=24.0) is False


def test_check_wallclock_budget_true_when_at_or_over_budget():
    assert wallclock_guard.check_wallclock_budget(24.0 * 3600.0, max_hours=24.0) is True
    assert wallclock_guard.check_wallclock_budget(25.0 * 3600.0, max_hours=24.0) is True


def test_check_wallclock_budget_none_means_unlimited():
    assert wallclock_guard.check_wallclock_budget(10_000.0 * 3600.0, max_hours=None) is False


# ── Fix Punkt 3: max_workers nicht mehr an len(symbol_pairs) gedeckelt ──────────────────────────────
def test_thread_pool_max_workers_is_n_jobs_not_capped_by_pair_count(monkeypatch, tmp_path):
    """Vorher: min(n_jobs, len(symbol_pairs)) -- bei n_jobs=22, 3 Strategien/Symbol waeren nur 3
    Worker angefordert worden. Jetzt: n_jobs direkt (der Pool nutzt ohnehin nie mehr als es
    Task gibt -- der Test verifiziert nur, dass die Deckelung selbst entfernt ist)."""
    from automation.optimizer import sweep
    from concurrent.futures import ThreadPoolExecutor as _RealThreadPoolExecutor

    captured_max_workers = []

    class _CapturingExecutor:
        def __init__(self, max_workers=None):
            captured_max_workers.append(max_workers)
            self._real = _RealThreadPoolExecutor(max_workers=max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._real.shutdown(wait=True)
            return False

        def map(self, fn, iterable):
            return self._real.map(fn, iterable)

    import concurrent.futures
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", _CapturingExecutor)

    pairs = [("S1", "A.ETORO", "OK"), ("S2", "A.ETORO", "OK"), ("S3", "A.ETORO", "OK")]
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {
        "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4,
                         "holdout_days": 45},
        "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
    })

    sweep.run_per_symbol_sweep(
        ["S1", "S2", "S3"], ["A.ETORO"], tier="all", n_jobs=22,
        optimize_symbol=lambda strategy, symbol, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert captured_max_workers == [22]  # NICHT min(22, 3) == 3


# ── Fix Punkt 5: sweep.py-Verdrahtung — dasselbe Muster wie disk_guard.sweep_abort_requested ────────
_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _cfg_dir(tmp_path, sweep_max_wallclock_h):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "optimizer.json").write_text(
        json.dumps({"sweep_max_wallclock_h": sweep_max_wallclock_h}), "utf-8")
    return cfg_dir


def test_sweep_skips_remaining_symbols_once_wallclock_budget_exceeded(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK"), ("S", "C.ETORO", "OK")]
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: _cfg_dir(tmp_path, sweep_max_wallclock_h=1.0))
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)

    # Fake-Uhr: sweep_t0 liest 0.0; nach Symbol A "vergehen" 2h (> 1h-Budget) -> B/C duerfen nicht
    # mehr starten.
    fake_clock = [0.0]
    monkeypatch.setattr(sweep.time, "perf_counter", lambda: fake_clock[0])

    started = []

    def fake_opt(strategy, symbol, **k):
        started.append(symbol)
        if symbol == "A.ETORO":
            fake_clock[0] = 2.0 * 3600.0
        return object()

    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO", "C.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt,
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert started == ["A.ETORO"]
    assert out == [tmp_path / "proposal_A.ETORO.json"]
    assert wallclock_guard.sweep_wallclock_exceeded.is_set()


def test_sweep_runs_to_completion_when_wallclock_budget_is_disabled(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: _cfg_dir(tmp_path, sweep_max_wallclock_h=None))
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)

    started = []

    def fake_opt(strategy, symbol, **k):
        started.append(symbol)
        return object()

    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt,
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert started == ["A.ETORO", "B.ETORO"]
    assert not wallclock_guard.sweep_wallclock_exceeded.is_set()


def test_sweep_defaults_to_the_24h_active_budget_when_key_missing(monkeypatch, tmp_path):
    """Fehlt der Key komplett (kaputte/minimale Config) -> Fail-Safe-Default 24h, NICHT
    deaktiviert (dieselbe Haltung wie disk_budget_gb/disk_reserve_gb: ein Config-Fehler darf die
    Absicherung nicht lautlos abschalten). Mit Millisekunden-Testlaufzeit loest das nie aus."""
    from automation.optimizer import sweep

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "optimizer.json").write_text(json.dumps({}), "utf-8")  # kein sweep_max_wallclock_h
    pairs = [("S", "A.ETORO", "OK")]
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)

    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=lambda strategy, symbol, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert not wallclock_guard.sweep_wallclock_exceeded.is_set()


# ── Config-Default: die reale optimizer.json setzt 24 ────────────────────────────────────────────
def test_real_config_default_is_24_hours():
    data = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert data["sweep_max_wallclock_h"] == 24
