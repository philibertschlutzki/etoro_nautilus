"""Issue #925 (Pitfall #300) — der 'missed_probability'-Plateau-Stopp (#806) kann per Konstruktion
hoechstens ~1,43 % des Trial-Budgets einsparen: sein Risikoterm enthaelt das gesparte Restbudget
selbst im Nenner (die Wahrscheinlichkeit, dass ein Weiterlaufen noch etwas findet, STEIGT mit dem
Restbudget r — der Nutzen des Abbruchs erscheint dadurch als Risiko, nicht als Ertrag). Fix:
'expected_yield' (jetzt Default) verlangt Abbruch, sobald der ERWARTETE Ertrag des Restbudgets
(p_hi * r) unter eine Schwelle faellt — das trennt Ertrag und Risiko und erlaubt einen Abbruch weit
vor Budget-Erschoepfung.
"""
import math

import optuna
import pytest

from automation.optimizer import run_optimization as ro
from automation.tests.test_issue_806_sequential_plateau_stop import _FakeStudy, _non_eligible_trials


# ---------------------------------------------------------------------------
# plateau_stop_expected_yield: reine Funktion, Referenztabelle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("m, r, expected_p_hi, expected_yield", [
    (48, 8, 3 / 48, (3 / 48) * 8),
    (100, 1, 0.03, 0.03),
    (20, 80, 0.15, 12.0),
])
def test_expected_yield_matches_reference_table(m, r, expected_p_hi, expected_yield):
    p_hi, yield_ = ro.plateau_stop_expected_yield(m, r)
    assert p_hi == pytest.approx(expected_p_hi, rel=1e-9)
    assert yield_ == pytest.approx(expected_yield, rel=1e-9)


def test_zero_modelled_trials_never_triggers_abort():
    p_hi, yield_ = ro.plateau_stop_expected_yield(0, 100)
    assert p_hi == 1.0
    assert yield_ == float("inf")


def test_zero_remaining_budget_is_zero_expected_yield():
    p_hi, yield_ = ro.plateau_stop_expected_yield(64, 0)
    assert p_hi == pytest.approx(3 / 64)
    assert yield_ == 0.0


# ---------------------------------------------------------------------------
# Geschlossene Form: r < m/6 (bei plateau_stop_min_expected_eligible=0.5, p_hi=3/m)
# ---------------------------------------------------------------------------

def test_closed_form_bound_r_less_than_m_over_six():
    """expected_yield < 0.5  ⟺  3r/m < 0.5  ⟺  r < m/6. Der Test leitet die Schranke aus der
    Formel her (nicht aus einer eingefrorenen Konstante) und verifiziert sie an mehreren (m, r)."""
    for m in (24, 48, 96, 300):
        boundary = m / 6.0
        r_just_below = max(1, int(boundary) - 1)
        r_just_above = int(boundary) + 1
        _, yield_below = ro.plateau_stop_expected_yield(m, r_just_below)
        _, yield_above = ro.plateau_stop_expected_yield(m, r_just_above)
        assert yield_below < 0.5, f"m={m}, r={r_just_below} sollte unter der Schwelle liegen"
        assert yield_above >= 0.5, f"m={m}, r={r_just_above} sollte ueber der Schwelle liegen"


def test_expected_yield_mode_stops_meaningfully_before_budget_exhaustion():
    """N=100, n_startup=16, plateau_min_modelled_trials=48, 0 eligible Trials: der Stopp feuert
    deutlich vor Budget-Erschoepfung (t=99 unter 'missed_probability', siehe test_issue_806).

    Die geschlossene Form r < m/6 mit m = t - n_startup, r = N - t loest exakt nach
    t > (6N + n_startup) / 7 auf — mit N=100, n_startup=16: t > 88 ⇒ Stopp bei t=89. Dieser Test
    verifiziert die tatsaechliche Kreuzungsstelle GEGEN DIESE HERGELEITETE FORMEL (kein
    eingefrorenes Literal) und dass sie klar VOR t=99 liegt (siehe
    test_closed_form_bound_r_less_than_m_over_six fuer die reine r-vs-m-Beziehung)."""
    n_startup = 16
    n_budget = 100
    plateau_min = 48
    W = {"n_startup_trials": n_startup, "plateau_min_modelled_trials": plateau_min}
    min_for_zero_eligible = n_startup + plateau_min
    expected_crossover = math.floor((6 * n_budget + n_startup) / 7.0) + 1
    stop_trial = None
    for n_completed in range(min_for_zero_eligible, n_budget + 1):
        trials = _non_eligible_trials(n_completed)
        study = _FakeStudy(trials, n_trials_budget=n_budget)
        ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=n_startup,
                                  stop_on_plateau=True)
        if study.stopped:
            stop_trial = n_completed
            break
    assert stop_trial is not None, "Der expected_yield-Stopp haette vor Budget-Erschoepfung feuern muessen"
    assert stop_trial == expected_crossover, (
        f"Stopp bei Trial {stop_trial}, aus der geschlossenen Form hergeleitet: "
        f"{expected_crossover}")
    assert stop_trial < n_budget - 5, (
        "Der neue Stopp muss klar vor Budget-Erschoepfung feuern (missed_probability haette "
        "hier erst bei ~99 gefeuert, siehe test_issue_806)")


def test_missed_probability_mode_remains_available_and_bit_identical():
    """plateau_stop_mode='missed_probability' reproduziert weiterhin das Pre-#925-Verhalten
    (Reproduktionslaeufe bleiben deterministisch vergleichbar)."""
    W = {"n_startup_trials": 16, "plateau_stop_mode": "missed_probability"}
    trials = _non_eligible_trials(116)  # 16 startup + 100 modelliert
    study = _FakeStudy(trials, n_trials_budget=117)  # Restbudget = 1
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              stop_on_plateau=True)
    assert study.stopped is True


def test_unknown_plateau_stop_mode_is_fail_loud():
    W = {"n_startup_trials": 16, "plateau_stop_mode": "bogus"}
    trials = _non_eligible_trials(116)
    study = _FakeStudy(trials, n_trials_budget=117)
    with pytest.raises(ValueError, match="plateau_stop_mode"):
        ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                                  stop_on_plateau=True)


def test_real_shipped_config_defaults_to_expected_yield():
    import json
    from automation.optimizer.trial_config import config_dir
    real_cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    assert real_cfg.get("plateau_stop_mode") == "expected_yield"
    assert real_cfg.get("plateau_stop_min_expected_eligible") == 0.5
