"""Issue #412 / #415 — Doppelte `(strategy, symbol)`-Paare kollabieren Worker auf eine Study.

`load_symbol_universe` (Listen-Quelle) und `enumerate_tunable_pairs` deduplizieren jetzt
order-preserving; `run_per_symbol_sweep` bricht fail-fast ab, falls trotzdem zwei Paare
denselben `study_name` ergaeben. Eindeutigkeit ist eine harte Vorbedingung der Per-Study-
Parallelitaet (Pitfall #76/#77), kein Implementierungsdetail.

Voll gemockt (HI-7): kein echter Backtest, kein echtes optimize_symbol/confirm.
"""
import json

import pytest

from automation.optimizer import sweep


_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _write_universe(tmp_path, universe):
    base_cfg = tmp_path / "automation" / "config"
    base_cfg.mkdir(parents=True, exist_ok=True)
    uni_dir = tmp_path / "data" / "universe"
    uni_dir.mkdir(parents=True, exist_ok=True)
    (uni_dir / "momentum_ls.json").write_text(json.dumps({"universe": universe}), "utf-8")
    return base_cfg


def test_universe_list_with_duplicates_is_deduped(tmp_path):
    """Listen-Universe ["A","B","A"] ⇒ ["A","B"] (order-preserving)."""
    base_cfg = _write_universe(tmp_path, ["A.ETORO", "B.ETORO", "A.ETORO"])
    out = sweep.load_symbol_universe(base_cfg)
    assert out == ["A.ETORO", "B.ETORO"]


def test_universe_dict_source_unchanged(tmp_path):
    """Dict-Quelle: Keys sind bereits eindeutig — Verhalten unveraendert."""
    base_cfg = _write_universe(tmp_path, {"A.ETORO": {}, "B.ETORO": {}})
    out = sweep.load_symbol_universe(base_cfg)
    assert out == ["A.ETORO", "B.ETORO"]


def test_enumerate_pairs_are_unique(monkeypatch):
    """Symbole mit Duplikat + alle tunable ⇒ keine doppelten (strategy, symbol)."""
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "is_symbol_tunable", lambda *a, **k: (True, "OK"))
    bars = {"A.ETORO": 10_000, "B.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["SmaCrossoverStrategy"], ["A.ETORO", "B.ETORO", "A.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("SmaCrossoverStrategy", "A.ETORO", "OK"),
                     ("SmaCrossoverStrategy", "B.ETORO", "OK")]


def test_order_preserved(monkeypatch):
    """Dedup aendert die Erst-Vorkommens-Reihenfolge nicht (deterministische Proposal-Reihenfolge)."""
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "is_symbol_tunable", lambda *a, **k: (True, "OK"))
    bars = {s: 10_000 for s in ["C.ETORO", "A.ETORO", "B.ETORO"]}
    pairs = sweep.enumerate_tunable_pairs(
        ["S"], ["C.ETORO", "A.ETORO", "B.ETORO", "C.ETORO", "A.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert [sym for _, sym, _ in pairs] == ["C.ETORO", "A.ETORO", "B.ETORO"]


def test_run_per_symbol_sweep_rejects_duplicate_study_names(monkeypatch, tmp_path):
    """Direkt injizierte Paarliste mit Duplikat ⇒ ValueError (Fail-Fast, Pitfall #66)."""
    dupe_pairs = [("S", "WDAY.ETORO", "OK"), ("S", "WDAY.ETORO", "OK")]
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: dupe_pairs)
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)

    with pytest.raises(ValueError, match="Doppelte Study-Namen"):
        sweep.run_per_symbol_sweep(
            ["S"], ["WDAY.ETORO"], tier="all", n_jobs=2,
            optimize_symbol=lambda *a, **k: object(),
            confirm=lambda *a, **k: {},
        )


def test_run_per_symbol_sweep_accepts_unique_study_names(monkeypatch, tmp_path):
    """Eindeutige Paare passieren die Assertion und werden dispatcht."""
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    # Issue #799 — der Sweep-Fortschritts-Checkpoint schreibt nach WORK; isoliert halten.
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})

    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=lambda *a, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert out == [tmp_path / "proposal_A.ETORO.json", tmp_path / "proposal_B.ETORO.json"]
