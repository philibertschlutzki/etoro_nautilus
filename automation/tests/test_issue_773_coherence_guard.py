"""Issue #773 (P1) — `check_log_return_coherence` ist ein Report-Nachtrag, kein Wächter; die
Fehlerklasse konnte deshalb zweimal überleben (#589/#620 → #756 → #771).

Fix: `run_optimization.check_study_coherence_violation_rate` bricht eine Study fail-loud aus dem
Promotions-Pfad, sobald der Anteil `oos_coherence_violation`-markierter Trials über
`optimizer.json.max_coherence_violation_rate` liegt; `confirm.confirm_per_symbol_promotion`
verweigert die Promotion, wenn das Flag gesetzt ist; `sweep.main()` gibt einen Non-Zero-Exit-Code
zurück, sobald der #742-Report mindestens einen FAIL-Invarianten-Check enthält.
"""
import json
import logging

import optuna

from automation.optimizer import run_optimization as ro


def _quiet_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    events = []

    class _H(logging.Handler):
        def emit(self, r):
            events.append(r.getMessage())

    lg.addHandler(_H())
    lg.setLevel(logging.INFO)
    lg.propagate = False
    return lg, events


def _study_with_trials(n, n_violations, evaluated=True):
    study = optuna.create_study(direction="maximize")
    for i in range(n):
        t = study.ask()
        t.set_user_attr("oos_evaluated", evaluated)
        t.set_user_attr("oos_coherence_violation", i < n_violations)
        study.tell(t, 1.0)
    return study


def test_rate_over_threshold_sets_flag_and_emits_event():
    study = _study_with_trials(10, n_violations=3)  # 30%
    lg, events = _quiet_logger("t773_over")
    result = ro.check_study_coherence_violation_rate(study, {"max_coherence_violation_rate": 0.2}, logger=lg)
    assert result is True
    assert study.user_attrs.get("coherence_violation_rate_exceeded") is True
    assert any("STUDY_ABORTED_ON_INVARIANT" in e for e in events)


def test_rate_at_or_below_threshold_is_bit_identical_no_op():
    study = _study_with_trials(10, n_violations=1)  # 10%
    result = ro.check_study_coherence_violation_rate(study, {"max_coherence_violation_rate": 0.5})
    assert result is False
    assert study.user_attrs.get("coherence_violation_rate_exceeded") is None


def test_zero_violations_is_bit_identical():
    study = _study_with_trials(10, n_violations=0)
    result = ro.check_study_coherence_violation_rate(study, {"max_coherence_violation_rate": 0.0})
    assert result is False


def test_missing_config_key_is_a_no_op():
    study = _study_with_trials(10, n_violations=10)
    result = ro.check_study_coherence_violation_rate(study, {})
    assert result is False
    assert study.user_attrs.get("coherence_violation_rate_exceeded") is None


def test_no_evaluated_trials_is_a_no_op():
    study = _study_with_trials(5, n_violations=0, evaluated=False)
    result = ro.check_study_coherence_violation_rate(study, {"max_coherence_violation_rate": 0.0})
    assert result is False


# ── confirm.py: Study wird nicht promotet ────────────────────────────────────────────────────────
def test_confirm_rejects_study_with_exceeded_coherence_flag(tmp_path, monkeypatch):
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps({"max_drawdown": 0.30}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps({"promotion_margin": 0.10}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    events = []
    monkeypatch.setattr(cmod, "emit_execution_event",
                        lambda logger, name, payload: events.append((name, payload)))

    study = optuna.create_study(direction="maximize")
    study.set_user_attr("coherence_violation_rate_exceeded", True)

    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})
    assert res["promote"] is False
    assert res["is_rejection_detail_override"] == "REJECT_COHERENCE_VIOLATION"
    assert any(name == "STUDY_REJECTED_ON_COHERENCE_VIOLATION" for name, _ in events)


def test_confirm_does_not_reject_when_flag_absent(tmp_path, monkeypatch):
    """Regression: das Flag darf keinen anderen Promotions-Pfad beeinflussen, wenn es fehlt."""
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "holdout_top_k": 5, "deflated_selection": False}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps(
        {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)

    from automation.optimizer.parsing import TournamentMetrics
    monkeypatch.setattr(
        cmod, "_holdout_metrics_for_params",
        lambda strategy, symbol, params, **kw: TournamentMetrics(
            oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
            oos_sortino=-0.5, oos_max_drawdown=0.05, oos_total_trades=30, win_count=1,
            fully_eligible_pairs=1, is_total_trades=100, oos_total_return=-0.02))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: -0.3)

    study = optuna.create_study(direction="maximize")  # kein coherence_violation_rate_exceeded
    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})
    assert res["is_rejection_detail_override"] != "REJECT_COHERENCE_VIOLATION"


# ── sweep.py: Non-Zero-Exit-Code bei mindestens einem FAIL ──────────────────────────────────────
def test_report_has_failing_invariant_detects_fail(tmp_path):
    from automation.optimizer.sweep import _report_has_failing_invariant

    p = tmp_path / "report.json"
    p.write_text(json.dumps({"invariant_checks": [{"passed": True}, {"passed": False}]}))
    assert _report_has_failing_invariant(p) is True


def test_report_has_failing_invariant_all_pass():
    from automation.optimizer.sweep import _report_has_failing_invariant
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"invariant_checks": [{"passed": True}, {"passed": True}]}, f)
        path = f.name
    try:
        assert _report_has_failing_invariant(path) is False
    finally:
        os.unlink(path)


def test_report_has_failing_invariant_fails_open_on_bad_path():
    from automation.optimizer.sweep import _report_has_failing_invariant
    assert _report_has_failing_invariant("/nonexistent/path/report.json") is False
