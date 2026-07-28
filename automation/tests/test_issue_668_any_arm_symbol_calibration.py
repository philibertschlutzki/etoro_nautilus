"""Issue #668 — `min_win_rate`-OR-Arm (0.15) per-Study strukturell unerreichbar: OR kollabiert
lautlos auf reines PF-Gate.

#660 lieferte nur die LIVE-Warnung (WARNING-Log), aber keinen Fix — die Schwelle blieb eine globale
Zahl, die per (Symbol, Strategie) strukturell unerreichbar sein kann. Fix: eine KONFIGURIERTE Policy
(``any_arm_unreachable_policy``, tournament.json): 'warn' (Default, bit-identisch), 'drop_arm'
(der unerreichbare Arm wird explizit gedroppt) oder 'recalibrate' (die Schwelle wird symbol-
spezifisch auf p99(beobachtet), gefloort, neu gesetzt) — beide Nicht-'warn'-Policies werden
explizit telemetriert (statt den OR-Arm lautlos kollabieren zu lassen) und können bereits
abgeschlossene Trials einer sonst leeren (``HOLDOUT_NO_ELIGIBLE_TRIALS``) Selektion retten.
"""
import optuna
import pytest

from automation.optimizer.reward import resolve_any_arm_policy
from automation.optimizer import confirm as cmod


_TCFG_BASE = {
    "eligible_requires_any": ["min_profit_factor", "min_win_rate"],
    "oos_min_win_rate": 0.15,
}

# TSLA.ETORO-artige, symbol-spezifisch niedrige Win-Rate-Verteilung (p99 ≈ 0.0968 < 0.15, #660-Referenz).
_LOW_WIN_RATES = [0.0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.0968]
# Cross-strategy-artige, erreichbare Verteilung (p99 > 0.15).
_HIGH_WIN_RATES = [0.10, 0.12, 0.14, 0.16, 0.18, 0.20]


# ── resolve_any_arm_policy (pure) ────────────────────────────────────────────────────────────────
def test_default_warn_policy_is_inert_even_when_unreachable():
    decision = resolve_any_arm_policy(_TCFG_BASE, {"min_win_rate": _LOW_WIN_RATES})
    # Issue #759 — any_arm_decision ist ein neues Feld (None bei Policy='warn': #759 trifft in
    # diesem Zweig kein Urteil, weil 'warn' unveraendert bit-identisch bleibt).
    assert decision == {"policy": "warn", "dropped_clauses": [], "recalibrated_thresholds": {},
                        "any_arm_decision": None}


def test_unknown_policy_raises_fail_loud():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="bogus")
    with pytest.raises(ValueError, match="any_arm_unreachable_policy"):
        resolve_any_arm_policy(tcfg, {"min_win_rate": _LOW_WIN_RATES})


def test_drop_arm_policy_explicitly_drops_unreachable_clause():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="drop_arm")
    decision = resolve_any_arm_policy(tcfg, {"min_win_rate": _LOW_WIN_RATES})
    assert decision["policy"] == "drop_arm"
    assert decision["dropped_clauses"] == ["min_win_rate"]
    assert decision["recalibrated_thresholds"] == {}


def test_recalibrate_policy_sets_p99_threshold():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="recalibrate")
    decision = resolve_any_arm_policy(tcfg, {"min_win_rate": _LOW_WIN_RATES})
    assert decision["policy"] == "recalibrate"
    assert decision["dropped_clauses"] == []
    sorted_vals = sorted(_LOW_WIN_RATES)
    expected_p99 = sorted_vals[max(0, min(len(sorted_vals) - 1, round(0.99 * (len(sorted_vals) - 1))))]
    assert decision["recalibrated_thresholds"]["oos_min_win_rate"] == pytest.approx(expected_p99)


def test_recalibrate_policy_has_no_floor_since_812():
    """Issue #812 — der globale Floor (min_win_rate_recalibration_floor) entfiel mit dem
    Default-Wechsel auf 'drop_arm' (toter Schluessel, entfernt): die rekalibrierte Schwelle ist
    IMMER das rohe p99, auch wenn ein aufrufer den (nicht mehr gelesenen) Alt-Schluessel setzt."""
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="recalibrate",
               min_win_rate_recalibration_floor=0.08)
    samples = [0.0, 0.01, 0.02, 0.03, 0.04, 0.0, 0.01, 0.02, 0.03, 0.04]
    # Issue #759 — mindestens any_arm_min_observations (Default 10) echte Beobachtungen noetig,
    # sonst any_arm_decision='insufficient_data' statt einer Rekalibrierung.
    decision = resolve_any_arm_policy(tcfg, {"min_win_rate": samples})
    assert decision["recalibrated_thresholds"]["oos_min_win_rate"] == max(samples)


def test_reachable_arm_yields_no_decision_regardless_of_policy():
    for policy in ("drop_arm", "recalibrate"):
        tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy=policy)
        decision = resolve_any_arm_policy(tcfg, {"min_win_rate": _HIGH_WIN_RATES})
        assert decision["dropped_clauses"] == []
        assert decision["recalibrated_thresholds"] == {}


def test_insufficient_samples_yields_no_decision():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="drop_arm")
    decision = resolve_any_arm_policy(tcfg, {"min_win_rate": [0.01, 0.02]})
    assert decision["dropped_clauses"] == []


# ── _rescue_eligible_trials_under_any_arm_policy (confirm.py) ───────────────────────────────────
def _study_with_trials(trial_specs):
    """``trial_specs``: Liste von (oos_eligible, oos_win_rate, oos_gate_deltas)-Tupeln."""
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    for i, (eligible, win_rate, deltas) in enumerate(trial_specs):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", eligible)
        study._storage.set_trial_user_attr(t._trial_id, "oos_win_rate", win_rate)
        study._storage.set_trial_user_attr(t._trial_id, "oos_gate_deltas", deltas)
        study.tell(t, float(i))
    return study


def test_rescue_returns_empty_for_default_warn_policy():
    specs = [(False, 0.05, {"oos_min_profit_factor": 0.2, "oos_min_win_rate": -0.10})] * 10
    study = _study_with_trials(specs)
    rescued, decision = cmod._rescue_eligible_trials_under_any_arm_policy(study, _TCFG_BASE)
    assert rescued == []
    assert decision["policy"] == "warn"


def test_rescue_drop_arm_recovers_trials_that_pass_profit_factor_alone():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="drop_arm",
               eligible_requires_all=["min_trades"])
    # 10 Trials, alle ohne min_win_rate eligible (Arm strukturell unerreichbar), aber PF-Delta >= 0.
    specs = [(False, 0.05 + 0.001 * i, {"oos_min_trades": 5.0, "oos_min_profit_factor": 0.2,
                                        "oos_min_win_rate": -0.10}) for i in range(10)]
    study = _study_with_trials(specs)
    rescued, decision = cmod._rescue_eligible_trials_under_any_arm_policy(study, tcfg)
    assert decision["policy"] == "drop_arm"
    assert decision["dropped_clauses"] == ["min_win_rate"]
    assert len(rescued) == 10
    # absteigend nach t.value sortiert (hier: absteigende trial-Reihenfolge, letzter Trial = value=9).
    assert rescued[0].value == 9.0


def test_rescue_respects_requires_all_violation():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="drop_arm",
               eligible_requires_all=["min_trades"])
    specs = [(False, 0.05, {"oos_min_trades": -1.0, "oos_min_profit_factor": 0.2,
                            "oos_min_win_rate": -0.10})] * 10
    study = _study_with_trials(specs)
    rescued, decision = cmod._rescue_eligible_trials_under_any_arm_policy(study, tcfg)
    assert decision["dropped_clauses"] == ["min_win_rate"]
    assert rescued == []  # min_trades-Delta < 0 ⇒ ausgeschlossen, unabhängig von der Any-Policy


def test_rescue_recalibrate_recovers_trials_above_recalibrated_threshold():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="recalibrate",
               eligible_requires_all=["min_trades"])
    # p99 der beobachteten Verteilung liegt bei ~0.0968 (< 0.15) ⇒ rekalibrierte Schwelle ≈ 0.0968.
    specs = [(False, wr, {"oos_min_trades": 5.0, "oos_min_profit_factor": -0.3,
                          "oos_min_win_rate": wr - 0.15}) for wr in _LOW_WIN_RATES]
    study = _study_with_trials(specs)
    rescued, decision = cmod._rescue_eligible_trials_under_any_arm_policy(study, tcfg)
    recalibrated = decision["recalibrated_thresholds"]["oos_min_win_rate"]
    assert recalibrated < 0.15
    # Nur der Trial mit der HÖCHSTEN Win-Rate (== p99-Sample) erreicht/übertrifft die rekalibrierte
    # Schwelle selbst (Trial mit wr=0.0968 == recalibrated).
    assert len(rescued) >= 1
    assert all(t.user_attrs["oos_win_rate"] >= recalibrated for t in rescued)


def test_rescue_no_effect_when_arm_already_reachable():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="drop_arm",
               eligible_requires_all=["min_trades"])
    specs = [(False, wr, {"oos_min_trades": 5.0, "oos_min_profit_factor": -0.2,
                          "oos_min_win_rate": wr - 0.15}) for wr in _HIGH_WIN_RATES]
    study = _study_with_trials(specs)
    rescued, decision = cmod._rescue_eligible_trials_under_any_arm_policy(study, tcfg)
    assert decision["dropped_clauses"] == []
    assert rescued == []


def test_rescue_noop_when_any_win_rate_clause_not_configured():
    tcfg = dict(_TCFG_BASE, any_arm_unreachable_policy="drop_arm",
               eligible_requires_any=["min_profit_factor"])  # kein min_win_rate im OR-Arm
    specs = [(False, 0.05, {"oos_min_profit_factor": 0.2, "oos_min_win_rate": -0.10})] * 10
    study = _study_with_trials(specs)
    rescued, decision = cmod._rescue_eligible_trials_under_any_arm_policy(study, tcfg)
    assert rescued == []
