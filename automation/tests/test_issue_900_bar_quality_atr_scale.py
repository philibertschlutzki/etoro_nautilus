"""Issue #900 (P2) — Der Bar-Qualitäts-Preflight toleriert bis zu 50% degenerierte Bars und prüft
die ATR-Skala nicht.

Fix: `max_frac_high_eq_low` verschärft 0.5 → 0.20; neue Kennzahlen `frac_zero_true_range` (Anteil
Bars mit True Range == 0 über die GESAMTE Kohorte, nicht nur high==low) und `atr_median_bps`
(absoluter Skalen-Check: eine ATR, die systematisch bei 2 bps statt 30 bps liegt, passiert die
relativen Degenerations-Kennzahlen vollständig).
"""
from automation.optimizer.sweep_diagnostics import check_bar_quality


def _flat_price_bars(n=100, price=100.0, tr_bps=40.0):
    """Bars mit konstanter Schwankungsbreite (Zufalls-Vorzeichen), keine Nullranges."""
    import random
    rng = random.Random(900)
    closes = [price]
    highs = []
    lows = []
    half = (tr_bps / 10_000.0) / 2.0
    for _ in range(n):
        c = closes[-1] * (1.0 + rng.choice([-1, 1]) * half * 0.1)
        closes.append(c)
        highs.append(c * (1.0 + half))
        lows.append(c * (1.0 - half))
    return highs, lows, closes[1:]


def test_synthetic_symbol_with_30pct_zero_range_bars_is_rejected():
    """Issue #900 Akzeptanzkriterium — ein synthetisches Symbol mit 30% Nullrange-Bars wird
    abgewiesen."""
    n = 100
    closes = [100.0 + i * 0.05 for i in range(n)]
    highs = []
    lows = []
    for i, c in enumerate(closes):
        if i % 10 < 3:  # 30% Nullrange: high==low==prev close (kein Move ueberhaupt)
            highs.append(closes[i - 1] if i > 0 else c)
            lows.append(closes[i - 1] if i > 0 else c)
        else:
            highs.append(c * 1.004)
            lows.append(c * 0.996)
    # Erzwinge exakt 30% Nullranges: die obigen "Nullrange"-Bars muessen high==low==prev_close sein.
    closes2 = list(closes)
    for i in range(n):
        if i % 10 < 3 and i > 0:
            closes2[i] = closes2[i - 1]
            highs[i] = closes2[i]
            lows[i] = closes2[i]

    result = check_bar_quality(highs, lows, closes2)
    assert result["passed"] is False
    assert result["frac_zero_true_range"] == 0.3
    assert "frac_zero_true_range" in result["reason"]


def test_synthetic_symbol_with_15pct_zero_range_bars_passes():
    n = 100
    closes = [100.0 + i * 0.05 for i in range(n)]
    highs = list(closes)
    lows = list(closes)
    for i, c in enumerate(closes):
        if i % 100 >= 15:
            highs[i] = c * 1.004
            lows[i] = c * 0.996
    # Nullrange-Bars (erste 15) behalten high==low==close, aber der Close selbst variiert
    # (nicht identisch zum Vorgaenger), damit weder frac_identical noch n_distinct triggert.
    result = check_bar_quality(highs, lows, closes)
    assert result["frac_zero_true_range"] <= 0.16
    assert result["passed"] is True


def test_atr_median_bps_scale_check_flags_systematically_tiny_atr():
    """Eine ATR, die systematisch bei 2 bps statt 30 bps liegt, muss den Preflight nicht mehr
    passieren (min_atr_median_bps, Default 5.0)."""
    highs, lows, closes = _flat_price_bars(n=100, tr_bps=2.0)
    result = check_bar_quality(highs, lows, closes)
    assert result["atr_median_bps"] < 5.0
    assert result["passed"] is False
    assert "atr_median_bps" in result["reason"]


def test_atr_median_bps_scale_check_passes_healthy_volatility():
    highs, lows, closes = _flat_price_bars(n=100, tr_bps=40.0)
    result = check_bar_quality(highs, lows, closes)
    assert result["atr_median_bps"] >= 5.0
    assert result["passed"] is True


def test_bar_coverage_and_median_delta_pass_through():
    """bar_coverage_ratio/median_delta_t_s sind reine Pass-Through-Werte im Ergebnis-Dict (Issue
    #900 Fix 1/#902 — Rohmaterial fuer bar_seconds an anderer Stelle)."""
    highs, lows, closes = _flat_price_bars(n=50, tr_bps=40.0)
    result = check_bar_quality(highs, lows, closes, bar_coverage_ratio=0.95, median_delta_t_s=3600.0)
    assert result["bar_coverage_ratio"] == 0.95
    assert result["median_delta_t_s"] == 3600.0


def test_high_eq_low_threshold_tightened_to_20pct():
    """Issue #900 Fix 2 — max_frac_high_eq_low 0.5 -> 0.20 (Default)."""
    n = 100
    closes = [100.0 + i * 0.05 for i in range(n)]
    highs = list(closes)
    lows = list(closes)
    for i in range(n):
        if i % 10 >= 3:  # 70% Bars mit Bewegung -> 30% high==low (ueber der neuen 0.20-Schwelle)
            highs[i] = closes[i] * 1.004
            lows[i] = closes[i] * 0.996
    result = check_bar_quality(highs, lows, closes)
    assert result["frac_high_eq_low"] == 0.3
    assert result["passed"] is False
    assert "frac_high_eq_low" in result["reason"]
