"""Issue #622 — n_trials=100 bei 14 Dimensionen ist faktisch Zufallssuche; Randlösungen fail-loud.

Fix: n_trials_per_dim (k >= 20) koppelt das Budget an die Dimensionalität; boundary_hit_fraction > 0.3
blockiert READY_FOR_PR (statt einer folgenlosen WARNING).
"""
import json
from pathlib import Path

from automation.optimizer.run_optimization import derive_n_trials


def test_derive_n_trials_scales_with_dim(monkeypatch):
    import automation.optimizer.bounds as bounds
    monkeypatch.setattr(bounds, "extract_numeric_bounds", lambda s: {f"p{i}": (0, 1) for i in range(14)})
    # k=20, dim=14 ⇒ 280 > base 100.
    assert derive_n_trials("ComboTrendVwapStrategy", 100, {"n_trials_per_dim": 20}) == 280


def test_derive_n_trials_never_below_base(monkeypatch):
    import automation.optimizer.bounds as bounds
    monkeypatch.setattr(bounds, "extract_numeric_bounds", lambda s: {"a": (0, 1), "b": (0, 1)})
    # k=20, dim=2 ⇒ 40 < base 100 ⇒ base bleibt.
    assert derive_n_trials("SmaCrossoverStrategy", 100, {"n_trials_per_dim": 20}) == 100


def test_derive_n_trials_legacy_without_key():
    assert derive_n_trials("X", 100, {}) == 100
    assert derive_n_trials("X", 100, {"n_trials_per_dim": 0}) == 100


def test_shipped_config_has_n_trials_per_dim():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["n_trials_per_dim"] >= 20
    assert "n_trials_per_dim" in cfg["_schema"]["fields"]
