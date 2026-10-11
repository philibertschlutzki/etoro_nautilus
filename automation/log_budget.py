"""Platzbudget fuer ``logs/``: nie mehr als ``MAX_BYTES`` (5 GiB) insgesamt.

Wird das Budget ueberschritten, schreibt ``enforce_log_budget`` einen ERROR in ``logs/errors/`` und
loescht die aeltesten Dateien (nach mtime), bis das Budget wieder eingehalten ist. Nie geloescht werden:
Error-Logs (alles unter ``logs/errors/`` sowie Dateien, deren Name ``error`` oder ``alert`` enthaelt),
Skripte (``*.sh``), die im Repo gefuehrten Berichte (``paper_reports/``) und Dateien, die in den letzten
``ACTIVE_GRACE_S`` Sekunden beschrieben wurden (laufender Bot).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 5 * 1024 ** 3
ACTIVE_GRACE_S = 15 * 60
ERROR_DIR_NAME = "errors"
PROTECTED_DIRS = (ERROR_DIR_NAME, "paper_reports")
PROTECTED_NAME_PARTS = ("error", "alert")
PROTECTED_SUFFIXES = (".sh",)

logger = logging.getLogger(__name__)


def error_log_dir(log_dir: Path) -> Path:
    path = Path(log_dir) / ERROR_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_error(log_dir: Path, name: str, message: str) -> None:
    """Haengt eine ERROR-Zeile an ``logs/errors/<name>`` (wird nie automatisch geloescht)."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    try:
        with open(error_log_dir(log_dir) / name, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} UTC | ERROR    | {message}\n")
    except OSError as exc:  # Platte voll o. ae.: nie den Aufrufer toeten
        logger.error("Error-Log nicht schreibbar (%s): %s", exc, message)


def is_protected(path: Path, log_dir: Path) -> bool:
    rel = path.relative_to(log_dir)
    if rel.parts and rel.parts[0] in PROTECTED_DIRS:
        return True
    name = path.name.lower()
    return name.endswith(PROTECTED_SUFFIXES) or any(p in name for p in PROTECTED_NAME_PARTS)


def _scan(log_dir: Path) -> list[tuple[float, int, Path]]:
    files = []
    for root, _dirs, names in os.walk(log_dir):
        for name in names:
            p = Path(root) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
    return files


def enforce_log_budget(log_dir: Path | str = "logs", *, max_bytes: int = MAX_BYTES,
                       now: float | None = None) -> dict:
    """Haelt ``log_dir`` unter ``max_bytes``. Rueckgabe: ``{total_before, total_after, deleted, over_budget}``."""
    log_dir = Path(log_dir)
    now = time.time() if now is None else now
    if not log_dir.is_dir():
        return {"total_before": 0, "total_after": 0, "deleted": [], "over_budget": False}
    files = _scan(log_dir)
    total = sum(size for _m, size, _p in files)
    result = {"total_before": total, "total_after": total, "deleted": [], "over_budget": False}
    if total <= max_bytes:
        return result
    append_error(log_dir, "log_budget_errors.log",
                 f"LOG_BUDGET_EXCEEDED: {total / 1024 ** 3:.2f} GiB > {max_bytes / 1024 ** 3:.2f} GiB — "
                 f"loesche die aeltesten Dateien (Error-Logs bleiben)")
    for mtime, size, path in sorted(files, key=lambda f: f[0]):
        if total <= max_bytes:
            break
        if is_protected(path, log_dir) or now - mtime < ACTIVE_GRACE_S:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        result["deleted"].append(str(path))
    result["total_after"] = total
    result["over_budget"] = total > max_bytes
    if result["over_budget"]:
        append_error(log_dir, "log_budget_errors.log",
                     f"LOG_BUDGET_NOT_REACHED: {total / 1024 ** 3:.2f} GiB nach dem Loeschen — nur geschuetzte "
                     f"oder aktive Dateien uebrig")
    else:
        append_error(log_dir, "log_budget_errors.log",
                     f"LOG_BUDGET_CLEANED: {len(result['deleted'])} Dateien geloescht, jetzt "
                     f"{total / 1024 ** 3:.2f} GiB")
    return result
