"""Issue #1024/#1173 (P0) — Bedingung 2 des blockierenden ``check_trailing_stop_loss_share`` teilte
Median durch Mittelwert.

Root-Cause: der #972/#1126-Fix stellte den ZÄHLER (``gross_loss_median_bps_trailing_stop``) auf den
robusten Median um, liess den NENNER (``oos_gross_loss_mean_bps``, ein ungeschuetztes Mittel ALLER
Verlust-Trades) aber unveraendert — zwei verschiedene Momente in einem Quotienten, dessen Schwelle
(1.25) gegen eine reine mean/mean-Messung kalibriert war. Fix: ein neues, robustes Nennerfeld
``gross_loss_median_bps`` (Median ALLER Verlust-Round-Trips); Zaehler und Nenner tragen seither
dieselbe Statistik. Die Schwelle (umbenannt zu ``trailing_stop_max_median_loss_ratio``) bleibt
bewusst UNVERAENDERT (Pitfall #423: der alte Zahlenwert, nicht neu kalibriert), bis ein Folgelauf
sie auf der neuen Skala verifiziert.
"""
import json
import statistics
from pathlib import Path

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


def test_config_key_renamed_and_consistent_with_implementation_default():
    optimizer_cfg = json.loads(
        (Path(rpt.__file__).resolve().parent.parent / "config" / "optimizer.json").read_text(
            "utf-8"))
    assert "trailing_stop_max_median_loss_ratio" in optimizer_cfg
    assert "trailing_stop_max_mean_loss_ratio" not in optimizer_cfg
    assert "trailing_stop_max_median_loss_ratio" in optimizer_cfg["_schema"]["fields"]
    assert "trailing_stop_max_mean_loss_ratio" not in optimizer_cfg["_schema"]["fields"]
    # Der historische, gegen mean/mean kalibrierte Zahlenwert bleibt unveraendert (Pitfall #423 —
    # eine stille Neukalibrierung waere eine Aufweichung, keine Korrektur).
    assert optimizer_cfg["trailing_stop_max_median_loss_ratio"] == 1.25


def test_numerator_and_denominator_are_the_same_statistic_on_a_right_skewed_distribution():
    """Auf einer rechtsschiefen Verlustverteilung (ein paar extreme Ausreisser) weichen Median und
    Mittelwert deutlich voneinander ab — die vorherige median/mean-Messung war deshalb KEINE
    kommensurable Kalibrierungsgrundlage. Diese Study speist BEIDE Seiten aus der median-basierten
    Aggregation (``gross_loss_median_bps_trailing_stop``/``gross_loss_median_bps``, dieselbe
    Statistik) — der berichtete ``median_loss_ratio`` ist median/median, nicht median/mean."""
    all_losses = [10.0, 12.0, 11.0, 9.0, 13.0, 10.5, 500.0]  # ein extremer Ausreisser
    ts_losses = [10.0, 12.0, 11.0, 9.0, 13.0, 10.5, 500.0]
    median_all = statistics.median(all_losses)
    mean_all = statistics.mean(all_losses)
    assert median_all != mean_all  # rechtsschief: die beiden Statistiken divergieren deutlich

    r = {
        "strategy": "A", "symbol": "B.ETORO",
        "exit_reason_histogram": {"TRAILING_STOP": len(ts_losses)},
        "oos_n_trailing_stop_losses": len(ts_losses),
        "gross_loss_median_bps_trailing_stop": statistics.median(ts_losses),
        "gross_loss_median_bps": median_all,
    }
    result = inv.check_trailing_stop_loss_share([r])
    expected_ratio = round(statistics.median(ts_losses) / median_all, 4)
    assert result.actual is None or "median_loss_ratio" not in (result.actual.get("A/B.ETORO") or {}) or (
        result.actual["A/B.ETORO"]["median_loss_ratio"] == expected_ratio)
    # Die veraltete mean-basierte Ratio waere numerisch verschieden (Beweis, dass eine
    # median/mean-Messung keine gueltige Kommensurabilitaets-Kalibrierung fuer diesen Check ist).
    mean_based_ratio = round(statistics.median(ts_losses) / mean_all, 4)
    assert mean_based_ratio != expected_ratio


def test_expected_text_names_the_median_median_formula():
    result = inv.check_trailing_stop_loss_share([])
    assert "median_loss_trailing_stop/median_loss_all" in result.expected
    assert "mean_loss" not in result.expected


def test_loss_metric_commensurability_fails_when_median_denominator_structurally_missing():
    # Zaehler vorhanden, Nenner fehlt, UND es gibt nachweislich Verlust-Trades ausserhalb der
    # Trailing-Stop-Teilmenge (oos_n_losses > oos_n_trailing_stop_losses) -> die #1024-
    # Fehlerklasse selbst waere hier reproduziert, wenn sie erneut auftraete.
    r = {
        "strategy": "A", "symbol": "B.ETORO",
        "gross_loss_median_bps_trailing_stop": 50.0,
        "gross_loss_median_bps": None,
        "oos_n_losses": 100, "oos_n_trailing_stop_losses": 40,
        "oos_gross_loss_mean_bps_pooled": None,
        "oos_gross_loss_mean_bps_trailing_stop_pooled": None,
    }
    result = inv.check_loss_metric_commensurability([r])
    assert result.passed is False
    assert result.actual["A/B.ETORO"]["median_loss_denominator_missing"] is True


def test_loss_metric_commensurability_passes_when_median_denominator_present():
    r = {
        "strategy": "A", "symbol": "B.ETORO",
        "gross_loss_median_bps_trailing_stop": 50.0,
        "gross_loss_median_bps": 45.0,
        "oos_n_losses": 100, "oos_n_trailing_stop_losses": 40,
        "oos_gross_loss_mean_bps_pooled": None,
        "oos_gross_loss_mean_bps_trailing_stop_pooled": None,
    }
    result = inv.check_loss_metric_commensurability([r])
    assert result.passed is True
