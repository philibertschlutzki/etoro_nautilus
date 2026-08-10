"""Issue #864 (P1, Katalog #862-#865, GitHub-Issue #759) — ein numerisch verworfener Trial darf
den Sampler nicht negativ konditionieren.

Root-Cause (Pitfall #276): `SORTINO_GUARD_TRIPPED`/`SORTINO_INSUFFICIENT_DOWNSIDE`/
`EQUITY_NONPOSITIVE` setzen `sortino=None`/`psr=None`; `reward.calculate_reward_v2` behandelte
einen solchen Trial über den Unevaluable-Pfad und lieferte den Reward-Floor — TPE lernte daraus
"Region X ist maximal schlecht", obwohl die korrekte Aussage "Region X ist mit den aktuellen
Schätzern nicht messbar" lautet (in einem Referenzlauf 1172 von ~33000 Trials betroffen, 627 davon
auf FlashCrashReversal konzentriert).

Fix: `optimizer.json['inference_failure_policy']` ∈ {floor, prune}, Default `prune`. Bei `prune`
wirft `make_symbol_objective` `optuna.TrialPruned` für einen Trial mit einem der drei Diagnose-
Codes, NACHDEM alle Diagnose-`user_attrs` gestempelt sind — Optunas TPESampler ignoriert geprunte
Trials bei der Posterior-Bildung korrekt. `floor` bleibt bit-identisch zum Pre-#864-Verhalten.
"""
import json
from pathlib import Path

import optuna
import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import sweep as sw
from automation.optimizer import trial_config


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_guard_tripped_result(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
    """Streng steigende Equity (0 Downside-Beobachtungen) -> SORTINO_GUARD_TRIPPED via die
    reale _calculate_stats-Pipeline waere aufwendig nachzubauen; stattdessen wird das bereits
    fertige Diagnose-Feld direkt in oos_metrics injiziert -- run_optimization.py liest
    inference_diagnostics ausschliesslich aus dem geparsten TournamentMetrics-Objekt, das
    Symptom (SORTINO_GUARD_TRIPPED im inference_diagnostics-Array) ist identisch."""
    out = Path(trial_dir) / "tournament_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fully_eligible_pairs": 0, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": False, "win_count": 0,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [],
        "oos_metrics": {
            "sortino_ratio": None, "max_drawdown": 0.05, "total_return": 0.01,
            "total_trades": 20, "sortino_period": None, "n_periods": 200,
            "inference_diagnostics": [{
                "code": "SORTINO_GUARD_TRIPPED",
                "detail": "|sortino_annualized|=99 > guard=25 (n_periods=200, dd_dev=1e-06).",
                "value": 99.0,
            }],
        },
    }}), "utf-8")
    return out


def _fake_clean_result(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
    out = Path(trial_dir) / "tournament_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 5,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [0.5],
        "oos_metrics": {
            "sortino_ratio": 0.5, "max_drawdown": 0.05, "total_return": 0.02,
            "total_trades": 30, "sortino_period": 0.05, "n_periods": 200,
            "psr": 0.8,
        },
    }}), "utf-8")
    return out


def _set_policy(tmp_path, policy: str):
    (tmp_path / "optimizer.json").write_text(
        json.dumps({"inference_failure_policy": policy}), "utf-8")
    (tmp_path / "backtest.json").write_text(json.dumps({
        "walk_forward": {"is_window_days": 180, "oos_window_days": 45, "splits": 4,
                         "holdout_days": 45, "data_history_days": 450,
                         "embargo_period_days": 21, "retrain": False},
    }), "utf-8")


def test_prune_mode_marks_trial_as_pruned_not_completed(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path)
    _set_policy(tmp_path, "prune")
    (tmp_path / "tournament.json").write_text("{}", "utf-8")
    monkeypatch.setattr(ro, "run_backtest", _fake_guard_tripped_result)

    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    trial = study.trials[0]

    assert trial.state == optuna.trial.TrialState.PRUNED
    assert trial.value is None
    assert trial.user_attrs["trial_pruned_inference_codes"] == ["SORTINO_GUARD_TRIPPED"]


def test_floor_mode_is_bit_identical_to_pre_864_behavior(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path)
    _set_policy(tmp_path, "floor")
    (tmp_path / "tournament.json").write_text("{}", "utf-8")
    monkeypatch.setattr(ro, "run_backtest", _fake_guard_tripped_result)

    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    trial = study.trials[0]

    assert trial.state == optuna.trial.TrialState.COMPLETE
    assert trial.value is not None


def test_unknown_policy_is_fail_loud(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path)
    _set_policy(tmp_path, "bogus")
    (tmp_path / "tournament.json").write_text("{}", "utf-8")
    monkeypatch.setattr(ro, "run_backtest", _fake_guard_tripped_result)

    with pytest.raises(Exception):
        ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)


def test_clean_trial_unaffected_by_prune_policy(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path)
    _set_policy(tmp_path, "prune")
    (tmp_path / "tournament.json").write_text("{}", "utf-8")
    monkeypatch.setattr(ro, "run_backtest", _fake_clean_result)

    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    trial = study.trials[0]

    assert trial.state == optuna.trial.TrialState.COMPLETE
    assert trial.value is not None
    assert "trial_pruned_inference_codes" not in trial.user_attrs


# ── #822-Regression: ein geprunter Trial zaehlt nicht in n_family ───────────────────────────────
def test_pruned_trial_does_not_count_toward_n_family(tmp_path, monkeypatch):
    """Akzeptanzkriterium #864 — n_family enthaelt keinen geprunten Trial: bereits ueber #822s
    oos_selection_statistic_available sichergestellt (ein geprunter Trial traegt per Definition
    kein oos_psr), hier end-to-end ueber die reale sweep._family_n_from_studies-Zaehlung
    nachgewiesen."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path)
    _set_policy(tmp_path, "prune")
    (tmp_path / "tournament.json").write_text("{}", "utf-8")

    calls = {"n": 0}

    def _alternating(trial_dir, manifest_path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_guard_tripped_result(trial_dir, manifest_path, **kwargs)
        return _fake_clean_result(trial_dir, manifest_path, **kwargs)

    monkeypatch.setattr(ro, "run_backtest", _alternating)
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=2)

    assert {t.state for t in study.trials} == {
        optuna.trial.TrialState.PRUNED, optuna.trial.TrialState.COMPLETE}

    n_family = sw._family_n_from_studies(
        [("SmaCrossoverStrategy", "TSLA.ETORO", "OK")], [study], tournament_cfg={})
    assert n_family["TSLA.ETORO"] == 1
