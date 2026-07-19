"""Issue #686 — Pflicht-SQLite-Purge für reward_semantics_version-Bumps.

Ergänzt den bereits vorhandenen IN-PROCESS-Guard (``run_optimization._check_reward_semantics_version``,
der eine EINZELNE geladene Study fail-loud abbricht + löscht, sobald ihre gestempelte
``reward_semantics_version`` von der aktuellen abweicht) um ein BULK-Purge-Werkzeug: nach einem
Semantik-Bump (z. B. #676/#677/#684, gebündelt in v12) sollen ALLE betroffenen Studies VOR dem
nächsten Sweep in einem Schritt bereinigt werden, statt N einzelne Sweep-Starts nacheinander
fail-loud abbrechen zu lassen — genau das im v11-Changelog referenzierte Runbook
("{WORK}/sweep/*.db löschen, NACHDEM alle Kohorte-Fixes gemergt sind").

CLI:
    python -m automation.optimizer.purge_stale_studies [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path

import optuna

from automation.optimizer.manifest import WORK
from automation.optimizer.trial_config import config_dir


def _current_reward_semantics_version(base_cfg: Path | None = None) -> int | None:
    cfg_dir = base_cfg or config_dir()
    opt_path = cfg_dir / "optimizer.json"
    if not opt_path.exists():
        return None
    try:
        return (json.loads(opt_path.read_text("utf-8")) or {}).get("reward_semantics_version")
    except (OSError, ValueError):
        return None


def find_stale_study_dbs(*, sweep_dir: Path | None = None,
                         current_version: int | None = None) -> list[dict]:
    """Scannt ALLE ``*.db``-Dateien im Standard-Sweep-Verzeichnis (SQLite-Default, Pitfall #53) und
    meldet jede Study, deren gestempelte ``reward_semantics_version`` von ``current_version``
    abweicht — dieselbe Bedingung wie der In-Process-Guard
    (``run_optimization._check_reward_semantics_version``), nur BULK statt einzeln beim nächsten
    Studien-Load. Rein lesend, KEINE Löschung (siehe ``purge_stale_studies``). Eine Study OHNE
    Trials ist per Definition nicht "vergiftet" (nichts zum Purgen) und wird übersprungen. Rückgabe
    je stale Study: ``{'db_path', 'study_name', 'stored_version', 'n_trials'}``."""
    if sweep_dir is None:
        sweep_dir = WORK / "sweep"
    if current_version is None:
        current_version = _current_reward_semantics_version()
    stale: list[dict] = []
    if current_version is None or not sweep_dir.exists():
        return stale
    for db_path in sorted(sweep_dir.glob("*.db")):
        storage = f"sqlite:///{db_path}"
        try:
            study_names = optuna.study.get_all_study_names(storage=storage)
        except Exception:
            continue
        for name in study_names:
            try:
                study = optuna.load_study(study_name=name, storage=storage)
            except Exception:
                continue
            n_trials = len(study.trials)
            if n_trials == 0:
                continue
            stored_version = study.user_attrs.get("reward_semantics_version")
            if stored_version != current_version:
                stale.append({
                    "db_path": str(db_path), "study_name": name,
                    "stored_version": stored_version, "n_trials": n_trials,
                })
    return stale


def purge_stale_studies(*, sweep_dir: Path | None = None, current_version: int | None = None,
                        dry_run: bool = False,
                        logger: logging.Logger | None = None) -> list[dict]:
    """Löscht jede DB-Datei, die MINDESTENS eine stale Study enthält (die Default-Konvention ist
    eine Study je SQLite-Datei, #412/Pitfall #77 — ``Path.unlink`` statt eines selektiven
    Trial-Löschens innerhalb der Datei). ``dry_run=True`` meldet nur, löscht nichts. Rückgabe: die
    Liste der (potenziell) betroffenen Studies (wie ``find_stale_study_dbs``).

    Issue #733 (Pitfall #223) — symmetrisches Löschen: eine Study ist ein Löschpaar aus `.db`
    UND dem zugehörigen ``WORK/study_name/trial_*/``-Baum, nicht nur der SQLite-Datei. Vor diesem
    Fix hinterliess jeder der 7 dokumentierten Pflicht-Purge-Übergänge (``optimizer.json``-
    Changelog v7→v14) eine komplette verwaiste Trial-Generation — die `.db` verschwand, der
    um Grössenordnungen grössere Trial-Baum blieb dauerhaft liegen."""
    logger = logger or logging.getLogger("optimizer")
    stale = find_stale_study_dbs(sweep_dir=sweep_dir, current_version=current_version)
    # Trial-Bäume liegen als Geschwister von ``sweep/`` unter WORK (``WORK/study_name/trial_*``,
    # vgl. trial_config.build_trial); ``sweep_dir.parent`` statt der harten WORK-Konstante hält die
    # Funktion isoliert testbar (ein injizierter ``sweep_dir`` bezieht sich dann auf denselben
    # temporären Wurzelbaum, nicht auf das reale ``data/optimizer``).
    work_root = (sweep_dir if sweep_dir is not None else (WORK / "sweep")).parent
    purged_paths: set[str] = set()
    for entry in stale:
        db_path = entry["db_path"]
        study_name = entry["study_name"]
        trial_root = work_root / study_name
        already_unlinked = db_path in purged_paths
        if dry_run:
            if not already_unlinked:
                logger.info(
                    "[DRY-RUN] Würde %s löschen (Study '%s', v%s ≠ aktuell, %d Trials).",
                    db_path, study_name, entry["stored_version"], entry["n_trials"],
                )
            if trial_root.exists():
                logger.info(
                    "[DRY-RUN] Würde Trial-Verzeichnis %s löschen (Study '%s').",
                    trial_root, study_name,
                )
        else:
            if not already_unlinked:
                try:
                    Path(db_path).unlink()
                    logger.warning(
                        "♻️ [#686] %s gelöscht (Study '%s', v%s ≠ aktuell, %d Trials) — stale "
                        "reward_semantics_version.",
                        db_path, study_name, entry["stored_version"], entry["n_trials"],
                    )
                except OSError as e:
                    logger.error("Fehler beim Löschen von %s: %s", db_path, e)
                    continue
            if trial_root.exists():
                try:
                    shutil.rmtree(trial_root)
                    logger.warning(
                        "♻️ [#733] Trial-Verzeichnis %s gelöscht (symmetrisches Löschen zu '%s').",
                        trial_root, db_path,
                    )
                except OSError as e:
                    logger.error("Fehler beim Löschen von %s: %s", trial_root, e)
        purged_paths.add(db_path)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Issue #686 — Bulk-Purge aller SQLite-Studies mit stale reward_semantics_version.")
    parser.add_argument("--dry-run", action="store_true", help="Nur melden, nichts löschen.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stale = purge_stale_studies(dry_run=args.dry_run)
    if not stale:
        print("Keine stale Studies gefunden — alles aktuell.")
    else:
        action = "würden" if args.dry_run else "wurden"
        print(f"{len(stale)} stale Stud(y/ies) {action} verarbeitet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
