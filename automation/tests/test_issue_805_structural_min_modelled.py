"""Issue #805 — `floor_plateau_k = 0`: der STRUCTURAL_ALL_UNEVALUABLE-Abbruch urteilte weiterhin auf
NULL TPE-modellierten Trials (AdxAtrMomentum verlor 124/124 Studies bei 13,3% Budget, 16 reine
Zufallsziehungen). Dritte Wiederkehr derselben Fehlerklasse: #488 -> #753 -> #769 -> #805.

Root-Cause: `min_for_structural = n_startup_trials + K` mit `K = weights.get('floor_plateau_k', 0)`
— der Key existierte seit #769 nur als explizite Dokumentation des immer schon gesetzten Default 0.
`min_for_structural` kollabierte damit auf EXAKT `n_startup_trials`, die Grenze, ab der
`TPESampler` ueberhaupt erst zu MODELLIEREN beginnt.

Fix: `floor_plateau_k` ENTFERNT, ersetzt durch `structural_min_modelled_trials_per_dim` (Default 3,
strukturgleich zu `plateau_min_modelled_trials_per_dim`/#768): `min_for_structural = n_startup_trials
+ ceil(k · dim)`, gedeckelt auf `n_trials_budget`. Ein explizit gesetzter Wert <= 0 ist ein
Konfigurationsfehler (`ValueError` beim Sweep-Start, analog `confirm.py['promotion_correction_mode']`,
#659) — der additive Zuschlag darf nie wieder 0 sein.
"""
import logging

import optuna
import pytest

from automation.optimizer import run_optimization as ro


# ---------------------------------------------------------------------------
# derive_structural_min_modelled_trials
# ---------------------------------------------------------------------------

def test_default_without_strategy_or_config_is_flat_three():
    assert ro.derive_structural_min_modelled_trials(None, {}) == 3


def test_missing_key_defaults_to_three_with_a_strategy():
    # AdxAtrMomentumStrategy hat dim=6 im echten Suchraum (spaces.py) — Katalog-Beleg #805.
    assert ro.derive_structural_min_modelled_trials("AdxAtrMomentumStrategy", {}) == 18


def test_configured_k_scales_with_dimension():
    opt_data = {"structural_min_modelled_trials_per_dim": 2}
    assert ro.derive_structural_min_modelled_trials("AdxAtrMomentumStrategy", opt_data) == 12


def test_flat_fallback_when_strategy_is_none():
    opt_data = {"structural_min_modelled_trials_per_dim": 4}
    assert ro.derive_structural_min_modelled_trials(None, opt_data) == 4


def test_flat_fallback_when_strategy_unknown_to_bounds():
    opt_data = {"structural_min_modelled_trials_per_dim": 5}
    assert ro.derive_structural_min_modelled_trials("NoSuchStrategyAtAll", opt_data) == 5


def test_explicit_zero_or_negative_k_never_collapses_to_zero_defensively():
    """derive_structural_min_modelled_trials selbst bleibt defensiv (Fallback auf den Default 3),
    falls sie dennoch mit einem ungueltigen Wert aufgerufen wird — die eigentliche Fail-Loud-
    Durchsetzung ist assert_structural_min_modelled_trials_valid (Sweep-Start), nicht diese reine
    Ableitungsfunktion."""
    assert ro.derive_structural_min_modelled_trials(None, {"structural_min_modelled_trials_per_dim": 0}) == 3
    assert ro.derive_structural_min_modelled_trials(
        None, {"structural_min_modelled_trials_per_dim": -5}) == 3


def test_result_is_never_zero_regardless_of_input():
    for k in (0, -1, -100, 0.0):
        assert ro.derive_structural_min_modelled_trials(None, {"structural_min_modelled_trials_per_dim": k}) >= 1


# ---------------------------------------------------------------------------
# assert_structural_min_modelled_trials_valid: FAIL-LOUD beim Sweep-Start
# ---------------------------------------------------------------------------

def test_missing_key_is_not_an_error():
    ro.assert_structural_min_modelled_trials_valid({})  # no raise
    ro.assert_structural_min_modelled_trials_valid({"n_startup_trials": 16})  # no raise


def test_valid_positive_value_is_not_an_error():
    ro.assert_structural_min_modelled_trials_valid({"structural_min_modelled_trials_per_dim": 3})


def test_explicit_zero_raises_value_error():
    """Akzeptanzkriterium #805: floor_plateau_k: 0 (bzw. sein Nachfolger auf 0) bricht den Start
    mit ValueError ab."""
    with pytest.raises(ValueError, match="STRUCTURAL_MIN_MODELLED_TRIALS_DEGENERATE"):
        ro.assert_structural_min_modelled_trials_valid({"structural_min_modelled_trials_per_dim": 0})


def test_explicit_negative_value_raises_value_error():
    with pytest.raises(ValueError, match="STRUCTURAL_MIN_MODELLED_TRIALS_DEGENERATE"):
        ro.assert_structural_min_modelled_trials_valid({"structural_min_modelled_trials_per_dim": -1})


def test_non_numeric_value_raises_value_error():
    with pytest.raises(ValueError, match="STRUCTURAL_MIN_MODELLED_TRIALS_INVALID"):
        ro.assert_structural_min_modelled_trials_valid({"structural_min_modelled_trials_per_dim": "oops"})


def test_real_shipped_config_passes_the_assertion():
    import json
    from automation.optimizer.trial_config import config_dir
    real_cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    ro.assert_structural_min_modelled_trials_valid(real_cfg)  # no raise


# ---------------------------------------------------------------------------
# floor_plateau_callback integration: FakeStudy, stop() bei n=n_startup+3*dim-1 False, bei +3*dim True
# ---------------------------------------------------------------------------

class _FakeTrial:
    def __init__(self, value, *, oos_evaluated=False, is_total_trades=0):
        self.value = value
        self.state = optuna.trial.TrialState.COMPLETE
        self.user_attrs = {"oos_evaluated": oos_evaluated, "is_total_trades": is_total_trades}


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials
        self._attrs = {}
        self.stopped = False

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v

    def stop(self):
        self.stopped = True


def _quiet_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    return lg


def test_no_stop_one_trial_below_the_dimension_scaled_threshold():
    # AdxAtrMomentumStrategy: dim=6, k=3 (Default) ⇒ Zuschlag=18. n_startup_trials=16 ⇒
    # min_for_structural=34. n=33 (eins darunter) darf NICHT stoppen.
    W = {"n_startup_trials": 16}
    trials = [_FakeTrial(-9.85, is_total_trades=0) for _ in range(33)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t805_below"), stop_on_plateau=True,
                              strategy="AdxAtrMomentumStrategy", symbol="AAA.ETORO")
    assert study.stopped is False


def test_stops_exactly_at_the_dimension_scaled_threshold():
    W = {"n_startup_trials": 16}
    trials = [_FakeTrial(-9.85, is_total_trades=0) for _ in range(34)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t805_at"), stop_on_plateau=True,
                              strategy="AdxAtrMomentumStrategy", symbol="AAA.ETORO")
    assert study.stopped is True


def test_old_degenerate_threshold_no_longer_triggers_a_stop():
    """Regression: 16 abgeschlossene Trials (== n_startup_trials, der ALTE min_for_structural bei
    floor_plateau_k=0) duerfen NICHT mehr stoppen — genau das #805-Symptom (0 modellierte Trials)."""
    W = {"n_startup_trials": 16}
    trials = [_FakeTrial(-9.85, is_total_trades=0) for _ in range(16)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t805_old_threshold"), stop_on_plateau=True,
                              strategy="AdxAtrMomentumStrategy", symbol="AAA.ETORO")
    assert study.stopped is False


def test_structural_threshold_is_capped_by_n_trials_budget():
    """Der Guard darf nie NACH dem regulaeren Budget-Ende urteilen (analog #768 fuer
    min_for_zero_eligible) — ein Budget von 20 Trials deckelt die dimensionsskalierte Schwelle
    (34) auf 20."""
    W = {"n_startup_trials": 16}
    trials = [_FakeTrial(-9.85, is_total_trades=0) for _ in range(20)]
    study = _FakeStudy(trials)
    study.set_user_attr("n_trials_budget", 20)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t805_budget_cap"), stop_on_plateau=True,
                              strategy="AdxAtrMomentumStrategy", symbol="AAA.ETORO")
    assert study.stopped is True
