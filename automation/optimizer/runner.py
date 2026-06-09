import subprocess
import json
from pathlib import Path
import os

def run_backtest(trial_dir: Path, manifest_path: Path) -> Path:
    """
    Ruft backtest_runner.py als Subprozess auf (check=False, timeout=10800).
    Liest den catalog_path aus dem Manifest (global_settings.catalog_path).
    Setzt das Environment:
       ETORO_CONFIG_DIR=trial_dir/config
       ETORO_LOGS_DIR=trial_dir/logs
       PYTHONUNBUFFERED=1
    argv lautet:
       [python, automation/backtest_runner.py, --momentum, --catalog-path <cat>,
        --config <manifest_path>, --output <trial_dir/tournament_result.json>]

    Gibt den absoluten Output-Pfad zurück.
    Wirft FileNotFoundError, falls nach dem Lauf keine Output-Datei existiert.
    """

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    catalog_path = manifest.get('global_settings', {}).get('catalog_path')
    if not catalog_path:
        raise ValueError("catalog_path missing in manifest")

    output_path = trial_dir / "tournament_result.json"

    env = os.environ.copy()
    env["ETORO_CONFIG_DIR"] = str(trial_dir / "config")
    env["ETORO_LOGS_DIR"] = str(trial_dir / "logs")
    env["PYTHONUNBUFFERED"] = "1"

    argv = [
        "python", "automation/backtest_runner.py",
        "--momentum",
        "--catalog-path", catalog_path,
        "--config", str(manifest_path),
        "--output", str(output_path)
    ]

    subprocess.run(argv, env=env, check=False, timeout=10800)

    if not output_path.exists():
        raise FileNotFoundError(f"Output file {output_path} not found after backtest run.")

    return output_path.resolve()
