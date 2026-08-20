"""Issue #1050/#1199 (P0, Katalog #1196-1221) — ``deflation_n_family_raw`` skaliert mit der Zahl
der Sweep-Läufe / ``n_evaluated > n_trials`` im Plateau-Zähler.

Symptom: ``check_counter_partition_consistency``, ``c429c992``:
``AdxAtrMomentum/NVDA {'n_trials': 36, 'n_evaluated': 176, 'breakdown_sum': 0}`` — der ausgewertete
Anteil ist 4,9x die Trial-Zahl.

Root-Cause: ``run_optimization.floor_plateau_callback`` stempelte ``plateau_n_evaluated``/
``plateau_counter_breakdown`` bislang AUSSCHLIESSLICH aus ``completed`` (STORE-SCOPED, ``study.
get_trials``/``study.trials`` — die GESAMTE Study-Historie über alle ``run_id``s hinweg), während
``invariants.check_counter_partition_consistency`` sie gegen ``n_trials`` (RUN-SCOPED,
``report._study_record``) prüfte — Zähler und Nenner derselben Identität mit verschiedenem Scope
(Pitfall #430-Klasse).

Fix:
1. ``floor_plateau_callback`` stempelt zusätzlich RUN-SCOPED Gegenstücke
   (``plateau_n_evaluated_run``/``plateau_counter_breakdown_run``, aus ``completed_run`` — nur
   Trials mit ``run_id``-Stempel == diesem Lauf); die store-scoped Felder bleiben UNVERÄNDERT (sie
   tragen die tatsächliche Plateau-Stop-Entscheidung).
2. ``check_counter_partition_consistency`` konsumiert seither ausschliesslich die ``_run``-Variante.
3. ``breakdown_sum == 0`` bei gefülltem ``reconstructed_total`` trägt jetzt den eigenen Grund
   ``BREAKDOWN_NOT_POPULATED`` statt in der generischen Partitionsverletzung unterzugehen."""
import optuna
import pytest

from automation.optimizer.run_optimization import floor_plateau_callback
from automation.optimizer import invariants as inv


class _FakeTrial:
    def __init__(self, value, *, run_id=None, oos_evaluated=True, oos_eligible=False,
                 state=optuna.trial.TrialState.COMPLETE):
        self.value = value
        self.state = state
        self.user_attrs = {"oos_evaluated": oos_evaluated, "oos_eligible": oos_eligible}
        if run_id is not None:
            self.user_attrs["run_id"] = run_id


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials
        self._attrs = {}

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v

    def stop(self):
        pass


def test_run_scoped_plateau_counters_are_narrower_than_store_scoped(caplog):
    """Reproduziert die #1050-Signatur konstruktiv: eine Study mit Trials aus ZWEI warm-gestarteten
    Läufen (5 eigene, 5 fremde) -- die store-scoped Zähler sehen alle 10, die run-scoped Zähler
    NUR die 5 eigenen."""
    weights = {"n_startup_trials": 2, "plateau_min_modelled_trials": 3}
    own_trials = [_FakeTrial(-1.0, run_id="run_current") for _ in range(5)]
    foreign_trials = [_FakeTrial(-1.0, run_id="run_prior") for _ in range(5)]
    study = _FakeStudy(own_trials + foreign_trials)

    floor_plateau_callback(study, study.trials[-1], weights=weights, n_startup_trials=2,
                           run_id="run_current")

    attrs = study.user_attrs
    assert attrs.get("plateau_n_evaluated") == 10, "Store-scoped bleibt UNVERAENDERT (volle Historie)"
    assert attrs.get("plateau_n_evaluated_run") == 5, "Run-scoped zaehlt NUR die eigenen Trials"
    breakdown_store = attrs.get("plateau_counter_breakdown")
    breakdown_run = attrs.get("plateau_counter_breakdown_run")
    assert breakdown_store is not None and breakdown_run is not None
    # Beide Zerlegungen sind hier strukturell 0 (kein is_rejection_detail auf den Fake-Trials) --
    # der Scope-Unterschied zeigt sich in n_evaluated, nicht in der Zerlegung selbst.
    assert sum(breakdown_run.values()) == 0


def test_run_scoped_counters_are_reported_scope_consistent_with_n_trials():
    """End-to-end: n_trials (run-scoped, wie im Report) UND plateau_n_evaluated_run (jetzt ebenso
    run-scoped) muessen sich zu genau n_trials summieren, wenn keine Trials entfernt wurden."""
    weights = {"n_startup_trials": 2, "plateau_min_modelled_trials": 3}
    own_trials = [_FakeTrial(-1.0, run_id="run_current") for _ in range(6)]
    foreign_trials = [_FakeTrial(-1.0, run_id="run_prior") for _ in range(30)]
    study = _FakeStudy(own_trials + foreign_trials)

    floor_plateau_callback(study, study.trials[-1], weights=weights, n_startup_trials=2,
                           run_id="run_current")

    attrs = study.user_attrs
    result = inv.check_counter_partition_consistency([{
        "strategy": "AdxAtrMomentumStrategy", "symbol": "NVDA.ETORO",
        "n_trials": 6,  # dieselbe run-scoped Zahl wie len(own_trials)
        "plateau_n_evaluated_run": attrs["plateau_n_evaluated_run"],
        "plateau_counter_breakdown_run": attrs["plateau_counter_breakdown_run"],
    }])
    assert result.passed is True, (
        f"n_trials=6 sollte mit dem run-scoped Zaehler (5,9x kleiner als store-scoped 36) "
        f"uebereinstimmen: {result.actual}"
    )


def test_store_scoped_only_record_is_not_applicable_to_the_fixed_check():
    """Ein Study-Record, der (Pre-Fix-Report oder ein Legacy-Consumer) nur die STORE-SCOPED Felder
    traegt, darf den Check nicht mehr fuettern -- er zaehlt als "keine run-scoped Telemetrie", nicht
    als faelschlich abgeglichene Store-Zahl."""
    result = inv.check_counter_partition_consistency([{
        "strategy": "AdxAtrMomentumStrategy", "symbol": "NVDA.ETORO", "n_trials": 36,
        "plateau_n_evaluated": 176,
        "plateau_counter_breakdown": {"invalidated_timebox": 0, "invalidated_trade_cap": 0,
                                      "discarded_is_gate": 0, "window_unreachable": 0,
                                      "not_evaluated": 0},
    }])
    assert result.passed is True
    assert result.actual is None


def test_partition_mismatch_reason_when_breakdown_is_populated_but_still_disagrees():
    """Gegenprobe zu BREAKDOWN_NOT_POPULATED: wenn die Zerlegung NICHT leer ist, aber die Summe
    trotzdem nicht auf n_trials aufgeht, ist der Grund PARTITION_MISMATCH (eine echte
    Zaehler-Divergenz), nicht BREAKDOWN_NOT_POPULATED."""
    result = inv.check_counter_partition_consistency([{
        "strategy": "S", "symbol": "X", "n_trials": 20, "plateau_n_evaluated_run": 5,
        "plateau_counter_breakdown_run": {"invalidated_timebox": 3, "invalidated_trade_cap": 0,
                                          "discarded_is_gate": 0, "window_unreachable": 0,
                                          "not_evaluated": 0},  # 5+3=8 != 20
    }])
    assert result.passed is False
    assert result.actual["S/X"]["reason"] == "PARTITION_MISMATCH"
