"""Issue #892 (P0) — Der Abdeckungs-Ledger überlebt den Lauf nicht, und ``mark_gate1_covered``
ist toter Code.

Drei unabhängige Befunde:

1. ``total_runs_started`` wurde im Speicher erhöht (``symbol_coverage.start_new_run``), aber erst
   beim ERSTEN abgeschlossenen Symbol auf die Platte geschrieben (``_record_coverage`` in
   ``run_per_symbol_sweep``) — ein Lauf, der vor dem ersten Symbolabschluss abbricht (0 Symbole),
   verliert den Zähler. Fix: sofortiges ``write_coverage`` nach ``start_new_run``.
2. ``symbol_coverage.mark_gate1_covered`` (Issue #841 Punkt 5) hatte NULL Call-Sites — ein Symbol,
   dessen Paare AUSSCHLIESSLICH an Gate 1 scheitern, blieb für ``check_symbol_coverage`` für immer
   "never_covered", obwohl es nachweislich JEDEN Lauf geprüft wird.
3. ``check_symbol_coverage`` warf ``never_covered`` (nie abgedeckt) und ``stale_symbols`` (abgedeckt,
   aber die Rotation seither übersprungen) in DIESELBE Konsequenz — bei ``len(universe)`` Symbolen
   braucht die erste Vollabdeckung mehrere Läufe (Bootstrap-Phase); bis dahin ist ``never_covered``
   der erwartete Zustand, kein Befund.
"""
import json

from automation.optimizer import symbol_coverage as sc
from automation.optimizer import invariants as inv

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _patch_common(monkeypatch, sweep_mod, tmp_path):
    monkeypatch.setattr(sweep_mod, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep_mod, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep_mod, "WORK", tmp_path)
    monkeypatch.setattr(sweep_mod, "config_dir", lambda: tmp_path)
    (tmp_path / "optimizer.json").write_text("{}", "utf-8")


# ── Fix Punkt 1: sofortige Persistenz von total_runs_started ────────────────────────────────────
def test_total_runs_started_survives_a_run_with_zero_completed_symbols(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: [])
    _patch_common(monkeypatch, sweep, tmp_path)

    sweep.run_per_symbol_sweep(
        ["S"], [], tier="all", n_jobs=1, run_id="run-empty",
        optimize_symbol=lambda strategy, symbol, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )

    ledger = sc.load_coverage(path=tmp_path / "symbol_coverage.json")
    assert ledger["total_runs_started"] == 1


# ── Fix Punkt 5: mark_gate1_covered wird jetzt aufgerufen ────────────────────────────────────────
def test_gate1_only_symbol_is_marked_covered_via_sweep(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    def _fake_enumerate(strategies, symbols, *, tier, available_bars, config, **kwargs):
        gate1_rejected = kwargs.get("gate1_rejected_symbols")
        if gate1_rejected is not None:
            # C.ETORO scheitert an Gate 1 fuer ALLE Strategien -> kein 'OK'-Paar unten.
            gate1_rejected.add("C.ETORO")
        return [("S", "A.ETORO", "OK")]

    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", _fake_enumerate)
    _patch_common(monkeypatch, sweep, tmp_path)

    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "C.ETORO"], tier="all", n_jobs=1, run_id="run-gate1",
        optimize_symbol=lambda strategy, symbol, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )

    ledger = sc.load_coverage(path=tmp_path / "symbol_coverage.json")
    assert ledger["symbols"]["C.ETORO"]["covered_gate1"] is True
    assert ledger["symbols"]["A.ETORO"].get("covered_gate1") is not True


def test_symbol_with_at_least_one_ok_pair_is_not_gate1_marked(monkeypatch, tmp_path):
    """Ein Symbol mit MINDESTENS einem 'OK'-Paar durchläuft den regulären _record_coverage-Pfad,
    auch wenn eine ANDERE Strategie für dasselbe Symbol an Gate 1 scheiterte."""
    from automation.optimizer import sweep

    def _fake_enumerate(strategies, symbols, *, tier, available_bars, config, **kwargs):
        gate1_rejected = kwargs.get("gate1_rejected_symbols")
        if gate1_rejected is not None:
            gate1_rejected.add("A.ETORO")
        return [("S2", "A.ETORO", "OK")]  # S1 scheiterte an Gate 1, S2 bestand

    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", _fake_enumerate)
    _patch_common(monkeypatch, sweep, tmp_path)

    sweep.run_per_symbol_sweep(
        ["S1", "S2"], ["A.ETORO"], tier="all", n_jobs=1, run_id="run-mixed",
        optimize_symbol=lambda strategy, symbol, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )

    ledger = sc.load_coverage(path=tmp_path / "symbol_coverage.json")
    assert ledger["symbols"]["A.ETORO"].get("covered_gate1") is not True
    assert ledger["symbols"]["A.ETORO"]["last_completed_run_id"] == "run-mixed"


# ── Fix Punkt 3/4: never_covered vs. stale_symbols, Bootstrap-Phase ──────────────────────────────
def test_coverage_report_flags_bootstrap_phase_on_first_run():
    universe = ["A.ETORO", "B.ETORO", "C.ETORO", "D.ETORO"]
    cov = sc.load_coverage(path="/nonexistent/does/not/exist.json")
    cov = sc.start_new_run(cov)
    # Nur A.ETORO in Lauf 1 abgedeckt (Budget reichte nicht fuer alle 4).
    cov = sc.record_symbol_completion(cov, "A.ETORO", run_id="run-1", run_index=1,
                                      completed_at_utc="2026-01-01T00:00:00Z")
    report = sc.coverage_report(cov, universe, max_age_runs=3)
    assert report["coverage_bootstrap_phase"] is True
    assert set(report["never_covered"]) == {"B.ETORO", "C.ETORO", "D.ETORO"}


def test_check_symbol_coverage_does_not_fail_never_covered_during_bootstrap():
    universe = ["A.ETORO", "B.ETORO", "C.ETORO", "D.ETORO"]
    cov = sc.load_coverage(path="/nonexistent/does/not/exist.json")
    cov = sc.start_new_run(cov)
    cov = sc.record_symbol_completion(cov, "A.ETORO", run_id="run-1", run_index=1,
                                      completed_at_utc="2026-01-01T00:00:00Z")
    result = inv.check_symbol_coverage(cov, universe, max_age_runs=3)
    assert result.passed is True


def test_check_symbol_coverage_still_fails_stale_symbols_during_bootstrap():
    """Ein bereits abgedecktes, aber seither uebersprungenes Symbol ist IMMER ein Befund —
    unabhaengig von der Bootstrap-Phase fuer die uebrigen, nie abgedeckten Symbole."""
    universe = ["A.ETORO", "B.ETORO", "C.ETORO", "D.ETORO"]
    cov = sc.load_coverage(path="/nonexistent/does/not/exist.json")
    for i in range(1, 6):
        cov["total_runs_started"] = i
        if i == 1:
            cov = sc.record_symbol_completion(cov, "A.ETORO", run_id=f"run-{i}", run_index=i,
                                              completed_at_utc="2026-01-01T00:00:00Z")
        cov["total_runs_started"] = i
    result = inv.check_symbol_coverage(cov, universe, max_age_runs=3)
    assert result.passed is False
    assert "A.ETORO" in (result.actual or {})


def test_never_covered_symbol_fails_outside_bootstrap_phase():
    """Nach genuegend Laeufen (Bootstrap-Phase vorbei) ist ein weiterhin nie abgedecktes Symbol
    ein echter Befund (analog dem urspruenglichen AK-2 aus #841, jetzt mit klarer Bootstrap-
    Abgrenzung)."""
    universe = ["A.ETORO", "B.ETORO"]
    cov = sc.load_coverage(path="/nonexistent/does/not/exist.json")
    cov["total_runs_started"] = 20
    cov = sc.record_symbol_completion(cov, "A.ETORO", run_id="run-20", run_index=20,
                                      completed_at_utc="2026-01-01T00:00:00Z")
    cov["total_runs_started"] = 20
    # B.ETORO wurde in 20 Laeufen nie abgedeckt -> estimated_symbols_per_run = 1/20 = 0.05,
    # bootstrap_runs_needed = ceil(2/0.05) = 40 > 20 -> eigentlich noch Bootstrap. Simuliere den
    # Nach-Bootstrap-Zustand direkt ueber viele Laeufe mit konstanter Abdeckung von nur A.
    cov["total_runs_started"] = 100
    result = inv.check_symbol_coverage(cov, universe, max_age_runs=3)
    assert result.passed is False
    assert "B.ETORO" in (result.actual or {})
    assert result.actual["B.ETORO"] == "never_covered"


# ── Fix Punkt 2: check_coverage_ledger_continuity ────────────────────────────────────────────────
def test_ledger_continuity_fails_when_reset_despite_prior_reports():
    result = inv.check_coverage_ledger_continuity(1, True)
    assert result.passed is False
    assert result.severity == "blocking"


def test_ledger_continuity_passes_on_the_true_first_run():
    result = inv.check_coverage_ledger_continuity(1, False)
    assert result.passed is True


def test_ledger_continuity_passes_once_the_counter_advanced():
    result = inv.check_coverage_ledger_continuity(2, True)
    assert result.passed is True
