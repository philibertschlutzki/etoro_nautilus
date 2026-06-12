import json, datetime as dt
from pathlib import Path
from automation.optimizer.trial_config import build_trial, config_dir
from automation.optimizer.parsing import TournamentMetrics

UTC = dt.timezone.utc
def _parse(s): return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

def _holdout_days():
    with open(config_dir() / "backtest.json") as f:
        return json.load(f).get("walk_forward", {}).get("holdout_days", 45)

def test_holdout_oos_disjoint_from_optimization_window(tmp_path, monkeypatch):
    now = dt.datetime(2026, 6, 11, 9, 0, tzinfo=UTC)
    hd = _holdout_days()
    # Optimierungs-Trial
    _, opt_mf = build_trial("ComboTrendVwapStrategy", {}, study_name="s", trial_number=0,
                            seed=42, now=now, holdout_days=hd, n_folds=4)
    opt = json.loads(Path(opt_mf).read_text())["global_settings"]
    opt_start, opt_end = _parse(opt["start_time"]), _parse(opt["end_time"])
    # Holdout-Confirm-Trial
    _, ho_mf = build_trial("ComboTrendVwapStrategy", {}, study_name="s_holdout", trial_number=0,
                           seed=42, now=now, holdout_days=0, n_folds=1, oos_window_days_override=hd)
    ho = json.loads(Path(ho_mf).read_text())["global_settings"]
    ho_end = _parse(ho["end_time"])
    ho_oos_start = ho_end - dt.timedelta(days=hd)
    # bewerteter OOS [ho_oos_start, ho_end] disjunkt von Optimierung [opt_start, opt_end]
    assert ho_oos_start >= opt_end          # kein echter Overlap
    assert ho_end > opt_end                 # Holdout liegt strikt nach der Optimierung

def test_trial_config_walkforward_matches_sizing(tmp_path):
    now = dt.datetime(2026, 6, 11, tzinfo=UTC)
    td, _ = build_trial("ComboTrendVwapStrategy", {}, study_name="s", trial_number=1,
                        seed=42, now=now, holdout_days=45, n_folds=4)
    wf = json.loads((Path(td) / "config" / "backtest.json").read_text())["walk_forward"]
    assert wf["splits"] == 4
    assert wf["holdout_days"] == 45

def cmod_dummy_metrics():
    return TournamentMetrics(
        oos_evaluated=True,
        oos_eligible=True,
        is_sortino_median=1.0,
        oos_sortino=1.0,
        oos_max_drawdown=0.1,
        oos_total_trades=10,
        win_count=10,
        fully_eligible_pairs=10
    )

def test_confirm_passes_holdout_days_as_oos_override(monkeypatch):
    from automation.optimizer import confirm as cmod
    captured = {}
    def spy_build(**kw):
        captured.update(kw)
        return (Path("/tmp/x"), Path("/tmp/x/m.json"))
    def fake_run(td, mp): return Path("/tmp/x/tournament_result.json")
    # parse_tournament/Output minimal mocken, sodass confirm nicht crasht:
    monkeypatch.setattr(cmod, "parse_tournament", lambda p: cmod_dummy_metrics())
    import optuna
    study = optuna.create_study(direction="maximize")
    study.add_trial(optuna.trial.create_trial(params={}, distributions={}, value=-1.0,
                                              user_attrs={"sampled_params": {}}))
    cmod.confirm_on_holdout(study, "ComboTrendVwapStrategy",
                            run_backtest=fake_run, build_trial=spy_build)
    hd = _holdout_days()
    assert captured.get("oos_window_days_override") == hd
    assert captured.get("holdout_days") == 0 and captured.get("n_folds") == 1
