import json
import optuna
from pathlib import Path
from automation.optimizer.manifest import WORK, catalog_fingerprint
from automation.optimizer.spaces import sample_params
from automation.optimizer.trial_config import build_trial, config_dir
from automation.optimizer.runner import run_backtest
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.reward import compute_reward
from automation.optimizer.confirm import confirm_on_holdout, export_proposal

STORAGE = f"sqlite:///{WORK / 'studies.db'}"

def make_objective(strategy: str):
    def objective(trial):
        sampled = sample_params(strategy, trial)
        trial.set_user_attr("sampled_params", sampled)

        cfg_dir = config_dir()
        optimizer_path = cfg_dir / "optimizer.json"
        seed = 42
        if optimizer_path.exists():
            with open(optimizer_path, "r", encoding="utf-8") as f:
                opt_data = json.load(f)
                seed = opt_data.get("seed", 42)

        trial_dir, manifest_path = build_trial(
            strategy_class=strategy,
            sampled=sampled,
            study_name=trial.study.study_name,
            trial_number=trial.number,
            seed=seed,
            n_folds=4,
            holdout_days=45
        )

        output_path = run_backtest(trial_dir, manifest_path)
        metrics = parse_tournament(output_path)

        tournament_path = cfg_dir / "tournament.json"
        risk_dd_cap = 0.30
        if tournament_path.exists():
            with open(tournament_path, "r", encoding="utf-8") as f:
                t_data = json.load(f)
                risk_dd_cap = t_data.get("max_drawdown", 0.30)

        universe_path = config_dir().parent.parent / "data" / "universe" / "momentum_ls.json"
        universe_size = 70
        if universe_path.exists():
            with open(universe_path, "r", encoding="utf-8") as f:
                u_data = json.load(f)
                universe_size = len(u_data.get("universe", []))

        reward = compute_reward(metrics, universe_size=universe_size, risk_dd_cap=risk_dd_cap)
        return reward
    return objective

def optimize(strategy: str, n_trials: int | None = None, n_jobs: int = 1):
    WORK.mkdir(parents=True, exist_ok=True)
    cfg_dir = config_dir()
    optimizer_path = cfg_dir / "optimizer.json"

    # Default values
    conf_n_trials = 100
    n_startup_trials = 16
    seed = 42

    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f)
            conf_n_trials = opt_data.get("n_trials", conf_n_trials)
            n_startup_trials = opt_data.get("n_startup_trials", n_startup_trials)
            seed = opt_data.get("seed", seed)

    if n_trials is None:
        n_trials = conf_n_trials

    study_name = f"study_{strategy}"

    sampler = optuna.samplers.TPESampler(
        multivariate=True,
        group=True,
        n_startup_trials=n_startup_trials,
        seed=seed
    )

    study = optuna.create_study(
        study_name=study_name,
        storage=STORAGE,
        direction="maximize",
        sampler=sampler,
        load_if_exists=True
    )

    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())

    study.optimize(make_objective(strategy), n_trials=n_trials, n_jobs=n_jobs)
    return study

def run(strategy: str, n_trials: int | None = None, n_jobs: int = 1):
    study = optimize(strategy, n_trials=n_trials, n_jobs=n_jobs)
    holdout_res = confirm_on_holdout(study, strategy)
    export_proposal(study, strategy, holdout_res)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run Hyperparameter Optimization")
    parser.add_argument("--strategy", type=str, required=True, help="Strategy class name to optimize")
    parser.add_argument("--n-trials", type=int, default=None, help="Number of trials (overrides config)")
    parser.add_argument("--n-jobs", type=int, default=1, help="Number of parallel worker jobs")

    args = parser.parse_args()
    run(strategy=args.strategy, n_trials=args.n_trials, n_jobs=args.n_jobs)
