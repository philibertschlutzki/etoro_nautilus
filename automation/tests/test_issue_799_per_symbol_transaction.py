"""Issue #799 — ein Fehler in Phase 1 vernichtete alle Ergebnisse aller bereits abgeschlossenen
Symbole; es gab keinen Checkpoint.

Root-Cause: ``executor.map(_run_optimize, pairs)`` war eine globale Barriere über ALLE Paare eines
Sweeps; die familienweite Multiplizität (#652) und Phase 2 (Confirm/Export/Retention) warteten auf
den Abschluss JEDES Paares, obwohl sie nur die Studies EINES Symbols brauchen. Der Fix macht den
Dispatch pro Symbol transaktional: sobald alle Strategien eines Symbols fertig sind, wird sofort
confirmt/exportiert/geräumt, dann geht es zum nächsten Symbol. Ein Fehler kostet höchstens ein
Symbol; ein Fortschritts-Checkpoint erlaubt einen Neustart ohne Duplikat-Proposals.
"""
import json
from pathlib import Path

from automation.optimizer import sweep

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


class _FakeTrial:
    def __init__(self, oos_evaluated=True):
        # Issue #822 — _family_n_from_studies zaehlt seit dem Fix
        # oos_selection_statistic_available statt oos_evaluated.
        self.user_attrs = {"oos_evaluated": oos_evaluated,
                           "oos_selection_statistic_available": oos_evaluated}


class _FakeStudy:
    def __init__(self, n_trials=10):
        self.trials = [_FakeTrial() for _ in range(n_trials)]
        self._attrs = {"n_trials_budget": n_trials}
        self.best_value = 1.0
        self.directions = ["maximize"]

    @property
    def user_attrs(self):
        return dict(self._attrs)


def _patch_common(monkeypatch, tmp_path, pairs):
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep, "WORK", tmp_path)


def test_failure_in_one_symbol_does_not_lose_prior_or_later_symbols(monkeypatch, tmp_path):
    """5 Symbole, jedes mit 1 Strategie; Symbol 3 wirft. Symbole 1-2 und 4-5 liefern trotzdem
    ihre Proposals; Symbol 3 landet in keinem Proposal, aber der Sweep laeuft komplett durch."""
    symbols = [f"SYM{i}.ETORO" for i in range(1, 6)]
    pairs = [("S", sym, "OK") for sym in symbols]

    def fake_opt(strategy, symbol, **k):
        if symbol == "SYM3.ETORO":
            raise RuntimeError("Backtest-Subprozess abgestürzt")
        return _FakeStudy()

    def fake_confirm(study, s, sym, gp, **k):
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], symbols, tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )
    expected = {tmp_path / f"proposal_S_{sym}.json" for sym in symbols if sym != "SYM3.ETORO"}
    assert set(out) == expected
    assert len(out) == 4


def test_symbol_level_family_n_is_scoped_to_its_own_studies(monkeypatch, tmp_path):
    """Zwei Strategien auf Symbol A (10 Trials je Study) und eine dritte Strategie auf Symbol B
    (10 Trials): deflation_n_family fuer S1/S2 (A) darf NICHT durch die jeweils ANDERE Strategie
    desselben Symbols aufgeblaeht werden.

    Issue #826 — VOR diesem Fix erhielt JEDE Strategie eines Symbols dieselbe SYMBOL-weite Summe
    (20 fuer A = S1 + S2). Das war exakt die #826-Root-Cause: eine PER-STRATEGIE-Entscheidung
    (``export_symbol_proposal`` je (Strategie, Symbol)) erhielt eine SYMBOL-weite Multiplizitaet —
    eine Erweiterung des Rosters um weitere Strategien haette die Promotion-Huerde JEDER
    bestehenden Strategie erhoeht, ohne dass sich deren eigene Evidenz geaendert haette. Unter dem
    neuen Default ``promotion_family_scope='per_strategy'`` erhaelt JEDE Study nur ihr EIGENES N1
    (``sweep._family_n_stage1_from_studies``)."""
    pairs = [("S1", "A.ETORO", "OK"), ("S2", "A.ETORO", "OK"), ("S3", "B.ETORO", "OK")]
    captured_family_n = {}

    def fake_opt(strategy, symbol, **k):
        return _FakeStudy(n_trials=10)

    def fake_confirm(study, s, sym, gp, *, deflation_n_family=0, deflation_family_period_returns=None, **k):
        captured_family_n[(s, sym)] = deflation_n_family
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    _patch_common(monkeypatch, tmp_path, pairs)
    sweep.run_per_symbol_sweep(
        ["S1", "S2", "S3"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )
    assert captured_family_n[("S1", "A.ETORO")] == 10  # NICHT 20 (S2s Trials zaehlen nicht mit)
    assert captured_family_n[("S2", "A.ETORO")] == 10  # NICHT 20 (S1s Trials zaehlen nicht mit)
    assert captured_family_n[("S3", "B.ETORO")] == 10  # 1 Study x 10 evaluierte Trials


def test_family_n_from_studies_still_sums_across_strategies_as_pure_telemetry(monkeypatch, tmp_path):
    """Issue #826 — die symbolweite Summe (``sweep._family_n_from_studies``) bleibt als
    Rueckwaertskompat-/Telemetrie-Groesse bestehen, fliesst aber seit diesem Fix NICHT mehr in
    ``deflation_n_family`` ein (siehe vorheriger Test) — hier ueber ``sweep._family_n_from_studies``
    direkt nachgewiesen, unabhaengig vom Confirm-Aufruf."""
    from automation.optimizer import sweep as sweep_module

    class _T:
        def __init__(self):
            self.user_attrs = {"oos_selection_statistic_available": True}

    class _S:
        def __init__(self, n):
            self.trials = [_T() for _ in range(n)]
            self.user_attrs = {}

    pairs = [("S1", "A.ETORO", "OK"), ("S2", "A.ETORO", "OK")]
    studies = [_S(10), _S(10)]
    assert sweep_module._family_n_from_studies(pairs, studies, tournament_cfg={}) == {"A.ETORO": 20}
    stage1 = sweep_module._family_n_stage1_from_studies(pairs, studies, tournament_cfg={})
    assert stage1 == {("S1", "A.ETORO"): 10, ("S2", "A.ETORO"): 10}
    stage2 = sweep_module._family_n_stage2_from_studies(pairs, studies, tournament_cfg={})
    assert stage2 == {"A.ETORO": 2}


def test_checkpoint_written_after_each_symbol(monkeypatch, tmp_path):
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]

    def fake_opt(strategy, symbol, **k):
        return _FakeStudy()

    _patch_common(monkeypatch, tmp_path, pairs)
    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1, run_id="run-abc",
        optimize_symbol=fake_opt, confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
    assert checkpoint["run_id"] == "run-abc"
    assert sorted(checkpoint["completed_symbols"]) == ["A.ETORO", "B.ETORO"]
    assert checkpoint["failed_pairs"] == []


def test_resume_skips_already_completed_symbols_and_avoids_duplicate_work(monkeypatch, tmp_path):
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    _patch_common(monkeypatch, tmp_path, pairs)

    (tmp_path / "sweep_progress.json").write_text(json.dumps({
        "run_id": "resumed-run", "completed_symbols": ["A.ETORO"], "failed_pairs": [],
    }), "utf-8")
    # das Proposal von A.ETORO existiert bereits aus dem "vorherigen" (abgebrochenen) Lauf.
    (tmp_path / "proposal_S_A.ETORO.json").write_text("{}", "utf-8")

    started = []

    def fake_opt(strategy, symbol, **k):
        started.append(symbol)
        return _FakeStudy()

    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1, run_id="resumed-run",
        optimize_symbol=fake_opt, confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert started == ["B.ETORO"], "A.ETORO darf wegen des Checkpoints NICHT erneut optimiert werden"
    assert set(out) == {tmp_path / "proposal_S_A.ETORO.json", tmp_path / "proposal_S_B.ETORO.json"}

    checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
    assert sorted(checkpoint["completed_symbols"]) == ["A.ETORO", "B.ETORO"]


def test_different_run_id_ignores_stale_checkpoint(monkeypatch, tmp_path):
    pairs = [("S", "A.ETORO", "OK")]
    _patch_common(monkeypatch, tmp_path, pairs)
    (tmp_path / "sweep_progress.json").write_text(json.dumps({
        "run_id": "old-run", "completed_symbols": ["A.ETORO"], "failed_pairs": [],
    }), "utf-8")

    started = []

    def fake_opt(strategy, symbol, **k):
        started.append(symbol)
        return _FakeStudy()

    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO"], tier="all", n_jobs=1, run_id="new-run",
        optimize_symbol=fake_opt, confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert started == ["A.ETORO"], "ein abweichender run_id darf den Checkpoint nicht als Resume nutzen"


def test_failed_pair_is_recorded_in_checkpoint(monkeypatch, tmp_path):
    pairs = [("S", "A.ETORO", "OK")]

    def fake_opt(strategy, symbol, **k):
        raise OSError("Platte voll")

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO"], tier="all", n_jobs=1, run_id="run-xyz",
        optimize_symbol=fake_opt, confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert out == []
    checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
    assert checkpoint["completed_symbols"] == ["A.ETORO"]  # Symbol GILT als abgeschlossen (kein Retry-Loop)
    assert len(checkpoint["failed_pairs"]) == 1
    assert checkpoint["failed_pairs"][0]["strategy"] == "S"
    assert checkpoint["failed_pairs"][0]["symbol"] == "A.ETORO"
    assert checkpoint["failed_pairs"][0]["exception_type"] == "OSError"


def test_an_oserror_in_one_pair_does_not_abort_the_sweep(monkeypatch, tmp_path):
    """Akzeptanzkriterium #799: ein OSError/StorageInternalError in einem Paar beendet den Sweep
    nicht."""
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]

    def fake_opt(strategy, symbol, **k):
        if symbol == "A.ETORO":
            raise OSError("database is locked")
        return _FakeStudy()

    _patch_common(monkeypatch, tmp_path, pairs)
    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert out == [tmp_path / "proposal_S_B.ETORO.json"]
