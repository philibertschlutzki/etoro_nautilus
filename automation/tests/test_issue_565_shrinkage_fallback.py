"""Issue #564 — Anti-Overfit-Shrinkage & Warm-Start im Standalone-Sweep (global_best-Fallback).

Im Standalone-``sweep``-Pfad erzeugt kein Artefakt ``global_best`` ⇒ study.enqueue_trial(global_best)
wird übersprungen (kein Gate-2-Warm-Start) UND normalized_param_distance(sampled, {}, bounds) = 0
⇒ param_pen ≡ 0 ⇒ die A4.3-Shrinkage ist wirkungslos (der Vektor tunt ungezügelt Richtung
IS/OOS-CV-Rausch, überfittet, fällt am Holdout durch). Fix: (1) Fail-Loud (shrinkage_inactive), (2)
definierter Fallback auf strategy_defaults als Shrinkage-Referenz UND Warm-Start-Seed.

Akzeptanzkriterien (Issue #564):
- Bei leerem global_best im Per-Symbol-Sweep: Study-Attr shrinkage_inactive erscheint (einmal je Study).
- Mit Fallback auf Defaults: pstdev(param_pen | trials) > 0 (Shrinkage wirkt), Warm-Start-Seed als Trial 0.
"""
import json
import statistics
from pathlib import Path

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config, bounds

CFG_DIR = Path("automation/config")


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: CFG_DIR)
    monkeypatch.setattr(trial_config, "config_dir", lambda: CFG_DIR)


def _fake_factory(captured):
    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        captured.append(json.loads(Path(manifest_path).read_text("utf-8")))
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
            "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05}}}), "utf-8")
        return out
    return _fake


# ── strategy_defaults-Loader + Resolver-Präzedenz ────────────────────────────────────────────
def test_load_strategy_defaults_params_excludes_schema():
    p = ro.load_strategy_defaults_params("SmaCrossoverStrategy", CFG_DIR)
    assert p.get("sma_period") == 5
    assert all(not k.startswith("_") for k in p)


def test_resolver_prefers_global_best(monkeypatch):
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {"sma_period": 33})
    seed, source = ro.resolve_symbol_shrinkage_seed("SmaCrossoverStrategy", CFG_DIR)
    assert source == "global_best"
    assert seed == {"sma_period": 33}


def test_resolver_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})
    seed, source = ro.resolve_symbol_shrinkage_seed("SmaCrossoverStrategy", CFG_DIR)
    assert source == "strategy_defaults"
    assert seed.get("sma_period") == 5


def test_resolver_none_when_nothing_available(monkeypatch):
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(ro, "load_strategy_defaults_params", lambda *a, **k: {})
    seed, source = ro.resolve_symbol_shrinkage_seed("SmaCrossoverStrategy", CFG_DIR)
    assert source == "none"
    assert seed == {}


# ── param_pen wirkt mit Defaults-Referenz (pstdev > 0), war vorher ≡ 0 ────────────────────────
def test_param_pen_nonzero_and_varies_with_defaults_reference():
    b = bounds.extract_numeric_bounds("SmaCrossoverStrategy")
    defaults = ro.load_strategy_defaults_params("SmaCrossoverStrategy", CFG_DIR)
    # Verschiedene gesampelte Vektoren ⇒ unterschiedliche Distanz zur Default-Referenz.
    sampled_vectors = [
        {"sma_period": 10, "cooldown_bars": 5},
        {"sma_period": 40, "cooldown_bars": 20},
        {"sma_period": 55, "cooldown_bars": 34},
    ]
    pens = [bounds.normalized_param_distance(s, defaults, b) for s in sampled_vectors]
    assert all(p > 0.0 for p in pens), "param_pen darf mit Defaults-Referenz nie still 0 sein."
    assert statistics.pstdev(pens) > 0.0, "Shrinkage muss über die Trials streuen (Gradient)."
    # Kontrast: gegen einen LEEREN Referenzvektor ist die Distanz immer 0 (der Ausgangsdefekt).
    assert all(bounds.normalized_param_distance(s, {}, b) == 0.0 for s in sampled_vectors)


# ── optimize_symbol: Fail-Loud shrinkage_inactive + Warm-Start-Seed als Trial 0 ──────────────
def test_optimize_symbol_defaults_fallback_marks_shrinkage_inactive(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_factory([]))
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})  # kein echtes globales Optimum
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)
    assert study.user_attrs.get("shrinkage_inactive") is True
    assert study.user_attrs.get("shrinkage_seed_source") == "strategy_defaults"
    # Warm-Start-Seed (Defaults: sma_period=5) erscheint als Trial 0.
    assert study.trials[0].params.get("sma_period") == 5


def test_optimize_symbol_with_global_best_is_active(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_factory([]))
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {"sma_period": 33, "cooldown_bars": 7})
    study = ro.optimize_symbol("SmaCrossoverStrategy", "BBB.ETORO", n_trials=1)
    assert study.user_attrs.get("shrinkage_inactive") is False
    assert study.user_attrs.get("shrinkage_seed_source") == "global_best"
    assert study.trials[0].params.get("sma_period") == 33
