"""Issue #953 (Katalog C, P1) — der Plateau-Frühstopp (#925 'expected_yield') approximiert die
Rule-of-Three-Obergrenze p_hi=3/m statt der EXAKTEN einseitigen Clopper-Pearson-Obergrenze
p_hi(t)=1-alpha^(1/t). ``plateau_stop_clopper_pearson``/``plateau_stop_mode='clopper_pearson'``
sind die literaturgetreue, additiv/opt-in verfügbare Umsetzung — der bereits empirisch validierte
Default ('expected_yield', 43% Budget-Ersparnis, #925) bleibt unverändert.
"""
import math

import pytest

from automation.optimizer import run_optimization as ro
from automation.tests.test_issue_806_sequential_plateau_stop import _FakeStudy, _non_eligible_trials


def test_clopper_pearson_formula_matches_exact_definition():
    m, alpha = 48, 0.05
    p_hi, _ = ro.plateau_stop_clopper_pearson(m, 10, alpha=alpha)
    assert p_hi == pytest.approx(1.0 - alpha ** (1.0 / m), rel=1e-12)


def test_clopper_pearson_close_to_but_distinct_from_rule_of_three():
    """Bei alpha=0.05 ist p_hi(t)=1-0.05^(1/t) asymptotisch nahe an 3/t, aber NICHT identisch —
    die exakte Formel ist strikt praeziser (kein geratener Faktor 3)."""
    m = 48
    p_hi_exact, _ = ro.plateau_stop_clopper_pearson(m, 10, alpha=0.05)
    p_hi_approx = 3.0 / m
    assert p_hi_exact != pytest.approx(p_hi_approx, rel=1e-9)
    assert p_hi_exact == pytest.approx(p_hi_approx, rel=0.05)  # aber nahe beieinander


def test_zero_modelled_trials_never_triggers_abort():
    p_hi, yield_ = ro.plateau_stop_clopper_pearson(0, 100)
    assert p_hi == 1.0
    assert yield_ == float("inf")


def test_zero_remaining_budget_is_zero_expected_yield():
    p_hi, yield_ = ro.plateau_stop_clopper_pearson(64, 0)
    assert yield_ == 0.0


def test_expected_yield_is_monotonically_decreasing_in_m():
    """Groesseres m (mehr Evidenz fuer 'kein eligibler Punkt') senkt p_hi und damit den erwarteten
    Restertrag bei festem Restbudget."""
    _, yield_small_m = ro.plateau_stop_clopper_pearson(20, 50)
    _, yield_large_m = ro.plateau_stop_clopper_pearson(200, 50)
    assert yield_large_m < yield_small_m


def test_plateau_stop_mode_clopper_pearson_stops_before_budget_exhaustion():
    n_startup = 16
    n_budget = 100
    plateau_min = 48
    W = {"n_startup_trials": n_startup, "plateau_min_modelled_trials": plateau_min,
        "plateau_stop_mode": "clopper_pearson", "plateau_stop_alpha": 0.05}
    min_for_zero_eligible = n_startup + plateau_min
    stop_trial = None
    for n_completed in range(min_for_zero_eligible, n_budget + 1):
        trials = _non_eligible_trials(n_completed)
        study = _FakeStudy(trials, n_trials_budget=n_budget)
        ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=n_startup,
                                  stop_on_plateau=True)
        if study.stopped:
            stop_trial = n_completed
            break
    assert stop_trial is not None
    assert stop_trial < n_budget - 5  # deutlich vor Budget-Erschoepfung, analog #925


def test_unknown_plateau_stop_mode_still_fails_loud_with_clopper_pearson_in_the_error_message():
    W = {"n_startup_trials": 16, "plateau_stop_mode": "bogus"}
    trials = _non_eligible_trials(116)
    study = _FakeStudy(trials, n_trials_budget=117)
    with pytest.raises(ValueError, match="clopper_pearson"):
        ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                                  stop_on_plateau=True)


def test_expected_yield_mode_default_behavior_is_unchanged():
    """Regressionsschutz: das Hinzufuegen von 'clopper_pearson' aendert NICHTS am validierten
    Default-Verhalten ('expected_yield')."""
    import json
    from automation.optimizer.trial_config import config_dir
    real_cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    assert real_cfg.get("plateau_stop_mode") == "expected_yield"
    assert real_cfg.get("plateau_stop_alpha") == 0.05
