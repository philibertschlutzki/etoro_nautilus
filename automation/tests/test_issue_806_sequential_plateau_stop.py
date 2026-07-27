"""Issue #806 — die ZERO_ELIGIBLE-Plateau-Schwelle `n_startup + 8·dim` war eine frei gewählte
Konstante ohne Fehlermodell: 30,4 % des konfigurierten Suchbudgets blieben ohne statistische
Begründung ungenutzt (flaches Plateau bei 53-64 %, konvergiert für grosses dim gegen
(3·dim + 8·dim)/(20·dim) = 55 %).

Fix: ein sequentielles Kriterium (Rule of Three) ersetzt die feste Trialzahl als eigentliche
Abbruch-Entscheidung. Bei 0 eligiblen aus `m` modellierten Trials ist die obere 95-%-Konfidenzgrenze
der Eligibility-Rate `p_hi = 3/m`; mit Restbudget `r` ist
`P(mindestens 1 eligibler Trial im Rest) ≈ 1 − (1 − p_hi)^r`. Abbruch, sobald dieser Wert unter
`plateau_stop_max_missed_probability` (Default 0.05) fällt. `plateau_min_modelled_trials_per_dim`
bleibt als UNTERGRENZE (gesenkt von 8 auf 6), und ein Constraint-Arm (`constraint_improvement_rate
> tau_c`) unterdrückt den Abbruch, solange sich der Sampler der feasiblen Region annähert.
"""
import math

import optuna
import pytest

from automation.optimizer import run_optimization as ro


# ---------------------------------------------------------------------------
# plateau_stop_missed_probability: reine Funktion, Referenztabelle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("m, r, expected_p_hi, expected_missed", [
    (64, 20, 3 / 64, 1 - (1 - 3 / 64) ** 20),
    (64, 1, 3 / 64, 3 / 64),
    (100, 1, 0.03, 0.03),
    (1000, 100, 0.003, 1 - (1 - 0.003) ** 100),
    (10, 5, 0.3, 1 - (1 - 0.3) ** 5),
])
def test_missed_probability_matches_reference_table(m, r, expected_p_hi, expected_missed):
    p_hi, missed = ro.plateau_stop_missed_probability(m, r)
    assert p_hi == pytest.approx(expected_p_hi, rel=1e-9)
    assert missed == pytest.approx(expected_missed, rel=1e-9)


def test_zero_modelled_trials_never_triggers_abort():
    """Keine Information (m=0) ⇒ maximale Unsicherheit ⇒ (1.0, 1.0), niemals < irgendeine
    sinnvolle Schranke."""
    p_hi, missed = ro.plateau_stop_missed_probability(0, 100)
    assert (p_hi, missed) == (1.0, 1.0)


def test_zero_remaining_budget_is_zero_missed_probability():
    """Kein Restbudget mehr ⇒ missed_probability=0.0 (unter jeder sinnvollen Schranke, das Urteil
    ist ohnehin faellig, da nichts mehr laeuft)."""
    p_hi, missed = ro.plateau_stop_missed_probability(64, 0)
    assert p_hi == pytest.approx(3 / 64)
    assert missed == 0.0


def test_p_hi_is_capped_at_one_for_tiny_m():
    p_hi, _ = ro.plateau_stop_missed_probability(1, 10)
    assert p_hi == 1.0


def test_larger_m_yields_smaller_p_hi_and_missed_probability():
    """Mehr modellierte Trials ohne einen einzigen eligiblen Treffer ⇒ engere Konfidenzgrenze ⇒
    kleinere P(noch etwas zu finden) beim GLEICHEN Restbudget."""
    _, missed_small_m = ro.plateau_stop_missed_probability(20, 20)
    _, missed_large_m = ro.plateau_stop_missed_probability(200, 20)
    assert missed_large_m < missed_small_m


def test_smaller_remaining_budget_yields_smaller_missed_probability():
    """Bei fixem m: je weniger Restbudget, desto kleiner die Wahrscheinlichkeit, darin noch etwas
    zu finden — monoton fallend in r."""
    _, missed_r5 = ro.plateau_stop_missed_probability(64, 5)
    _, missed_r50 = ro.plateau_stop_missed_probability(64, 50)
    assert missed_r5 < missed_r50


# ---------------------------------------------------------------------------
# floor_plateau_callback integration: sequentielles Kriterium statt fester Trialzahl
# ---------------------------------------------------------------------------

class _FakeTrial:
    def __init__(self, value, *, oos_evaluated=None, oos_eligible=None,
                 oos_total_trades=None, constraint_violation=None,
                 state=optuna.trial.TrialState.COMPLETE):
        self.value = value
        self.state = state
        self.user_attrs = {}
        if oos_evaluated is not None:
            self.user_attrs["oos_evaluated"] = oos_evaluated
        if oos_eligible is not None:
            self.user_attrs["oos_eligible"] = oos_eligible
        if oos_total_trades is not None:
            self.user_attrs["oos_total_trades"] = oos_total_trades
        if constraint_violation is not None:
            self.user_attrs["oos_constraint_violations"] = (constraint_violation,)


class _FakeStudy:
    def __init__(self, trials, n_trials_budget=None):
        self.trials = trials
        self._attrs = {}
        self.stopped = False
        if n_trials_budget is not None:
            self._attrs["n_trials_budget"] = n_trials_budget

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v

    def stop(self):
        self.stopped = True


def _non_eligible_trials(n, **kw):
    return [_FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8, **kw)
            for _ in range(n)]


def test_no_stop_while_missed_probability_is_still_above_threshold():
    """16 Startup + 20 modellierte Trials, 0 eligibel, grosszuegiges Restbudget (80 Trials):
    P(noch etwas zu finden) bleibt weit ueber der 5%-Schranke ⇒ KEIN Abbruch, obwohl die alte
    feste Schwelle (n_startup+6·dim mit dim=1 flach ⇒ 22) hier laengst ueberschritten waere."""
    W = {"n_startup_trials": 16}
    trials = _non_eligible_trials(36)  # 16 startup + 20 modelliert
    study = _FakeStudy(trials, n_trials_budget=116)  # Restbudget = 116-36 = 80
    p_hi, missed = ro.plateau_stop_missed_probability(20, 80)
    assert missed > 0.05, "Testkonstruktion sollte eine hohe Restwahrscheinlichkeit ergeben"
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              stop_on_plateau=True)
    assert study.stopped is False
    assert study.user_attrs.get("zero_eligible_plateau_warned") is None


def test_stops_once_missed_probability_drops_below_threshold():
    """Dieselbe Study spaeter im Lauf: 100 modellierte Trials, 0 eligibel, kleines Restbudget (1
    Trial) ⇒ P(noch etwas zu finden) faellt unter 5% ⇒ Abbruch (Rule-of-Three)."""
    W = {"n_startup_trials": 16}
    trials = _non_eligible_trials(116)  # 16 startup + 100 modelliert
    study = _FakeStudy(trials, n_trials_budget=117)  # Restbudget = 117-116 = 1
    p_hi, missed = ro.plateau_stop_missed_probability(100, 1)
    assert missed < 0.05, "Testkonstruktion sollte eine niedrige Restwahrscheinlichkeit ergeben"
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              stop_on_plateau=True)
    assert study.stopped is True
    assert study.user_attrs.get("zero_eligible_plateau_warned") is True


def test_constraint_arm_suppresses_an_otherwise_due_abort():
    """Trotz einer Restwahrscheinlichkeit unter der Schranke (wie im Test oben) UNTERDRUECKT der
    Constraint-Arm den Abbruch, wenn sich der Sampler nachweislich der feasiblen Region annaehert
    (constraint_improvement_rate > tau_c=0.05, #754) — genau der Fall, den Rule-of-Three allein
    nicht sieht."""
    W = {"n_startup_trials": 16}
    startup = _non_eligible_trials(16)
    # 100 modellierte Trials mit STARK fallender Constraint-Verletzung (erste Haelfte hoch, zweite
    # Haelfte niedrig) ⇒ konstruiert eine hohe constraint_improvement_rate.
    modelled_first_half = [
        _FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8,
                  constraint_violation=10.0)
        for _ in range(50)
    ]
    modelled_second_half = [
        _FakeTrial(-0.5, oos_evaluated=True, oos_eligible=False, oos_total_trades=8,
                  constraint_violation=0.1)
        for _ in range(50)
    ]
    trials = startup + modelled_first_half + modelled_second_half
    study = _FakeStudy(trials, n_trials_budget=117)  # Restbudget = 117-116 = 1 (waere sonst Abbruch)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              stop_on_plateau=True)
    assert study.stopped is False, "Constraint-Arm haette den Abbruch unterdruecken muessen"
    assert study.user_attrs.get("zero_eligible_plateau_warned") is None


def test_missing_n_trials_budget_falls_back_to_the_fixed_lower_bound():
    """Ohne bekanntes Budget (z. B. globaler Pfad/Legacy-Tests) kann keine Restwahrscheinlichkeit
    berechnet werden ⇒ Fallback auf das alte, feste Verhalten: Abbruch sobald die Untergrenze
    (min_for_zero_eligible) erreicht ist — bit-identisch zu Pre-#806."""
    W = {"n_startup_trials": 16, "plateau_min_modelled_trials": 20}
    trials = _non_eligible_trials(36)  # 16 startup + 20 modelliert == min_for_zero_eligible
    study = _FakeStudy(trials)  # KEIN n_trials_budget
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              stop_on_plateau=True)
    assert study.stopped is True


def test_missing_n_trials_budget_still_respects_the_lower_bound():
    W = {"n_startup_trials": 16, "plateau_min_modelled_trials": 20}
    trials = _non_eligible_trials(35)  # 16 startup + 19 modelliert < 20
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              stop_on_plateau=True)
    assert study.stopped is False


def test_study_early_stop_event_carries_sequential_criterion_telemetry(caplog):
    import json
    import logging

    W = {"n_startup_trials": 16}
    trials = _non_eligible_trials(116)
    study = _FakeStudy(trials, n_trials_budget=117)
    with caplog.at_level(logging.INFO, logger="optimizer"):
        ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                                  logger=logging.getLogger("optimizer"), stop_on_plateau=True)
    events = [json.loads(r.message.split("[JSON_EVENT]", 1)[1].strip())
             for r in caplog.records if "[JSON_EVENT]" in r.message]
    early_stop = [e for e in events if e.get("event_type") == "STUDY_EARLY_STOP"]
    assert len(early_stop) == 1
    assert early_stop[0]["p_hi"] == pytest.approx(3 / 100)
    assert early_stop[0]["remaining_budget"] == 1
    assert early_stop[0]["missed_probability"] == pytest.approx(3 / 100)


def test_plateau_min_modelled_trials_per_dim_default_is_six_not_eight():
    """Akzeptanzkriterium #806 (Umsetzung 2): der Default sinkt von 8 auf 6, weil das sequentielle
    Kriterium die eigentliche Entscheidung traegt."""
    import json
    from automation.optimizer.trial_config import config_dir
    real_cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    assert real_cfg.get("plateau_min_modelled_trials_per_dim") == 6


def test_real_shipped_config_has_the_new_probability_threshold():
    import json
    from automation.optimizer.trial_config import config_dir
    real_cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    assert real_cfg.get("plateau_stop_max_missed_probability") == pytest.approx(0.05)
