"""Issue #1056/#1205 (P1, Katalog #1196-1221) — Der Kalibrierungs-Check rechnet Spearman über
13-14 Study-Mediane gegen eine feste 0,30-Schwelle.

Symptom: ``check_trailing_stop_risk_calibration_acceptance`` forderte rho >= 0,30 aus n=13-14
Study-Medianen. SE(rho) ~ 1/sqrt(n-1) ~ 0,289 -- die Schwelle lag nur 1,04 SE von null entfernt.
Beobachtete Spannweite ueber 13 Laeufe: -0,7363 bis +0,2308 (Delta = 0,967 ~ 3,3 SE) auf identischem
Code. Zusaetzlich ist die Aggregationsebene falsch: ueber Studies gemittelt ist rho durch die
Strategie-Komposition konfundiert (Haltedauer, Session-Verankerung, ATR-Floor-Bindung), nicht durch
die Stop-Kalibrierung.

Fix:
1. ``report._within_study_stop_calibration_spearman`` — rho INNERHALB jeder Study ueber deren
   Trials (Paare aus je-Trial gesampeltem ``atr_trailing_multiplier`` und gemessener
   ``oos_stop_distance_bps_median`` aus #1054/#1203), gespeichert als
   ``stop_calibration_spearman_within_study``/``stop_calibration_n_pairs_within_study`` in
   ``report._study_record``.
2. ``invariants.check_trailing_stop_risk_calibration_acceptance`` aggregiert die je-Study-Werte
   n-gewichtet ueber alle Studies (``combined_spearman = sum(rho_i*n_i) / sum(n_i)``).
3. ``passed=None`` (nicht ``False``) wenn die gepoolte Paarzahl < 8 -- konsistent mit dem
   #1147-Muster (Pitfall #413).
4. Schwelle n-abhaengig: ``rho >= max(0.30, 2*SE(n))`` mit ``SE(n) = 1/sqrt(n-1)``; Konfidenz-
   intervall im Detailtext.
5. Ist rho nicht berechenbar, wird der Grund explizit gestempelt
   (``SPEARMAN_UNAVAILABLE_INSUFFICIENT_PAIRS``), statt das Feld wegzulassen.

Akzeptanzkriterien: Detailtext enthaelt n, rho, CI und Schwelle; kein FAIL ohne berichtetes rho
oder expliziten Unavailability-Grund; ``passed=None`` bei n < 8.
"""
import math
import sys
import types
import unittest.mock as mock

import pytest

if "nautilus_trader" not in sys.modules:
    class _MockModule(types.ModuleType):
        def __getattr__(self, name):
            return mock.MagicMock()

    for _mod in (
        "nautilus_trader", "nautilus_trader.backtest", "nautilus_trader.backtest.engine",
        "nautilus_trader.backtest.models", "nautilus_trader.model", "nautilus_trader.model.data",
        "nautilus_trader.model.enums", "nautilus_trader.model.identifiers",
        "nautilus_trader.model.currencies", "nautilus_trader.model.objects",
        "nautilus_trader.model.instruments", "nautilus_trader.config", "nautilus_trader.common",
        "nautilus_trader.common.enums", "nautilus_trader.common.actor", "nautilus_trader.core",
        "nautilus_trader.core.message", "nautilus_trader.portfolio", "nautilus_trader.test_engine",
        "nautilus_trader.persistence", "nautilus_trader.persistence.catalog",
        "nautilus_trader.execution", "nautilus_trader.execution.messages",
    ):
        sys.modules[_mod] = _MockModule(_mod)

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


# --- report._within_study_stop_calibration_pairs / _within_study_stop_calibration_spearman -------

def _trial_attrs(*pairs):
    """pairs: Liste von (atr_trailing_multiplier, oos_stop_distance_bps_median)."""
    return [
        {"sampled_params": {"atr_trailing_multiplier": k}, "oos_stop_distance_bps_median": d}
        for k, d in pairs
    ]


def test_pairs_skip_trials_missing_either_side():
    attrs = _trial_attrs((1.0, 10.0), (2.0, 20.0))
    attrs.append({"sampled_params": {"atr_trailing_multiplier": 3.0}})  # kein gemessener Wert
    attrs.append({"sampled_params": {}, "oos_stop_distance_bps_median": 30.0})  # kein Multiplikator
    k_values, d_values = rpt._within_study_stop_calibration_pairs(attrs)
    assert k_values == [1.0, 2.0]
    assert d_values == [10.0, 20.0]


def test_pairs_skip_non_positive_multiplier():
    attrs = _trial_attrs((0.0, 10.0), (-1.0, 20.0), (1.5, 15.0))
    k_values, _ = rpt._within_study_stop_calibration_pairs(attrs)
    assert k_values == [1.5]


def test_spearman_undefined_below_three_pairs():
    attrs = _trial_attrs((1.0, 10.0), (2.0, 20.0))
    rho, n = rpt._within_study_stop_calibration_spearman(attrs)
    assert rho is None
    assert n == 2


def test_spearman_perfect_positive_monotone_relationship():
    attrs = _trial_attrs((1.0, 10.0), (2.0, 20.0), (3.0, 30.0), (4.0, 40.0))
    rho, n = rpt._within_study_stop_calibration_spearman(attrs)
    assert rho == pytest.approx(1.0)
    assert n == 4


def test_spearman_inverse_relationship_is_negative():
    attrs = _trial_attrs((1.0, 40.0), (2.0, 30.0), (3.0, 20.0), (4.0, 10.0))
    rho, n = rpt._within_study_stop_calibration_spearman(attrs)
    assert rho == pytest.approx(-1.0)


# --- invariants.check_trailing_stop_risk_calibration_acceptance: n-gewichtete Aggregation --------

def _study(strategy, symbol, *, spearman, n_pairs, atr_bps=10.0, k=2.0, loss_bps=20.0,
           n_ts_losses=40, trailing_stop_count=10, total_exits=100):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_median_bps": atr_bps, "atr_trailing_multiplier_median": k,
        "gross_loss_median_bps_trailing_stop": loss_bps,
        "oos_n_trailing_stop_losses": n_ts_losses,
        "exit_reason_histogram": {"TRAILING_STOP": trailing_stop_count},
        "oos_total_trades_with_exit_telemetry": total_exits,
        "stop_calibration_spearman_within_study": spearman,
        "stop_calibration_n_pairs_within_study": n_pairs,
    }


def test_combined_spearman_is_n_weighted_average_across_studies():
    """Zwei Studies mit stark unterschiedlicher Paarzahl duerfen nicht gleich gewichtet werden --
    die grosse Study (n=90) muss das Ergebnis dominieren."""
    studies = [
        _study("A", "X.ETORO", spearman=1.0, n_pairs=90),
        _study("B", "Y.ETORO", spearman=-1.0, n_pairs=10),
        _study("C", "Z.ETORO", spearman=1.0, n_pairs=10),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    expected = (1.0 * 90 + (-1.0) * 10 + 1.0 * 10) / 110
    assert result.actual["stop_calibration_spearman"] == round(expected, 4)
    assert result.actual["stop_calibration_n_pairs"] == 110


def test_studies_with_fewer_than_three_pairs_are_excluded_from_aggregation():
    """Eine Study mit nur 2 WITHIN-STUDY-Paaren traegt keine definierte Spearman-Korrelation
    (siehe _within_study_stop_calibration_spearman) und darf die Aggregation nicht verwaessern."""
    studies = [
        _study("A", "X.ETORO", spearman=1.0, n_pairs=10),
        _study("B", "Y.ETORO", spearman=1.0, n_pairs=10),
        _study("C", "Z.ETORO", spearman=None, n_pairs=2),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.actual["stop_calibration_n_pairs"] == 20
    assert result.actual["stop_calibration_n_studies_with_pairs"] == 2


def test_passed_is_none_not_false_when_pooled_pairs_below_eight():
    """Issue #1056/#1205 Fix Punkt 3 -- < 8 gepoolte Paare ist INCONCLUSIVE, kein FAIL."""
    studies = [
        _study("A", "X.ETORO", spearman=-1.0, n_pairs=3),
        _study("B", "Y.ETORO", spearman=-1.0, n_pairs=3),
        _study("C", "Z.ETORO", spearman=-1.0, n_pairs=1),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_unavailable_reason_is_stamped_explicitly_not_omitted():
    """Issue #1056/#1205 Fix Punkt 5 -- der Grund steht EXPLIZIT im actual-Dict."""
    studies = [
        _study("A", "X.ETORO", spearman=None, n_pairs=0),
        _study("B", "Y.ETORO", spearman=None, n_pairs=0),
        _study("C", "Z.ETORO", spearman=None, n_pairs=0),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.passed is None
    assert "stop_calibration_unavailable_reason" in result.actual
    assert result.actual["stop_calibration_unavailable_reason"] == (
        "SPEARMAN_UNAVAILABLE_INSUFFICIENT_PAIRS")
    assert result.actual["stop_calibration_spearman"] is None


def test_threshold_is_n_dependent_and_stricter_than_030_at_small_n():
    """Bei kleinem (aber >= 8) n liegt 2*SE(n) UEBER 0,30 -- die Schwelle ist strenger als die
    vormalige feste 0,30-Grenze, exakt das #1056-Akzeptanzkriterium."""
    # n=9 (3 Studies x 3 Paare): SE(9) = 1/sqrt(8) ~ 0.3536, 2*SE ~ 0.7071 > 0.30.
    studies = [
        _study("A", "X.ETORO", spearman=0.5, n_pairs=3),
        _study("B", "Y.ETORO", spearman=0.5, n_pairs=3),
        _study("C", "Z.ETORO", spearman=0.5, n_pairs=3),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    se = 1.0 / math.sqrt(9 - 1)
    expected_threshold = max(0.3, 2.0 * se)
    assert expected_threshold > 0.3
    assert result.actual["stop_calibration_threshold"] == round(expected_threshold, 4)
    # rho=0.5 < 0.7071 -> FAILt an der strengeren, n-abhaengigen Schwelle.
    assert result.passed is False


def test_threshold_falls_back_to_030_floor_at_large_n():
    """Bei grossem n (>= ~45 Paaren) faellt 2*SE(n) unter 0,30 -- der min_spearman-Floor greift,
    die Schwelle wird NICHT laxer als 0,30. Drei Studies (Kandidaten-Gate braucht >= 3), gleiches
    rho, damit das n-gewichtete Aggregat unveraendert 0,35 bleibt."""
    studies = [
        _study("A", "X.ETORO", spearman=0.35, n_pairs=30),
        _study("B", "Y.ETORO", spearman=0.35, n_pairs=30),
        _study("C", "Z.ETORO", spearman=0.35, n_pairs=30),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.actual["stop_calibration_threshold"] == 0.3
    se = 1.0 / math.sqrt(90 - 1)
    assert 2.0 * se < 0.3


def test_detail_text_reports_n_rho_ci_and_threshold_on_pass():
    # Zwei zusaetzliche Studies erfuellen nur das (>= 3 Studies)-Kandidaten-Gate fuer Kriterium 2/3;
    # ohne eigene Kalibrierungspaare (spearman=None/n_pairs=0) tragen sie NICHTS zur n=40-Aggregation
    # von Kriterium 1 bei.
    studies = [
        _study("A", "X.ETORO", spearman=0.9, n_pairs=40),
        _study("B", "Y.ETORO", spearman=None, n_pairs=0),
        _study("C", "Z.ETORO", spearman=None, n_pairs=0),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.passed is True
    assert "n=40" in result.detail
    assert "0.9" in result.detail
    ci = result.actual["stop_calibration_ci"]
    assert len(ci) == 2 and ci[0] < result.actual["stop_calibration_spearman"] < ci[1]


def test_detail_text_reports_n_rho_ci_and_threshold_on_fail():
    studies = [
        _study("A", "X.ETORO", spearman=-0.9, n_pairs=40),
        _study("B", "Y.ETORO", spearman=None, n_pairs=0),
        _study("C", "Z.ETORO", spearman=None, n_pairs=0),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    assert result.passed is False
    assert "n=40" in result.detail
    assert "-0.9" in result.detail
    assert "SE(40)" in result.detail


def test_provenance_exposes_per_study_spearman_breakdown():
    studies = [
        _study("A", "X.ETORO", spearman=0.9, n_pairs=20),
        _study("B", "Y.ETORO", spearman=0.4, n_pairs=20),
        _study("C", "Z.ETORO", spearman=None, n_pairs=0),
    ]
    result = inv.check_trailing_stop_risk_calibration_acceptance(studies)
    breakdown = result.provenance["stop_calibration_spearman_by_study"]
    assert breakdown["A/X.ETORO"] == {"spearman": 0.9, "n_pairs": 20}
    assert breakdown["B/Y.ETORO"] == {"spearman": 0.4, "n_pairs": 20}
