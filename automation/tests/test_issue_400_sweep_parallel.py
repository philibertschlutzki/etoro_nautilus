"""Issue #400 — `--n-jobs` in sweep.py wurde ignoriert (Sweep lief strikt sequenziell).

`run_per_symbol_sweep` verteilt die (strategy, symbol)-Paare jetzt ueber einen
ThreadPoolExecutor (Ansatz 4: je Paar eine eigene SQLite-Study). Die Tests sind voll
gemockt (HI-7, kein echter Backtest) und beweisen Nebenlaeufigkeit deterministisch ueber
eine threading.Barrier: laeuft der Sweep faelschlich sequenziell, fuellt sich die Barrier
nie und der Lauf scheitert am Timeout (Regressions-Schutz).
"""
import threading

import pytest

from automation.optimizer import sweep

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _patch_common(monkeypatch, tmp_path, pairs):
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)


def test_n_jobs_gt_1_runs_pairs_concurrently(monkeypatch, tmp_path):
    """Drei Paare, n_jobs=3: eine Barrier(3) loest nur auf, wenn alle drei gleichzeitig laufen.
    Ein sequenzieller Regress wuerde am Barrier-Timeout (BrokenBarrierError) scheitern."""
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK"), ("S", "C.ETORO", "OK")]
    barrier = threading.Barrier(len(pairs), timeout=10)
    started: list[str] = []
    lock = threading.Lock()

    def fake_opt(strategy, symbol, **k):
        with lock:
            started.append(symbol)
        barrier.wait()  # nur erfuellbar, wenn alle drei Paare nebenlaeufig hier ankommen
        return object()

    def fake_confirm(study, s, sym, gp, **k):
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO", "C.ETORO"], tier="all", n_jobs=3,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )
    assert sorted(started) == ["A.ETORO", "B.ETORO", "C.ETORO"]
    # executor.map bewahrt die Eingabereihenfolge → deterministische Proposal-Reihenfolge
    assert out == [tmp_path / "proposal_A.ETORO.json",
                   tmp_path / "proposal_B.ETORO.json",
                   tmp_path / "proposal_C.ETORO.json"]


def test_n_jobs_1_is_sequential_and_ordered(monkeypatch, tmp_path):
    """n_jobs=1 (Default) bleibt sequenziell und ordnungserhaltend (Rueckwaerts-Kompat)."""
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    seen: list[str] = []

    def fake_opt(strategy, symbol, **k):
        seen.append(symbol)
        return object()

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=lambda *a, **k: {},
    )
    assert seen == ["A.ETORO", "B.ETORO"]
    assert out == [tmp_path / "proposal_A.ETORO.json", tmp_path / "proposal_B.ETORO.json"]


def test_worker_exception_propagates_fail_fast(monkeypatch, tmp_path):
    """Fail-Fast: ein fundamentaler Fehler in einem Paar-Worker wird nicht verschluckt,
    sondern propagiert (kein globales try/except Exception)."""
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]

    def fake_opt(strategy, symbol, **k):
        raise RuntimeError("fundamentaler Fehler")

    _patch_common(monkeypatch, tmp_path, pairs)
    with pytest.raises(RuntimeError, match="fundamentaler Fehler"):
        sweep.run_per_symbol_sweep(
            ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=2,
            optimize_symbol=fake_opt, confirm=lambda *a, **k: {},
        )
