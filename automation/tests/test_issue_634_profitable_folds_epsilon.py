"""Issue #634 — `oos_min_profitable_folds ≥ 0,5` auf degenerierten, hoch-varianten Folds.

208/285 Rejections zitierten `oos_min_profitable_folds`. Der Zähler nutzte ein striktes `> 0.0` auf
roh-varianten Fold-Returns (T≈50/Fold): ein Fold mit Return +1e-6 zählte als "profitabel", einer mit
−1e-6 nicht — der Zähler kippte an Rausch-Nullen. Die konfigurierte Fold-Winsorisierung war zudem im
Zähler ungenutzt.

Fix: (1) `fold_profit_epsilon`-Schwelle statt striktem `> 0.0`, (2) Zählung auf der winsorisierten
Fold-Return-Sequenz (identisch zu `oos_fold_returns`).
"""
import json
from pathlib import Path

import pytest

import automation.backtest_runner as br

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def _reset_caches():
    br._fold_profit_epsilon_cache = None
    br._fold_winsorize_cache = None


def _fold(total_return):
    return {"total_return": total_return}


def test_production_config_declares_epsilon():
    assert TCFG["fold_profit_epsilon"] == pytest.approx(0.0005)


def test_counter_invariant_against_noise_zeros(monkeypatch, tmp_path):
    """Akzeptanzkriterium (#634): Test mit Folds [+1e-7, −1e-7, +0.02, −0.02] ⇒ stabiler Zähler
    (beide Rausch-Nullen werden identisch behandelt, unabhängig vom Vorzeichen)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "tournament.json").write_text(json.dumps({
        "fold_profit_epsilon": 0.0005, "fold_winsorize_lower": None, "fold_winsorize_upper": None,
    }), "utf-8")
    monkeypatch.setattr(br, "config_dir", lambda: cfg_dir)
    _reset_caches()

    per_fold = [_fold(1e-7), _fold(-1e-7), _fold(0.02), _fold(-0.02)]
    oos_metrics = {}
    br.apply_fold_aggregation(oos_metrics, per_fold)

    assert oos_metrics["oos_profitable_folds"] == 1  # nur +0.02 uebersteigt die epsilon-Schwelle
    assert oos_metrics["oos_folds_total"] == 4
    _reset_caches()


def test_legacy_strict_positive_without_epsilon_key(monkeypatch, tmp_path):
    """Fehlt fold_profit_epsilon ⇒ striktes > 0.0 (Legacy, bit-identisch, Zero-Hardcoding)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "tournament.json").write_text(json.dumps({}), "utf-8")
    monkeypatch.setattr(br, "config_dir", lambda: cfg_dir)
    _reset_caches()

    per_fold = [_fold(1e-9), _fold(-1e-9)]
    oos_metrics = {}
    br.apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["oos_profitable_folds"] == 1  # 1e-9 > 0.0 (Legacy striktes Verhalten)
    _reset_caches()


def test_winsorization_applied_to_counter(monkeypatch, tmp_path):
    """Akzeptanzkriterium (#634): die konfigurierte Winsorisierung ist im Zähler wirksam (vorher
    deklariert, aber ungenutzt)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "tournament.json").write_text(json.dumps({
        "fold_profit_epsilon": 0.0, "fold_winsorize_lower": 0.2, "fold_winsorize_upper": 0.8,
    }), "utf-8")
    monkeypatch.setattr(br, "config_dir", lambda: cfg_dir)
    _reset_caches()

    # 5 Folds, sortiert [-0.01, -0.005, 0.001, 0.002, 0.5]: bei [0.2, 0.8]-Winsorisierung (Indizes
    # round(0.2*4)=1 ⇒ -0.005, round(0.8*4)=3 ⇒ 0.002) wird JEDER Wert auf [-0.005, 0.002] geklemmt —
    # der Ausreisser +0.5 landet bei 0.002 (bleibt > 0 ⇒ weiterhin profitabel), −0.01 bei −0.005
    # (bleibt <= 0). Die geklemmte Sequenz beweist, dass die Winsorisierung im Zähler wirksam ist.
    per_fold = [_fold(-0.01), _fold(-0.005), _fold(0.001), _fold(0.002), _fold(0.5)]
    oos_metrics = {}
    br.apply_fold_aggregation(oos_metrics, per_fold)

    assert max(oos_metrics["oos_fold_returns"]) < 0.5  # der Ausreisser wurde geklemmt
    assert oos_metrics["oos_fold_returns"] == pytest.approx([-0.005, -0.005, 0.001, 0.002, 0.002])
    assert oos_metrics["oos_profitable_folds"] == 3  # 0.001, 0.002, 0.002 (geklemmter Ausreisser)
    _reset_caches()


def test_real_backtest_runner_uses_production_config(monkeypatch):
    """Ohne Monkeypatch liest apply_fold_aggregation die ausgelieferte tournament.json (0.0005)."""
    _reset_caches()
    per_fold = [_fold(0.0003), _fold(-0.0002), _fold(0.001)]
    oos_metrics = {}
    br.apply_fold_aggregation(oos_metrics, per_fold)
    # 0.0003 liegt UNTER der 0.0005-Schwelle ⇒ nicht profitabel; nur 0.001 zaehlt.
    assert oos_metrics["oos_profitable_folds"] == 1
    _reset_caches()
