"""Issue #1082/#1230 (P1, Katalog #1247+) — die Report-Zerlegung addierte drei unabhängige Mediane.

Symptom. §2.4 überschreibt die Tabelle mit ``realized_loss_bps = stop_distance_bps +
trigger_to_fill_gap_bps``. Die gezeigten Zahlen erfüllen die Gleichung nicht: Residuum Median
+12,00 bps = 16,17 % des Median-Verlusts, positiv in 151 von 154 Studies (Spannweite −2,78 bis
+74,66). Beispiel Vwap/GOOGL: 15,54 + 44,60 = 60,14 gegen ausgewiesene 87,90.

Root-Cause. ``backtest_runner.py`` bildete drei GETRENNTE Mediane über drei getrennte Listen. Die
per-Round-Trip-Identität hält (0 Verletzungen ausserhalb NATGAS) — der Median einer Summe ist aber
nicht die Summe der Mediane, und beide Summanden sind rechtsschief und positiv korreliert.

Fix.
1. Die Anteile JE ROUND-TRIP bilden (``stop_distance_bps / realized_loss_bps``,
   ``trigger_to_fill_gap_bps / realized_loss_bps``) und DANN medianisieren. Neue Felder
   ``stop_distance_share_median``, ``trigger_to_fill_gap_share_median`` — summieren sich per
   Konstruktion auf 1 (bis auf Rundung).
2. §2.4-Tabelle um die beiden Anteilsspalten erweitert; die Überschrift behauptet keine
   Additivität der drei absoluten Mediane mehr.
3. Neue Invariante ``check_stop_loss_share_decomposition`` (severity ``medium``): FAIL, wenn
   ``|stop_distance_share_median + trigger_to_fill_gap_share_median − 1| > 0,02``.
"""
import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import summary_de as sde


# --- backtest_runner._aggregate_exit_telemetry: Anteile je Round-Trip, dann medianisiert --------

def test_sum_of_medians_does_not_equal_median_of_sum_on_right_skewed_correlated_data():
    """Reproduziert die Root-Cause-Behauptung direkt an rechtsschiefen Summanden, deren
    Rangordnung sich zwischen Stopdistanz, Gap und Summe unterscheidet: die Summe der (getrennt
    gebildeten) Mediane weicht von der Summe der tatsächlich zusammengehörigen (per-Round-Trip)
    Werte ab -- exakt die im Report zuvor implizit behauptete (aber falsche) Gleichung."""
    import statistics
    pairs = [(5.0, 50.0), (6.0, 3.0), (7.0, 4.0), (100.0, 1.0), (8.0, 40.0)]
    stop_values = [p[0] for p in pairs]
    gap_values = [p[1] for p in pairs]
    sum_of_medians = statistics.median(stop_values) + statistics.median(gap_values)
    # Der "wahre" Verlust je Round-Trip ist die PAARWEISE Summe; ihr Median ist eine ANDERE Zahl.
    median_of_sums = statistics.median([s + g for s, g in pairs])
    assert sum_of_medians == pytest.approx(11.0)
    assert median_of_sums == pytest.approx(48.0)
    assert sum_of_medians != median_of_sums


def test_aggregate_exit_telemetry_computes_shares_per_round_trip_then_medianizes():
    from automation.backtest_runner import _aggregate_exit_telemetry
    # Dieselben rechtsschiefen Paare wie oben (Rangordnung von Stopdistanz/Gap/Summe divergiert),
    # als TRAILING_STOP-Round-Trips mit der per-Round-Trip-Identitaet realized_loss_bps = stop +
    # gap.
    pairs = [(5.0, 50.0), (6.0, 3.0), (7.0, 4.0), (100.0, 1.0), (8.0, 40.0)]
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "stop_distance_bps": s,
         "trigger_to_fill_gap_bps": g, "realized_loss_bps": s + g}
        for s, g in pairs
    ]
    result = _aggregate_exit_telemetry(meta_list)
    # Anteile je Round-Trip (s/(s+g)): 5/55, 6/9, 7/11, 100/101, 8/48 -> sortiert Median 7/11.
    assert result["stop_distance_share_median"] == pytest.approx(7.0 / 11.0, abs=1e-4)
    assert result["trigger_to_fill_gap_share_median"] == pytest.approx(4.0 / 11.0, abs=1e-4)
    # Jedes Paar summiert sich per Konstruktion (derselbe Nenner) auf 1 -- gilt fuer die Mediane
    # selbst dann, weil 1-x eine monotone Transformation ist (Median bleibt unter ihr invariant).
    assert result["stop_distance_share_median"] + result["trigger_to_fill_gap_share_median"] == \
        pytest.approx(1.0, abs=1e-6)
    # Die alten, NICHT additiven absoluten Mediane bleiben als Kontext erhalten (Root-Cause-Aussage:
    # ihre Summe ist NICHT der Median-Verlust).
    assert result["stop_distance_bps_median"] == pytest.approx(7.0)
    assert result["trigger_to_fill_gap_bps_median"] == pytest.approx(4.0)
    assert result["realized_loss_bps_median"] == pytest.approx(48.0)
    assert (result["stop_distance_bps_median"] + result["trigger_to_fill_gap_bps_median"]
            != result["realized_loss_bps_median"])


def test_aggregate_exit_telemetry_shares_none_without_data():
    from automation.backtest_runner import _aggregate_exit_telemetry
    result = _aggregate_exit_telemetry([{"exit_reason": "TIME_BOX"}])
    assert result["stop_distance_share_median"] is None
    assert result["trigger_to_fill_gap_share_median"] is None


def test_aggregate_exit_telemetry_skips_zero_denominator_round_trips_for_shares():
    from automation.backtest_runner import _aggregate_exit_telemetry
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "stop_distance_bps": 10.0,
         "trigger_to_fill_gap_bps": -10.0, "realized_loss_bps": 0.0},  # Nenner 0 -> ausgeschlossen
        {"exit_reason": "TRAILING_STOP", "stop_distance_bps": 10.0,
         "trigger_to_fill_gap_bps": 2.0, "realized_loss_bps": 12.0},
    ]
    result = _aggregate_exit_telemetry(meta_list)
    assert result["stop_distance_share_median"] == pytest.approx(10.0 / 12.0)


# --- report._study_record: die beiden neuen Study-Felder ----------------------------------------

def test_study_record_stamps_both_share_fields_from_trial_attrs():
    from automation.optimizer.report import _study_record

    class _T:
        value = 1.0
        params = {}
        user_attrs = {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_stop_distance_share_median": 0.72,
            "oos_trigger_to_fill_gap_share_median": 0.28,
        }

    class _S:
        trials = [_T()]
        best_value = 1.0
        user_attrs = {}

    record, _checks = _study_record({"symbol": "X.ETORO", "strategy": "A"}, _S())
    assert record["stop_distance_share_median"] == pytest.approx(0.72)
    assert record["trigger_to_fill_gap_share_median"] == pytest.approx(0.28)


# --- invariants.check_stop_loss_share_decomposition ----------------------------------------------

def _record(strategy, symbol, *, stop_share, gap_share):
    return {"strategy": strategy, "symbol": symbol,
            "stop_distance_share_median": stop_share, "trigger_to_fill_gap_share_median": gap_share}


def test_passes_when_shares_sum_close_to_one():
    records = [_record("A", "X.ETORO", stop_share=0.72, gap_share=0.28)]
    result = inv.check_stop_loss_share_decomposition(records)
    assert result.passed is True
    assert result.severity == "medium"


def test_fails_when_shares_deviate_from_one_beyond_tolerance():
    """Reproduziert das Symptom: Residuum 16,17% des Verlusts -- eine Anteilssumme deutlich unter
    1 (die additive Ueberschreibung fehlt Kontext, der in den nicht mehr additiven Medianen lag)."""
    records = [_record("Vwap", "GOOGL.ETORO", stop_share=0.55, gap_share=0.30)]  # Summe 0.85
    result = inv.check_stop_loss_share_decomposition(records)
    assert result.passed is False
    offender = result.actual["Vwap/GOOGL.ETORO"]
    assert offender["sum_deviation"] == pytest.approx(0.15, abs=1e-4)


def test_within_tolerance_rounding_still_passes():
    records = [_record("A", "X.ETORO", stop_share=0.70, gap_share=0.29)]  # Summe 0.99, Abw. 0.01
    result = inv.check_stop_loss_share_decomposition(records)
    assert result.passed is True


def test_exactly_at_the_tolerance_boundary_passes():
    records = [_record("A", "X.ETORO", stop_share=0.70, gap_share=0.28)]  # Summe 0.98, Abw. 0.02
    result = inv.check_stop_loss_share_decomposition(records)
    assert result.passed is True


def test_not_applicable_without_any_study_carrying_both_share_fields():
    result = inv.check_stop_loss_share_decomposition([{"strategy": "A", "symbol": "X.ETORO"}])
    assert result.passed is True
    assert result.actual is None


def test_mixed_cohort_only_flags_the_offending_study():
    records = [
        _record("A", "X.ETORO", stop_share=0.72, gap_share=0.28),  # OK
        _record("B", "Y.ETORO", stop_share=0.50, gap_share=0.20),  # Summe 0.70, FAIL
    ]
    result = inv.check_stop_loss_share_decomposition(records)
    assert result.passed is False
    assert len(result.actual) == 1
    assert "B/Y.ETORO" in result.actual
    assert "A/X.ETORO" not in result.actual


# --- summary_de.py Abschnitt 2.4: Anteilsspalten sichtbar, keine Additivitätsbehauptung ----------

def _report_with_decomp_studies(rows):
    return {
        "run_id": "run-x", "run_status": "complete",
        "started_at_utc": "2026-08-21T05:00:00Z", "wallclock_s": 100.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "symbols_completed": None, "symbols_planned": None,
        "studies": rows,
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }


def test_section_2_4_shows_share_columns_and_does_not_claim_additivity():
    rows = [{
        "strategy": "Vwap", "symbol": "GOOGL.ETORO",
        "stop_distance_bps_measured": 15.54, "trigger_to_fill_gap_bps": 44.60,
        "realized_loss_bps": 87.90,  # bewusst NICHT 15.54+44.60 (Symptom-Beispiel aus der Issue)
        "stop_distance_share_median": 0.439, "trigger_to_fill_gap_share_median": 0.384,
    }]
    text = sde.generate_german_summary(_report_with_decomp_studies(rows))
    section = text.split("### 2.4")[1]
    assert "Anteil Stopdistanz" in section
    assert "Anteil Absetzen-zu-Fill-Gap" in section
    assert "43.9" in section or "43,9" in section
    assert "NICHT additiv" in section
    # Der alte, additive Deutungssatz ("realized_loss_bps = stop_distance_bps + ...") darf als
    # BEHAUPTETE Gleichung fuer die gezeigten Zahlen nicht mehr im Artefakt stehen.
    assert "realized_loss_bps = stop_distance_bps + trigger_to_fill_gap_bps`" not in section
