"""Issue #400 — `--n-jobs` in sweep.py wurde ignoriert (Sweep lief strikt sequenziell).

`run_per_symbol_sweep` verteilt die (strategy, symbol)-Paare jetzt ueber einen
ThreadPoolExecutor (Ansatz 4: je Paar eine eigene SQLite-Study). Die Tests sind voll
gemockt (HI-7, kein echter Backtest) und beweisen Nebenlaeufigkeit deterministisch ueber
eine threading.Barrier: laeuft der Sweep faelschlich sequenziell, fuellt sich die Barrier
nie und der Lauf scheitert am Timeout (Regressions-Schutz).

Issue #799 — der Dispatch ist seither PRO SYMBOL transaktional: die aeussere Schleife ueber
Symbole laeuft sequenziell (jedes Symbol wird sofort confirmt/exportiert/geraeumt, bevor das
naechste beginnt — genau das macht einen Fehler auf hoechstens ein Symbol begrenzt statt den
gesamten Sweep zu vernichten); ``n_jobs`` parallelisiert NUR NOCH die Strategien INNERHALB
EINES Symbols. Der Nebenlaeufigkeits-Test unten nutzt daher mehrere Strategien auf DEMSELBEN
Symbol (vorher: mehrere Symbole mit je einer Strategie) — unter #799 laeuft Ersteres parallel,
Letzteres bewusst sequenziell (ein Symbol nach dem anderen).
"""
import threading

from automation.optimizer import sweep

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _patch_common(monkeypatch, tmp_path, pairs):
    # Issue #799 — der Sweep-Fortschritts-Checkpoint schreibt nach WORK; isoliert halten.
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)


def test_n_jobs_gt_1_runs_strategies_of_one_symbol_concurrently(monkeypatch, tmp_path):
    """Issue #799 — die Parallelisierung ist jetzt INNERHALB eines Symbols (ueber seine
    Strategien), nicht mehr ueber Symbole hinweg (die Symbol-Schleife selbst ist absichtlich
    sequenziell, siehe Moduldoc). Drei Strategien AUF DEMSELBEN Symbol, n_jobs=3: eine Barrier(3)
    loest nur auf, wenn alle drei gleichzeitig laufen. Ein sequenzieller Regress wuerde am
    Barrier-Timeout (BrokenBarrierError) scheitern."""
    pairs = [("S1", "A.ETORO", "OK"), ("S2", "A.ETORO", "OK"), ("S3", "A.ETORO", "OK")]
    barrier = threading.Barrier(len(pairs), timeout=10)
    started: list[str] = []
    lock = threading.Lock()

    def fake_opt(strategy, symbol, **k):
        with lock:
            started.append(strategy)
        barrier.wait()  # nur erfuellbar, wenn alle drei Strategien nebenlaeufig hier ankommen
        return object()

    def fake_confirm(study, s, sym, gp, **k):
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    # Issue #799 — der Sweep-Fortschritts-Checkpoint schreibt nach WORK; isoliert halten.
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)

    out = sweep.run_per_symbol_sweep(
        ["S1", "S2", "S3"], ["A.ETORO"], tier="all", n_jobs=3,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )
    assert sorted(started) == ["S1", "S2", "S3"]
    # executor.map bewahrt die Eingabereihenfolge → deterministische Proposal-Reihenfolge
    assert sorted(out) == sorted([tmp_path / "proposal_S1_A.ETORO.json",
                                  tmp_path / "proposal_S2_A.ETORO.json",
                                  tmp_path / "proposal_S3_A.ETORO.json"])


def test_symbols_are_processed_one_at_a_time_not_concurrently(monkeypatch, tmp_path):
    """Issue #799 — Gegenprobe zum obigen Test: mehrere SYMBOLE (je einer eigenen Strategie)
    laufen NICHT gleichzeitig; eine Barrier(3) wuerde hier NIE aufgeloest. Verifiziert per
    Timeout-Erwartung (BrokenBarrierError), dass die Symbol-Schleife sequenziell bleibt —
    genau die Eigenschaft, die die Transaktionalitaet (#799) voraussetzt."""
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK"), ("S", "C.ETORO", "OK")]
    barrier = threading.Barrier(len(pairs), timeout=0.3)

    def fake_opt(strategy, symbol, **k):
        barrier.wait()  # darf NIE alle drei gleichzeitig sehen -> muss BrokenBarrierError werfen
        return object()

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO", "C.ETORO"], tier="all", n_jobs=3,
        optimize_symbol=fake_opt, confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    # Jedes Symbol scheitert einzeln an der Barrier (isoliert, #799) -> kein Proposal, kein Crash.
    assert out == []


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


def test_worker_exception_is_isolated_per_pair_not_fail_fast(monkeypatch, tmp_path):
    """Issue #799 — Root-Cause des Katalogs: vorher vernichtete ein einziger fundamentaler Fehler
    (propagiert durch executor.map) die Ergebnisse ALLER bereits abgeschlossenen Studies. Jetzt
    wird jede Exception in _run_optimize gefangen, als STUDY_FAILED protokolliert, und liefert
    None fuer GENAU dieses Paar — der Sweep laeuft weiter statt zu crashen."""
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]

    def fake_opt(strategy, symbol, **k):
        if symbol == "A.ETORO":
            raise RuntimeError("fundamentaler Fehler")
        return object()

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=2,
        optimize_symbol=fake_opt, confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    # A.ETORO ist fehlgeschlagen (kein Proposal), B.ETORO lief normal durch.
    assert out == [tmp_path / "proposal_B.ETORO.json"]
