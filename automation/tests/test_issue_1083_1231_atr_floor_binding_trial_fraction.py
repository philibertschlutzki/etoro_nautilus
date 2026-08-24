"""Issue #1083/#1231 (P1, Katalog #1247+) — ATR-Floor-Bindung als Study-Binärwert statt als
Trial-Anteil.

Symptom. ``atr_floor_binding_studies`` vergleicht ``atr_raw_median_bps`` gegen
``atr_floor_bps_derived`` — eine Study-Ebene-Binärentscheidung. In 149 von 154 Studies gilt aber
``atr_median_bps != max(atr_raw_median_bps, atr_floor_bps_derived)``; Beispiel Sma/GOOGL: roh
9,246, Floor 8,823 (bindet nicht), effektiv 12,037 — 30 % über dem rohen Wert.

Root-Cause. Jeder Trial hat sein eigenes ``k`` und damit seinen eigenen effektiven Floor
(``3·c_rt / k``). ``max(median(raw), median(floor))`` ist nicht ``median(max(raw_i, floor_i))``.

Fix.
1. ``atr_floor_binding_trial_fraction`` je Study stempeln: Anteil der Trials mit
   ``atr_raw_i < floor_i``.
2. ``atr_floor_binding_studies`` behält den Study-Eintrag, ergänzt aber ``binding_trial_fraction``
   in der ``detail``-Struktur.
3. §5.3 weist beides aus: Anzahl gebundener Studies und Median des Trial-Anteils.
4. Schwelle für die Aufnahme: ``binding_trial_fraction >= 0,5`` ODER ``median(raw) < floor``.
"""
import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sde


# --- Root-Cause: Median und Maximum sind nicht vertauschbar --------------------------------------

def test_median_of_max_is_not_max_of_medians_on_a_constructed_example():
    """Reproduziert die Root-Cause-Behauptung direkt: jeder Trial hat sein eigenes k (und damit
    seinen eigenen effektiven Floor), sodass max(median(raw), median(floor)) von
    median(max(raw_i, floor_i)) abweicht."""
    import statistics
    # Trials mit stark unterschiedlichem k -> stark unterschiedlicher effektiver Floor je Trial.
    raw_i = [9.0, 9.246, 9.5, 3.0, 30.0]
    floor_i = [7.0, 7.2, 7.5, 12.0, 7.9]  # ein Ausreisser (12.0) treibt den Floor-Median nicht
    median_raw = statistics.median(raw_i)
    median_floor = statistics.median(floor_i)
    max_of_medians = max(median_raw, median_floor)
    median_of_max = statistics.median([max(r, f) for r, f in zip(raw_i, floor_i)])
    assert max_of_medians != median_of_max


# --- report._stamp_atr_floor_bps_derived: atr_floor_binding_trial_fraction -----------------------

def test_binding_trial_fraction_reproduces_the_sma_googl_symptom():
    """Sma/GOOGL: roh-Median 9,246, Floor-Median 8,823 (bindet auf Study-Ebene NICHT), aber ein
    erheblicher Trial-Anteil bindet PER-TRIAL (das Symptom, das die binaere Study-Metrik
    verdeckt)."""
    # Vier Trials: drei mit kleinem k (hoher effektiver Floor, bindet), einer mit grossem k
    # (niedriger Floor, bindet nicht) -- Study-Median von raw/floor liegt knapp NICHT im Bindungs-
    # Bereich, aber 3/4 Trials binden individuell.
    studies_out = [{
        "symbol": "GOOGL.ETORO", "strategy": "SmaCrossoverStrategy",
        "atr_trailing_multiplier_median": 1.6,
        "_atr_floor_binding_trial_pairs": [
            (1.0, 8.0),   # floor = 3*4.0/1.0 = 12.0 -> 8.0 < 12.0 bindet
            (1.0, 9.0),   # floor = 12.0 -> bindet
            (1.2, 9.5),   # floor = 3*4.0/1.2 = 10.0 -> bindet
            (5.0, 30.0),  # floor = 3*4.0/5.0 = 2.4 -> bindet NICHT
        ],
    }]
    rpt._stamp_atr_floor_bps_derived(
        studies_out, atr_floor_bps_by_symbol={"GOOGL.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"GOOGL.ETORO": 4.0}, min_stop_to_cost_ratio=3.0)
    assert studies_out[0]["atr_floor_binding_trial_fraction"] == pytest.approx(0.75)


def test_binding_trial_fraction_is_none_without_base_floor_or_cost_basis():
    studies_out = [{
        "symbol": "X.ETORO", "strategy": "A", "atr_trailing_multiplier_median": 1.5,
        "_atr_floor_binding_trial_pairs": [(1.0, 5.0)],
    }]
    rpt._stamp_atr_floor_bps_derived(
        studies_out, atr_floor_bps_by_symbol={}, round_trip_cost_bps_by_symbol={},
        min_stop_to_cost_ratio=3.0)
    assert studies_out[0]["atr_floor_binding_trial_fraction"] is None


def test_binding_trial_fraction_is_none_without_any_trial_pairs():
    studies_out = [{"symbol": "X.ETORO", "strategy": "A", "atr_trailing_multiplier_median": 1.5}]
    rpt._stamp_atr_floor_bps_derived(
        studies_out, atr_floor_bps_by_symbol={"X.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"X.ETORO": 4.0}, min_stop_to_cost_ratio=3.0)
    assert studies_out[0]["atr_floor_binding_trial_fraction"] is None


def test_internal_trial_pairs_field_never_leaks_into_the_final_study_record():
    studies_out = [{
        "symbol": "GOOGL.ETORO", "strategy": "A", "atr_trailing_multiplier_median": 1.5,
        "_atr_floor_binding_trial_pairs": [(1.0, 5.0), (2.0, 9.0)],
    }]
    rpt._stamp_atr_floor_bps_derived(
        studies_out, atr_floor_bps_by_symbol={"GOOGL.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"GOOGL.ETORO": 4.0}, min_stop_to_cost_ratio=3.0)
    assert "_atr_floor_binding_trial_pairs" not in studies_out[0]


def test_study_record_collects_raw_per_trial_pairs_from_trial_attrs():
    class _T:
        value = 1.0
        params = {}
        user_attrs = {
            "oos_evaluated": True, "oos_eligible": True,
            "sampled_params": {"atr_trailing_multiplier": 1.7},
            "oos_atr_raw_median_bps": 9.246,
        }

    class _S:
        trials = [_T()]
        best_value = 1.0
        user_attrs = {}

    record, _checks = rpt._study_record({"symbol": "GOOGL.ETORO", "strategy": "A"}, _S())
    assert record["_atr_floor_binding_trial_pairs"] == [(1.7, 9.246)]


def test_zero_raw_median_is_a_valid_binding_measurement_not_treated_as_missing():
    """Akzeptanzkriterium — fuer die 5 Studies mit atr_raw_median_bps = 0,0 ist der Anteil nahe 1
    (0.0 < jeder positive Floor bindet immer)."""
    studies_out = [{
        "symbol": "X.ETORO", "strategy": "A", "atr_trailing_multiplier_median": 1.5,
        "_atr_floor_binding_trial_pairs": [(1.0, 0.0), (2.0, 0.0), (3.0, 0.0)],
    }]
    rpt._stamp_atr_floor_bps_derived(
        studies_out, atr_floor_bps_by_symbol={"X.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"X.ETORO": 4.0}, min_stop_to_cost_ratio=3.0)
    assert studies_out[0]["atr_floor_binding_trial_fraction"] == pytest.approx(1.0)


# --- invariants.check_atr_scale_homogeneity: binding_trial_fraction als OR-Arm -------------------

def _study(strategy, symbol, *, atr_raw=None, atr_floor_derived=None, atr_median_bps=None,
          binding_trial_fraction=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_raw_median_bps": atr_raw, "atr_floor_bps_derived": atr_floor_derived,
        "atr_median_bps": atr_median_bps, "atr_floor_binding_trial_fraction": binding_trial_fraction,
    }


def test_binding_trial_fraction_above_half_flags_a_study_the_median_criterion_would_miss():
    """Sma/GOOGL-Symptom: der Study-Median bindet NICHT (raw > floor), aber der Trial-Anteil
    bindet -- muss ueber den neuen OR-Arm trotzdem als gebunden erscheinen."""
    records = [
        _study("SmaCrossoverStrategy", "GOOGL.ETORO", atr_raw=9.246, atr_floor_derived=8.823,
              binding_trial_fraction=0.75),
    ]
    result = inv.check_atr_scale_homogeneity(records)
    assert "SmaCrossoverStrategy/GOOGL.ETORO" in result.provenance["atr_floor_binding_studies"]
    detail = result.provenance["atr_floor_binding_studies_detail"]["SmaCrossoverStrategy/GOOGL.ETORO"]
    assert detail["criterion"] == "binding_trial_fraction_majority"
    assert detail["binding_trial_fraction"] == pytest.approx(0.75)


def test_binding_trial_fraction_below_half_does_not_flag_alone():
    records = [
        _study("A", "X.ETORO", atr_raw=9.246, atr_floor_derived=8.823, binding_trial_fraction=0.3),
    ]
    result = inv.check_atr_scale_homogeneity(records)
    assert "A/X.ETORO" not in result.provenance["atr_floor_binding_studies"]


def test_existing_raw_below_floor_criterion_still_carries_the_trial_fraction_as_context():
    """Fix Punkt 2 — der bestehende Study-Eintrag (raw < floor) bleibt, ergaenzt aber
    binding_trial_fraction als Kontext, unabhaengig vom Wert."""
    records = [
        _study("DynamicBreakoutStrategy", "TSLA.ETORO", atr_raw=0.1627, atr_floor_derived=7.9820,
              atr_median_bps=9.5, binding_trial_fraction=0.9),
    ]
    result = inv.check_atr_scale_homogeneity(records)
    detail = result.provenance["atr_floor_binding_studies_detail"]["DynamicBreakoutStrategy/TSLA.ETORO"]
    assert detail["criterion"] == "atr_raw_median_bps_below_floor"
    assert detail["binding_trial_fraction"] == pytest.approx(0.9)


def test_both_criteria_binding_are_not_double_counted():
    records = [
        _study("A", "X.ETORO", atr_raw=0.5, atr_floor_derived=8.0, binding_trial_fraction=0.9),
    ]
    result = inv.check_atr_scale_homogeneity(records)
    assert result.provenance["atr_floor_binding_studies"].count("A/X.ETORO") == 1


def test_study_without_either_criterion_but_with_low_trial_fraction_is_still_measured():
    records = [
        _study("A", "X.ETORO", atr_raw=50.0, atr_floor_derived=8.0, binding_trial_fraction=0.1),
    ]
    result = inv.check_atr_scale_homogeneity(records)
    assert result.provenance["atr_floor_binding_evaluable"] is True
    assert "A/X.ETORO" not in result.provenance["atr_floor_binding_studies"]


# --- summary_de.py §5.3: Median des Trial-Anteils sichtbar ---------------------------------------

def _report_with_studies(rows, atr_floor_binding_studies=None):
    return {
        "run_id": "run-x", "run_status": "complete",
        "studies": rows,
        "invariant_checks": [],
        "cross_study": {
            "atr_floor_binding_studies": atr_floor_binding_studies or {
                "evaluable": True, "studies": [], "detail": {}},
        },
    }


def test_section_5_3_shows_the_median_trial_binding_fraction():
    rows = [
        {"strategy": "A", "symbol": "X.ETORO", "atr_floor_binding_trial_fraction": 0.75},
        {"strategy": "B", "symbol": "Y.ETORO", "atr_floor_binding_trial_fraction": 0.25},
    ]
    section = sde._section_5_anomalies(_report_with_studies(rows))
    assert "Median des Trial-Bindungsanteils" in section
    assert "50.0" in section or "50,0" in section  # Median(0.75, 0.25) = 0.5


def test_section_5_3_omits_the_median_line_without_any_measured_study():
    rows = [{"strategy": "A", "symbol": "X.ETORO"}]
    section = sde._section_5_anomalies(_report_with_studies(rows))
    assert "Median des Trial-Bindungsanteils" not in section
