"""Issue #1029/#1178 (P1) — Fill-Slippage ohne Vorzeichenkonvention und ohne Report-Ausweis.

Root-Cause: ``stop_exit_slippage_bps = (closing_price - trailing_stop_price) / trailing_stop_price
* 10000`` war NICHT seitenbereinigt. Fuer eine LONG-Position (schliessender SELL) ist ein Fill UNTER
dem Stop advers; fuer eine SHORT-Position (schliessender BUY) ist ein Fill UEBER dem Stop advers —
dieselbe Rohformel liefert fuer denselben oekonomischen Sachverhalt je nach Seite ENTGEGENGESETZTE
Vorzeichen, sodass LONG- und SHORT-Round-Trips im selben Median vermischt wurden.

Fix: ``backtest_runner.resolve_stop_exit_slippage_bps`` ist seitenbereinigt und liefert eine ADVERSE
Groesse (``+`` = advers). Ausserdem: die Groesse erscheint jetzt in Report-Abschnitt 2.4 (neben
c_rt) und in der neuen Invariante ``check_stop_exit_slippage_materiality``.
"""
import pytest

from automation.backtest_runner import resolve_stop_exit_slippage_bps
from automation.optimizer import invariants as inv
from automation.optimizer import summary_de


def test_long_close_below_stop_is_adverse_positive():
    # LONG-Position, schliessender SELL bei 99 gegen einen Stop von 100 -> schlechter gefuellt.
    result = resolve_stop_exit_slippage_bps(99.0, 100.0, is_short_close=False)
    assert result == pytest.approx(100.0)  # (100-99)/100*10000 = 100 bps advers


def test_long_close_above_stop_is_favorable_negative():
    # LONG-Position, schliessender SELL bei 101 gegen einen Stop von 100 -> guenstiger gefuellt.
    result = resolve_stop_exit_slippage_bps(101.0, 100.0, is_short_close=False)
    assert result == pytest.approx(-100.0)


def test_short_close_above_stop_is_adverse_positive():
    # SHORT-Position, schliessender BUY bei 101 gegen einen Stop von 100 -> schlechter (teurer)
    # gefuellt.
    result = resolve_stop_exit_slippage_bps(101.0, 100.0, is_short_close=True)
    assert result == pytest.approx(100.0)


def test_short_close_below_stop_is_favorable_negative():
    # SHORT-Position, schliessender BUY bei 99 gegen einen Stop von 100 -> guenstiger (billiger)
    # gefuellt.
    result = resolve_stop_exit_slippage_bps(99.0, 100.0, is_short_close=True)
    assert result == pytest.approx(-100.0)


def test_same_raw_fill_gap_yields_opposite_sign_for_opposite_sides():
    """Der Kernbeweis der Regression: derselbe rohe Fill-vs-Stop-Abstand (101 gegen 100) ist fuer
    eine LONG-Position guenstig, fuer eine SHORT-Position advers -- entgegengesetztes Vorzeichen
    fuer denselben Rohwert, korrekt seitenbereinigt."""
    long_result = resolve_stop_exit_slippage_bps(101.0, 100.0, is_short_close=False)
    short_result = resolve_stop_exit_slippage_bps(101.0, 100.0, is_short_close=True)
    assert long_result == -short_result


def test_none_when_inputs_missing():
    assert resolve_stop_exit_slippage_bps(None, 100.0, is_short_close=False) is None
    assert resolve_stop_exit_slippage_bps(100.0, None, is_short_close=False) is None
    assert resolve_stop_exit_slippage_bps(100.0, 0.0, is_short_close=False) is None


# ── check_stop_exit_slippage_materiality ────────────────────────────────────────────────────────
def test_materiality_check_fails_on_the_reference_symptom():
    records = [
        {"strategy": "A", "symbol": "X.ETORO", "stop_exit_slippage_bps": -12.408,
         "gross_loss_median_bps_trailing_stop": 16.33},
    ]
    result = inv.check_stop_exit_slippage_materiality(records)
    assert result.passed is False
    assert result.severity == "high"


def test_materiality_check_passes_when_slippage_is_small_relative_to_the_loss():
    records = [
        {"strategy": "A", "symbol": "X.ETORO", "stop_exit_slippage_bps": -1.0,
         "gross_loss_median_bps_trailing_stop": 100.0},
    ]
    result = inv.check_stop_exit_slippage_materiality(records)
    assert result.passed is True


def test_materiality_check_not_applicable_without_data():
    result = inv.check_stop_exit_slippage_materiality([{"strategy": "A", "symbol": "X"}])
    assert result.passed is True
    assert result.actual is None


# ── summary_de.py Abschnitt 2.4 ──────────────────────────────────────────────────────────────────
def test_summary_section_2_4_lists_slippage_next_to_round_trip_cost():
    report = {
        "studies": [
            {"strategy": "A", "symbol": "X.ETORO", "stop_exit_slippage_bps": -12.41,
             "round_trip_cost_bps": 4.0},
        ],
        "cross_study": {},
    }
    text = summary_de._section_2_monetary_result(report)
    assert "Slippage" in text
    assert "-12.41" in text or "-12,41" in text
