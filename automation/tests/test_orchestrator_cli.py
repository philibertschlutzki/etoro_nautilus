import pytest
from automation import daily_orchestrator as orch

def test_dry_run_flag_is_removed():
    parser = orch.build_arg_parser()
    with pytest.raises(SystemExit):              # argparse: unrecognized argument
        parser.parse_args(["--dry-run"])

def test_no_deploy_flag_recognized():
    parser = orch.build_arg_parser()
    assert parser.parse_args(["--no-deploy"]).no_deploy is True
    assert parser.parse_args([]).no_deploy is False

def test_phase5_no_deploy_early_exit(tmp_path, monkeypatch, caplog):
    import json
    # Fixture-Tournament: mind. 1 Symbol besteht sein OOS-Gate (Whitelist nicht leer)
    tournament = {
        "fully_eligible_pairs": 1,
        "oos_not_evaluable_pairs": 0, "oos_failed_pairs": 0,
        "per_symbol_winners": {"AAA.ETORO": {
            "strategy": "SmaCrossoverStrategy", "oos_eligible": True, "oos_evaluated": True}},
        "aggregate_winner": {
            "strategy": "SmaCrossoverStrategy", "win_count": 1,
            "oos_evaluated": True, "oos_eligible": True,
            "oos_metrics": {"sortino_ratio": 1.0, "max_drawdown": 0.10}},
    }
    tfile = tmp_path / "tournament.json"
    tfile.write_text(json.dumps(tournament), encoding="utf-8")

    # State-/Whitelist-Pfad in tmp umleiten, damit kein Repo-Schreibzugriff nötig ist
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path, raising=False)
    (tmp_path / "data" / "state").mkdir(parents=True, exist_ok=True)

    # Mock bot_script.exists() so we don't fail before reaching the no_deploy logic
    bot_script_dir = tmp_path / "automation"
    bot_script_dir.mkdir(parents=True, exist_ok=True)
    (bot_script_dir / "momentum_ls_run.py").touch()

    # Bot-Start MUSS unterbleiben:
    def _fail_popen(*a, **k):
        raise AssertionError("Popen darf bei --no-deploy NICHT aufgerufen werden")
    monkeypatch.setattr(orch.subprocess, "Popen", _fail_popen)

    logger = orch.logging.getLogger("t")
    logger.setLevel(orch.logging.INFO)
    rc = orch.phase5_live_deployment(logger,
                                     {"universe": []},
                                     {"tournament_path": str(tfile)},
                                     no_deploy=True)
    assert rc == 0
    assert "LIVE_DEPLOY_SKIPPED_NO_DEPLOY" in caplog.text
