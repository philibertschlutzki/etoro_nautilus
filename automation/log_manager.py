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

import contextvars
import json
import logging
import logging.handlers
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ─── Konstanten ───────────────────────────────────────────────────────────────
LOG_MAX_BYTES   = 1 * 1024 * 1024   # 1 MB (für leichte LLM-Analyse)
LOG_BACKUP_CNT  = 5                  # 5 Rotations-Dateien à max 1 MB
LOG_RETENTION_D = 7                  # Dateien älter als 7 Tage löschen

# Issue #741 — pro Logger-Name der Pfad seiner JSONL-Event-Sidecar-Datei (populiert von
# ``setup_bot_logging``). ``emit_execution_event`` schlägt hier ausschliesslich über
# ``logger.name`` nach — keine der ~15 bestehenden Aufrufstellen muss den Pfad selbst kennen.
_JSONL_SIDECAR_PATHS: dict[str, Path] = {}

# Issue #780 — pro Logger-Name der ``run_id`` DIESES Prozess-Laufs (populiert von
# ``setup_bot_logging``, EINMAL je Sweep — im Gegensatz zu strategy/symbol/study_name unten NICHT
# thread-lokal, weil ein Lauf genau EINE run_id über alle parallelen Worker-Threads hinweg hat).
_ACTIVE_RUN_IDS: dict[str, str] = {}

# Issue #780 — Im parallelen Sweep (ThreadPoolExecutor, #755) laufen mehrere Studies gleichzeitig
# in EINEN Logger; ohne Study-Identität pro Record ist eine Warnung/ein Event nicht mehr eindeutig
# zuordenbar (reihenfolgebasierte Heuristiken lieferten nachweislich falsche Zahlen — 590 statt 705
# Zero-Eligible-Zuordnungen, siehe #780-Katalog). ``ContextVar`` statt Thread-Local: jeder native
# Thread (inkl. jedes ``ThreadPoolExecutor``-Worker-Threads) erhält implizit einen EIGENEN,
# unabhängigen Root-Context — ``bind_study_context`` gesetzt INNERHALB von ``optimize_symbol``
# (das selbst im Worker-Thread läuft) ist daher automatisch pro Study isoliert, ohne dass die ~20
# bestehenden Log-Call-Sites selbst strategy/symbol durchreichen müssen.
_current_strategy: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "optimizer_strategy", default=None)
_current_symbol: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "optimizer_symbol", default=None)
_current_study_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "optimizer_study_name", default=None)


@contextmanager
def bind_study_context(*, strategy: str | None = None, symbol: str | None = None,
                       study_name: str | None = None):
    """Issue #780 — bindet strategy/symbol/study_name für die Laufzeit des ``with``-Blocks (und
    jeden daraus aufgerufenen Code, auch tief verschachtelt) an DIESEN Thread/Task, OHNE dass eine
    einzige der ~20 ``logger.warning(...)``/``emit_execution_event(...)``-Call-Sites einen neuen
    Parameter erhält. ``StructuredFormatter`` (Prosa-Zeilen-Präfix) UND ``emit_execution_event``
    (JSONL-Sidecar-Pflichtfelder) lesen denselben Kontext. Reentrant-sicher: verschachtelte Aufrufe
    stellen beim Verlassen exakt den vorherigen Wert wieder her (auch ``None``, falls unset)."""
    tokens = (
        _current_strategy.set(strategy) if strategy is not None else None,
        _current_symbol.set(symbol) if symbol is not None else None,
        _current_study_name.set(study_name) if study_name is not None else None,
    )
    try:
        yield
    finally:
        # Issue #800 — ein ``ValueError`` aus ``ContextVar.reset()`` ("was created in a different
        # Context") bedeutet NIE "keine Verletzung": der Token wurde in einem Context erzeugt, der
        # beim Reset-Versuch nicht mehr der aktive ist (z. B. GC-Finalisierung eines Generators nach
        # einem Fehler VOR dem ``__exit__``, siehe #800-Root-Cause). Das darf die eigentliche
        # Ausnahme, die gerade propagiert, nicht unter 35 Folgefehlern begraben — daher hier
        # verschluckt (nur ``logger.debug``), NICHT erneut geworfen und NICHT print()ed.
        if tokens[0] is not None:
            try:
                _current_strategy.reset(tokens[0])
            except ValueError:
                logging.getLogger("optimizer").debug(
                    "[#800] _current_strategy.reset() Token-Mismatch (anderer Context) ignoriert.")
        if tokens[1] is not None:
            try:
                _current_symbol.reset(tokens[1])
            except ValueError:
                logging.getLogger("optimizer").debug(
                    "[#800] _current_symbol.reset() Token-Mismatch (anderer Context) ignoriert.")
        if tokens[2] is not None:
            try:
                _current_study_name.reset(tokens[2])
            except ValueError:
                logging.getLogger("optimizer").debug(
                    "[#800] _current_study_name.reset() Token-Mismatch (anderer Context) ignoriert.")


def _current_study_context() -> dict[str, str | None]:
    """Issue #780 — der aktuell gebundene Kontext (fehlt ein Feld ⇒ ``None``, ausserhalb jedes
    ``bind_study_context``-Blocks bit-identisch zu 'kein Kontext bekannt')."""
    return {
        "strategy": _current_strategy.get(),
        "symbol": _current_symbol.get(),
        "study_name": _current_study_name.get(),
    }


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

        # Issue #780 — einheitliches [strategy/symbol]-Präfix aus dem ambient bind_study_context
        # (contextvars, siehe oben): jede Warnung im parallelen Sweep ist damit ohne Reihenfolge-
        # Heuristik einer Study zuordenbar. Ausserhalb jedes bind_study_context-Blocks (z. B.
        # Nicht-Optimizer-Logger) bleibt das Präfix leer — bit-identisch zu HEAD.
        ctx = _current_study_context()
        if ctx["strategy"] or ctx["symbol"]:
            message = f"[{ctx['strategy'] or '?'}/{ctx['symbol'] or '?'}] {message}"

        line = f"{ts} | {emoji} {level:<8} | {name} | {message}"

        # Stacktrace anhängen (eingerückt)
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            # Jeden Stacktrace-Zeile einrücken für LLM-Parsing
            exc_lines = exc_text.splitlines()
            indented  = "\n".join(f"  ║ {l}" for l in exc_lines)
            line += f"\n  ╔ STACKTRACE:\n{indented}\n  ╚ END STACKTRACE"

        return line


def default_run_id() -> str:
    """Issue #740 — Default-``run_id`` für einen Einzel-Lauf (Sweep/Optimizer):
    ``f"{git_commit()}_{timestamp}"``. Wiederverwendet ``manifest.git_commit()`` (Provenienz-
    Primitive) statt eine zweite Git-Abfrage zu implementieren. Lazy-Import, damit ``log_manager``
    (ein generisches, von ``automation.optimizer`` UNABHÄNGIGES Basismodul) nicht am Modul-Top-Level
    von ``automation.optimizer`` abhängt."""
    from automation.optimizer.manifest import git_commit
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{git_commit()}_{ts}"


def setup_bot_logging(
    log_name: str,
    log_dir: Path | str | None = None,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_CNT,
    level: int = logging.DEBUG,
    run_id: str | None = None,
) -> logging.Logger:
    """
    Richtet ein LLM-optimiertes Logging-System für den Trading Bot ein.

    Features:
    - RotatingFileHandler: max 1 MB pro Datei (Default, Dauerbetrieb-Logger)
    - Issue #740: bei gesetztem ``run_id`` stattdessen ein NICHT-rotierender ``FileHandler`` auf
      einer pro-Lauf-eindeutigen Datei — ein einzelner, hochvolumiger Lauf (z. B. ein
      `--tier all`-Sweep über hunderte Trials) verliert dann nie mehr seinen Anfang an den
      1 MB×5-Ringpuffer (das dokumentierte „Tail-Fragment"-Muster).
    - StreamHandler: INFO-Level für Terminal-Output
    - StructuredFormatter: klarer, maschinenlesbarer Output
    - Automatischer Log-Cleanup beim Start

    Args:
        log_name:     Name des Loggers und Basis des Log-Dateinamens.
        log_dir:      Verzeichnis für Log-Dateien (Default: PROJECT_ROOT/logs/).
        max_bytes:    Max. Größe pro Log-Datei in Bytes (Default: 1 MB). Ignoriert bei ``run_id``.
        backup_count: Anzahl der Rotations-Dateien. Ignoriert bei ``run_id``.
        level:        Log-Level (Default: DEBUG).
        run_id:       Issue #740 — bei gesetztem Wert schreibt der Logger NICHT-rotierend nach
                      ``{log_dir}/{log_name}_{run_id}.log`` (ein Lauf = eine vollständige Datei).
                      ``None`` (Default) erhält das bisherige, tages-geteilte Rotationsverhalten für
                      Dauerbetrieb-Logger (``momentum_ls_run``, ``catalog_service``, …) bit-identisch.

    Returns:
        Konfigurierter Logger.
    """
    if log_dir is None:
        log_dir = Path(__file__).parent.parent / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log-Cleanup
    cleanup_old_logs(log_dir)

    formatter = StructuredFormatter()

    if run_id:
        # Issue #740 — pro-Lauf-Datei, KEINE Rotation: der ganze Lauf bleibt in einer Datei.
        log_path = log_dir / f"{log_name}_{run_id}.log"
        file_handler: logging.Handler = logging.FileHandler(str(log_path), encoding="utf-8")
        jsonl_path = log_dir / f"{log_name}_{run_id}.events.jsonl"
        summary_suffix = "kein Rotationslimit (pro-Lauf-Datei, Issue #740)"
    else:
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path = log_dir / f"{log_name}_{today_str}.log"
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        jsonl_path = log_dir / f"{log_name}_{today_str}.events.jsonl"
        summary_suffix = f"max {max_bytes // 1024} KB, {backup_count} Backups"

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

    # Issue #741 — JSONL-Sidecar-Pfad für diesen Logger-Namen registrieren, EGAL ob rotierend
    # oder pro-Lauf: ``emit_execution_event`` schlägt ausschliesslich über ``logger.name`` nach.
    _JSONL_SIDECAR_PATHS[log_name] = jsonl_path
    # Issue #780 — dieselbe run_id (falls gesetzt) für JEDES ``emit_execution_event`` dieses
    # Loggers verfügbar machen, ohne dass jede Call-Site sie selbst kennen/durchreichen muss.
    if run_id:
        _ACTIVE_RUN_IDS[log_name] = run_id
    else:
        _ACTIVE_RUN_IDS.pop(log_name, None)

    logger.info(
        f"[LogManager] Logger '{log_name}' initialisiert. "
        f"Log-Datei: {log_path} ({summary_suffix})"
    )
    return logger


def cleanup_old_logs(
    log_dir: Path | str,
    max_age_days: int = LOG_RETENTION_D,
) -> int:
    """
    Löscht alle Log-Dateien im Verzeichnis, die älter als max_age_days sind.

    Issue #741/#746 — erfasst zusätzlich zu ``*.log*`` (Prosa-Logs + Rotations-Backups) auch die
    ``*.jsonl``-Sidecar-Dateien (strukturierte Events, #741): beide sind ROHDATEN mit derselben
    7-Tage-Aufbewahrung. Das aggregierte Report-Artefakt (#742) liegt bewusst AUSSERHALB von
    ``logs/`` (unter ``data/optimizer/reports/``) und ist von dieser Funktion nie betroffen (#746).

    Returns:
        Anzahl der gelöschten Dateien.
    """
    log_dir    = Path(log_dir)
    cutoff_ts  = time.time() - max_age_days * 86400
    deleted    = 0

    if not log_dir.exists():
        return 0

    candidates = dict.fromkeys(list(log_dir.glob("*.log*")) + list(log_dir.glob("*.jsonl")))
    for f in candidates:
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


_SCALAR_KEY_TYPES = (str, int, float, bool, type(None))


def _canonical_key(k: Any) -> str:
    """Issue #937/#938 (Pitfall #294) — kanonische String-Form eines beliebigen Dict-Keys.

    ``json.dumps`` koerziert nur ``str``/``int``/``float``/``bool``/``None``-Keys automatisch nach
    ``str``; jeder andere Key-Typ (insbesondere ``tuple``, die Hauskonvention für (Strategie,
    Symbol)-Paare an anderer Stelle im Repo, siehe ``_contracts.pair_key``) wirft ``TypeError`` —
    unabhängig vom ``default``-Hook, der NUR für Werte greift. Für Tuples/Listen/Frozensets wird
    exakt das ``"a/b"``-Format erzeugt, das der Rest des Systems für Paar-Keys bereits verwendet
    (``_contracts.PAIR_KEY_SEP``), damit ein sanitisierter Tuple-Key und ein handgebauter
    String-Key bitgleich sind."""
    if isinstance(k, str):
        return k
    if isinstance(k, _SCALAR_KEY_TYPES):
        return str(k)
    if isinstance(k, (tuple, list)):
        return "/".join(_canonical_key(p) for p in k)
    if isinstance(k, frozenset):
        return "/".join(sorted(_canonical_key(p) for p in k))
    return repr(k)


def _sanitize_for_json(obj: Any, _depth: int = 0) -> Any:
    """Issue #937 — rekursiv jeden Dict-Key auf einen JSON-zulässigen ``str`` abbilden. Werte
    bleiben unangetastet (``json.dumps(..., default=str)`` bleibt dafür zuständig). ``_depth``-Cap
    schützt vor pathologisch verschachtelten/zyklischen Strukturen in einem Telemetrie-Payload."""
    if _depth > 32:
        return "<max_depth_exceeded>"
    if isinstance(obj, dict):
        return {_canonical_key(k): _sanitize_for_json(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v, _depth + 1) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((_sanitize_for_json(v, _depth + 1) for v in obj), key=repr)
    return obj


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
    # Issue #780 — strategy/symbol/study_name/run_id sind ab jetzt PFLICHTFELDER jeder JSONL-Zeile
    # (Schema-Validierungs-Akzeptanzkriterium): aus dem ambient bind_study_context/run_id-Registry
    # vorbelegt, ein EXPLIZIT im payload übergebener Wert überschreibt ihn (Rückwärtskompatibilität
    # für die ~15 bestehenden Call-Sites, die strategy/symbol bereits selbst im payload tragen).
    event = {
        "event_type":   event_type,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        **_current_study_context(),
        "run_id": _ACTIVE_RUN_IDS.get(logger.name),
        **payload,
    }
    # Issue #937 (Pitfall #295) — ein Telemetrie-Sink darf NIE eine Exception an den Aufrufer
    # weitergeben: seine Ausfallwirkung wäre sonst grösser als die des beobachteten Systems (hier:
    # ein 143-Symbol-Sweep, der an einem JSON-Serialisierungsfehler im Log-Pfad stirbt, siehe
    # #937-Katalog). Nicht-skalare Dict-Keys (z.B. (strategy, symbol)-Tuples) werden zuerst über
    # ``_sanitize_for_json`` in die Hauskonvention "strategy/symbol" überführt (Pitfall #294);
    # verbleibende Fehler (inkl. NaN/Infinity — ``allow_nan=False`` ist Absicht, siehe unten)
    # erzeugen einen Ersatz-Event statt eine Ausnahme zu werfen.
    try:
        payload_json = json.dumps(
            _sanitize_for_json(event), ensure_ascii=False, default=str,
            sort_keys=True, allow_nan=False,
        )
    except Exception as exc:
        payload_json = json.dumps({
            "event_type": "TELEMETRY_SERIALIZATION_FAILED",
            "original_event_type": str(event.get("event_type", "?")),
            "error": f"{type(exc).__name__}: {exc}",
            "offending_key_types": sorted({type(k).__name__ for k in event}),
            "timestamp_utc": event["timestamp_utc"],
        }, ensure_ascii=False)
        level = logging.ERROR
    try:
        logger.log(level, f"[JSON_EVENT] {payload_json}")
    except Exception:
        pass  # Telemetrie darf die Berechnung niemals abbrechen (Pitfall #295).
    _append_jsonl_sidecar(logger.name, event)


def _append_jsonl_sidecar(logger_name: str, event: dict[str, Any]) -> None:
    """Issue #741 — hängt ``event`` als EINE valide, dekorationsfreie JSON-Zeile an die für
    ``logger_name`` registrierte ``.events.jsonl``-Datei an (additiv zur Prosa-Logzeile, die
    ``[JSON_EVENT] {...}`` weiterhin innerhalb einer formatierten Zeile trägt — für Menschen/
    Terminal unverändert). Kein registrierter Sidecar-Pfad (Logger nie via ``setup_bot_logging``
    initialisiert, z. B. in ad-hoc-Tests) ⇒ stiller No-Op. Fail-open: ein Sidecar-Schreibfehler
    darf den aufrufenden Trading-/Optimizer-Pfad nie crashen (analog Champion-Store/Retention)."""
    jsonl_path = _JSONL_SIDECAR_PATHS.get(logger_name)
    if jsonl_path is None:
        return
    try:
        # Issue #937 — derselbe Key-Sanitizer wie im Prosa-JSON_EVENT-Pfad: ohne ihn würde ein
        # Tuple-Key hier (statt in ``emit_execution_event``) den ``TypeError`` werfen, weil beide
        # Pfade unabhängig voneinander ``json.dumps`` auf demselben ``event``-Dict aufrufen.
        line = json.dumps(_sanitize_for_json(event), ensure_ascii=False, default=str,
                          sort_keys=True, allow_nan=False)
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
    except OSError:
        logging.getLogger("log_manager").warning(
            "[#741] JSONL-Sidecar-Schreiben fehlgeschlagen für %s", jsonl_path, exc_info=True
        )
    except Exception:
        # Issue #937 (Pitfall #295) — Serialisierungsfehler dürfen diesen Fail-open-Sink ebenso
        # wenig zum Absturz bringen wie den Prosa-Log-Pfad.
        logging.getLogger("log_manager").warning(
            "[#937] JSONL-Sidecar-Serialisierung fehlgeschlagen für %s", jsonl_path, exc_info=True
        )


def emit_gate1_rejection(
    logger: logging.Logger,
    available_days: float,
    required_days: float,
    *,
    error_category: str = "REJECT_DATA_INSUFFICIENT_GEOMETRY",
    symbol: str | None = None,
    level: int = logging.ERROR,
) -> None:
    """Issue #531 — strukturiertes ``GATE_1_REJECTION``-Telemetry-Event bei Daten-Insuffizienz.

    Macht die Diskrepanz zwischen der REAL vorhandenen Bar-Spanne (``available_days``) und der von
    der Walk-Forward-Geometrie geforderten Spanne (``required_days``) explizit sichtbar — statt den
    letzten OOS-Fold/Holdout still zu klemmen (No-Clamping-Policy). ``delta_days`` = Defizit.

    Mandatory Payload (AGENTS.md §14 / Issue #531):
        {"event": "GATE_1_REJECTION", "error_category": "REJECT_DATA_INSUFFICIENT_GEOMETRY",
         "available_days": <float>, "required_days": <float>, "delta_days": <float>}
    """
    payload: dict[str, Any] = {
        "event":          "GATE_1_REJECTION",
        "error_category": error_category,
        "available_days": round(float(available_days), 2),
        "required_days":  round(float(required_days), 2),
        "delta_days":     round(float(required_days) - float(available_days), 2),
    }
    if symbol is not None:
        payload["symbol"] = symbol
    emit_execution_event(logger, "GATE_1_REJECTION", payload, level=level)


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
