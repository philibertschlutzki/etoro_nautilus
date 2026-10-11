"""Betriebs-Wächter für den Paper-Bot (per Cron/systemd-Timer alle 5 Minuten, nur lesend gegenüber eToro).

Zwei Aufgaben, beide ohne Netzwerkzugriff und ohne Orders:

1. Bot-Check: Läuft der Inkubations-Bot (flock-gehaltene Sperrdatei) oder gerade ein Orchestrator? Fällt der Bot aus,
   steht EINMAL pro Ausfall (danach stündlich als Erinnerung) eine ERROR-Zeile mit Beweisen in
   ``logs/errors/ops_guard_errors.log``: letzte PID, Laufzeit, Alter und letzte Zeilen des Bot-Logs und ob ``Bot
   erfolgreich beendet`` im Log steht (sauberes Ende) oder fehlt (hartes Ende, z. B. SIGKILL/Absturz). Der Wächter
   startet den Bot NICHT neu.
2. Log-Budget: ``logs/`` darf insgesamt nie 5 GiB erreichen. Ab ``TRIM_FROM_BYTES`` (4,5 GiB, Sicherheitsabstand zwischen
   zwei Läufen) schreibt er einen ERROR-Eintrag und löscht die ältesten Dateien, bis höchstens ``TRIM_TO_BYTES``
   (4,0 GiB) belegt sind. Nie gelöscht werden: Fehler-Logs (alles unter ``logs/errors/`` und jede Datei, deren Name
   ``error`` enthält), Sperrdateien, ``.gitkeep``, von Git verfolgte Dateien, Dateien jünger als 10 Minuten (könnten
   gerade beschrieben werden) und alles, was kein regulärer Datei-Eintrag ist (Symlinks, Verzeichnisse).

Exit-Codes: 0 = ok, 1 = Befund (Bot aus oder Budget getrimmt/nicht haltbar; Cron-Mail), 2 = interner Fehler.
Der Lauf ist still, solange nichts Auffälliges passiert.

Cron (alle 5 Minuten; ``flock`` verhindert Überlappung)::

    */5 * * * * cd /home/<user>/etoro_nautilus && flock -n /tmp/ops_guard.lock ./venv/bin/python -m automation.ops_guard >> logs/ops_guard.log 2>&1

Ohne Zugriff auf ``crontab`` (z. B. wegen NoNewPrivs) geht dasselbe als systemd-User-Timer; ``--timer-help`` druckt die
beiden Unit-Dateien mit den echten Pfaden.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
ERROR_DIR = LOG_DIR / "errors"
ERROR_LOG = ERROR_DIR / "ops_guard_errors.log"
STATE_PATH = PROJECT_ROOT / "data" / "state" / "ops_guard_state.json"
BOT_LOCK = PROJECT_ROOT / "data" / "state" / "incubation_bot.lock"

GIB = 1024 ** 3
LIMIT_BYTES = 5 * GIB          # harte Obergrenze für logs/ insgesamt
TRIM_FROM_BYTES = int(4.5 * GIB)
TRIM_TO_BYTES = 4 * GIB
MIN_AGE_S = 600                # jüngere Dateien werden nie gelöscht
ERROR_LOG_MAX_BYTES = 5 * 1024 * 1024
ERROR_LOG_KEEP = 3
REMIND_EVERY_S = 3600
CLEAN_EXIT_MARKER = "Bot erfolgreich beendet"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(now: datetime | None = None) -> str:
    return (now or _now()).strftime("%Y-%m-%d %H:%M:%S UTC")


# ─── Fehler-Log ────────────────────────────────────────────────────────────────────────────────────────────

def append_error(message: str, *, error_log: Path = ERROR_LOG, now: datetime | None = None) -> None:
    """Hängt eine ERROR-Zeile an das (geschützte) Fehler-Log an und rotiert es ab 5 MB (``.1`` … ``.3``)."""
    error_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        if error_log.exists() and error_log.stat().st_size > ERROR_LOG_MAX_BYTES:
            for i in range(ERROR_LOG_KEEP, 0, -1):
                src = error_log.with_name(f"{error_log.name}.{i - 1}") if i > 1 else error_log
                dst = error_log.with_name(f"{error_log.name}.{i}")
                if src.exists():
                    os.replace(src, dst)
    except OSError:
        pass
    with open(error_log, "a", encoding="utf-8") as fh:
        fh.write(f"{_stamp(now)} ERROR {message}\n")


# ─── Log-Budget ────────────────────────────────────────────────────────────────────────────────────────────

def _tracked_files(log_dir: Path) -> set[Path]:
    try:
        proc = subprocess.run(["git", "-C", str(log_dir.parent), "ls-files", "-z", "--", log_dir.name],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode != 0:
        return set()
    return {log_dir.parent / p for p in proc.stdout.split("\0") if p}


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def is_protected(path: Path, log_dir: Path, tracked: set[Path], now_ts: float, *, min_age_s: float = MIN_AGE_S) -> bool:
    name = path.name.lower()
    try:
        rel = path.relative_to(log_dir)
    except ValueError:
        return True
    if rel.parts and rel.parts[0] == "errors":
        return True
    if "error" in name or name.endswith(".lock") or name == ".gitkeep":
        return True
    if path in tracked:
        return True
    try:
        st = path.lstat()
    except OSError:
        return True
    return now_ts - st.st_mtime < min_age_s


def enforce_log_budget(log_dir: Path = LOG_DIR, *, trim_from: int = TRIM_FROM_BYTES, trim_to: int = TRIM_TO_BYTES,
                       limit: int = LIMIT_BYTES, error_log: Path = ERROR_LOG, now: datetime | None = None,
                       min_age_s: float = MIN_AGE_S) -> dict:
    """Trimmt ``log_dir`` bei Überschreitung; gibt ein Ergebnis-Dict zurück (``trimmed`` = Anzahl gelöschter Dateien)."""
    now = now or _now()
    total = dir_size(log_dir) if log_dir.is_dir() else 0
    result = {"total_before": total, "total_after": total, "trimmed": 0, "freed": 0, "stuck": False}
    if total < trim_from:
        return result
    tracked = _tracked_files(log_dir)
    files = []
    for root, _dirs, names in os.walk(log_dir):
        for name in names:
            p = Path(root) / name
            if p.is_symlink() or is_protected(p, log_dir, tracked, now.timestamp(), min_age_s=min_age_s):
                continue
            try:
                st = p.lstat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
    files.sort()
    append_error(
        f"Log-Budget: logs/ belegt {total / GIB:.2f} GiB (Grenze {limit / GIB:.0f} GiB, Trimm-Start {trim_from / GIB:.1f} GiB) "
        f"— lösche die ältesten Dateien bis höchstens {trim_to / GIB:.1f} GiB.", error_log=error_log, now=now)
    for mtime, size, p in files:
        if total <= trim_to:
            break
        try:
            p.unlink()
        except OSError as exc:
            append_error(f"Log-Budget: {p} nicht löschbar ({exc}).", error_log=error_log, now=now)
            continue
        total -= size
        result["trimmed"] += 1
        result["freed"] += size
        append_error(f"Log-Budget: gelöscht {p.relative_to(log_dir.parent)} ({size / 1024 ** 2:.1f} MiB, "
                     f"geändert {datetime.fromtimestamp(mtime, timezone.utc):%Y-%m-%d %H:%M} UTC).",
                     error_log=error_log, now=now)
    result["total_after"] = total
    if total > trim_to:
        result["stuck"] = True
        append_error(f"Log-Budget: nach dem Löschen noch {total / GIB:.2f} GiB; nur geschützte oder zu junge Dateien "
                     f"übrig (Fehler-Logs, Git-getrackte Dateien, < {min_age_s / 60:.0f} min alt).", error_log=error_log, now=now)
    return result


# ─── Bot-Check ─────────────────────────────────────────────────────────────────────────────────────────────

def _proc_cmdlines() -> list[tuple[int, str]]:
    out = []
    proc_root = Path("/proc")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            continue
        out.append((int(entry.name), raw))
    return out


def find_processes(cmdlines: list[tuple[int, str]] | None = None) -> dict[str, list[int]]:
    cmdlines = _proc_cmdlines() if cmdlines is None else cmdlines
    me = os.getpid()
    found: dict[str, list[int]] = {"bot": [], "orchestrator": []}
    for pid, cmd in cmdlines:
        if pid == me or "ops_guard" in cmd:
            continue
        if "momentum_ls_run.py" in cmd and "python" in cmd:
            found["bot"].append(pid)
        elif "daily_orchestrator.py" in cmd and "python" in cmd:
            found["orchestrator"].append(pid)
    return found


def read_lock(lock_path: Path = BOT_LOCK) -> dict:
    try:
        return json.loads(lock_path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return {}


def latest_bot_log(log_dir: Path = LOG_DIR) -> Path | None:
    cands = sorted(log_dir.glob("incubation_bot_*.log"), key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def bot_log_evidence(log_path: Path | None, *, tail_lines: int = 3, now_ts: float | None = None) -> str:
    if log_path is None or not log_path.exists():
        return "kein Bot-Log gefunden"
    now_ts = now_ts if now_ts is not None else _now().timestamp()
    try:
        size = log_path.stat().st_size
        with open(log_path, "rb") as fh:
            fh.seek(max(0, size - 200_000))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError as exc:
        return f"Bot-Log nicht lesbar ({exc})"
    tail = [_ANSI.sub("", ln) for ln in tail if ln.strip()]
    age_min = (now_ts - log_path.stat().st_mtime) / 60.0
    clean = any(CLEAN_EXIT_MARKER in ln for ln in tail[-200:])
    last = " | ".join(ln[:160] for ln in tail[-tail_lines:])
    if clean:
        ending = f"sauberes Ende ('{CLEAN_EXIT_MARKER}' vorhanden)"
    else:
        ending = f"HARTES Ende (kein '{CLEAN_EXIT_MARKER}' im Log: SIGKILL/Absturz)"
    return f"Log {log_path.name}, letzte Änderung vor {age_min:.0f} min, {ending}; letzte Zeilen: {last}"


def classify(bot_pids: list[int], orchestrator_pids: list[int], lock_held: bool | None) -> str:
    """``running`` | ``starting`` (Orchestrator läuft, Bot noch nicht) | ``down``."""
    if bot_pids or lock_held:
        return "running"
    if orchestrator_pids:
        return "starting"
    return "down"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def check_bot(*, state_path: Path = STATE_PATH, lock_path: Path = BOT_LOCK, log_dir: Path = LOG_DIR,
              error_log: Path = ERROR_LOG, now: datetime | None = None,
              cmdlines: list[tuple[int, str]] | None = None, lock_held: bool | None | str = "auto") -> str:
    """Gibt den Status zurück und schreibt bei einem Ausfall ERROR-Zeilen (einmal, dann stündlich)."""
    now = now or _now()
    procs = find_processes(cmdlines)
    if lock_held == "auto":
        from automation.paper_report import bot_running
        lock_held = bot_running(lock_path)
    status = classify(procs["bot"], procs["orchestrator"], lock_held)
    state = _load_state(state_path)
    last = state.get("status")
    lock = read_lock(lock_path)
    if status == "running":
        state.update({"status": "running", "last_running_utc": now.isoformat(),
                      "last_pid": (procs["bot"] or [lock.get("pid")])[0], "down_since_utc": None, "last_alert_utc": None})
    elif status == "starting":
        state.update({"status": "starting"})
    else:
        down_since = state.get("down_since_utc") if last == "down" else None
        if down_since is None:
            down_since = now.isoformat()
            ev = bot_log_evidence(latest_bot_log(log_dir), now_ts=now.timestamp())
            append_error(f"Inkubations-Bot läuft NICHT (letzte PID {state.get('last_pid') or lock.get('pid')}, "
                         f"zuletzt laufend {state.get('last_running_utc')}); {ev}", error_log=error_log, now=now)
            state["last_alert_utc"] = now.isoformat()
        else:
            last_alert = state.get("last_alert_utc")
            due = (not last_alert) or (now - datetime.fromisoformat(last_alert)).total_seconds() >= REMIND_EVERY_S
            if due:
                mins = (now - datetime.fromisoformat(down_since)).total_seconds() / 60.0
                append_error(f"Inkubations-Bot läuft weiterhin NICHT (seit {mins:.0f} min).", error_log=error_log, now=now)
                state["last_alert_utc"] = now.isoformat()
        state.update({"status": "down", "down_since_utc": down_since})
    _save_state(state, state_path)
    return status


# ─── CLI ───────────────────────────────────────────────────────────────────────────────────────────────────

def timer_help() -> str:
    root = PROJECT_ROOT
    py = root / "venv" / "bin" / "python"
    return f"""# ~/.config/systemd/user/etoro-ops-guard.service
[Unit]
Description=eToro Paper-Bot Betriebs-Wächter (Bot-Check + Log-Budget)
[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart=/usr/bin/flock -n /tmp/ops_guard.lock {py} -m automation.ops_guard

# ~/.config/systemd/user/etoro-ops-guard.timer
[Unit]
Description=eToro Wächter alle 5 Minuten
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target

# Aktivieren: systemctl --user daemon-reload && systemctl --user enable --now etoro-ops-guard.timer
"""


def run() -> int:
    code = 0
    try:
        status = check_bot()
        if status == "down":
            code = 1
            print(f"{_stamp()} ERROR Inkubations-Bot läuft NICHT (Details in logs/errors/ops_guard_errors.log).", file=sys.stderr)
    except Exception as exc:  # Wächter darf nie lautlos sterben
        append_error(f"ops_guard Bot-Check fehlgeschlagen: {exc!r}")
        print(f"{_stamp()} ERROR ops_guard Bot-Check: {exc!r}", file=sys.stderr)
        code = 2
    try:
        res = enforce_log_budget()
        if res["trimmed"] or res["stuck"]:
            code = max(code, 1)
            print(f"{_stamp()} ERROR Log-Budget: {res['trimmed']} Datei(en) gelöscht, {res['freed'] / 1024 ** 2:.0f} MiB frei, "
                  f"jetzt {res['total_after'] / GIB:.2f} GiB.", file=sys.stderr)
    except Exception as exc:
        append_error(f"ops_guard Log-Budget fehlgeschlagen: {exc!r}")
        print(f"{_stamp()} ERROR ops_guard Log-Budget: {exc!r}", file=sys.stderr)
        code = 2
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Betriebs-Wächter: Bot-Check + Log-Budget (5 GiB)")
    parser.add_argument("--timer-help", action="store_true", help="systemd-User-Timer-Units drucken")
    args = parser.parse_args(argv)
    if args.timer_help:
        print(timer_help())
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
