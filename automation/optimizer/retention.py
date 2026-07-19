"""Issue #733 — Normalfall-Retention für abgeschlossene Trial-Verzeichnisse.

Ergänzt das symmetrische stale-Purge (``purge_stale_studies``, das ein `.db` + das zugehörige
``trial_*/``-Verzeichnis als EIN Löschpaar behandelt) um den Normalfall: eine Study, die einfach
abgeschlossen wurde (kein ``reward_semantics_version``-Mismatch), behält bislang JEDES
``trial_*/``-Verzeichnis für immer — der grösste Einzeltreiber des 101-GB-Wachstums von
``data/optimizer`` (siehe RC1/RC2 im Issue-Katalog). Dieses Modul liefert das Werkzeug dafür:

* ``collect_referenced_trial_dirs`` — sammelt jeden ``trial_dir``, der aktuell von einem
  ``champions/*.json``-Eintrag (Feld ``provenance.source_trial_dir``) oder einem offenen
  ``proposal_*.json`` (Felder ``holdout_trial_dir`` / ``trial_dir`` / ``holdout.trial_dir``,
  je nach Proposal-Variante — Symbol- vs. Global-Pfad, siehe ``confirm.py``) referenziert wird.
* ``prune_completed_trial_dirs`` — löscht aus ``WORK/study_name/trial_*/`` alles ausser den
  übergebenen ``keep``-Pfaden.

Vorbild-Pattern (Alters-/Retention-Idee, hier nur referenzbasiert statt alters-basiert):
``automation/log_manager.py:cleanup_old_logs``.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from automation.optimizer.manifest import WORK


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _add_ref(keep: set[Path], value) -> None:
    if isinstance(value, str) and value:
        keep.add(Path(value).resolve())


def collect_referenced_trial_dirs(work_dir: Path | None = None) -> set[Path]:
    """Sammelt alle aktuell referenzierten ``trial_dir``-Pfade aus ``champions/`` und offenen
    ``proposal_*.json``-Dateien (rein lesend, keine Mutation). Ein nicht-existentes/kaputtes
    Verzeichnis bzw. eine kaputte JSON-Datei wird übersprungen (defensiv, analog
    ``champions._read_entry``) statt den Aufrufer abstürzen zu lassen."""
    if work_dir is None:
        work_dir = WORK
    keep: set[Path] = set()

    champions_dir = work_dir / "champions"
    if champions_dir.exists():
        for p in sorted(champions_dir.glob("champion_*.json")):
            entry = _read_json(p)
            if entry is None:
                continue
            _add_ref(keep, (entry.get("provenance") or {}).get("source_trial_dir"))

    if work_dir.exists():
        for p in sorted(work_dir.glob("proposal_*.json")):
            payload = _read_json(p)
            if payload is None:
                continue
            # Issue #615/#671 (Symbol-Pfad, confirm.export_symbol_proposal).
            _add_ref(keep, payload.get("holdout_trial_dir"))
            # Issue #615 (Global-Pfad, confirm.export_proposal → confirm_on_holdout-Rückgabe).
            holdout = payload.get("holdout")
            if isinstance(holdout, dict):
                _add_ref(keep, holdout.get("trial_dir"))

    return keep


def prune_completed_trial_dirs(
    study_name: str,
    keep: set[Path],
    *,
    work_dir: Path | None = None,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> list[Path]:
    """Löscht jedes ``WORK/study_name/trial_*/``-Verzeichnis, das NICHT in ``keep`` liegt.

    Aufzurufen NACHDEM eine Study abgeschlossen ist (Confirm + Export + ggf. Champion-Store
    gelaufen sind) — sonst würde ein noch offener Trial-Schreibvorgang kollidieren (siehe
    Issue-Katalog §0.4). ``dry_run=True`` meldet nur, löscht nichts. Rückgabe: die Liste der
    (potenziell) gelöschten Pfade."""
    if work_dir is None:
        work_dir = WORK
    logger = logger or logging.getLogger("optimizer")
    study_dir = work_dir / study_name
    pruned: list[Path] = []
    if not study_dir.exists():
        return pruned

    keep_resolved = {p.resolve() for p in keep}
    for trial_dir in sorted(study_dir.glob("trial_*")):
        if not trial_dir.is_dir():
            continue
        if trial_dir.resolve() in keep_resolved:
            continue
        if dry_run:
            logger.info(
                "[DRY-RUN] Würde Trial-Verzeichnis %s entfernen (Study '%s' abgeschlossen).",
                trial_dir, study_name,
            )
        else:
            try:
                shutil.rmtree(trial_dir)
            except OSError as e:
                logger.warning("[#733] Konnte Trial-Verzeichnis %s nicht entfernen: %s", trial_dir, e)
                continue
            logger.debug(
                "♻️ [#733] Trial-Verzeichnis %s entfernt (Study '%s' abgeschlossen, weder von "
                "champions/ noch von einem offenen Proposal referenziert).",
                trial_dir, study_name,
            )
        pruned.append(trial_dir)
    return pruned
