"""Issue #839 (P1) — Eine gemessene GR-01-Verletzung hat keine Konsequenz.

`invariants.check_holding_time_cap` FAILt korrekt, wenn eine Study die #714/GR-01-Zeitbox
verletzt, aber bis dahin passierte danach nichts: die Study durchlief Eligibility, Confirm,
Deflation und Champion-Store, als wären ihre Metriken gültig. Der Fix führt drei Konsequenzen ein:

1. `invariants.compute_trial_timebox_violations` — je-Trial-Verletzung (Haltedauer gegen den
   gesampelten oder globalen `max_bars_in_trade`-Deckel), aggregiert je Study.
2. `confirm.confirm_per_symbol_promotion` verwirft eine Study mit `REJECT_INVALID_TIMEBOX`, wenn
   `timebox_violation_fraction > tournament.json['timebox_violation_tolerance']` — VOR jedem
   statistischen Gate.
3. `champions._configured_admissible_reject_details` lässt `REJECT_INVALID_TIMEBOX` NIE zu (analog
   `REJECT_HOLDOUT_GATE`) — ein Kandidat mit gebrochenem Exit-Pfad landet nie im Champion-Store.
"""
import json

import optuna

from automation.optimizer import champions
from automation.optimizer import invariants as inv


# ── invariants.compute_trial_timebox_violations (reine Funktion) ────────────────────────────────
def test_no_trials_with_holding_data_is_zero_fraction():
    result = inv.compute_trial_timebox_violations([])
    assert result == {
        "timebox_violation_trades": 0,
        "timebox_evaluated_trades": 0,
        "timebox_violation_fraction": 0.0,
        "timebox_violated": False,
    }


def test_trial_within_sampled_cap_is_not_a_violation():
    attrs = [{"oos_max_holding_time_s": 23 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}}]
    result = inv.compute_trial_timebox_violations(attrs)
    assert result["timebox_violated"] is False
    assert result["timebox_violation_trades"] == 0


def test_trial_beyond_sampled_cap_is_a_violation():
    attrs = [{"oos_max_holding_time_s": 100 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}}]
    result = inv.compute_trial_timebox_violations(attrs)
    assert result["timebox_violated"] is True
    assert result["timebox_violation_trades"] == 1
    assert result["timebox_violation_fraction"] == 1.0


def test_missing_sampled_max_bars_falls_back_to_global_cap():
    """Strategie sampelt max_bars_in_trade nicht ⇒ Fallback auf den globalen #714/GR-01-Deckel
    (24 Bars) — dieselbe konservative Schranke wie check_holding_time_cap."""
    attrs = [{"oos_max_holding_time_s": 25 * 3600.0}]  # kein sampled_params
    result = inv.compute_trial_timebox_violations(attrs)
    assert result["timebox_violated"] is True

    attrs_ok = [{"oos_max_holding_time_s": 20 * 3600.0}]
    result_ok = inv.compute_trial_timebox_violations(attrs_ok)
    assert result_ok["timebox_violated"] is False


def test_fraction_over_mixed_cohort():
    attrs = [
        {"oos_max_holding_time_s": 100 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}},
        {"oos_max_holding_time_s": 10 * 3600.0, "sampled_params": {"max_bars_in_trade": 24}},
        {"oos_max_holding_time_s": None},  # nicht oos_evaluated -> zaehlt nicht mit
    ]
    result = inv.compute_trial_timebox_violations(attrs)
    assert result["timebox_evaluated_trades"] == 2
    assert result["timebox_violation_trades"] == 1
    assert result["timebox_violation_fraction"] == 0.5


def test_check_holding_time_cap_is_blocking_severity():
    result = inv.check_holding_time_cap([{"strategy": "S", "symbol": "X", "max_holding_time_s": 100 * 3600.0}])
    assert result.severity == "blocking"
    assert result.passed is False


# ── confirm.py: REJECT_INVALID_TIMEBOX vor allen statistischen Gates ─────────────────────────────
def _study_with_timebox_trials(n_violations: int, n_ok: int):
    study = optuna.create_study(direction="maximize")
    for _ in range(n_violations):
        t = study.ask()
        t.set_user_attr("oos_max_holding_time_s", 100 * 3600.0)
        t.set_user_attr("sampled_params", {"max_bars_in_trade": 24})
        t.set_user_attr("oos_eligible", True)
        study.tell(t, 1.0)
    for _ in range(n_ok):
        t = study.ask()
        t.set_user_attr("oos_max_holding_time_s", 10 * 3600.0)
        t.set_user_attr("sampled_params", {"max_bars_in_trade": 24})
        t.set_user_attr("oos_eligible", True)
        study.tell(t, 1.0)
    return study


def test_confirm_rejects_study_with_timebox_violation(tmp_path, monkeypatch):
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "timebox_violation_tolerance": 0.0}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps({"promotion_margin": 0.10}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    events = []
    monkeypatch.setattr(cmod, "emit_execution_event",
                        lambda logger, name, payload, level=None: events.append((name, payload)))

    study = _study_with_timebox_trials(n_violations=1, n_ok=0)
    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})

    assert res["promote"] is False
    assert res["is_rejection_detail_override"] == "REJECT_INVALID_TIMEBOX"
    assert any(name == "STUDY_REJECTED_ON_TIMEBOX_VIOLATION" for name, _ in events)


def test_confirm_does_not_reject_clean_study_on_timebox(tmp_path, monkeypatch):
    """Regressionswächter: eine Study OHNE Zeitbox-Verletzung darf durch den neuen Check nicht
    beeinflusst werden (kein falsch-positiver REJECT_INVALID_TIMEBOX)."""
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "holdout_top_k": 5, "deflated_selection": False,
         "timebox_violation_tolerance": 0.0}))
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

    study = _study_with_timebox_trials(n_violations=0, n_ok=5)
    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})

    assert res.get("is_rejection_detail_override") != "REJECT_INVALID_TIMEBOX"


# ── champions.py: REJECT_INVALID_TIMEBOX ist nie zulaessig ──────────────────────────────────────
def test_champion_allowlist_never_admits_timebox_rejection():
    admissible = champions._configured_admissible_reject_details(
        {"champion_admissible_reject_details": ["REJECT_INVALID_TIMEBOX", "REJECT_HOLDOUT_DSR_DROP"]})
    assert "REJECT_INVALID_TIMEBOX" not in admissible
    assert "REJECT_HOLDOUT_DSR_DROP" in admissible


def test_champion_allowlist_default_excludes_timebox_rejection():
    admissible = champions._configured_admissible_reject_details({})
    assert "REJECT_INVALID_TIMEBOX" not in admissible


# ── sweep.py: Fail-Fast-Preflight-Entscheidungsfunktion ──────────────────────────────────────────
def test_fail_fast_helper_returns_none_when_nothing_listed_fails():
    from automation.optimizer.sweep import _first_failing_fail_fast_invariant

    checks = [{"name": "check_reward_term_variance", "passed": False},
              {"name": "check_holding_time_cap", "passed": True}]
    assert _first_failing_fail_fast_invariant(checks, ["check_holding_time_cap"]) is None


def test_fail_fast_helper_detects_listed_failure():
    from automation.optimizer.sweep import _first_failing_fail_fast_invariant

    checks = [{"name": "check_holding_time_cap", "passed": False}]
    assert _first_failing_fail_fast_invariant(checks, ["check_holding_time_cap"]) == "check_holding_time_cap"


def test_fail_fast_helper_empty_list_is_disabled():
    from automation.optimizer.sweep import _first_failing_fail_fast_invariant

    checks = [{"name": "check_holding_time_cap", "passed": False}]
    assert _first_failing_fail_fast_invariant(checks, []) is None
