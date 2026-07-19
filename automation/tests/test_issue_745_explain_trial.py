"""Issue #745 — CLI `automation/optimizer/explain_trial.py`.

Deckt ab: deterministische, vollständige Ausgabe gegen eine Test-SQLite-Fixture; ein fehlender
Trial liefert eine klare Fehlermeldung statt eines Stacktrace.
"""
import json

import optuna
import pytest

from automation.optimizer import explain_trial as et


def _make_study(storage_url: str, study_name: str):
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                load_if_exists=True)
    trial = study.ask()
    trial.set_user_attr("oos_evaluated", True)
    trial.set_user_attr("oos_eligible", False)
    trial.set_user_attr("oos_total_trades", 15)
    trial.set_user_attr("is_total_trades", 40)
    trial.set_user_attr("hit_trade_cap", False)
    trial.set_user_attr("oos_coherence_violation", False)
    trial.set_user_attr("is_rejection_detail", "REJECT_OOS_MIN_TRADES")
    trial.set_user_attr("oos_rejection_reasons", ["min_trades: 15 < 20", "min_win_rate: 0.1 < 0.15"])
    trial.set_user_attr("oos_gate_deltas", {"min_trades": -5, "min_win_rate": -0.05, "max_drawdown": 0.20})
    trial.set_user_attr("reward_terms", {
        "branch": "unevaluable", "base": -1.0, "divergence": 0.0, "dd_penalty": 0.1,
        "param_pen": 0.05, "turnover": 0.02, "fold_dispersion": 0.01, "tie_breaker": 0.0,
    })
    study.tell(trial, -1.18)
    return study


@pytest.fixture
def wired_study(tmp_path, monkeypatch):
    storage_url = f"sqlite:///{tmp_path / 'study.db'}"
    study_name = "study_TestStrat_A_ETORO"
    _make_study(storage_url, study_name)
    monkeypatch.setattr(et, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    return study_name


def test_build_explanation_sections_and_near_miss_ordering(wired_study):
    study, _, _ = et._load_study("TestStrat", "A.ETORO")
    trial = et._find_trial(study, 0)
    explanation = et.build_explanation(study, trial)

    assert explanation["trial_number"] == 0
    assert explanation["reward"]["value"] == -1.18
    assert explanation["reward"]["terms"]["base"] == -1.0
    assert explanation["gates"]["oos_total_trades"] == 15

    stages = [c["stage"] for c in explanation["rejection_chain"]]
    assert "is_gate" in stages
    assert explanation["rejection_chain"][0]["detail"] == "REJECT_OOS_MIN_TRADES"

    # Knappste Marge zuerst: |min_win_rate|=0.05 < |max_drawdown|=0.20 < |min_trades|=5.
    metrics_in_order = [d["metric"] for d in explanation["near_miss_deltas"]]
    assert metrics_in_order == ["min_win_rate", "max_drawdown", "min_trades"]


def test_cli_markdown_output(wired_study, capsys):
    rc = et.main(["--strategy", "TestStrat", "--symbol", "A.ETORO", "--trial", "0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "# Trial #0" in out
    assert "## Reward" in out
    assert "## Gates" in out
    assert "## Rejection-Kette" in out
    assert "## Near-Miss-Deltas" in out
    assert "REJECT_OOS_MIN_TRADES" in out


def test_cli_json_output_is_valid_and_deterministic(wired_study, capsys):
    rc = et.main(["--strategy", "TestStrat", "--symbol", "A.ETORO", "--trial", "0", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["trial_number"] == 0
    assert payload["gates"]["oos_eligible"] is False

    rc2 = et.main(["--strategy", "TestStrat", "--symbol", "A.ETORO", "--trial", "0", "--format", "json"])
    assert rc2 == 0
    payload2 = json.loads(capsys.readouterr().out)
    assert payload == payload2


def test_missing_trial_returns_clear_error_not_a_traceback(wired_study, capsys):
    rc = et.main(["--strategy", "TestStrat", "--symbol", "A.ETORO", "--trial", "999"])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Trial #999" in captured.err
    assert "nicht in Study" in captured.err


def test_missing_study_returns_clear_error(monkeypatch, capsys):
    monkeypatch.setattr(et, "resolve_storage",
                        lambda *, study_name, base_cfg=None: "sqlite:////nonexistent/does_not_exist.db")
    rc = et.main(["--strategy", "GhostStrategy", "--symbol", "ZZZ.ETORO", "--trial", "0"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "nicht gefunden" in captured.err
