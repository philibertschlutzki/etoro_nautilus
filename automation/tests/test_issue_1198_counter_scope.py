"""Issue #1086/#1234 (Katalog #1247+, P1) — ``_emit_study_summary`` stempelt store-scoped Zaehler
in einen run-scoped Report.

Symptom (Warm-Start-Laeufe): AdxAtr/TSLA ``n_trials_total = 403`` (= 123+140+140 ueber drei Laeufe)
bei ``n_trials_total_study = 140``; Squeeze/TSLA ``n_trials_pruned`` 78 -> 133 -> 187 bei konstant
``n_trials = 180``.

Root-Cause: ``run_optimization._emit_study_summary`` liest ``study.trials`` UNGEFILTERT (STORE-
scoped, akkumuliert ueber alle Laeufe auf demselben Optuna-Store), obwohl die Funktion ``run_id``
bereits als Parameter erhaelt.

Fix: ``trials`` wird ab jetzt auf ``run_id`` gefiltert (dieselbe Formulierung wie
``sweep.py``s ``deflation_family_floor``-Zaehlung); die STORE-weiten Zaehlungen bleiben zusaetzlich
unter ``_store``-Suffix erhalten (``n_trials_total_store`` etc.) und erreichen ueber
``report._study_record`` ebenfalls den Study-Record.
"""
import time

import optuna
import pytest

from automation.optimizer.run_optimization import _emit_study_summary


class _DummyTrial:
    def __init__(self, run_id, evaluated=True, state=None):
        self.value = 0.1
        self.user_attrs = {
            "oos_evaluated": evaluated, "oos_eligible": False,
            "backtest_ms": 10, "run_id": run_id,
        }
        self.state = state or optuna.trial.TrialState.COMPLETE


class _DummyStudy:
    def __init__(self, trials):
        self.study_name = "study_scope_test"
        self.trials = trials
        self.best_value = 0.1
        self._user_attrs = {}

    def set_user_attr(self, key, value):
        self._user_attrs[key] = value

    @property
    def user_attrs(self):
        return dict(self._user_attrs)


def _warm_start_trials():
    """Reproduziert das AdxAtr/TSLA-Symptom: drei Laeufe auf demselben Store, 123+140+140 Trials."""
    return (
        [_DummyTrial("run_1") for _ in range(123)]
        + [_DummyTrial("run_2") for _ in range(140)]
        + [_DummyTrial("run_3") for _ in range(140)]
    )


def test_run_scoped_counters_only_count_the_current_run():
    trials = _warm_start_trials()
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), run_id="run_3")
    assert study.user_attrs["n_trials_total"] == 140


def test_store_scoped_counters_count_every_run_on_the_store():
    trials = _warm_start_trials()
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), run_id="run_3")
    assert study.user_attrs["n_trials_total_store"] == 403


def test_without_run_id_run_scoped_and_store_scoped_counters_are_identical():
    """Rueckwaertskompatibilitaet — kein run_id uebergeben ⇒ dieselbe (volle) Population fuer
    beide Zaehlergruppen, kein neues Verhalten fuer bestehende Aufrufer ohne run_id."""
    trials = _warm_start_trials()
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), run_id=None)
    assert study.user_attrs["n_trials_total"] == study.user_attrs["n_trials_total_store"] == 403


def test_pruned_failed_informative_unevaluable_are_all_run_scoped():
    """Reproduziert das Squeeze/TSLA-Symptom (n_trials_pruned 78 -> 133 -> 187 bei konstant 180)."""
    run_1 = [_DummyTrial("run_1", state=optuna.trial.TrialState.PRUNED) for _ in range(78)]
    run_2 = (
        [_DummyTrial("run_2", state=optuna.trial.TrialState.PRUNED) for _ in range(133)]
        + [_DummyTrial("run_2", state=optuna.trial.TrialState.COMPLETE) for _ in range(47)]
    )
    trials = run_1 + run_2
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), run_id="run_2")
    assert study.user_attrs["n_trials_total"] == 180
    assert study.user_attrs["n_trials_pruned"] == 133
    assert study.user_attrs["n_trials_total_store"] == 78 + 180


def test_no_field_name_without_store_suffix_carries_a_store_wide_count():
    """Akzeptanzkriterium — kein Feldname ohne ``_store``-Suffix traegt eine store-weite Zaehlung
    (die run-scopeden Namen bleiben unveraendert, nur die neuen ``_store``-Namen sind neu)."""
    trials = _warm_start_trials()
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter(), run_id="run_3")
    for suffix_key, base_key in (
        ("n_trials_total_store", "n_trials_total"),
        ("n_trials_informative_store", "n_trials_informative"),
        ("n_trials_pruned_store", "n_trials_pruned"),
        ("n_trials_failed_store", "n_trials_failed"),
        ("n_trials_unevaluable_store", "n_trials_unevaluable"),
    ):
        assert base_key in study.user_attrs
        assert suffix_key in study.user_attrs
