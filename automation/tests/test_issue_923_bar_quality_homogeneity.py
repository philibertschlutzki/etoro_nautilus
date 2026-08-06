"""Issue #923 — der #900-Bar-Qualitaets-Preflight berechnete frac_zero_true_range/atr_median_bps/
bar_coverage_ratio/median_delta_t_s bereits (sweep._load_symbol_bar_quality_sample), aber (a)
bar_coverage_ratio wurde NIE als Ablehnungskriterium ausgewertet (Gate 1 bestand ein Symbol mit
grosser Datenspanne, aber ueberwiegend Luecken im Bar-Raster), (b) die Werte erreichten
report._study_record nie (bar_seconds blieb am hart kodierten _contracts.BAR_SECONDS_DEFAULT
haengen), und (c) es gab keine Invariante gegen die beobachtete Faktor-11,3-Streuung von
n_periods innerhalb desselben Symbols.
"""
import json

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import sweep_diagnostics as sd
from automation.optimizer import sweep as sw


def _flat_bars(n: int, price: float = 100.0) -> tuple[list, list, list]:
    """Bars, die JEDE Degenerations-Kennzahl AUSSER bar_coverage_ratio bestehen (genug distinkte
    Closes, keine Nullranges, ATR oberhalb der Skalenschwelle) — isoliert den bar_coverage_ratio-
    Effekt von den bereits bestehenden #900-Kennzahlen."""
    highs = [price + 1.0 + (i % 5) * 0.1 for i in range(n)]
    lows = [price - 1.0 - (i % 5) * 0.1 for i in range(n)]
    closes = [price + (i % 20) * 0.3 for i in range(n)]
    return highs, lows, closes


def test_check_bar_quality_passes_on_healthy_coverage():
    highs, lows, closes = _flat_bars(50)
    result = sd.check_bar_quality(highs, lows, closes, bar_coverage_ratio=0.95)
    assert result["passed"] is True


def test_check_bar_quality_fails_on_low_bar_coverage_ratio():
    """Die #923-Root-Cause: eine grosse Datenspanne mit ueberwiegend Luecken im Bar-Raster bestand
    Gate 1 vorher unveraendert (1099 Tage Spanne, 30% Abdeckung)."""
    highs, lows, closes = _flat_bars(50)
    result = sd.check_bar_quality(highs, lows, closes, bar_coverage_ratio=0.30)
    assert result["passed"] is False
    assert "bar_coverage_ratio" in result["reason"]


def test_check_bar_quality_bar_coverage_none_is_not_checked():
    """Kein Preflight-Aufrufer liefert den Wert (z. B. Alt-Call-Site) ⇒ nicht pruefbar, kein Fail
    allein deswegen (rueckwaertskompatibel)."""
    highs, lows, closes = _flat_bars(50)
    result = sd.check_bar_quality(highs, lows, closes, bar_coverage_ratio=None)
    assert result["passed"] is True


def test_check_bar_quality_respects_configured_threshold():
    highs, lows, closes = _flat_bars(50)
    result = sd.check_bar_quality(highs, lows, closes, bar_coverage_ratio=0.55,
                                  min_bar_coverage_ratio=0.5)
    assert result["passed"] is True
    result2 = sd.check_bar_quality(highs, lows, closes, bar_coverage_ratio=0.45,
                                   min_bar_coverage_ratio=0.5)
    assert result2["passed"] is False


def test_check_n_periods_homogeneity_passes_within_ratio():
    records = [
        {"symbol": "XOM.ETORO", "strategy": "A", "oos_n_periods_median": 200},
        {"symbol": "XOM.ETORO", "strategy": "B", "oos_n_periods_median": 400},
    ]
    result = inv.check_n_periods_homogeneity(records)
    assert result.passed is True


def test_check_n_periods_homogeneity_fails_on_the_923_symptom():
    """Beobachtete Spannweite auf XOM: OpeningRangeBreakout Median 72, AdxAtrMomentum Median 816
    (Faktor 11,3)."""
    records = [
        {"symbol": "XOM.ETORO", "strategy": "OpeningRangeBreakout", "oos_n_periods_median": 72},
        {"symbol": "XOM.ETORO", "strategy": "AdxAtrMomentum", "oos_n_periods_median": 816},
    ]
    result = inv.check_n_periods_homogeneity(records)
    assert result.passed is False
    assert result.severity == "high"
    assert "XOM.ETORO" in result.actual


def test_check_n_periods_homogeneity_ignores_missing_data():
    records = [
        {"symbol": "XOM.ETORO", "strategy": "A", "oos_n_periods_median": None},
        {"symbol": "NKE.ETORO", "strategy": "B", "oos_n_periods_median": 300},
    ]
    result = inv.check_n_periods_homogeneity(records)
    assert result.passed is True


def test_symbol_bar_quality_cache_round_trip(tmp_path):
    payload = {"XOM.ETORO": {"median_delta_t_s": 3600.0, "bar_coverage_ratio": 0.92,
                             "atr_median_bps": 12.5, "frac_zero_true_range": 0.01, "passed": True}}
    path = sw.write_symbol_bar_quality_cache(tmp_path, payload)
    assert path.exists()
    loaded = sw.read_symbol_bar_quality_cache(tmp_path)
    assert loaded == payload


def test_symbol_bar_quality_cache_read_is_fail_open_when_missing(tmp_path):
    assert sw.read_symbol_bar_quality_cache(tmp_path) == {}


def test_symbol_bar_quality_cache_read_is_fail_open_on_garbage(tmp_path):
    (tmp_path / "symbol_bar_quality.json").write_text("not json", encoding="utf-8")
    assert sw.read_symbol_bar_quality_cache(tmp_path) == {}


def test_optimizer_json_carries_the_new_threshold():
    from automation.optimizer.trial_config import config_dir
    with open(config_dir() / "optimizer.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["bar_quality"]["min_bar_coverage_ratio"] == 0.6
