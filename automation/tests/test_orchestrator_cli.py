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
    from automation.optimizer.manifest import catalog_fingerprint

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

    # Issue #993 — die Whitelist ist erst nicht-leer, wenn ein vollstaendiges Promotionsrecord die
    # Deployment-Grenze (deployment_gate.evaluate_deployment_eligibility) besteht; das Phase-4-
    # Tournament-Feld ``oos_eligible``/``oos_evaluated`` allein genuegt seit #993 nicht mehr.
    optimizer_dir = tmp_path / "data" / "optimizer"
    optimizer_dir.mkdir(parents=True, exist_ok=True)
    proposal = {
        "status": "READY_FOR_PR",
        "R_symbol": 1.0,
        "R_global": 0.2,
        "promotion_margin": 0.0,
        "data_snapshot_sha256": catalog_fingerprint(),
        "holdout": {"symbol": {
            "deflated_dsr": 0.97, "oos_psr": 0.80, "holdout_ci_lower_sortino": 0.05,
            "pbo": 0.30, "pbo_n_configs": 40,
            # Issue #1007 (Katalog #858) — neunte deployment_gate-Klausel 'study_invariants_clean'
            # ist fail-closed bei fehlendem Feld; [] == "geprueft, keine blockierende Invariante",
            # noetig, damit dieses Fixture weiterhin ALLE neun Klauseln besteht.
            "blocking_invariant_names": [],
        }, "global": {}},
    }
    (optimizer_dir / "proposal_SmaCrossoverStrategy_AAA.ETORO.json").write_text(
        json.dumps(proposal), encoding="utf-8")

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
