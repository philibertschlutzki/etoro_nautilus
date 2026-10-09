"""Periodischer Report für das Demo-Papertrading (nur lesend, keine Orders).

``python -m automation.papertrading_report`` schreibt einen Statusbericht auf stdout und hängt ihn an
``logs/papertrading_report.log`` an:

* lebt der Inkubations-Bot (PID aus ``data/state/incubation_bot.lock``)?
* Guthaben und offene Positionen des Paper-Kontos (``demo_order_check``, dieselbe Konto-Prüfung),
  Differenz zum Stand des letzten Reports (``data/state/papertrading_report_state.json``);
* Order-/Positionszeilen und Fehler aus dem heutigen Bot-Log (``logs/incubation_bot_<YYYYMMDD>.log``);
* Stufen je Paar (``data/state/deployment_stages.json``).

Exit-Code 0 = Bericht erzeugt und Bot lebt, 1 = Bot läuft nicht oder Konto-Abfrage fehlgeschlagen,
2 = Konfigurationsfehler (Keys fehlen, Konto-Prüfung verweigert). Der Einsatz als Cronjob steht in
``automation/papertrading_report.sh``."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = PROJECT_ROOT / "data" / "state" / "incubation_bot.lock"
STAGES_PATH = PROJECT_ROOT / "data" / "state" / "deployment_stages.json"
STATE_PATH = PROJECT_ROOT / "data" / "state" / "papertrading_report_state.json"
REPORT_LOG = PROJECT_ROOT / "logs" / "papertrading_report.log"
TRADE_RE = re.compile(r"OrderSubmitted|OrderAccepted|OrderFilled|OrderRejected|OrderDenied|OrderCanceled|"
                      r"PositionOpened|PositionClosed|PositionChanged")
ERROR_RE = re.compile(r"\[(ERROR|CRITICAL)\]|Traceback")


def bot_status() -> tuple[bool, str]:
    try:
        lock = json.loads(LOCK_PATH.read_text("utf-8"))
        pid = int(lock["pid"])
    except (OSError, ValueError, KeyError):
        return False, "kein Lock (Bot nicht gestartet oder beendet)"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, f"PID {pid} läuft nicht mehr (Lock veraltet)"
    except PermissionError:
        pass
    return True, f"PID {pid} läuft (gestartet {lock.get('started_utc', '?')}, Umgebung {lock.get('environment', '?')})"


def log_events(day: datetime, limit: int = 20) -> tuple[list[str], list[str]]:
    path = PROJECT_ROOT / "logs" / f"incubation_bot_{day:%Y%m%d}.log"
    trades: list[str] = []
    errors: list[str] = []
    try:
        for line in path.read_text("utf-8", errors="replace").splitlines():
            if TRADE_RE.search(line):
                trades.append(line[:260])
            elif ERROR_RE.search(line):
                errors.append(line[:260])
    except OSError:
        return [], [f"Bot-Log {path.name} nicht lesbar"]
    return trades[-limit:], errors[-limit:]


def stages() -> dict[str, str]:
    try:
        return {k: (v or {}).get("stage", "?") for k, v in json.loads(STAGES_PATH.read_text("utf-8")).items()}
    except (OSError, ValueError):
        return {}


def account_lines() -> tuple[list[str], dict | None, int]:
    """Konto-Stufe 1 von ``demo_order_check`` (nur lesend). Rückgabe: Zeilen, Zustand, Exit-Code."""
    from dotenv import load_dotenv
    from automation import demo_order_check as doc
    load_dotenv(str(PROJECT_ROOT / ".env"))
    api_key, user_key = os.getenv("ETORO_API_KEY", ""), os.getenv("ETORO_USER_KEY", "")
    if not api_key or not user_key:
        return ["FEHLER: ETORO_API_KEY / ETORO_USER_KEY fehlen in .env."], None, 2
    from automation.account_guard import PaperAccountError, verify_paper_account
    try:
        doc.REST_BASE, doc.PNL_URL = doc._BASES[verify_paper_account(api_key, user_key)]
    except PaperAccountError as exc:
        return [f"FEHLER: {exc}"], None, 2
    lines: list[str] = []

    async def _read() -> dict:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            return await doc._get_pnl(session, api_key, user_key)

    try:
        pnl = asyncio.run(_read())
    except Exception as exc:  # noqa: BLE001 — Report darf nie mit Traceback enden
        return [f"FEHLER Konto-Abfrage: {exc}"], None, 1
    positions = doc.open_positions(pnl)
    data = pnl.get("clientPortfolio", pnl)
    state = {"credit": doc.credit(pnl), "positions": sorted(positions),
             "unrealized_pnl": float(data.get("unrealizedPnL") or 0.0)}
    lines.append(f"Guthaben: {state['credit']:.2f} USD | unrealisierter PnL: {state['unrealized_pnl']:.2f} USD | "
                 f"offene Positionen: {len(positions)}")
    return lines, state, 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Report für das Demo-Papertrading (nur lesend)").parse_args(argv)
    now = datetime.now(timezone.utc)
    alive, bot_txt = bot_status()
    acct, state, rc = account_lines()
    out = [f"=== Papertrading-Report {now:%Y-%m-%d %H:%M} UTC ===", f"Bot: {bot_txt}", *acct]
    if state is not None:
        try:
            prev = json.loads(STATE_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            prev = None
        if prev:
            out.append(f"Seit letztem Report ({prev.get('utc', '?')}): Guthaben {state['credit'] - prev['credit']:+.2f} USD, "
                       f"Positionen {len(prev.get('positions', []))} -> {len(state['positions'])}")
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({**state, "utc": f"{now:%Y-%m-%dT%H:%M:%SZ}"}), "utf-8")
    st = stages()
    out.append("Stufen: " + (", ".join(f"{k}={v}" for k, v in sorted(st.items())) or "keine"))
    trades, errors = log_events(now)
    out.append(f"Order-/Positionsereignisse heute (letzte {len(trades)}):" if trades else "Order-/Positionsereignisse heute: keine")
    out.extend(f"  {t}" for t in trades)
    if errors:
        out.append(f"Fehler im Bot-Log (letzte {len(errors)}):")
        out.extend(f"  {e}" for e in errors)
    text = "\n".join(out)
    print(text)
    try:
        REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REPORT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")
    except OSError as exc:
        print(f"WARNUNG: Report-Log nicht schreibbar: {exc}", file=sys.stderr)
    if rc:
        return rc
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
