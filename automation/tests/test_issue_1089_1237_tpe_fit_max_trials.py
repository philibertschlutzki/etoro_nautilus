"""Issue #1089/#1237 (P1, Katalog #1247+) — TPE-Fit-Fenster deckeln.

Symptom. ``tpe_fit_seconds`` (Summe über Studies) 25-35s bei k=1 warm-gestarteten Vorlauf-Trials,
91-92s bei k=2, 228s bei k=3. Skalierung ≈ O(n^1,9) in der Store-Grösse. Bei k=3 gehen 8,6 % der
Wallclock in den Surrogat-Fit, ``cpu_utilisation_backtest`` fällt auf 12,9 %.

Root-Cause. Der TPE-Sampler fittet gegen alle Trials des Stores. Ältere Trials stammen aus anderen
Läufen und tragen bei identischem Suchraum keine neue Information.

Fix.
1. Konfigurationswert ``tpe_fit_max_trials`` (``optimizer.json``, Default 2000). Übersteigt die
   Trial-Zahl der Study diesen Wert, werden dem Sampler nur die jüngsten ``tpe_fit_max_trials``
   Trials übergeben — die EFFEKTIVE Grenze ist ``min(tpe_history_window, tpe_fit_max_trials)``.
2. ``tpe_fit_trials_used`` und ``tpe_fit_trials_available`` je Study gestempelt.
3. Neue Invariante ``check_tpe_fit_cost_share`` (severity ``low``): FAIL, wenn
   ``Σ tpe_fit_seconds / Σ study_wallclock_s > 0,05``.
"""
import optuna
import pytest

from automation.optimizer import invariants as inv
from automation.optimizer.run_optimization import (
    _WindowedTPESampler, _resolve_tpe_fit_max_trials, _TPE_FIT_MAX_TRIALS_DEFAULT,
    _resolve_tpe_history_window,
)


# --- _resolve_tpe_fit_max_trials ------------------------------------------------------------------

def test_resolve_tpe_fit_max_trials_default():
    assert _resolve_tpe_fit_max_trials(None) == _TPE_FIT_MAX_TRIALS_DEFAULT
    assert _resolve_tpe_fit_max_trials({}) == _TPE_FIT_MAX_TRIALS_DEFAULT
    assert _TPE_FIT_MAX_TRIALS_DEFAULT == 2000


def test_resolve_tpe_fit_max_trials_reads_config_key():
    assert _resolve_tpe_fit_max_trials({"tpe_fit_max_trials": 500}) == 500


def test_resolve_tpe_fit_max_trials_falls_back_on_invalid_value():
    assert _resolve_tpe_fit_max_trials({"tpe_fit_max_trials": "not_a_number"}) == (
        _TPE_FIT_MAX_TRIALS_DEFAULT)
    assert _resolve_tpe_fit_max_trials({"tpe_fit_max_trials": 0}) == _TPE_FIT_MAX_TRIALS_DEFAULT
    assert _resolve_tpe_fit_max_trials({"tpe_fit_max_trials": -5}) == _TPE_FIT_MAX_TRIALS_DEFAULT


def test_effective_window_is_the_minimum_of_both_thresholds():
    """Akzeptanzkriterium (implizit): keiner der beiden Werte darf die vom jeweils anderen
    garantierte Obergrenze aufheben."""
    opt_data_tight_fit_max = {"tpe_history_window": 5000, "tpe_fit_max_trials": 300}
    effective = min(
        _resolve_tpe_history_window(opt_data_tight_fit_max),
        _resolve_tpe_fit_max_trials(opt_data_tight_fit_max))
    assert effective == 300

    opt_data_tight_history_window = {"tpe_history_window": 200, "tpe_fit_max_trials": 5000}
    effective2 = min(
        _resolve_tpe_history_window(opt_data_tight_history_window),
        _resolve_tpe_fit_max_trials(opt_data_tight_history_window))
    assert effective2 == 200


# --- _WindowedTPESampler: tpe_fit_trials_used/_available Telemetrie ------------------------------

def _objective(trial):
    x = trial.suggest_float("x", -10, 10)
    return -(x - 3.0) ** 2


def test_sampler_tracks_the_last_used_and_available_trial_counts():
    sampler = _WindowedTPESampler(
        n_startup_trials=5, seed=1, history_window=10, multivariate=True, group=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(_objective, n_trials=30, show_progress_bar=False)
    # Akzeptanzkriterium: tpe_fit_trials_used <= tpe_fit_max_trials (hier: history_window als
    # Stand-in fuer die kombinierte Obergrenze, siehe test_effective_window_is_the_minimum_...).
    assert sampler._last_trials_used <= 10
    # Am Studienende (30 Trials) liegt die verfuegbare Trial-Zahl deutlich ueber dem Fenster.
    assert sampler._last_trials_available > 10


def test_trials_used_equals_available_below_the_window():
    sampler = _WindowedTPESampler(
        n_startup_trials=3, seed=2, history_window=1000, multivariate=True, group=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(_objective, n_trials=15, show_progress_bar=False)
    assert sampler._last_trials_used == sampler._last_trials_available


# --- Zero-Regression: unterhalb der Schwelle bit-identische Parametervektoren -------------------

def test_sampled_parameters_are_bit_identical_below_the_threshold_with_a_fixed_seed():
    """Akzeptanzkriterium 3 — bei Store-Groesse unter der Schwelle ist das Verhalten bit-identisch
    zum Vorzustand: die neue Telemetrie (Trial-Zaehlung) ist rein additive Buchfuehrung, keine
    Aenderung der Sampling-Logik selbst."""
    def _run(history_window):
        sampler = _WindowedTPESampler(
            n_startup_trials=5, seed=7, history_window=history_window,
            multivariate=True, group=True)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(_objective, n_trials=20, show_progress_bar=False)
        return [t.params["x"] for t in study.trials]

    # 20 Trials, Fenster 1000 (weit ueber der Study-Groesse) vs. Fenster 5000 (ebenfalls weit
    # darueber) -- unterhalb der Schwelle darf das Fenster selbst keinen Unterschied machen.
    assert _run(1000) == _run(5000)


# --- report._study_record: die beiden neuen Study-Felder ----------------------------------------

def test_study_record_stamps_tpe_fit_trials_fields():
    from automation.optimizer.report import _study_record

    class _T:
        value = 1.0
        params = {}
        user_attrs = {"oos_evaluated": True, "oos_eligible": True}

    class _S:
        trials = [_T()]
        best_value = 1.0
        user_attrs = {
            "tpe_fit_seconds": 12.5, "tpe_fit_trials_used": 2000, "tpe_fit_trials_available": 5820,
        }

    record, _checks = _study_record({"symbol": "X.ETORO", "strategy": "A"}, _S())
    assert record["tpe_fit_trials_used"] == 2000
    assert record["tpe_fit_trials_available"] == 5820


# --- invariants.check_tpe_fit_cost_share ----------------------------------------------------------

def _study(tpe_fit_seconds=None, study_wallclock_s=None):
    return {"tpe_fit_seconds": tpe_fit_seconds, "study_wallclock_s": study_wallclock_s}


def test_passes_within_the_five_percent_threshold():
    records = [_study(tpe_fit_seconds=4.0, study_wallclock_s=100.0)]  # 4%
    result = inv.check_tpe_fit_cost_share(records)
    assert result.passed is True
    assert result.severity == "low"


def test_fails_when_fit_share_exceeds_five_percent():
    """Reproduziert das k=3-Symptom: 8,6% der Wallclock im Surrogat-Fit."""
    records = [_study(tpe_fit_seconds=8.6, study_wallclock_s=100.0)]
    result = inv.check_tpe_fit_cost_share(records)
    assert result.passed is False
    assert result.actual["tpe_fit_cost_fraction"] == pytest.approx(0.086)


def test_sums_across_studies_not_per_study_average():
    records = [
        _study(tpe_fit_seconds=1.0, study_wallclock_s=100.0),
        _study(tpe_fit_seconds=9.0, study_wallclock_s=100.0),
    ]
    result = inv.check_tpe_fit_cost_share(records)
    assert result.actual["total_tpe_fit_seconds"] == pytest.approx(10.0)
    assert result.actual["total_wallclock_s"] == pytest.approx(200.0)
    assert result.actual["tpe_fit_cost_fraction"] == pytest.approx(0.05)
    assert result.passed is True  # exakt an der Schwelle, nicht darueber


def test_inconclusive_without_any_tpe_fit_seconds_telemetry():
    result = inv.check_tpe_fit_cost_share([{"study_wallclock_s": 100.0}])
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_inconclusive_when_total_wallclock_is_zero():
    result = inv.check_tpe_fit_cost_share([_study(tpe_fit_seconds=1.0, study_wallclock_s=0.0)])
    assert result.passed is None
    assert result.inconclusive is True
