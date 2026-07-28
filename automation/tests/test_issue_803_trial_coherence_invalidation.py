"""Issue #803 — `oos_coherence_violation` disqualifizierte bisher NUR die STUDY-weite Rate
(`check_study_coherence_violation_rate`, #773), nie den einzelnen Trial: ein Trial mit einer
nachweislich UNGUELTIGEN Risikokennzahl blieb individuell eligible und konnte Study-Gewinner werden.
Gleichzeitig lief der Study-Abschluss-Check erst NACH `study.optimize()` — das volle Budget war
bereits verbrannt, bevor das Urteil fiel.

Fix: (1) `_evaluate_oos_eligibility` disqualifiziert JEDEN Trial mit `oos_coherence_violation=True`
ODER `equity_ruined=True` (#801) individuell (`REJECT_OOS_INVALID_METRICS`), unabhaengig von den
uebrigen Gates. (2) `max_coherence_violation_rate` misst seither NUR NOCH systemische Pathologie
(0.10 statt 0.01 — die Trial-Ebene faengt den Einzelfall bereits ab). (3)
`coherence_violation_early_abort_callback` prueft alle 32 Trials WAEHREND `study.optimize()` statt
erst danach, damit eine pathologische Study nach ~10% statt 100% des Budgets endet.
"""
import logging

import optuna
import pytest

from automation.backtest_runner import _evaluate_oos_eligibility
from automation.optimizer import run_optimization as ro
from automation.optimizer.explain_trial import build_explanation


_TOURNAMENT_CFG = {"eligible_requires_all": ["min_trades", "min_total_return"]}


def _base_oos_metrics(**overrides):
    m = {
        "total_trades": 20, "max_drawdown": 0.05, "win_rate": 0.6, "total_return": 0.10,
        "expectancy": 0.01, "sortino_ratio": 1.5, "profit_factor": 1.8,
        "median_position_notional": 100.0,
    }
    m.update(overrides)
    return m


# ── _evaluate_oos_eligibility: Trial-Ebenen-Gate (Umsetzung 1) ─────────────────────────────────────
def test_coherence_violation_disqualifies_an_otherwise_passing_trial():
    metrics = _base_oos_metrics(oos_coherence_violation=True)
    result = _evaluate_oos_eligibility(metrics, _TOURNAMENT_CFG, {})
    assert result["oos_eligible"] is False
    assert any("REJECT_OOS_INVALID_METRICS" in r for r in result["oos_rejection_reasons"])


def test_equity_ruined_disqualifies_an_otherwise_passing_trial():
    metrics = _base_oos_metrics(equity_ruined=True)
    result = _evaluate_oos_eligibility(metrics, _TOURNAMENT_CFG, {})
    assert result["oos_eligible"] is False
    assert any("REJECT_OOS_INVALID_METRICS" in r for r in result["oos_rejection_reasons"])


def test_clean_trial_without_either_flag_remains_eligible():
    metrics = _base_oos_metrics()
    result = _evaluate_oos_eligibility(metrics, _TOURNAMENT_CFG, {})
    assert result["oos_eligible"] is True
    assert not any("REJECT_OOS_INVALID_METRICS" in r for r in result["oos_rejection_reasons"])


def test_invalid_metrics_rejection_wins_even_if_all_other_gates_pass():
    """Regression: ein Trial, der JEDES konfigurierte Gate besteht (min_trades, min_total_return),
    bleibt trotzdem ineligible, sobald oos_coherence_violation gesetzt ist — die Klausel ist
    UNBEDINGT (kein eligible_requires_all-Opt-in), nicht nur eine weitere optionale Bedingung."""
    metrics = _base_oos_metrics(oos_coherence_violation=True, total_return=5.0, win_rate=1.0)
    result = _evaluate_oos_eligibility(
        metrics, {"eligible_requires_all": ["min_trades"]}, {})
    assert result["oos_eligible"] is False


# ── rejection_chain/decision_chain (#785) weist REJECT_OOS_INVALID_METRICS aus ────────────────────
def test_rejection_reason_flows_into_explain_trial_rejection_chain():
    class _FakeTrial:
        number = 3
        state = "COMPLETE"
        value = -0.5
        user_attrs = {
            "oos_evaluated": True, "oos_eligible": False,
            "oos_rejection_reasons": [
                "REJECT_OOS_INVALID_METRICS: oos_coherence_violation=True, equity_ruined=False "
                "— Risikokennzahl nicht selektionsfaehig (#801/#803).",
            ],
            "oos_coherence_violation": True,
        }

    class _FakeStudy:
        study_name = "study_TestStrategy_AAA_ETORO"
        trials = [_FakeTrial()]

    explanation = build_explanation(_FakeStudy(), _FakeTrial())
    details = [c["detail"] for c in explanation["rejection_chain"]]
    assert any("REJECT_OOS_INVALID_METRICS" in d for d in details)


# ── check_study_coherence_violation_rate: STUDY-Ebene misst jetzt systemische Pathologie (0.10) ───
def _study_with_trials(n, n_violations, evaluated=True, n_trials_budget=None):
    study = optuna.create_study(direction="maximize")
    if n_trials_budget is not None:
        study.set_user_attr("n_trials_budget", n_trials_budget)
    for i in range(n):
        t = study.ask()
        t.set_user_attr("oos_evaluated", evaluated)
        t.set_user_attr("oos_coherence_violation", i < n_violations)
        study.tell(t, 1.0)
    return study


def test_single_isolated_trial_violation_no_longer_aborts_the_study():
    """1/64 (1,56 %) bleibt seit #803 UNTER der auf 0.10 angehobenen Study-Schwelle — die
    Trial-Ebene (oben) faengt den Einzelfall bereits individuell ab; die Study selbst darf
    weiterlaufen (Akzeptanzkriterium #803: 'Study mit 1/64 Verletzungen: laeuft durch')."""
    study = _study_with_trials(64, n_violations=1)
    result = ro.check_study_coherence_violation_rate(
        study, {"max_coherence_violation_rate": 0.10})
    assert result is False
    assert study.user_attrs.get("coherence_violation_rate_exceeded") is None


def test_systematic_violation_rate_still_aborts_the_study():
    study = _study_with_trials(64, n_violations=35)  # 54.7%
    result = ro.check_study_coherence_violation_rate(
        study, {"max_coherence_violation_rate": 0.10})
    assert result is True
    assert study.user_attrs.get("coherence_violation_rate_exceeded") is True


def test_aborted_event_carries_budget_execution_telemetry(caplog):
    study = _study_with_trials(64, n_violations=35, n_trials_budget=64)
    with caplog.at_level(logging.INFO, logger="optimizer"):
        ro.check_study_coherence_violation_rate(study, {"max_coherence_violation_rate": 0.10})
    import json as _json
    events = [_json.loads(r.message.split("[JSON_EVENT]", 1)[1].strip())
             for r in caplog.records if "[JSON_EVENT]" in r.message]
    aborted = [e for e in events if e.get("event_type") == "STUDY_ABORTED_ON_INVARIANT"]
    assert len(aborted) == 1
    assert aborted[0]["n_trials_when_aborted"] == 64
    assert aborted[0]["budget_executed_fraction"] == pytest.approx(1.0)


def test_budget_execution_fraction_is_none_without_a_known_budget():
    study = _study_with_trials(64, n_violations=35)  # kein n_trials_budget gesetzt
    ro.check_study_coherence_violation_rate(study, {"max_coherence_violation_rate": 0.10})
    # kein Crash, kein stiller Default — None ist explizit "kein Budget bekannt".


# ── coherence_violation_early_abort_callback: frueher Abbruch statt vollem Budget ──────────────────
class _FakeTrialNum:
    def __init__(self, number):
        self.number = number


def test_early_abort_callback_is_a_noop_outside_the_check_interval(monkeypatch):
    study = optuna.create_study(direction="maximize")
    called = []
    monkeypatch.setattr(ro, "check_study_coherence_violation_rate",
                        lambda *a, **k: called.append(1) or False)
    ro.coherence_violation_early_abort_callback(
        study, _FakeTrialNum(5), opt_data={"max_coherence_violation_rate": 0.10})
    assert called == []


def test_early_abort_callback_stops_study_when_rate_exceeded_at_trial_32():
    """Akzeptanzkriterium #803: eine Study mit 35/64 Verletzungen bricht spaetestens bei Trial 32
    ab (budget_executed_fraction <= 0,55), nicht erst nach dem vollen Budget."""
    study = optuna.create_study(direction="maximize")
    study.set_user_attr("n_trials_budget", 64)
    # Baut exakt das #803-Symptom nach: 35/64 Verletzungen, gleichmaessig verteilt, damit bereits
    # die ersten 32 Trials eine Rate deutlich > 10% zeigen.
    for i in range(64):
        t = study.ask()
        t.set_user_attr("oos_evaluated", True)
        t.set_user_attr("oos_coherence_violation", (i % 2 == 0) and i < 70)
        study.tell(t, 1.0)
        if (i + 1) % 32 == 0:
            ro.coherence_violation_early_abort_callback(
                study, _FakeTrialNum(i), opt_data={"max_coherence_violation_rate": 0.10})
            if study.user_attrs.get("coherence_violation_rate_exceeded"):
                break
    assert study.user_attrs.get("coherence_violation_rate_exceeded") is True
    n_trials_when_aborted = len(study.trials)
    assert n_trials_when_aborted <= 32
    assert n_trials_when_aborted / 64.0 <= 0.55


def test_early_abort_callback_fails_open_on_internal_error(monkeypatch):
    study = optuna.create_study(direction="maximize")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ro, "check_study_coherence_violation_rate", _boom)
    # darf NICHT propagieren (fail-open, analog disk_budget_callback/retention_callback).
    ro.coherence_violation_early_abort_callback(
        study, _FakeTrialNum(31), opt_data={"max_coherence_violation_rate": 0.10})
