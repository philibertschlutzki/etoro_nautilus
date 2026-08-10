"""Issue #841 (P0) — Keine Abdeckungsgarantie: der Universums-Schwanz verhungert.

Die Symbolreihenfolge ist die reine Einfüge-Reihenfolge aus data/universe/momentum_ls.json
(Momentum-Rang) — ohne Rotation des Startoffsets und ohne Gedächtnis, wann ein Symbol zuletzt
optimiert wurde. `symbol_coverage.py` führt ein Abdeckungs-Ledger (`data/optimizer/
symbol_coverage.json`), auf dem `sweep_symbol_order_policy='least_recently_covered'` (Default)
die Dispatch-Reihenfolge sortiert und `invariants.check_symbol_coverage` die Vollständigkeit
über mehrere Läufe hinweg prüft.

Akzeptanzkriterien:
- AK-1: Zwei aufeinanderfolgende Läufe mit einem Budget für je 3 von 7 Symbolen decken zusammen
  6 verschiedene Symbole ab (nicht 2× dieselben 3).
- AK-2: check_symbol_coverage FAILt für ein Symbol, das drei Läufe lang übersprungen wurde, und
  PASSt, sobald es abgedeckt ist.
- AK-3: sweep_symbol_order_policy='universe' reproduziert die heutige Reihenfolge bitgleich.
- AK-4: Die Reihenfolge ist bei identischem Ledger deterministisch.
- AK-5: Nach einem vollständigen Lauf über alle Symbole gilt never_covered == [].
"""
import json

from automation.optimizer import symbol_coverage as sc
from automation.optimizer import invariants as inv


def _empty():
    return sc.load_coverage(path="/nonexistent/path/does/not/exist.json")


def test_load_coverage_fails_open_on_missing_file():
    cov = _empty()
    assert cov == {"schema_version": 1, "total_runs_started": 0, "symbols": {}}


def test_ak3_universe_policy_preserves_insertion_order():
    symbols = ["XOM.ETORO", "NKE.ETORO", "NFLX.ETORO"]
    cov = _empty()
    ordered = sc.order_symbols(symbols, cov, policy="universe")
    assert ordered == symbols


def test_ak4_least_recently_covered_is_deterministic_for_identical_ledger():
    symbols = ["A.ETORO", "B.ETORO", "C.ETORO"]
    cov = _empty()
    cov = sc.record_symbol_completion(cov, "B.ETORO", run_id="r1", run_index=1,
                                      completed_at_utc="2026-01-01T00:00:00Z")
    ordered_1 = sc.order_symbols(symbols, cov, policy="least_recently_covered")
    ordered_2 = sc.order_symbols(symbols, cov, policy="least_recently_covered")
    assert ordered_1 == ordered_2
    # A/C nie abgedeckt -> zuerst (Reihenfolge untereinander stabil = urspruengliche Listenreihenfolge)
    assert ordered_1[:2] == ["A.ETORO", "C.ETORO"]
    assert ordered_1[2] == "B.ETORO"


def test_least_recently_covered_orders_oldest_completion_first():
    symbols = ["A.ETORO", "B.ETORO"]
    cov = _empty()
    cov = sc.record_symbol_completion(cov, "A.ETORO", run_id="r1", run_index=1,
                                      completed_at_utc="2026-01-05T00:00:00Z")
    cov = sc.record_symbol_completion(cov, "B.ETORO", run_id="r1", run_index=1,
                                      completed_at_utc="2026-01-01T00:00:00Z")
    ordered = sc.order_symbols(symbols, cov, policy="least_recently_covered")
    assert ordered == ["B.ETORO", "A.ETORO"]  # B ist AELTER (frueheres Datum) -> zuerst


def test_unknown_policy_raises():
    import pytest
    with pytest.raises(ValueError):
        sc.order_symbols(["A"], _empty(), policy="bogus")


# ── AK-1: zwei Laeufe decken zusammen mehr Symbole ab als ein einzelner ─────────────────────────
def test_ak1_two_runs_with_partial_budget_cover_different_symbols():
    universe = ["A.ETORO", "B.ETORO", "C.ETORO", "D.ETORO", "E.ETORO", "F.ETORO", "G.ETORO"]
    cov = _empty()

    # Lauf 1: nur Budget fuer 3 Symbole.
    cov = sc.start_new_run(cov)
    ordered_1 = sc.order_symbols(universe, cov, policy="least_recently_covered")
    completed_1 = ordered_1[:3]
    for i, sym in enumerate(completed_1):
        cov = sc.record_symbol_completion(cov, sym, run_id="run-1", run_index=1,
                                          completed_at_utc=f"2026-01-01T00:0{i}:00Z")

    # Lauf 2: erneut Budget fuer 3 Symbole -- muss die ANDEREN abdecken (least-recently-covered).
    cov = sc.start_new_run(cov)
    ordered_2 = sc.order_symbols(universe, cov, policy="least_recently_covered")
    completed_2 = ordered_2[:3]
    for i, sym in enumerate(completed_2):
        cov = sc.record_symbol_completion(cov, sym, run_id="run-2", run_index=2,
                                          completed_at_utc=f"2026-01-02T00:0{i}:00Z")

    assert set(completed_1).isdisjoint(set(completed_2))
    assert len(set(completed_1) | set(completed_2)) == 6


# ── AK-2/AK-5: check_symbol_coverage ─────────────────────────────────────────────────────────────
def test_ak2_stale_symbol_fails_and_passes_once_covered():
    universe = ["A.ETORO", "B.ETORO"]
    cov = _empty()
    # A wurde vor 5 Laeufen zuletzt abgedeckt, B gerade eben. Aktueller total_runs_started = 5.
    cov["total_runs_started"] = 5
    cov = sc.record_symbol_completion(cov, "A.ETORO", run_id="old", run_index=0,
                                      completed_at_utc="2025-01-01T00:00:00Z")
    cov["total_runs_started"] = 5  # record_symbol_completion uebernimmt den bestehenden Wert
    cov = sc.record_symbol_completion(cov, "B.ETORO", run_id="now", run_index=5,
                                      completed_at_utc="2026-01-01T00:00:00Z")

    result = inv.check_symbol_coverage(cov, universe, max_age_runs=3)
    assert result.passed is False
    assert "A.ETORO" in (result.actual or {})

    # A wird jetzt (Lauf 6) abgedeckt -> PASS.
    cov["total_runs_started"] = 6
    cov = sc.record_symbol_completion(cov, "A.ETORO", run_id="fresh", run_index=6,
                                      completed_at_utc="2026-01-02T00:00:00Z")
    result_after = inv.check_symbol_coverage(cov, universe, max_age_runs=3)
    assert result_after.passed is True


def test_ak5_never_covered_after_full_run_is_empty():
    universe = ["A.ETORO", "B.ETORO"]
    cov = _empty()
    cov = sc.start_new_run(cov)
    for sym in universe:
        cov = sc.record_symbol_completion(cov, sym, run_id="run-1", run_index=1,
                                          completed_at_utc="2026-01-01T00:00:00Z")
    report = sc.coverage_report(cov, universe, max_age_runs=3)
    assert report["never_covered"] == []


def test_never_covered_symbol_is_flagged():
    universe = ["A.ETORO", "B.ETORO"]
    cov = _empty()
    report = sc.coverage_report(cov, universe, max_age_runs=3)
    assert report["never_covered"] == ["A.ETORO", "B.ETORO"]


def test_gate1_covered_symbol_counts_as_covered():
    universe = ["A.ETORO"]
    cov = _empty()
    cov = sc.start_new_run(cov)
    cov = sc.mark_gate1_covered(cov, "A.ETORO", run_id="run-1", run_index=1,
                               completed_at_utc="2026-01-01T00:00:00Z")
    report = sc.coverage_report(cov, universe, max_age_runs=3)
    assert report["never_covered"] == []
    assert cov["symbols"]["A.ETORO"]["covered_gate1"] is True


# ── sweep.py-Integration: run_per_symbol_sweep schreibt/liest den Ledger ─────────────────────────
_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _patch_common(monkeypatch, sweep_mod, tmp_path, pairs):
    monkeypatch.setattr(sweep_mod, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep_mod, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep_mod, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep_mod, "WORK", tmp_path)
    monkeypatch.setattr(sweep_mod, "config_dir", lambda: tmp_path)


def test_sweep_writes_coverage_ledger_after_each_symbol(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    _patch_common(monkeypatch, sweep, tmp_path, pairs)
    (tmp_path / "optimizer.json").write_text("{}", "utf-8")

    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1, run_id="run-coverage",
        optimize_symbol=lambda strategy, symbol, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )

    ledger = sc.load_coverage(path=tmp_path / "symbol_coverage.json")
    assert set(ledger["symbols"].keys()) == {"A.ETORO", "B.ETORO"}
    assert ledger["total_runs_started"] == 1
    for sym in ("A.ETORO", "B.ETORO"):
        assert ledger["symbols"][sym]["last_completed_run_id"] == "run-coverage"
        assert ledger["symbols"][sym]["n_completions"] == 1


def test_sweep_default_policy_reorders_by_coverage_recency(monkeypatch, tmp_path):
    """Mit 'least_recently_covered' (Default) wird ein bereits (frisch) abgedecktes Symbol NACH
    einem nie abgedeckten optimiert."""
    from automation.optimizer import sweep

    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    _patch_common(monkeypatch, sweep, tmp_path, pairs)
    (tmp_path / "optimizer.json").write_text("{}", "utf-8")

    # Vorab-Ledger: A.ETORO wurde bereits abgedeckt, B.ETORO nie.
    pre_existing = sc.record_symbol_completion(
        sc.load_coverage(path=tmp_path / "symbol_coverage.json"), "A.ETORO",
        run_id="old-run", run_index=1, completed_at_utc="2026-01-01T00:00:00Z")
    sc.write_coverage(pre_existing, path=tmp_path / "symbol_coverage.json")

    order_seen = []

    def _fake_optimize(strategy, symbol, **k):
        order_seen.append(symbol)
        return object()

    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1, run_id="run-2",
        optimize_symbol=_fake_optimize,
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert order_seen == ["B.ETORO", "A.ETORO"]  # nie abgedeckt zuerst


def test_sweep_universe_policy_preserves_insertion_order(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    _patch_common(monkeypatch, sweep, tmp_path, pairs)
    (tmp_path / "optimizer.json").write_text(json.dumps({"sweep_symbol_order_policy": "universe"}), "utf-8")

    pre_existing = sc.record_symbol_completion(
        sc.load_coverage(path=tmp_path / "symbol_coverage.json"), "A.ETORO",
        run_id="old-run", run_index=1, completed_at_utc="2026-01-01T00:00:00Z")
    sc.write_coverage(pre_existing, path=tmp_path / "symbol_coverage.json")

    order_seen = []
    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1, run_id="run-3",
        optimize_symbol=lambda strategy, symbol, **k: order_seen.append(symbol) or object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert order_seen == ["A.ETORO", "B.ETORO"]  # Einfuege-Reihenfolge, UNVERAENDERT
