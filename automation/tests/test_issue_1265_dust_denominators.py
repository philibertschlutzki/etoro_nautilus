"""Issue #1265 (GH #1135) — "Dust-Round-Trips vor jedem gepoolten Nenner ausschliessen".

Symptom (Issue-Text): ``check_dust_round_trip_share`` FAILt mit sechs Studies (Rsi2 7,12%,
TrendPullback 7,73%, VwapExhaustion 6,86%); absolut gefiltert werden 2497/2334/1408/1013
Round-Trips. Root-Cause laut Issue: "Der Dust-Boden greift an der Quelle
(``_filter_dust_round_trips``), die gefilterten Round-Trips zählen aber weiterhin in
Exit-Reason-Anteilen, Win-Rate und der 35-%-TRAILING_STOP-Vorbedingung des Kalibrierungs-Checks."

INVESTIGATIONS-ERGEBNIS (dieser Fix): eine gruendliche Nachverfolgung jeder genannten Kennzahl
zeigt, dass ``_filter_dust_round_trips`` (seit #946/#1112) Dust-Round-Trips bereits AN DER QUELLE
verwirft — VOR jeder IS/OOS-Aufteilung UND vor jedem Konsumenten (win_rate/exit_reason_histogram/
expectancy werden ausschliesslich aus den vier gefilterten Listen gebildet, siehe
``backtest_runner.extract_metrics``, Zeile ~5646). Die 35-%-TRAILING_STOP-Vorbedingung
(``invariants.check_trailing_stop_risk_calibration_acceptance``) konsumierte den bereits
bereinigten Nenner ``oos_total_trades_with_exit_telemetry`` schon VOR diesem Fix korrekt (siehe
Issue #1062/#1211, ``test_issue_1061_1210_1062_1211_dust_denominator.py``). Es existiert also
KEIN messbarer Rechenfehler mehr in den genannten Kennzahlen.

Die tatsaechlich verbleibende Luecke ist die im "Fix"-Abschnitt des Issues explizit genannte
ZWEITE Anforderung: "je Kennzahl den verwendeten Nenner stempeln (``*_denominator_n``)" — und das
Akzeptanzkriterium "Für jede gepoolte Kennzahl ist der Nenner im Artefakt ablesbar". Das war vor
diesem Fix NICHT der Fall: ein Leser musste den Nenner mehrerer Kennzahlen (z. B.
``time_box_exit_fraction``) selbst aus ``exit_reason_histogram`` rekonstruieren, statt ihn direkt
abzulesen. Dieser Fix ergaenzt GENAU diese fehlende Sichtbarkeit — additiv, ohne die (bereits
korrekten) zugrundeliegenden Werte zu aendern:

  * ``exit_reason_histogram_denominator_n`` / ``time_box_exit_fraction_denominator_n``
    (``report.py``, Study-Ebene) — Σ der Histogramm-Werte.
  * ``holdout_win_rate_denominator_n`` / ``holdout_profit_factor_denominator_n``
    (``report.py``, Holdout-Ebene) — ``holdout_total_trades`` (bereits dust-bereinigt).
  * ``holdout_expectancy_capital_weighted_denominator_n`` (``report.py``) — ``holdout_total_trades``
    abzueglich des expectancy-spezifischen 5%-Median-Notional-Bodens (#1031).
  * ``trailing_stop_exit_share_denominator_n`` (``invariants.check_trailing_stop_risk_calibration_
    acceptance``s ``actual``-Dict, alle drei Rueckgabezweige) — ``total_exits`` (bereits
    dust-bereinigt).

``check_dust_round_trip_share`` selbst bleibt UNVERAENDERT (weiterhin das Datenqualitäts-Signal,
Akzeptanzkriterium 2)."""
import inspect

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


# --- report.py: Study-Ebene (exit_reason_histogram / time_box_exit_fraction) -------------------

def test_sum_exit_reason_histograms_is_the_denominator_time_box_exit_fraction_normalizes_against():
    """Reine Arithmetik-Kontrolle: der Nenner, den report.py jetzt als
    exit_reason_histogram_denominator_n/time_box_exit_fraction_denominator_n stempelt, ist exakt
    Σ der Histogramm-Werte -- dieselbe Groesse, die _time_box_exit_fraction intern zur Normierung
    verwendet."""
    trial_attrs = [
        {"oos_exit_reason_histogram": {"TIME_BOX": 3, "TRAILING_STOP": 5}},
        {"oos_exit_reason_histogram": {"TIME_BOX": 2, "SIGNAL_REVERSAL": 1}},
    ]
    histogram = rpt._sum_exit_reason_histograms(trial_attrs)
    denominator_n = sum(histogram.values())
    assert denominator_n == 11
    assert rpt._time_box_exit_fraction(trial_attrs) == round(5 / 11, 4)


def test_report_stamps_exit_reason_histogram_denominator_n_next_to_the_histogram():
    source = inspect.getsource(rpt._study_record)
    assert (
        '"exit_reason_histogram_denominator_n": sum(_study_exit_reason_histogram.values()) or None'
        in source
    )


def test_report_stamps_time_box_exit_fraction_denominator_n_next_to_the_fraction():
    source = inspect.getsource(rpt._study_record)
    assert (
        '"time_box_exit_fraction_denominator_n": sum(_study_exit_reason_histogram.values()) or None'
        in source
    )


# --- report.py: Holdout-Ebene (win_rate / profit_factor / expectancy_capital_weighted) ----------

def test_report_stamps_holdout_win_rate_and_profit_factor_denominator_n_from_oos_total_trades():
    """win_rate = wins/n und profit_factor = gross_profit/gross_loss teilen sich DIESELBE
    Population n = oos_total_trades (backtest_runner._calculate_stats) -- beide Denominator-Felder
    muessen deshalb denselben Rohwert lesen."""
    source = inspect.getsource(rpt._study_record)
    assert '"holdout_win_rate_denominator_n": holdout_metrics.get("oos_total_trades")' in source
    assert '"holdout_profit_factor_denominator_n": holdout_metrics.get("oos_total_trades")' in source


def test_holdout_expectancy_capital_weighted_denominator_n_subtracts_only_the_notional_floor():
    """expectancy_capital_weighted's Population ist oos_total_trades (bereits dust-bereinigt an
    der Quelle) MINUS dem expectancy-eigenen 5%-Median-Notional-Boden
    (oos_expectancy_notional_degenerate_count, #1031) -- KEIN zweiter Abzug von Dust, das waere
    Doppelzaehlung (Dust erreicht oos_total_trades bereits nicht)."""
    source = inspect.getsource(rpt._study_record)
    assert (
        'int(holdout_metrics.get("oos_total_trades") or 0)\n'
        '             - int(holdout_metrics.get("oos_expectancy_notional_degenerate_count") or 0))'
        in source
    )
    # Kein Abzug von oos_dust_round_trips_filtered_count in dieser Formel (Doppelzaehlung).
    formula_start = source.index("holdout_expectancy_capital_weighted_denominator_n")
    formula_region = source[formula_start:formula_start + 600]
    assert "oos_dust_round_trips_filtered_count" not in formula_region


# --- invariants.check_trailing_stop_risk_calibration_acceptance: alle drei Rueckgabezweige ------

def _study(strategy, symbol, *, k_atr_mult, atr_bps, loss_bps, n_ts_losses,
           trailing_stop_count, total_exits_clean, dust_excluded=0):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_trailing_multiplier_median": k_atr_mult, "atr_median_bps": atr_bps,
        "gross_loss_median_bps_trailing_stop": loss_bps,
        "oos_n_trailing_stop_losses": n_ts_losses,
        "exit_reason_histogram": {"TRAILING_STOP": trailing_stop_count},
        "oos_total_trades_with_exit_telemetry": total_exits_clean,
        "dust_round_trips_filtered": dust_excluded,
        "stop_calibration_spearman_within_study": 1.0,
        "stop_calibration_n_pairs_within_study": 10,
    }


def test_denominator_n_present_on_the_final_pass_fail_branch():
    result = inv.check_trailing_stop_risk_calibration_acceptance([
        _study("A", "X.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=40, total_exits_clean=100, dust_excluded=50),
        _study("B", "Y.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=0, total_exits_clean=1, dust_excluded=0),
        _study("C", "Z.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=0, total_exits_clean=1, dust_excluded=0),
    ])
    assert result.actual["trailing_stop_exit_share_denominator_n"] == 102  # 100 + 1 + 1, bereinigt


def test_denominator_n_present_on_the_fewer_than_3_studies_inconclusive_branch():
    result = inv.check_trailing_stop_risk_calibration_acceptance([
        _study("A", "X.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=5,
               trailing_stop_count=5, total_exits_clean=20, dust_excluded=3),
    ])
    assert result.passed is None
    assert result.actual["trailing_stop_exit_share_denominator_n"] == 20


def test_denominator_n_present_on_the_insufficient_calibration_pairs_branch():
    def _study_few_pairs(strategy, symbol, total_exits_clean):
        rec = _study(strategy, symbol, k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0,
                     n_ts_losses=40, trailing_stop_count=10, total_exits_clean=total_exits_clean)
        rec["stop_calibration_n_pairs_within_study"] = 2  # < 3, wird gar nicht aggregiert
        return rec

    result = inv.check_trailing_stop_risk_calibration_acceptance([
        _study_few_pairs("A", "X.ETORO", 30),
        _study_few_pairs("B", "Y.ETORO", 30),
        _study_few_pairs("C", "Z.ETORO", 30),
    ])
    assert result.passed is None
    assert result.actual["trailing_stop_exit_share_denominator_n"] == 90


# --- check_dust_round_trip_share bleibt unveraendert (Akzeptanzkriterium 2) ---------------------

def test_check_dust_round_trip_share_is_unchanged_as_a_data_quality_signal():
    """Akzeptanzkriterium 2 (#1265) — dieser Check bleibt als reines Datenqualitäts-Signal
    (severity 'high', kein Blocker) erhalten, unveraendert durch diesen Fix."""
    result = inv.check_dust_round_trip_share([
        {"dust_round_trips_filtered": 10, "oos_total_trades_with_exit_telemetry": 90},
    ])
    assert result.passed is False  # 10/100 = 10% > max_share Default (0.01)
    assert result.severity == "high"
