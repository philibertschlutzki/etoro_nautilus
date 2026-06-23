import json
import subprocess
from pathlib import Path
import os
import optuna

class BacktestRunError(RuntimeError):
    """Subprocess-Backtest fehlgeschlagen (returncode != 0 oder kein Output)."""


def _run_backtest_inprocess(trial_dir: Path, manifest_path: Path) -> Path:
    """A4.9: In-Process-Variante. Ruft den importierbaren Entry `run_backtest_inprocess`
    (im Test über `runner.run_backtest_inprocess` mockbar). Fault-Isolation sinkt von Trial-
    auf Study-Ebene: fachliche Trial-Fehler → `optuna.TrialPruned`; fundamentale Fehler
    (ImportError/ModuleNotFoundError/SyntaxError) propagieren (Fail-Fast, crasht die Study)."""
    output_path = Path(trial_dir) / "tournament_result.json"
    fn = globals().get("run_backtest_inprocess")
    if fn is None:
        from automation.backtest_runner import run_backtest_inprocess as fn
    try:
        fn(manifest_path, output_path)
    except (ImportError, ModuleNotFoundError, SyntaxError):
        raise  # fundamentaler Fehler → Fail-Fast
    except optuna.TrialPruned:
        raise
    except Exception as e:
        raise optuna.TrialPruned(f"In-process backtest failed: {e}")
    if not output_path.exists():
        raise optuna.TrialPruned("In-process backtest produced no output")
    return output_path


def run_backtest(trial_dir: Path, manifest_path: Path, *, mode: str = "subprocess") -> Path:
    """mode='subprocess' (Default, unverändert) ruft backtest_runner.py als Subprozess
       (check=False, timeout=10800); mode='inprocess' ruft den In-Process-Entry (A4.9).
       catalog_path wird aus dem Manifest (global_settings.catalog_path) gelesen.
       Env: ETORO_CONFIG_DIR=trial_dir/config, ETORO_LOGS_DIR=trial_dir/logs, PYTHONUNBUFFERED=1.
       argv: [python, automation/backtest_runner.py, --momentum, --catalog-path <cat>,
              --config <manifest_path>, --output <trial_dir/tournament_result.json>]
       Gibt den Output-Pfad zurück; raise BacktestRunError, falls Output fehlt (Subprozess)."""
    if mode == "inprocess":
        return _run_backtest_inprocess(trial_dir, manifest_path)

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    catalog_path = manifest.get("global_settings", {}).get("catalog_path")
    if not catalog_path:
        raise ValueError("Missing catalog_path in manifest global_settings")

    output_path = trial_dir / "tournament_result.json"

    env = os.environ.copy()
    env["ETORO_CONFIG_DIR"] = str(trial_dir / "config")
    env["ETORO_LOGS_DIR"] = str(trial_dir / "logs")
    env["PYTHONUNBUFFERED"] = "1"

    argv = [
        "python", "automation/backtest_runner.py",
        "--momentum",
        "--catalog-path", str(catalog_path),
        "--config", str(manifest_path),
        "--output", str(output_path)
    ]

    result = subprocess.run(argv, env=env, timeout=10800, check=False, capture_output=True, text=True)

    if result.returncode != 0 or not output_path.exists():
        print(f"Subprocess crashed with return code {result.returncode}, skipping trial...")
        stderr_tail = result.stderr if result.stderr else "No stderr"
        if result.stderr:
            print(f"Subprocess stderr:\n{result.stderr}")
        raise BacktestRunError(stderr_tail)

    return output_path
