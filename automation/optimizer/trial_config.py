import json
import shutil
import datetime as dt
from pathlib import Path
from automation.optimizer.manifest import git_commit, catalog_fingerprint, sha256_file, WORK
from automation.optimizer.resolve import resolve_params

def config_dir() -> Path:
    """Returns the default configuration directory."""
    import os
    if "ETORO_CONFIG_DIR" in os.environ:
        return Path(os.environ["ETORO_CONFIG_DIR"])
    # Default to automation/config from WORK parent
    return WORK.parent.parent / "automation" / "config"

def build_trial(
    strategy_class: str,
    sampled: dict,
    *,
    study_name: str,
    trial_number: int,
    seed: int,
    now: dt.datetime | None = None,
    holdout_days: int | None = None,
    n_folds: int | None = None,
    oos_window_days_override: int | None = None,
    base_cfg: Path | None = None
) -> tuple[Path, Path]:
    """
    Erzeugt isoliertes trial_dir; kopiert config_dir()-Inhalt nach trial_dir/config;
    schreibt experiment_manifest.json (manifest_version='1.0', Provenienz, global_settings,
    genau EINE Strategie mit resolve_params()-Ergebnis). Gibt (trial_dir, manifest_path) zurück.

    Window: end = midnight(now); if end.weekday()==6: end -= 1 Tag; end -= holdout_days;
            start = end - (is_window_days + n_folds*oos_window_days) Tage.
    """
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    if base_cfg is None:
        base_cfg = config_dir()

    backtest_cfg_path = base_cfg / "backtest.json"
    with open(backtest_cfg_path, "r", encoding="utf-8") as f:
        bt_data = json.load(f)

    wf = bt_data.get("walk_forward", {})
    if holdout_days is None:
        holdout_days = wf.get("holdout_days", 45)
    if n_folds is None:
        n_folds = wf.get("splits", 1)

    is_window_days = wf.get("is_window_days", 120)
    oos_window_days = oos_window_days_override if oos_window_days_override is not None else wf.get("oos_window_days", 30)

    # Calculate dates
    # Midnight of `now`
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # If Sunday (weekday() == 6), rollback to Saturday
    if end.weekday() == 6:
        end -= dt.timedelta(days=1)

    end -= dt.timedelta(days=holdout_days)
    start = end - dt.timedelta(days=is_window_days + n_folds * oos_window_days)

    # Setup directories
    trial_dir = WORK / study_name / f"trial_{trial_number:04d}"
    trial_cfg_dir = trial_dir / "config"
    trial_cfg_dir.mkdir(parents=True, exist_ok=True)

    # NEU: Logs-Verzeichnis für den Backtest-Subprozess anlegen (Fix Issue #346)
    (trial_dir / "logs").mkdir(parents=True, exist_ok=True)

    # Copy all JSON files from base_cfg
    for p in base_cfg.glob("*.json"):
        shutil.copy2(p, trial_cfg_dir / p.name)

    bt_trial_path = trial_cfg_dir / "backtest.json"
    if bt_trial_path.exists():
        with open(bt_trial_path, "r", encoding="utf-8") as f:
            bt_trial = json.load(f)
        bt_trial["walk_forward"] = {
            "is_window_days": is_window_days,
            "oos_window_days": oos_window_days,   # override-aware
            "splits": n_folds,
            "holdout_days": holdout_days,
            "walk_forward_active": True,
        }
        with open(bt_trial_path, "w", encoding="utf-8") as f:
            json.dump(bt_trial, f, indent=4)

    resolved_params = resolve_params(strategy_class, sampled, base_cfg)

    strategy_module = ""
    config_class = ""
    strats_path = base_cfg / "strategies.json"
    if strats_path.exists():
        with open(strats_path, "r", encoding="utf-8") as f:
            strats_data = json.load(f)
            for s in strats_data.get("strategies", []):
                if s.get("strategy_class") == strategy_class:
                    strategy_module = s.get("strategy_module", "")
                    config_class = s.get("config_class", "")
                    break

    # Resolve catalog_path from config or fallback to default
    raw_catalog_path = bt_data.get("catalog_path", "data/nautilus")
    # WORK is PROJECT_ROOT / "data" / "optimizer", so WORK.parent.parent is PROJECT_ROOT
    catalog_path = (WORK.parent.parent / raw_catalog_path).resolve()

    # Manifest payload
    tournament_file = base_cfg / "tournament.json"
    t_hash = sha256_file(tournament_file) if tournament_file.exists() else "unknown"

    manifest_payload = {
        "manifest_version": "1.0",
        "provenance": {
            "git_commit": git_commit(),
            "data_snapshot_sha256": catalog_fingerprint(),
            "frozen_tournament_sha256": t_hash,
            "study_name": study_name,
            "trial_number": trial_number
        },
        "global_settings": {
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "seed": seed,
            "catalog_path": str(catalog_path)
        },
        "strategies": [
            {
                "strategy_class": strategy_class,
                "strategy_module": strategy_module,
                "config_class": config_class,
                "params": resolved_params,
                "active": True
            }
        ]
    }

    manifest_path = trial_dir / "experiment_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_payload, f, indent=2)

    return trial_dir, manifest_path
