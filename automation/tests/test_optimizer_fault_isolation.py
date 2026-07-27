import json, subprocess
import optuna, pytest
from pathlib import Path
from automation.optimizer import runner as runner_mod
from automation.optimizer.runner import run_backtest, BacktestRunError

class _FakeProc:
    def __init__(self, rc, stderr=""): self.returncode, self.stderr, self.stdout = rc, stderr, ""

def test_run_backtest_raises_backtesterror_not_pruned(monkeypatch, tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"global_settings": {"catalog_path": str(tmp_path)}}))
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **k: _FakeProc(1, "boom"))
    with pytest.raises(BacktestRunError):
        run_backtest(tmp_path, manifest)

def test_run_backtest_raises_on_missing_output(monkeypatch, tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"global_settings": {"catalog_path": str(tmp_path)}}))
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **k: _FakeProc(0))  # rc 0, aber keine Datei
    with pytest.raises(BacktestRunError):
        run_backtest(tmp_path, manifest)

def test_objective_prunes_on_backtest_error(monkeypatch, tmp_path):
    from automation.optimizer.run_optimization import make_objective
    def boom(*a, **k): raise BacktestRunError("subprocess died")
    obj = make_objective("ComboTrendVwapStrategy", run_backtest=boom,
                         build_trial=lambda **k: (tmp_path, tmp_path / "m.json"))
    study = optuna.create_study(direction="maximize")
    # study.optimize mit catch=() → TrialPruned wird intern korrekt als PRUNED verbucht, kein Crash
    study.optimize(obj, n_trials=1, catch=(json.JSONDecodeError, OSError))
    assert study.trials[0].state == optuna.trial.TrialState.PRUNED

def test_objective_penalizes_empty_tournament(monkeypatch, tmp_path):
    """Issue-1-Pfad: valides null-winner JSON ⇒ Penalty, NICHT Pruning."""
    from automation.optimizer.run_optimization import make_objective
    out = tmp_path / "tournament_result.json"
    out.write_text(json.dumps({"fully_eligible_pairs": 0, "aggregate_winner": None}))
    obj = make_objective("ComboTrendVwapStrategy",
                         run_backtest=lambda td, mp, **kw: out,
                         build_trial=lambda **k: (tmp_path, tmp_path / "m.json"))
    study = optuna.create_study(direction="maximize")
    study.optimize(obj, n_trials=1, catch=(json.JSONDecodeError, OSError))
    assert study.trials[0].state == optuna.trial.TrialState.COMPLETE  # Penalty-Wert, kein Crash

def test_confirm_handles_subprocess_failure():
    from automation.optimizer.confirm import confirm_on_holdout
    def boom(td, mp, **kw): raise BacktestRunError("holdout died")
    fake_build = lambda **k: (Path("/tmp/x"), Path("/tmp/x/m.json"))
    study = optuna.create_study(direction="maximize")
    study.add_trial(optuna.trial.create_trial(
        params={}, distributions={}, value=-1.0,
        user_attrs={"sampled_params": {}}))
    res = confirm_on_holdout(study, "ComboTrendVwapStrategy",
                             run_backtest=boom, build_trial=fake_build)
    assert res["passed"] is False
    assert res["reason"] == "holdout_subprocess_failed"

def test_run_exports_no_viable_when_all_pruned(monkeypatch):
    """Alle Trials PRUNED ⇒ export_no_viable_proposal, confirm NICHT aufgerufen."""
    import automation.optimizer.run_optimization as ro
    study = optuna.create_study(direction="maximize")  # 0 COMPLETE trials
    monkeypatch.setattr(ro, "optimize", lambda *a, **k: study)
    called = {"no_viable": False, "confirm": False}
    monkeypatch.setattr(ro, "export_no_viable_proposal",
                        lambda s, strat: called.__setitem__("no_viable", True))
    monkeypatch.setattr(ro, "confirm_on_holdout",
                        lambda *a, **k: called.__setitem__("confirm", True))
    ro.run("ComboTrendVwapStrategy", n_trials=1)
    assert called["no_viable"] is True and called["confirm"] is False
