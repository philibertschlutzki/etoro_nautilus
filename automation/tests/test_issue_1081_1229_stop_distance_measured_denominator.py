"""Issue #1081/#1229 (P0, Katalog #1247+) — der blockierende Stop-Quotient lief auf einem Produkt
zweier Mediane.

Symptom. ``realized_stop_loss_ratio`` = Verlust / (``atr_trailing_multiplier_median`` ·
``atr_median_bps``). Die tatsächlich getaggte Distanz (``stop_distance_bps_measured``) steht im
selben Record und weicht um Faktor 0,525–3,543 ab (Median 1,290). Auswirkung auf die blockierende
Entscheidung: Median-Quotient 3,393 → 2,224, Akzeptanzanteil 40,9 % → 66,9 %, Offender > 10 von 9
auf 6 (B-5).

Root-Cause. ``report.py`` bildet die konfigurierte Distanz aus zwei unabhängig medianisierten
Grössen. Median eines Produkts ≠ Produkt der Mediane, und ``k`` und ``ATR_eff`` sind über den
kostengekoppelten Floor korreliert.

Fix.
1. ``realized_stop_loss_ratio`` auf ``gross_loss_median_bps_trailing_stop /
   stop_distance_bps_measured`` umgestellt. ``stop_distance_bps`` (= k·ATR) bleibt als
   MODELLIERTE Referenz erhalten, umbenannt zu ``stop_distance_bps_modelled``, und der alte
   Quotient als ``realized_stop_loss_ratio_vs_modelled`` (Zero-Regression).
2. ``check_effective_stop_distance`` und ``check_trailing_stop_risk_calibration_acceptance``
   konsumieren die GEMESSENE Variante. Fehlt sie, INCONCLUSIVE statt Rückfall auf die modellierte.
3. Neue Invariante ``check_stop_distance_model_fidelity`` (severity ``high``): FAIL, wenn
   ``|stop_distance_bps_measured / stop_distance_bps_modelled − 1| > 0,25`` in mehr als 25 % der
   Studies.
"""
import pytest

from automation.optimizer import invariants as inv
from automation.optimizer.report import _study_record


# --- report._study_record: beide Quotienten, eindeutig benannt ---------------------------------

def test_realized_stop_loss_ratio_and_vs_modelled_are_both_stamped_and_distinct():
    class _T:
        value = 1.0
        params = {}
        user_attrs = {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_atr_median_bps": 10.0,
            "sampled_params": {"atr_trailing_multiplier": 2.0},  # modelliert: 20.0
            "oos_gross_loss_mean_bps_trailing_stop": 30.0,
            "oos_gross_loss_median_bps_trailing_stop": 30.0,
            "oos_n_trailing_stop_losses": 40,
            "oos_stop_distance_bps_median": 25.0,  # gemessen: 25.0 (!= modelliert)
        }

    class _S:
        trials = [_T()]
        best_value = 1.0
        user_attrs = {}

    record, _checks = _study_record({"symbol": "X.ETORO", "strategy": "A"}, _S())
    assert record["stop_distance_bps_modelled"] == 20.0
    assert record["realized_stop_loss_ratio"] == pytest.approx(30.0 / 25.0)  # gemessen
    assert record["realized_stop_loss_ratio_vs_modelled"] == pytest.approx(30.0 / 20.0)  # modelliert
    assert record["realized_stop_loss_ratio"] != record["realized_stop_loss_ratio_vs_modelled"]


def test_realized_stop_loss_ratio_is_none_without_measured_distance_no_fallback_to_modelled():
    """Fehlt die gemessene Distanz, bleibt realized_stop_loss_ratio None -- kein stiller Rueckfall
    auf die modellierte Groesse (die unter realized_stop_loss_ratio_vs_modelled weiterhin verfuegbar
    bleibt)."""
    class _T:
        value = 1.0
        params = {}
        user_attrs = {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_atr_median_bps": 10.0,
            "sampled_params": {"atr_trailing_multiplier": 2.0},
            "oos_gross_loss_mean_bps_trailing_stop": 30.0,
            "oos_gross_loss_median_bps_trailing_stop": 30.0,
            "oos_n_trailing_stop_losses": 40,
            # kein oos_stop_distance_bps_median
        }

    class _S:
        trials = [_T()]
        best_value = 1.0
        user_attrs = {}

    record, _checks = _study_record({"symbol": "X.ETORO", "strategy": "A"}, _S())
    assert record["realized_stop_loss_ratio"] is None
    assert record["realized_stop_loss_ratio_vs_modelled"] == pytest.approx(30.0 / 20.0)


# --- invariants.check_stop_distance_model_fidelity ----------------------------------------------

def _record(strategy, symbol, *, measured, modelled):
    return {"strategy": strategy, "symbol": symbol,
            "stop_distance_bps_measured": measured, "stop_distance_bps_modelled": modelled}


def test_fidelity_passes_within_tolerance():
    records = [_record("A", "X.ETORO", measured=20.0, modelled=18.0)]  # 11.1% Abweichung
    result = inv.check_stop_distance_model_fidelity(records)
    assert result.passed is True


def test_fidelity_fails_when_more_than_25_percent_of_studies_deviate_beyond_threshold():
    """Reproduziert B-5: Faktor 0,525-3,543 (Median 1,290) Abweichung -- weit ueber der
    25-%-Toleranz auf einem grossen Anteil der Studies."""
    offending = [
        _record("A", f"S{i}.ETORO", measured=10.0, modelled=10.0 * f)
        for i, f in enumerate([0.525, 1.858, 3.543])  # |ratio - 1| jeweils > 0.25
    ]
    clean = [_record("B", "Y.ETORO", measured=10.0, modelled=10.5)]  # 5% Abweichung
    result = inv.check_stop_distance_model_fidelity(offending + clean)
    assert result.passed is False
    assert result.severity == "high"
    assert len(result.actual) == 3
    assert "A/S0.ETORO" in result.actual


def test_fidelity_offender_carries_both_values_and_relative_deviation():
    records = [_record("A", "X.ETORO", measured=25.0, modelled=20.0)]  # +25% -> Grenzfall (nicht > )
    result = inv.check_stop_distance_model_fidelity(records)
    assert result.passed is True  # exakt 0.25 ist NICHT > 0.25
    records_over = [_record("A", "X.ETORO", measured=25.01, modelled=20.0)]
    result_over = inv.check_stop_distance_model_fidelity(records_over)
    assert result_over.passed is False
    offender = result_over.actual["A/X.ETORO"]
    assert offender["stop_distance_bps_measured"] == 25.01
    assert offender["stop_distance_bps_modelled"] == 20.0
    assert offender["relative_deviation"] > 0.25


def test_fidelity_inconclusive_without_any_study_carrying_both_fields():
    result = inv.check_stop_distance_model_fidelity([{"strategy": "A", "symbol": "X.ETORO"}])
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False
    assert result.actual is None


def test_fidelity_skips_studies_missing_either_field_but_still_judges_the_rest():
    records = [
        {"strategy": "A", "symbol": "X.ETORO", "stop_distance_bps_measured": 20.0},  # kein modelled
        _record("B", "Y.ETORO", measured=10.0, modelled=10.0),  # 0% Abweichung
    ]
    result = inv.check_stop_distance_model_fidelity(records)
    assert result.passed is True
    assert result.evaluability["n_candidates"] == 1


# --- Regressionswaechter: keine Invariante konsumiert mehr die modellierte Distanz als Nenner ---

def test_no_invariant_uses_stop_distance_bps_modelled_as_a_ratio_denominator():
    """Akzeptanzkriterium — die einzigen verbleibenden Konsumenten von
    ``stop_distance_bps_modelled`` sind reine Vergleichs-/Provenienz-Kontexte (ATR-Floor-Bindung,
    Kosten-Adaequanz der Floor-KONSTRUKTION, Mikrostruktur-Floor gegen die SIMULIERTE Distanz) --
    keiner davon ist eine Risiko-Ratio mit der gemessenen Verlustgroesse im Zaehler. Die primaeren
    Risiko-Quotienten (check_effective_stop_distance, check_trailing_stop_risk_calibration_
    acceptance) lesen ausschliesslich stop_distance_bps_measured/oos_stop_distance_bps_median."""
    import inspect
    for fn in (inv.check_effective_stop_distance, inv.check_trailing_stop_risk_calibration_acceptance):
        code_lines = [
            line for line in inspect.getsource(fn).splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert 'get("stop_distance_bps_modelled")' not in code
        assert '"atr_trailing_multiplier_median"] * float(r["atr_median_bps"' not in code
        assert "stop_distance_bps_measured" in code
