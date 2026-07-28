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

import optuna

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


def release_trial_dir(trial_dir: Path, *, keep: bool, logger: logging.Logger | None = None) -> bool:
    """Issue #794 — kontinuierliche Trial-Retention (Haupthebel, Gegenstueck zur #733-Study-Ebene).

    Löscht ``trial_dir`` SOFORT per ``shutil.rmtree``, wenn ``keep=False``. Das Trial-Verzeichnis
    wird nach dem Parsen des Ergebnisses von KEINEM Konsumenten mehr gelesen — Ausnahmen sind der
    aktuell beste Trial der Study (Confirm/Holdout referenziert ihn) und ein Trial, dessen
    Subprozess fehlgeschlagen ist (Forensik). Gibt ``True`` zurück, wenn tatsächlich gelöscht
    wurde (``False`` bei ``keep=True`` oder wenn das Verzeichnis bereits fehlt — idempotent)."""
    logger = logger or logging.getLogger("optimizer")
    trial_dir = Path(trial_dir)
    if keep or not trial_dir.exists():
        return False
    try:
        shutil.rmtree(trial_dir)
    except OSError as e:
        logger.warning("[#794] Konnte Trial-Verzeichnis %s nicht freigeben: %s", trial_dir, e)
        return False
    return True


def release_non_best_trial_dirs(
    study,
    *,
    work_dir: Path | None = None,
    logger: logging.Logger | None = None,
) -> list[Path]:
    """Issue #794 — kontinuierliche Trial-Retention als Optuna-Callback: nach JEDEM abgeschlossenen
    Trial wird der gesamte ``trial_*/``-Baum dieser Study gegen die aktuell gerechtfertigte Menge
    abgeglichen — der/die aktuell beste(n) eligible(n) Trial(s) (``study.best_trial``/
    ``study.best_trials`` im Pareto-Fall, defensiv über ``try/except ValueError`` für Studies ohne
    abgeschlossenen Trial) PLUS jeder Trial, dessen Zustand NICHT ``COMPLETE`` ist (PRUNED/FAIL —
    der Forensik-Pfad für einen fehlgeschlagenen Subprozess, siehe ``runner.BacktestRunError``).

    Bewusst als Neuabgleich statt als manuell verfolgter „vorheriger bester Trial": ein Wechsel des
    besten Trials gibt seinen Vorgänger automatisch frei, ohne eine zusätzliche Zustandsvariable
    zu pflegen (self-healing — kein verwaister Zeiger möglich). Fail-open pro Verzeichnis (siehe
    ``release_trial_dir``); ein Fehler bei einem Pfad überspringt nur diesen.
    """
    if work_dir is None:
        work_dir = WORK
    logger = logger or logging.getLogger("optimizer")
    study_dir = work_dir / study.study_name
    released: list[Path] = []
    if not study_dir.exists():
        return released

    keep_numbers: set[int] = set()
    try:
        if len(getattr(study, "directions", None) or ["maximize"]) > 1:
            keep_numbers.update(t.number for t in study.best_trials)
        else:
            keep_numbers.add(study.best_trial.number)
    except ValueError:
        pass  # keine abgeschlossenen (feasiblen) Trials -> nichts als "bester" zu schuetzen

    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE:
            keep_numbers.add(t.number)

    for trial_dir in sorted(study_dir.glob("trial_*")):
        if not trial_dir.is_dir():
            continue
        try:
            n = int(trial_dir.name.rsplit("_", 1)[-1])
        except ValueError:
            continue
        if n in keep_numbers:
            continue
        if release_trial_dir(trial_dir, keep=False, logger=logger):
            released.append(trial_dir)
    return released


def prune_orphaned_trial_dirs(
    work_dir: Path | None = None,
    *,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> list[Path]:
    """Issue #794 — Lauf-Start-Purge: räumt JEDES ``trial_*/``-Verzeichnis unter JEDEM ``study_*/``
    ab, das weder von ``champions/`` noch von einem offenen ``proposal_*.json`` referenziert wird.
    Räumt die Altlast abgebrochener Vorläufe ab, bevor ein neuer Lauf beginnt. Sicher, solange kein
    Sweep läuft: ein Trial-Verzeichnis trägt keine Ergebnisse, die nicht bereits in der Optuna-
    SQLite (``trial.user_attrs``) oder in ``champions/``/``proposal_*.json`` stehen (siehe
    Issue-Katalog §6, manuelle Vorwegnahme)."""
    if work_dir is None:
        work_dir = WORK
    logger = logger or logging.getLogger("optimizer")
    pruned: list[Path] = []
    if not work_dir.exists():
        return pruned
    keep = collect_referenced_trial_dirs(work_dir)
    for study_dir in sorted(work_dir.glob("study_*")):
        if not study_dir.is_dir():
            continue
        pruned.extend(prune_completed_trial_dirs(
            study_dir.name, keep, work_dir=work_dir, dry_run=dry_run, logger=logger))
    return pruned
