"""Periodisches Reporting für das Paper-Trading-Konto (nur lesend, per Cron).

Fragt Kontostand und offene Positionen des per ``ETORO_PAPER_ACCOUNT_CID`` angehefteten Spielgeld-Kontos ab, prüft
den Inkubations-Bot und vergleicht mit dem letzten Lauf: neue Positionen = ``ERÖFFNET``, verschwundene Positionen =
``GESCHLOSSEN``. Es werden NIE Orders gesendet. Der Lauf ist still (kein stdout, Exit 0), solange nichts passiert und
keine Zusammenfassung fällig ist.

Ausgabe
  * Ereignisse + Zusammenfassungen: ``logs/paper_report_alerts.log`` (eine Zeile je Ereignis) und stdout.
  * Bericht: ``logs/reports/paper_report_<JJJJMMTT>.md`` (angehängt).
  * Zustand: ``data/state/paper_report_state.json`` (bekannte Positionen, Zeit der letzten Zusammenfassung).
  * Optional Push aufs Handy: ``PAPER_REPORT_NTFY_URL`` in ``.env`` (z. B. ``https://ntfy.sh/<geheimes-thema>``);
    ohne die Variable wird nichts nach aussen gesendet.

Exit-Codes: 0 = ok, 1 = API-/Konto-Fehler (Cron-Mail), 2 = Konfiguration (Keys fehlen, ETORO_ENV != demo, falsches Konto).

Ersteinrichtung auf dem Ubuntu-Server (``python3 -m automation.paper_report --cron-help`` druckt dasselbe mit den
echten Pfaden):

  1. Manuell testen (muss ``OK`` und Guthaben melden, schreibt die erste Zusammenfassung)::

         cd ~/etoro_nautilus && source venv/bin/activate
         python -m automation.paper_report --force-summary

  2. Cron-Dienst prüfen::

         systemctl is-active cron        # muss "active" ausgeben; sonst: sudo systemctl enable --now cron

  3. Crontab öffnen (``crontab -e``, beim ersten Mal einen Editor wählen) und diese Zeilen ans Ende setzen. Alle
     15 Minuten; ``flock`` verhindert überlappende Läufe; absolute Pfade, weil Cron kein ``source`` und keine
     Login-Umgebung kennt::

         SHELL=/bin/bash
         */15 * * * * cd /home/<user>/etoro_nautilus && flock -n /tmp/paper_report.lock ./venv/bin/python -m automation.paper_report >> logs/paper_report_cron.log 2>&1

  4. Prüfen, dass Cron läuft::

         crontab -l
         grep CRON /var/log/syslog | tail      # zeigt die Aufrufe
         tail -f logs/paper_report_cron.log    # nur bei Fehlern gefüllt

  Zeitzone: Cron nutzt die Systemzeit (``timedatectl``); Berichte stehen in UTC.
  Deaktivieren: ``crontab -e`` und die Zeile löschen bzw. mit ``#`` auskommentieren.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "data" / "state" / "paper_report_state.json"
ALERTS_PATH = PROJECT_ROOT / "logs" / "paper_report_alerts.log"
REPORT_DIR = PROJECT_ROOT / "logs" / "reports"
INCUBATION_DIR = PROJECT_ROOT / "data" / "state" / "incubation"
BOT_LOCK = PROJECT_ROOT / "data" / "state" / "incubation_bot.lock"
PNL_URLS = {"demo": "https://public-api.etoro.com/api/v1/trading/info/demo/pnl",
            "real": "https://public-api.etoro.com/api/v1/trading/info/real/pnl"}   # real: nur das angeheftete Paper-Konto
NTFY_ENV = "PAPER_REPORT_NTFY_URL"
SUMMARY_EVERY_HOURS = 4.0

CRON_LINE = ("*/15 * * * * cd {root} && flock -n /tmp/paper_report.lock {python} -m automation.paper_report "
             ">> logs/paper_report_cron.log 2>&1")


class ReportError(RuntimeError):
    """API- oder Konto-Fehler beim Abruf."""


def _first(item: dict, *keys, default=None):
    for key in keys:
        if item.get(key) is not None:
            return item[key]
    return default


def parse_positions(pnl: dict) -> dict[str, dict]:
    """``{position_id: details}`` aus einer PnL-Antwort (dieselben Feld-Fallbacks wie demo_order_check)."""
    data = pnl.get("clientPortfolio", pnl)
    out: dict[str, dict] = {}
    for item in data.get("Positions", data.get("positions", [])) or []:
        pid = _first(item, "PositionID", "positionID", "positionId")
        if not pid:
            continue
        out[str(pid)] = {
            "instrument_id": int(_first(item, "InstrumentID", "instrumentID", "instrumentId", default=0) or 0),
            "is_buy": _first(item, "IsBuy", "isBuy"),
            "amount": _first(item, "Amount", "amount", "InvestedAmount", "investedAmount"),
            "open_rate": _first(item, "OpenRate", "openRate"),
            "open_time": _first(item, "OpenDateTime", "openDateTime", "OpenTimestamp"),
            "pnl": _first(item, "NetProfit", "netProfit", "unrealizedPnL", "UnrealizedPnL", "Profit", "profit"),
        }
    return out


def parse_credit(pnl: dict) -> float:
    data = pnl.get("clientPortfolio", pnl)
    return float(data.get("credit") or data.get("credits") or data.get("availableCash") or 0) or 0.0


def diff_positions(previous: dict[str, dict], current: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """``(eröffnet, geschlossen)``; geschlossen trägt den zuletzt gesehenen Zustand."""
    opened = {k: v for k, v in current.items() if k not in previous}
    closed = {k: v for k, v in previous.items() if k not in current}
    return opened, closed


def _fmt_position(pid: str, d: dict, names: dict[int, str]) -> str:
    sym = names.get(d.get("instrument_id", 0), f"Instrument {d.get('instrument_id')}")
    side = "Long" if d.get("is_buy") in (True, 1) else ("Short" if d.get("is_buy") in (False, 0) else "?")
    parts = [f"{side} {sym}", f"Position {pid}"]
    if d.get("amount") is not None:
        parts.append(f"{float(d['amount']):.2f} USD")
    if d.get("pnl") is not None:
        parts.append(f"PnL {float(d['pnl']):+.2f}")
    return ", ".join(parts)


def instrument_names(map_path: Path | None = None) -> dict[int, str]:
    try:
        data = json.loads((map_path or PROJECT_ROOT / "automation" / "config" / "instrument_map.json").read_text("utf-8"))
        return {int(k): spec.get("symbol", str(k)) for k, spec in data["instruments"].items()}
    except (OSError, ValueError, KeyError):
        return {}


def bot_running(lock_path: Path = BOT_LOCK) -> bool | None:
    """True, wenn die flock-gehaltene Sperrdatei des Inkubations-Bots belegt ist; None, wenn keine Sperrdatei existiert."""
    if not lock_path.exists():
        return None
    try:
        with open(lock_path, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
            return False
    except OSError:
        return None


def ledger_counts(directory: Path = INCUBATION_DIR) -> dict[str, int]:
    """Anzahl Session-Bars je Paar-Ledger (aktuelles Ledger, nicht archivierte)."""
    out: dict[str, int] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.jsonl")):
        if path.stem.count(".") == 0:       # archivierte Ledger heissen <paar>.<sha12>.jsonl
            try:
                out[path.stem] = sum(1 for line in path.read_text("utf-8").splitlines() if line.strip())
            except OSError:
                pass
    return out


def bot_log_errors(log_dir: Path, now: datetime) -> tuple[Path | None, int]:
    """Heutiges Inkubations-Bot-Log und die Zahl der ERROR-/Traceback-Zeilen."""
    path = log_dir / f"incubation_bot_{now.strftime('%Y%m%d')}.log"
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return None, 0
    return path, sum(1 for ln in lines if " ERROR" in ln or "Traceback" in ln or "CRITICAL" in ln)


def load_state(path: Path | None = None) -> dict:
    path = path or STATE_PATH
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", "utf-8")
    tmp.replace(path)


def build_events(state: dict, positions: dict[str, dict], credit: float, *, now: datetime, names: dict[int, str],
                 bot: bool | None, ledgers: dict[str, int], errors: int, force_summary: bool,
                 summary_hours: float = SUMMARY_EVERY_HOURS) -> tuple[list[str], dict]:
    """``(Zeilen, neuer Zustand)``. Beim allerersten Lauf (kein Zustand) werden vorhandene Positionen nur als
    Ausgangsbestand übernommen und nicht als Ereignis gemeldet."""
    first_run = "positions" not in state
    previous = state.get("positions", {})
    opened, closed = ({}, {}) if first_run else diff_positions(previous, positions)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"{stamp} ERÖFFNET {_fmt_position(k, v, names)}" for k, v in opened.items()]
    lines += [f"{stamp} GESCHLOSSEN {_fmt_position(k, v, names)} (zuletzt gesehen)" for k, v in closed.items()]
    if bot is False:
        lines.append(f"{stamp} WARNUNG Inkubations-Bot läuft nicht (Sperrdatei frei).")
    if errors and errors > state.get("last_error_count", 0):
        lines.append(f"{stamp} WARNUNG {errors - state.get('last_error_count', 0)} neue Fehlerzeilen im Bot-Log.")
    last_summary = state.get("last_summary")
    due = first_run or force_summary or last_summary is None or (
        now - datetime.fromisoformat(last_summary) >= timedelta(hours=summary_hours))
    new_state = dict(state, positions=positions, last_credit=credit, last_error_count=errors)
    if due:
        total_pnl = sum(float(v["pnl"]) for v in positions.values() if v.get("pnl") is not None)
        trades = state.get("trades_since_summary", 0) + len(opened) + len(closed)
        bars = ", ".join(f"{k}: {n} Bars" for k, n in ledgers.items()) or "noch keine Evidenz"
        bot_txt = {True: "läuft", False: "läuft NICHT", None: "unbekannt (keine Sperrdatei)"}[bot]
        lines.append(f"{stamp} ZUSAMMENFASSUNG Guthaben {credit:.2f} USD, {len(positions)} offene Position(en) "
                     f"(PnL {total_pnl:+.2f}), {trades} Positionsänderung(en) seit dem letzten Bericht, "
                     f"Bot {bot_txt}, Ledger: {bars}.")
        new_state.update(last_summary=now.isoformat(), trades_since_summary=0)
    else:
        new_state["trades_since_summary"] = state.get("trades_since_summary", 0) + len(opened) + len(closed)
    return lines, new_state


def fetch_pnl(api_key: str, user_key: str, environment: str, *, opener=urllib.request.urlopen) -> dict:
    from automation.account_guard import _USER_AGENT
    req = urllib.request.Request(PNL_URLS[environment], headers={
        "x-api-key": api_key, "x-user-key": user_key, "x-request-id": str(uuid.uuid4()), "User-Agent": _USER_AGENT})
    try:
        with opener(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise ReportError(f"PnL-Abfrage fehlgeschlagen: {exc}") from exc


def push(lines: list[str], url: str | None, *, opener=urllib.request.urlopen) -> None:
    """Optionaler Push (ntfy-kompatibel: POST mit Text). Fehler hier brechen den Report nie ab."""
    if not url or not lines:
        return
    try:
        req = urllib.request.Request(url, data="\n".join(lines).encode("utf-8"), method="POST",
                                     headers={"Title": "eToro Paper-Trading", "User-Agent": "etoro-nautilus/1.0"})
        opener(req, timeout=15).close()
    except Exception as exc:  # noqa: BLE001
        print(f"HINWEIS: Push fehlgeschlagen: {exc}", file=sys.stderr)


def write_outputs(lines: list[str], now: datetime, *, alerts_path: Path | None = None,
                  report_dir: Path | None = None) -> None:
    if not lines:
        return
    alerts_path, report_dir = alerts_path or ALERTS_PATH, report_dir or REPORT_DIR
    alerts_path.parent.mkdir(parents=True, exist_ok=True)
    with open(alerts_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    report_dir.mkdir(parents=True, exist_ok=True)
    with open(report_dir / f"paper_report_{now.strftime('%Y%m%d')}.md", "a", encoding="utf-8") as f:
        f.write("\n".join(f"- {ln}" for ln in lines) + "\n")


def run(*, force_summary: bool = False, now: datetime | None = None, fetch=fetch_pnl, environ=None) -> int:
    environ = environ if environ is not None else os.environ
    now = now or datetime.now(timezone.utc)
    from automation.account_guard import PaperAccountError, verify_paper_account
    from automation.papertrading import PaperTradingError, assert_demo_environment
    try:
        assert_demo_environment(environ.get("ETORO_ENV"))
    except PaperTradingError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    api_key, user_key = environ.get("ETORO_API_KEY", ""), environ.get("ETORO_USER_KEY", "")
    if not api_key or not user_key:
        print("FEHLER: ETORO_API_KEY / ETORO_USER_KEY fehlen in .env.", file=sys.stderr)
        return 2
    try:
        env_name = verify_paper_account(api_key, user_key, environ=environ)
    except PaperAccountError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    try:
        pnl = fetch(api_key, user_key, env_name)
    except ReportError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    positions, credit = parse_positions(pnl), parse_credit(pnl)
    _, errors = bot_log_errors(PROJECT_ROOT / "logs", now)
    lines, new_state = build_events(
        load_state(), positions, credit, now=now, names=instrument_names(), bot=bot_running(),
        ledgers=ledger_counts(), errors=errors, force_summary=force_summary)
    write_outputs(lines, now)
    save_state(new_state)
    if lines:
        print("\n".join(lines))
        push(lines, environ.get(NTFY_ENV))
    return 0


def cron_help() -> str:
    python = PROJECT_ROOT / "venv" / "bin" / "python"
    python = python if python.exists() else Path(sys.executable)
    return "\n".join([
        "Cron-Einrichtung (Ubuntu):",
        f"  1. Test:        cd {PROJECT_ROOT} && source venv/bin/activate && python -m automation.paper_report --force-summary",
        "  2. Dienst:      systemctl is-active cron   (sonst: sudo systemctl enable --now cron)",
        "  3. Crontab:     crontab -e   und diese Zeilen anhängen:",
        "                    SHELL=/bin/bash",
        "                    " + CRON_LINE.format(root=PROJECT_ROOT, python=python),
        "  4. Kontrolle:   crontab -l ; grep CRON /var/log/syslog | tail ; tail -f logs/paper_report_cron.log",
        f"  Ergebnisse:     {ALERTS_PATH}  (Ereignisse), {REPORT_DIR}/ (Tagesbericht)",
        f"  Push (optional): {NTFY_ENV}=https://ntfy.sh/<geheimes-thema> in .env eintragen",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper-Trading-Report (nur lesend, für Cron)")
    parser.add_argument("--force-summary", action="store_true", help="Zusammenfassung jetzt ausgeben")
    parser.add_argument("--cron-help", action="store_true", help="Cron-Einrichtung mit den echten Pfaden drucken")
    args = parser.parse_args(argv)
    if args.cron_help:
        print(cron_help())
        return 0
    from dotenv import load_dotenv
    load_dotenv(str(PROJECT_ROOT / ".env"))
    return run(force_summary=args.force_summary)


if __name__ == "__main__":
    sys.exit(main())
