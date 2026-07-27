import json
import subprocess
import time
from pathlib import Path
import os
import optuna

class BacktestRunError(RuntimeError):
    """Subprocess-Backtest fehlgeschlagen (returncode != 0 oder kein Output)."""


def _persist_subprocess_logs(trial_dir: Path, stdout: str, stderr: str) -> None:
    """Issue #416 — Subprozess-stdout/stderr pro Trial nach ``trial_dir/logs/`` schreiben — AUCH im
    Erfolgsfall. Vorher wurde der Output bei ``returncode==0`` komplett verworfen (``capture_output``),
    sodass die reichhaltige Backtest-Diagnose (Daten-Fenster via `_format_backtest_window`/#403,
    `[OOS-Drop]`-Trail/#257, Per-Pair-Trade-Counts, Tournament-Rejections) je erfolgreichem Trial
    verloren ging → Per-Trial-Fehleranalyse unmoeglich. Das ``logs/``-Verzeichnis existiert seit
    Pitfall #62/#346; ``mkdir(exist_ok=True)`` ist defensiv."""
    log_dir = Path(trial_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "backtest_stdout.log").write_text(stdout, encoding="utf-8")
    (log_dir / "backtest_stderr.log").write_text(stderr, encoding="utf-8")


def _record_timing(timings: dict | None, t0: float) -> None:
    """Issue #415 — schreibt die Wall-Clock-Dauer seit ``t0`` in den optionalen ``timings``-Out-Dict
    (``duration_s`` + gerundete ``backtest_ms``). Out-Param statt geaenderter Rueckgabe-Signatur, damit
    KEINE bestehende ``run_backtest``-Aufrufstelle bricht (Signatur-Kompat, Pitfall #33)."""
    if timings is not None:
        duration_s = time.perf_counter() - t0
        timings["duration_s"] = duration_s
        timings["backtest_ms"] = round(duration_s * 1000)


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


def run_backtest(trial_dir: Path, manifest_path: Path, *, mode: str = "subprocess",
                 timings: dict | None = None, config_dir: Path | None = None) -> Path:
    """mode='subprocess' (Default, unverändert) ruft backtest_runner.py als Subprozess
       (check=False, timeout=10800); mode='inprocess' ruft den In-Process-Entry (A4.9).
       catalog_path wird aus dem Manifest (global_settings.catalog_path) gelesen.
       Env: ETORO_CONFIG_DIR=config_dir (Default trial_dir/config, siehe Issue #796),
       ETORO_LOGS_DIR=trial_dir/logs, PYTHONUNBUFFERED=1.
       argv: [python, automation/backtest_runner.py, --momentum, --catalog-path <cat>,
              --config <manifest_path>, --output <trial_dir/tournament_result.json>]
       Gibt den Output-Pfad zurück; raise BacktestRunError, falls Output fehlt (Subprozess).

       Issue #415 — misst die Wall-Clock-Dauer in BEIDEN Modi. Ist ``timings`` ein Dict, werden
       ``duration_s``/``backtest_ms`` hineingeschrieben (Out-Param ⇒ keine Signatur-Aenderung).

       Issue #796 — ``config_dir`` erlaubt EINE eingefrorene Study-Config (via
       ``trial_config.freeze_study_config``) statt der Pro-Trial-Kopie unter ``trial_dir/config``
       zu referenzieren; fehlt der Parameter (Default ``None``), bleibt das Verhalten bit-identisch
       zu HEAD (``trial_dir/config``, wie es ``build_trial(copy_config=True)`` anlegt)."""
    if mode == "inprocess":
        t0 = time.perf_counter()
        try:
            return _run_backtest_inprocess(trial_dir, manifest_path)
        finally:
            _record_timing(timings, t0)

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    catalog_path = manifest.get("global_settings", {}).get("catalog_path")
    if not catalog_path:
        raise ValueError("Missing catalog_path in manifest global_settings")

    output_path = trial_dir / "tournament_result.json"

    env = os.environ.copy()
    env["ETORO_CONFIG_DIR"] = str(config_dir) if config_dir is not None else str(trial_dir / "config")
    env["ETORO_LOGS_DIR"] = str(trial_dir / "logs")
    env["PYTHONUNBUFFERED"] = "1"

    argv = [
        "python", "automation/backtest_runner.py",
        "--momentum",
        "--catalog-path", str(catalog_path),
        "--config", str(manifest_path),
        "--output", str(output_path)
    ]

    t0 = time.perf_counter()
    result = subprocess.run(argv, env=env, timeout=10800, check=False, capture_output=True, text=True)
    _record_timing(timings, t0)

    # Issue #416 — Output IMMER persistieren (auch im Erfolgsfall), bevor ueber returncode
    # entschieden wird. getattr, da Test-Mocks (SimpleNamespace) stdout/stderr nicht setzen muessen.
    _persist_subprocess_logs(trial_dir, getattr(result, "stdout", "") or "",
                             getattr(result, "stderr", "") or "")

    if result.returncode != 0 or not output_path.exists():
        print(f"Subprocess crashed with return code {result.returncode}, skipping trial...")
        stderr_tail = result.stderr if result.stderr else "No stderr"
        if result.stderr:
            print(f"Subprocess stderr:\n{result.stderr}")
        raise BacktestRunError(stderr_tail)

    return output_path
