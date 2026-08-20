"""Issue #1053/#1202 (P1, Katalog #1196-1221) — Blockierendes Verdikt driftet mit dem Warm-Start.

Symptom (B-7): ``check_effective_stop_distance`` FAILte ueber vier TSLA-Laeufe auf identischer
Datenlage mal, mal nicht, mit wechselnden Offendern und Werten 10,2049-13,3619. Empirisch bestaetigt
(``logs/run_*.json``): ``atr_trailing_multiplier_median`` driftete zwischen vier identisch
konfigurierten TSLA-Laeufen von 0,84 bis 1,42 bei GLEICHER Trial-Zahl (120/100).

Root-Cause: der Quotient wurde aus ZWEI UNABHAENGIG ueber die GESAMTE Study gemittelten Groessen
gebildet -- Nenner (``atr_trailing_multiplier_median · atr_median_bps``) ueber ALLE Trials
unbedingt, Zaehler (``gross_loss_median_bps_trailing_stop``) NUR ueber Trials MIT mindestens einem
Stop-Exit. Zusaetzlich exploriert Optunas TPE-Sampler in jedem Re-Run unterschiedliche Punkte im
Multiplikator-Raum.

Fix:
1. ``report._effective_stop_ratio_for_trial`` -- Zaehler UND Nenner IMMER auf demselben Trial.
2. ``report._effective_stop_ratio_cohort`` -- Median dieser PER-TRIAL-Ratio ueber die "eligible
   Kohorte" (Trials mit >= 3 nachweislichen TRAILING_STOP-Exits IN DIESEM Trial), gespeichert als
   ``effective_stop_ratio_cohort_median``/``effective_stop_ratio_cohort_n``.
3. ``winner_effective_stop_ratio`` -- der GEWINNER-Wert (bestbewerteter Trial) bleibt SEPARAT
   telemetriert, fliesst aber NICHT mehr in das Verdikt ein.
4. ``invariants.check_effective_stop_distance`` konsumiert die Kohorten-Felder PRIMAER (mit
   Fallback auf die alte Berechnung fuer Legacy-Records ohne die neuen Felder).

Akzeptanzkriterien: zwei Laeufe auf demselben Store und Symbol liefern dasselbe ``passed``; Delta
des Kohorten-Medians < 5 %.
"""
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


# --- report._effective_stop_ratio_for_trial / _effective_stop_ratio_cohort ------------------------

def _trial(k, atr, loss, n_stop_losses):
    return {
        "sampled_params": {"atr_trailing_multiplier": k},
        "oos_atr_median_bps": atr,
        "oos_gross_loss_mean_bps_trailing_stop": loss,
        "oos_n_trailing_stop_losses": n_stop_losses,
    }


def test_ratio_for_trial_uses_the_same_trials_own_k_atr_and_loss():
    ratio = rpt._effective_stop_ratio_for_trial(_trial(2.0, 10.0, 30.0, 5))
    assert ratio == pytest.approx(1.5)


def test_ratio_for_trial_is_none_when_any_component_missing():
    assert rpt._effective_stop_ratio_for_trial(
        {"sampled_params": {}, "oos_atr_median_bps": 10.0,
         "oos_gross_loss_mean_bps_trailing_stop": 30.0}) is None
    assert rpt._effective_stop_ratio_for_trial(
        {"sampled_params": {"atr_trailing_multiplier": 2.0},
         "oos_gross_loss_mean_bps_trailing_stop": 30.0}) is None
    assert rpt._effective_stop_ratio_for_trial(
        {"sampled_params": {"atr_trailing_multiplier": 2.0}, "oos_atr_median_bps": 10.0}) is None


def test_ratio_for_trial_is_none_for_non_positive_denominator():
    assert rpt._effective_stop_ratio_for_trial(_trial(0.0, 10.0, 30.0, 5)) is None
    assert rpt._effective_stop_ratio_for_trial(_trial(-1.0, 10.0, 30.0, 5)) is None


def test_cohort_excludes_trials_below_the_per_trial_stop_exit_floor():
    """Ein Trial mit nur 2 nachweislichen Stop-Exits (< 3) traegt keinen robusten mittleren
    Stop-Verlust und darf die Kohorte nicht verwaessern."""
    trials = [_trial(2.0, 10.0, 30.0, 5), _trial(2.0, 10.0, 30.0, 5), _trial(2.0, 10.0, 30.0, 5),
              _trial(5.0, 10.0, 999.0, 2)]  # ausgeschlossen (n_stop_losses=2 < 3)
    median, n = rpt._effective_stop_ratio_cohort(trials)
    assert median == pytest.approx(1.5)
    assert n == 3


def test_cohort_is_none_without_any_eligible_trial():
    median, n = rpt._effective_stop_ratio_cohort([_trial(2.0, 10.0, 30.0, 1)])
    assert median is None
    assert n == 0


def test_cohort_holds_numerator_and_denominator_on_the_same_trial_even_under_sampler_drift():
    """Der Kern des #1202-Fixes: obwohl die Trials sehr unterschiedliche k-Werte sampeln (wie ein
    TPE-Sampler es zwischen Re-Runs tut), bleibt jede Ratio intern konsistent (Zaehler/Nenner
    IMMER vom selben Trial) -- der Median ist robust gegen die Streuung der k-Werte selbst."""
    trials = [
        _trial(0.5, 10.0, 15.0, 5),   # ratio 3.0
        _trial(3.0, 10.0, 90.0, 5),   # ratio 3.0
        _trial(1.5, 10.0, 45.0, 5),   # ratio 3.0
    ]
    median, n = rpt._effective_stop_ratio_cohort(trials)
    assert median == pytest.approx(3.0)
    assert n == 3


# --- invariants.check_effective_stop_distance: primaerer Kohorten-Pfad ----------------------------

def _study(strategy, symbol, *, cohort_median, cohort_n, winner_ratio=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "effective_stop_ratio_cohort_median": cohort_median,
        "effective_stop_ratio_cohort_n": cohort_n,
        "winner_effective_stop_ratio": winner_ratio,
    }


def test_cohort_path_passes_within_band():
    result = inv.check_effective_stop_distance([
        _study("A", "X.ETORO", cohort_median=1.5, cohort_n=10, winner_ratio=0.2),
    ])
    assert result.passed is True
    assert result.actual["A/X.ETORO"]["ratio_median"] == 1.5


def test_winner_ratio_is_telemetried_but_does_not_drive_the_verdict():
    """Der Gewinner-Wert liegt AUSSERHALB beider Schranken (0.1 < min_ratio), der Kohorten-Median
    liegt IM Band -- das Verdikt folgt der Kohorte, nicht dem Gewinner (Akzeptanzkriterium #1202:
    ``winner_effective_stop_ratio`` ist Telemetrie, kein Veto)."""
    result = inv.check_effective_stop_distance([
        _study("A", "X.ETORO", cohort_median=1.5, cohort_n=10, winner_ratio=0.05),
    ])
    assert result.passed is True
    assert result.actual["A/X.ETORO"]["winner_effective_stop_ratio"] == 0.05


def test_cohort_path_fails_low_and_high_offenders():
    result = inv.check_effective_stop_distance([
        _study("Low", "X.ETORO", cohort_median=0.1, cohort_n=10),
        _study("High", "Y.ETORO", cohort_median=15.0, cohort_n=10),
    ])
    assert result.passed is False
    assert "Low/X.ETORO" in result.detail or "0.1" in str(result.actual)
    assert result.actual["Low/X.ETORO"]["ratio_median"] == 0.1
    assert result.actual["High/Y.ETORO"]["ratio_median"] == 15.0


def test_cohort_n_below_min_cohort_trials_is_inconclusive_not_a_pass():
    result = inv.check_effective_stop_distance([
        _study("A", "X.ETORO", cohort_median=1.5, cohort_n=2),
    ], min_cohort_trials=5)
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_two_runs_with_identical_cohort_data_yield_identical_verdicts():
    """Akzeptanzkriterium #1202: zwei Laeufe auf demselben Store/Symbol mit identischer Kohorte
    liefern dasselbe passed -- eine reine Funktion derselben Eingabedaten ist per Konstruktion
    deterministisch, das ist hier direkt nachweisbar (kein Sampler-Seed/Zufall im Check selbst)."""
    run_1 = inv.check_effective_stop_distance([
        _study("MeanReversionStrategy", "TSLA.ETORO", cohort_median=2.5, cohort_n=12),
    ])
    run_2 = inv.check_effective_stop_distance([
        _study("MeanReversionStrategy", "TSLA.ETORO", cohort_median=2.5, cohort_n=12),
    ])
    assert run_1.passed == run_2.passed
    assert run_1.actual == run_2.actual


# --- Legacy-Fallback: Records ohne die neuen #1202-Kohorten-Felder --------------------------------

def test_legacy_records_without_cohort_fields_fall_back_to_the_old_computation():
    """Ein Record OHNE die neuen Felder (z. B. ein Legacy-Report vor #1202) faellt auf die
    unveraenderte Vor-#1202-Berechnung zurueck, statt INCONCLUSIVE zu werden."""
    legacy = {
        "strategy": "A", "symbol": "X.ETORO",
        "atr_median_bps": 20.0, "atr_trailing_multiplier_median": 2.0,
        "gross_loss_median_bps_trailing_stop": 16.0,  # ratio == 0.4 (min bound)
        "oos_n_trailing_stop_losses": 40,
    }
    result = inv.check_effective_stop_distance([legacy])
    assert result.passed is True
    assert result.actual["A/X.ETORO"]["ratio_median"] == 0.4
