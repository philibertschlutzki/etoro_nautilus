"""
automation/ai_loop/memory.py
=============================
Issue #1104 — append-only JSONL ledger: ``logs/ai_optimization_ledger.jsonl``.

This is the ONLY file Issue #1104 creates outside ``automation/ai_loop/`` — it is interface #1
of the AI-Loop's two-interface architecture (see ``automation/ai_loop/__init__.py``). Every
entry is one line of JSON (JSONL); the file is APPEND-ONLY — never rewritten/truncated by this
module — so it stays safe to append to across cycles/processes over time (an ``fsync`` per
appended line keeps individual lines durable/atomic on any local filesystem).

Note: ``logs/*.jsonl`` is already covered by the repo's root ``.gitignore``
(``/logs/*.jsonl``) — the same rule that already excludes ``logs/optimizer_*.events.jsonl`` —
so this ledger is a runtime artifact, not something this change commits to git.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_NAME = "ai_optimization_ledger.jsonl"

_write_lock = threading.Lock()


def default_ledger_path(log_dir: Path | None = None) -> Path:
    """Resolves the default ledger path: ``{log_dir or PROJECT_ROOT/logs}/ai_optimization_ledger.jsonl``."""
    if log_dir is not None:
        return Path(log_dir) / DEFAULT_LEDGER_NAME
    project_root = Path(__file__).resolve().parent.parent.parent
    return project_root / "logs" / DEFAULT_LEDGER_NAME


def ensure_ledger_exists(ledger_path: Path | None = None) -> Path:
    """Creates the ledger file (and its parent ``logs/`` dir) if missing; a no-op otherwise.
    Never truncates an existing file."""
    path = ledger_path or default_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    return path


class LedgerWriter:
    """Append-only writer for ``logs/ai_optimization_ledger.jsonl``."""

    def __init__(self, ledger_path: Path | None = None):
        self.ledger_path = Path(ledger_path) if ledger_path is not None else default_ledger_path()

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Appends ``entry`` as one JSON line. Stamps ``ts_utc``/``entry_id`` if the caller
        hasn't already set them (never overwrites caller-supplied values). Returns the exact
        dict that was written (including the stamped fields)."""
        record = dict(entry)
        record.setdefault("ts_utc", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        record.setdefault("entry_id", uuid.uuid4().hex)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        return record


def append_entry(entry: dict[str, Any], *, ledger_path: Path | None = None) -> dict[str, Any]:
    """Convenience wrapper: ``LedgerWriter(ledger_path).append(entry)``."""
    return LedgerWriter(ledger_path).append(entry)


def read_entries(ledger_path: Path | None = None) -> Iterator[dict[str, Any]]:
    """Yields every ledger entry, oldest first. A line that fails to parse as JSON (e.g. a
    partially-flushed last line from a killed process) is skipped with a warning — never lets
    one bad line make the whole ledger unreadable."""
    path = Path(ledger_path) if ledger_path is not None else default_ledger_path()
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ledger-Zeile %d in %s ist kein valides JSON — übersprungen.", lineno, path)


def last_n_entries(n: int, *, ledger_path: Path | None = None) -> list[dict[str, Any]]:
    """Returns up to the last ``n`` ledger entries, oldest-of-the-selected-window first (same
    relative order as the file)."""
    if n <= 0:
        return []
    entries = list(read_entries(ledger_path))
    return entries[-n:]
