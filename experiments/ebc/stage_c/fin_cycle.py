"""Regelmäßige, automatische Finanzierungsmessung (Demo-Konto) als Zyklus: Freitag öffnen, Dienstag schließen.

Ein Zyklus öffnet freitags 19:40 UTC (US-Börse, FX und Krypto offen) je Klasse/Seite eine kleine Testposition
(``run_stage_c.py fin``: K1 Long/Short <= 100 USD, K2 Long/Short ~1010 USD, K5 Long <= 100 USD), lässt sie über das
Wochenende und die Nacht auf Dienstag laufen (der tägliche ``ebc-stagec-monitor`` schreibt die Gebührenfelder) und
schließt sie dienstags 07:10 UTC (>= 80 h später) per ``closeall``; danach läuft ``evaluate_stage_c.py``.

SICHERHEIT: Standard ist ENTSCHÄRFT. Ohne die Datei ``results/stage_c/fin_cycle.ARMED`` (Inhalt ``demo``) laufen
die Phasen nur als Dry-Run (GET, nie ein POST). Scharfschalten: ``touch`` bzw. ``echo demo > ...ARMED``,
entschärfen: Datei löschen. Aufträge nur über ``run_stage_c.py`` (Konto-Wächter, Bot-Instrumente ausgeschlossen,
nur Demo, ``--confirm-account demo``). Zustand: ``results/stage_c/fin_cycle.json``.

Aufruf (systemd-Timer ruft zweimal wöchentlich ``--auto``)::

    python fin_cycle.py --auto [--now 2026-10-23T19:41:00+00:00]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

OUT = "results/stage_c"
STATE = f"{OUT}/fin_cycle.json"
ARMED = f"{OUT}/fin_cycle.ARMED"
HERE = os.path.dirname(os.path.abspath(__file__))
OPEN_WEEKDAY, OPEN_FROM, OPEN_TO = 4, (19, 30), (19, 55)         # Freitag 19:30-19:55 UTC
MIN_HOLD_H = 80.0
CLASSES = "K1,K2,K5"


def decide(now: dt.datetime, state: dict) -> str:
    """'open' | 'close' | 'rotate' | 'wait'. Rein, ohne I/O. ``state``: {opened_utc, closed_utc} als ISO-Strings oder fehlend."""
    now = now.astimezone(dt.timezone.utc)
    opened = dt.datetime.fromisoformat(state["opened_utc"]) if state.get("opened_utc") else None
    closed = dt.datetime.fromisoformat(state["closed_utc"]) if state.get("closed_utc") else None
    cycle_open = opened is not None and (closed is None or closed < opened)
    hm = (now.hour, now.minute)
    in_open_window = now.weekday() == OPEN_WEEKDAY and OPEN_FROM <= hm < OPEN_TO
    if cycle_open:
        if (now - opened).total_seconds() < MIN_HOLD_H * 3600:
            return "wait"
        return "rotate" if in_open_window else "close"       # abgelaufener Zyklus am Freitagsfenster: schliessen und neu öffnen
    return "open" if in_open_window else "wait"


def load_state() -> dict:
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def save_state(st: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def armed() -> bool:
    return os.path.exists(ARMED) and open(ARMED).read().strip() == "demo"


def run(py: str, args: list[str]) -> int:
    print("+", " ".join(args), flush=True)
    return subprocess.run([py, *args]).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--auto", action="store_true")
    g.add_argument("--open-now", action="store_true",
                   help="Zyklus sofort eröffnen (z. B. Montag nach US-Eröffnung); nur mit ARMED-Datei scharf")
    ap.add_argument("--now", default=None)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    now = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    st = load_state()
    action = "open" if a.open_now else decide(now, st)
    if a.open_now and st.get("opened_utc") and not (st.get("closed_utc") and st["closed_utc"] >= st["opened_utc"]):
        print("Zyklus bereits offen (opened_utc", st["opened_utc"], ") - erst closeall, dann --open-now")
        return 1
    live = ["--live", "--confirm-account", "demo"] if armed() else []
    stage = os.path.join(HERE, "run_stage_c.py")
    print(f"{now.isoformat()} Aktion={action} Modus={'SCHARF (Demo)' if live else 'ENTSCHAERFT (Dry-Run)'}")
    if action == "open":
        rc = run(a.python, [stage, "fin", "--env", "demo", "--classes", CLASSES, *live])
        if live and rc == 0:
            st.update(opened_utc=now.isoformat(), closed_utc=None)
            save_state(st)
        return rc
    if action in ("close", "rotate"):
        rc = run(a.python, [stage, "closeall", "--env", "demo", *live])
        if live and rc == 0:
            st["closed_utc"] = now.isoformat()
            save_state(st)
            run(a.python, [os.path.join(HERE, "evaluate_stage_c.py")])
        if action == "rotate" and rc == 0:
            rc = run(a.python, [stage, "fin", "--env", "demo", "--classes", CLASSES, *live])
            if live and rc == 0:
                st.update(opened_utc=now.isoformat(), closed_utc=None)
                save_state(st)
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
