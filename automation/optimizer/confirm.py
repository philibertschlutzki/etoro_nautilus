import json
from pathlib import Path
from automation.optimizer.trial_config import build_trial, config_dir
from automation.optimizer.runner import run_backtest, BacktestRunError
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.manifest import WORK

def confirm_on_holdout(
    study,
    strategy: str,
    *,
    run_backtest=run_backtest,
    build_trial=build_trial
) -> dict:
    """
    Trial mit holdout_days=0, n_folds=1 (Holdout = reguläres OOS).
    Liest risk_dd_cap aus tournament.json.
    passed = oos_evaluated & oos_eligible & oos_sortino>0 & oos_max_drawdown<=cap.
    Rückgabe: {'passed': bool, 'metrics': dict, 'trial_dir': str}.
    """
    best_trial = study.best_trial
    sampled = best_trial.user_attrs.get("sampled_params", best_trial.params)

    cfg_dir = config_dir()
    optimizer_path = cfg_dir / "optimizer.json"
    seed = 42
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f)
            seed = opt_data.get("seed", 42)

    # Dynamisch Holdout-Tage auslesen (Zero-Hardcoding)
    backtest_path = cfg_dir / "backtest.json"
    holdout_days_cfg = 45
    if backtest_path.exists():
        with open(backtest_path, "r", encoding="utf-8") as f:
            bt_data = json.load(f)
            holdout_days_cfg = bt_data.get("walk_forward", {}).get("holdout_days", 45)

    # Erzeuge Holdout-Trial mit holdout_days=0 und n_folds=1, aber OOS override auf Holdout-Länge
    trial_dir, manifest_path = build_trial(
        strategy_class=strategy,
        sampled=sampled,
        study_name=f"{study.study_name}_holdout",
        trial_number=best_trial.number,
        seed=seed,
        holdout_days=0,
        n_folds=1,
        oos_window_days_override=holdout_days_cfg
    )

    # Subprozess/Backtest ausführen
    try:
        output_path = run_backtest(trial_dir, manifest_path)
        metrics = parse_tournament(output_path)
    except BacktestRunError:
        return {
            "passed": False,
            "metrics": {},
            "trial_dir": str(trial_dir),
            "reason": "holdout_subprocess_failed"
        }

    tournament_path = cfg_dir / "tournament.json"
    risk_dd_cap = 0.30  # Fallback
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            t_data = json.load(f)
            risk_dd_cap = t_data.get("max_drawdown", 0.30)

    oos_sortino = metrics.oos_sortino if metrics.oos_sortino is not None else 0.0

    passed = (
        metrics.oos_evaluated and
        metrics.oos_eligible and
        oos_sortino > 0.0 and
        metrics.oos_max_drawdown <= risk_dd_cap
    )

    return {
        "passed": passed,
        "metrics": {
            "oos_sortino": metrics.oos_sortino,
            "oos_max_drawdown": metrics.oos_max_drawdown,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_eligible": metrics.oos_eligible,
        },
        "trial_dir": str(trial_dir)
    }


def export_no_viable_proposal(study, strategy: str) -> Path:
    """
    Exportiert ein Proposal mit status="NO_VIABLE_TRIAL", falls alle Trials gepruned wurden.
    """
    payload = {
        "strategy": strategy,
        "status": "NO_VIABLE_TRIAL",
        "reward": None,
        "proposed_params_override": {},
        "note": "No valid trial found due to data coverage or OOS gating rejection.",
        "holdout": None
    }

    out_path = WORK / f"proposal_{strategy}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


def export_proposal(study, strategy: str, holdout: dict) -> Path:
    """
    Schreibt data/optimizer/proposal_<strategy>.json mit proposed_params_override
    (best_trial.user_attrs['sampled_params']), Reward, Holdout-Metriken,
    status = 'READY_FOR_PR' wenn holdout['passed'] sonst 'REJECTED_ON_HOLDOUT'.
    """
    best_trial = study.best_trial
    sampled = best_trial.user_attrs.get("sampled_params", best_trial.params)

    status = "READY_FOR_PR" if holdout.get("passed") else "REJECTED_ON_HOLDOUT"

    payload = {
        "strategy": strategy,
        "status": status,
        "reward": best_trial.value,
        "proposed_params_override": sampled,
        "holdout": holdout
    }

    out_path = WORK / f"proposal_{strategy}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path
