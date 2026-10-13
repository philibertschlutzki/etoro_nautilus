"""Täglicher Gesundheitscheck des Paper-Bots (nur lesend, ohne Netzwerk, ohne Orders).

Liest ausschließlich lokale Zustandsdateien und systemd/Logs und schreibt einen kurzen Bericht nach
``results/daily/<JJJJ-MM-TT>.md``:

  * Neustarts: ``NRestarts`` und Startzeit von ``etoro-paper-bot.service`` im Vergleich zum letzten Check
    (``data/state/daily_health_state.json``).
  * ERROR-Zeilen der letzten 24 h in ``logs/incubation_bot_<Datum>.log`` (heute und gestern).
  * Tages-PnL und Abstand zum Tagesstopp (3 %) und zum Drawdown-Stopp (10 %) aus ``incubation_equity_state.json``.
  * Offene Positionen: Bot (``demo_trades_open.json``) und Fremd (im Konto laut ``paper_report_state.json``, aber
    nicht vom Bot geführt).

Exit-Codes: 0 = unauffällig, 1 = Befund (steht im Bericht unter "Befunde"), 2 = interner Fehler.
Systemd-Timer: ``deploy/systemd/etoro-daily-health.{service,timer}``.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "data" / "state"
LOG_DIR = PROJECT_ROOT / "logs"
REPORT_DIR = PROJECT_ROOT / "results" / "daily"
CHECK_STATE = STATE_DIR / "daily_health_state.json"
UNIT = "etoro-paper-bot.service"
DAILY_STOP = 0.03
DD_STOP = 0.10
WARN_SHARE = 0.5     # Befund, sobald die Hälfte des Abstands zu einem Stopp aufgebraucht ist
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def unit_info(unit: str = UNIT) -> dict[str, str]:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "NRestarts,ActiveState,SubState,ActiveEnterTimestampMonotonic,ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def recent_errors(now: datetime, log_dir: Path = LOG_DIR) -> tuple[int, list[tuple[str, int]]]:
    """ERROR/CRITICAL/Traceback-Zeilen der letzten 24 h; zweiter Wert: häufigste Meldungen (ohne Zeitstempel)."""
    since = now - timedelta(hours=24)
    counts: Counter[str] = Counter()
    for day in (now - timedelta(days=1), now):
        try:
            lines = (log_dir / f"incubation_bot_{day:%Y%m%d}.log").read_text("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for ln in lines:
            ln = _ANSI.sub("", ln)
            if " ERROR" not in ln and "CRITICAL" not in ln and "Traceback" not in ln:
                continue
            stamp = ln[:19]
            try:
                if datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc) < since:
                    continue
            except ValueError:
                pass   # Zeile ohne Zeitstempel (z. B. Traceback-Teil): zählen
            counts[re.sub(r"\d+", "#", ln[30:] if len(ln) > 30 else ln)[:140]] += 1
    return sum(counts.values()), counts.most_common(5)


def equity_view() -> dict:
    st = _json(STATE_DIR / "incubation_equity_state.json")
    try:
        hwm, start, last = float(st["hwm"]), float(st["day_start_equity"]), float(st["last_equity"])
    except (KeyError, TypeError, ValueError):
        return {}
    daily_loss = 1 - last / start
    dd = 1 - last / hwm
    return {"hwm": hwm, "start": start, "last": last, "pnl": last - start, "pnl_pct": last / start - 1,
            "daily_loss": daily_loss, "dd": dd, "updated": st.get("updated_utc", "?"), "day_key": st.get("day_key", "?")}


def positions_view() -> dict:
    bot = _json(STATE_DIR / "demo_trades_open.json")
    account = _json(STATE_DIR / "paper_report_state.json").get("positions", {}) or {}
    foreign = {k: v for k, v in account.items() if k not in bot}
    return {"bot": len(bot), "foreign": len(foreign), "account": len(account),
            "foreign_amount": sum(float(v.get("amount") or 0) for v in foreign.values())}


def build_report(now: datetime, prev: dict) -> tuple[str, list[str], dict]:
    findings: list[str] = []
    info = unit_info()
    restarts = int(info.get("NRestarts") or 0)
    started = info.get("ActiveEnterTimestamp", "?")
    state = info.get("ActiveState", "?")
    new_state = {"checked_utc": now.isoformat(), "n_restarts": restarts, "active_enter": started}
    if state != "active":
        findings.append(f"Bot-Dienst nicht aktiv (ActiveState={state}).")
    if prev:
        if restarts > prev.get("n_restarts", restarts):
            findings.append(f"{restarts - prev['n_restarts']} automatische Neustart(s) seit {prev.get('checked_utc', '?')[:16]}.")
        elif started != prev.get("active_enter", started):
            findings.append(f"Dienst seit dem letzten Check neu gestartet (Start {started}), aber kein Auto-Neustart (z. B. Orchestrator).")
    n_err, top = recent_errors(now)
    eq, pos = equity_view(), positions_view()
    if not eq:
        findings.append("Equity-Zustand fehlt oder ist unlesbar.")
    else:
        if eq["daily_loss"] >= DAILY_STOP * WARN_SHARE:
            findings.append(f"Tagesverlust {eq['daily_loss']:.2%} ist über der Hälfte des {DAILY_STOP:.0%}-Tagesstopps.")
        if eq["dd"] >= DD_STOP * WARN_SHARE:
            findings.append(f"Drawdown {eq['dd']:.2%} ist über der Hälfte des {DD_STOP:.0%}-Stopps.")
        try:
            age = now - datetime.fromisoformat(eq["updated"])
            if age > timedelta(hours=2):
                findings.append(f"Equity-Zustand ist {age.total_seconds() / 3600:.1f} h alt (Bot läuft nicht?).")
        except ValueError:
            pass
    L = [f"# Bot-Gesundheitscheck {now:%Y-%m-%d}", "", f"Stand: {now:%Y-%m-%d %H:%M} UTC", "",
         "## Befunde", ""] + ([f"- {f}" for f in findings] or ["- keine"]) + ["", "## Dienst", "",
         f"- {UNIT}: {state}/{info.get('SubState', '?')}, aktiv seit {started}",
         f"- NRestarts gesamt: {restarts} (letzter Check: {prev.get('n_restarts', 'n/a') if prev else 'erster Lauf'})", "",
         f"## Fehler (letzte 24 h): {n_err} ERROR-Zeilen", ""] + [f"- {c}x {m}" for m, c in top]
    L += ["", "## PnL und Stopps", ""]
    if eq:
        L += [f"- Equity jetzt {eq['last']:,.2f} USD, Tagesstart {eq['start']:,.2f}, Hoch {eq['hwm']:,.2f} (Stand {eq['updated'][:16]} UTC)",
              f"- Tages-PnL {eq['pnl']:+,.2f} USD ({eq['pnl_pct']:+.2%}); Abstand zum {DAILY_STOP:.0%}-Tagesstopp: "
              f"{DAILY_STOP - eq['daily_loss']:.2%}-Punkte ({eq['start'] * DAILY_STOP + eq['pnl']:,.0f} USD)",
              f"- Drawdown vom Hoch {eq['dd']:.2%}; Abstand zum {DD_STOP:.0%}-Stopp: {DD_STOP - eq['dd']:.2%}-Punkte ({eq['hwm'] * DD_STOP - (eq['hwm'] - eq['last']):,.0f} USD)"]
    else:
        L.append("- keine Daten")
    L += ["", "## Offene Positionen", "",
          f"- Bot: {pos['bot']}", f"- Fremd (Konto, nicht vom Bot): {pos['foreign']} ({pos['foreign_amount']:,.0f} USD)",
          f"- Konto laut letztem Paper-Bericht: {pos['account']}", ""]
    return "\n".join(L), findings, new_state


def main() -> int:
    now = datetime.now(timezone.utc)
    try:
        text, findings, new_state = build_report(now, _json(CHECK_STATE))
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"{now:%Y-%m-%d}.md").write_text(text, "utf-8")
        CHECK_STATE.write_text(json.dumps(new_state, indent=1), "utf-8")
    except Exception as exc:   # noqa: BLE001 - Timer-Lauf soll sauber mit Code 2 enden
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    print(text)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
