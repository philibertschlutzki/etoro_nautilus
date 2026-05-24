"""
automation/log_manager.py
===========================
LLM-optimiertes Logging-System für den eToro Nautilus Trading Bot.

Merkmale:
  - RotatingFileHandler: max 1 MB pro Log-Datei, 5 Rotations-Kopien
  - Log-Retention: Dateien älter als 7 Tage werden automatisch gelöscht
  - Strukturierte Log-Level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  - JSON-Event-Logs für Execution-Events (LLM-freundlich analysierbar)
  - Klare Stacktraces mit Kontext-Informationen

Verwendung:
    from automation.log_manager import setup_bot_logging, emit_execution_event

    logger = setup_bot_logging("live_bot")
    emit_execution_event(logger, "ORDER_SUBMITTED", {"symbol": "BTC.ETORO", "amount": 100.0})
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ─── Konstanten ───────────────────────────────────────────────────────────────
LOG_MAX_BYTES   = 1 * 1024 * 1024   # 1 MB (für leichte LLM-Analyse)
LOG_BACKUP_CNT  = 5                  # 5 Rotations-Dateien à max 1 MB
LOG_RETENTION_D = 7                  # Dateien älter als 7 Tage löschen


class StructuredFormatter(logging.Formatter):
    """
    LLM-freundlicher Log-Formatter.

    Format: TIMESTAMP | LEVEL    | LOGGER | MESSAGE
    JSON-Events werden als `[JSON_EVENT] {...}` markiert für einfaches Parsing.
    Stacktraces sind vollständig und eingerückt für bessere Lesbarkeit.
    """

    _LEVEL_EMOJIS = {
        "DEBUG":    "🔍",
        "INFO":     "ℹ️ ",
        "WARNING":  "⚠️ ",
        "ERROR":    "❌",
        "CRITICAL": "🚨",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts      = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds")
        level   = record.levelname
        emoji   = self._LEVEL_EMOJIS.get(level, "  ")
        name    = record.name[:30].ljust(30)
        message = record.getMessage()

        line = f"{ts} | {emoji} {level:<8} | {name} | {message}"

        # Stacktrace anhängen (eingerückt)
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            # Jeden Stacktrace-Zeile einrücken für LLM-Parsing
            exc_lines = exc_text.splitlines()
            indented  = "\n".join(f"  ║ {l}" for l in exc_lines)
            line += f"\n  ╔ STACKTRACE:\n{indented}\n  ╚ END STACKTRACE"

        return line


def setup_bot_logging(
    log_name: str,
    log_dir: Path | str | None = None,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_CNT,
    level: int = logging.DEBUG,
) -> logging.Logger:
    """
    Richtet ein LLM-optimiertes Logging-System für den Trading Bot ein.

    Features:
    - RotatingFileHandler: max 1 MB pro Datei
    - StreamHandler: INFO-Level für Terminal-Output
    - StructuredFormatter: klarer, maschinenlesbarer Output
    - Automatischer Log-Cleanup beim Start

    Args:
        log_name:     Name des Loggers und Basis des Log-Dateinamens.
        log_dir:      Verzeichnis für Log-Dateien (Default: PROJECT_ROOT/logs/).
        max_bytes:    Max. Größe pro Log-Datei in Bytes (Default: 1 MB).
        backup_count: Anzahl der Rotations-Dateien.
        level:        Log-Level (Default: DEBUG).

    Returns:
        Konfigurierter Logger.
    """
    if log_dir is None:
        log_dir = Path(__file__).parent.parent / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_path  = log_dir / f"{log_name}_{today_str}.log"

    # Log-Cleanup
    cleanup_old_logs(log_dir)

    formatter = StructuredFormatter()

    # RotatingFileHandler (max 1 MB, 5 Backups)
    file_handler = logging.handlers.RotatingFileHandler(
        str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # StreamHandler für Terminal
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)

    logger = logging.getLogger(log_name)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False

    logger.info(
        f"[LogManager] Logger '{log_name}' initialisiert. "
        f"Log-Datei: {log_path} (max {max_bytes // 1024} KB, {backup_count} Backups)"
    )
    return logger


def cleanup_old_logs(
    log_dir: Path | str,
    max_age_days: int = LOG_RETENTION_D,
) -> int:
    """
    Löscht alle Log-Dateien im Verzeichnis, die älter als max_age_days sind.

    Returns:
        Anzahl der gelöschten Dateien.
    """
    log_dir    = Path(log_dir)
    cutoff_ts  = time.time() - max_age_days * 86400
    deleted    = 0

    if not log_dir.exists():
        return 0

    for f in log_dir.glob("*.log*"):
        try:
            if f.stat().st_mtime < cutoff_ts:
                f.unlink()
                deleted += 1
                logging.getLogger("log_manager").debug(
                    f"[LogManager] Gelöscht (zu alt): {f.name}"
                )
        except OSError as e:
            logging.getLogger("log_manager").warning(
                f"[LogManager] Konnte {f.name} nicht löschen: {e}"
            )

    if deleted:
        logging.getLogger("log_manager").info(
            f"[LogManager] {deleted} Log-Datei(en) gelöscht (älter als {max_age_days} Tage)."
        )
    return deleted


def emit_execution_event(
    logger: logging.Logger,
    event_type: str,
    payload: dict[str, Any],
    level: int = logging.INFO,
) -> None:
    """
    Emittiert ein strukturiertes JSON-Execution-Event.

    Diese Funktion erzeugt LLM-parsbare Log-Einträge für alle wichtigen
    Execution-Events (Orders, Fills, Positions, Fehler).

    Format:
        [JSON_EVENT] {"event_type": "...", "timestamp_utc": "...", ...}

    Args:
        logger:     Ziel-Logger.
        event_type: Kategorie des Events (z.B. "ORDER_SUBMITTED", "POSITION_CLOSED").
        payload:    Event-Daten (beliebiges Dict).
        level:      Log-Level (Default: INFO).
    """
    event = {
        "event_type":   event_type,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        **payload,
    }
    logger.log(level, f"[JSON_EVENT] {json.dumps(event, ensure_ascii=False, default=str)}")


def emit_order_event(
    logger: logging.Logger,
    symbol: str,
    side: str,
    amount_usd: float,
    price: float | None = None,
    order_id: str | None = None,
    status: str = "SUBMITTED",
    extra: dict | None = None,
) -> None:
    """
    Spezialisiertes Execution-Event für Handelsorders.

    Erzeugt einen standardisierten, LLM-freundlichen Log-Eintrag für
    alle Order-bezogenen Events mit vollständigem Kontext.
    """
    payload = {
        "symbol":     symbol,
        "side":       side,
        "amount_usd": amount_usd,
        "status":     status,
    }
    if price is not None:
        payload["price"] = price
    if order_id is not None:
        payload["order_id"] = order_id
    if extra:
        payload.update(extra)

    emit_execution_event(logger, f"ORDER_{status}", payload)


def get_log_summary(log_dir: Path | str) -> dict[str, Any]:
    """
    Erstellt eine Zusammenfassung aller Log-Dateien im Verzeichnis.
    Nützlich für LLM-Analyse und Monitoring.
    """
    log_dir = Path(log_dir)
    summary: dict[str, Any] = {
        "log_dir":    str(log_dir),
        "files":      [],
        "total_size": 0,
    }

    if not log_dir.exists():
        return summary

    for f in sorted(log_dir.glob("*.log*")):
        try:
            size = f.stat().st_size
            summary["files"].append({
                "name":        f.name,
                "size_kb":     round(size / 1024, 1),
                "modified":    datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
            summary["total_size"] += size
        except OSError:
            pass

    summary["total_size_kb"] = round(summary["total_size"] / 1024, 1)
    summary["file_count"]    = len(summary["files"])
    return summary
