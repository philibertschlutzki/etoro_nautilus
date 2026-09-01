"""Issue #1339 (GH #1233) — ``intrabar_range_median_bps`` misst die tatsächliche Intrabar-Spanne
(ohne Vorgänger-Close), getrennt von der Close-zu-Close-``atr_median_bps``, die eine
Achsen-Degeneration (``high == low``) nicht anzeigen kann (Pitfall #477).
"""
from automation.optimizer.sweep_diagnostics import check_bar_quality


def test_high_eq_low_with_varying_closes_yields_healthy_atr_but_zero_intrabar_range():
    """Dokumentiert die Fehlerklasse: eine Bar-Serie mit high==low und wechselnden Closes liefert
    frac_zero_true_range ~= 0 UND intrabar_range_median_bps == 0 — die TR bezieht ihre gesamte
    Groesse aus dem Vorgaenger-Close."""
    closes = [100.0 + i * 0.5 for i in range(30)]
    highs = list(closes)
    lows = list(closes)  # high == low fuer jede Bar (Ein-Tick-Achse).

    result = check_bar_quality(highs, lows, closes, min_distinct_closes=5)

    assert result["frac_high_eq_low"] == 1.0
    assert result["frac_zero_true_range"] < 0.1  # TR > 0 dank Vorgaenger-Close-Spruengen
    assert result["intrabar_range_median_bps"] == 0.0
    assert result["atr_median_bps"] is not None and result["atr_median_bps"] > 0


def test_real_intrabar_range_is_positive_after_ohlc_expansion():
    closes = [100.0 + i * 0.3 for i in range(30)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]

    result = check_bar_quality(highs, lows, closes, min_distinct_closes=5)
    assert result["intrabar_range_median_bps"] > 0.0


def test_min_intrabar_range_threshold_default_is_noop():
    """Sperrvermerk (#1246): min_intrabar_range_median_bps bleibt 0.0 bis zum Messlauf nach #1330
    (#1342/GH #1236) — ein Wert von 0.0 (degenerierte Achse) darf NICHT allein deswegen failen."""
    closes = [100.0] * 30
    result = check_bar_quality(closes, closes, closes, min_distinct_closes=1,
                               max_frac_high_eq_low=1.0,
                               max_frac_identical_consecutive_closes=1.0,
                               max_frac_zero_true_range=1.0, min_atr_median_bps=0.0)
    assert result["intrabar_range_median_bps"] == 0.0
    assert "intrabar_range_median_bps" not in (result.get("reason") or "")


def test_explicit_nonzero_threshold_fails_degenerate_axis():
    closes = [100.0] * 30
    result = check_bar_quality(closes, closes, closes, min_distinct_closes=1,
                               max_frac_high_eq_low=1.0,
                               max_frac_identical_consecutive_closes=1.0,
                               max_frac_zero_true_range=1.0, min_atr_median_bps=0.0,
                               min_intrabar_range_median_bps=1.0)
    assert result["passed"] is False
    assert "intrabar_range_median_bps" in result["reason"]


def test_intrabar_range_median_bps_appears_in_empty_input_result():
    result = check_bar_quality([], [], [])
    assert "intrabar_range_median_bps" in result
    assert result["intrabar_range_median_bps"] is None


def test_min_atr_median_bps_docstring_names_close_to_close():
    assert "CLOSE-ZU-CLOSE" in check_bar_quality.__doc__.upper() or \
           "CLOSE-ZU-CLOSE" in check_bar_quality.__doc__
