"""Periodische Kalibrierung der Backtest-Ertragsschätzung gegen den realisierten Netto-Ertrag (nur lesend am Konto).

Liest ``data/state/trade_estimates.jsonl`` (Events von ``TradeLedger``), rechnet je Anlageklasse und gesamt
``λ = Regression durch den Ursprung realisiert ~ λ·geschätzt`` (begrenzt auf [0, 1]) und schreibt
``data/state/estimate_calibration.json`` (wird vom Bot beim nächsten Trade-Start gelesen und als ``kalibriert`` ins Log gesetzt)
sowie ``results/calibration/<Datum>.md``. Es ändert weder Ein- noch Ausstiege noch Positionsgrössen; unter 30 Trades bleibt
alles bei λ = 1. Läuft über den systemd-User-Timer ``etoro-calibrate.timer``.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.trade_estimate import calibrate, calibration_lines

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = PROJECT_ROOT / "data" / "state" / "trade_estimates.jsonl"
CALIBRATION_PATH = PROJECT_ROOT / "data" / "state" / "estimate_calibration.json"
REPORT_DIR = PROJECT_ROOT / "results" / "calibration"
WINDOW_DAYS = 60


def load_events(path: Path, since: datetime) -> list[dict]:
    try:
        rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if str(r.get("ts", "")) >= since.strftime("%Y-%m-%dT%H:%M:%S")]


def render(now: datetime, cal: dict, events: list[dict]) -> str:
    L = [f"# Kalibrierung Backtest-Schätzung {now:%Y-%m-%d}", "", f"Stand: {now:%Y-%m-%d %H:%M} UTC, Fenster {WINDOW_DAYS} Tage, "
         f"{cal['n_trades']} geschlossene Trades mit Schätzung.", "", f"Verfahren: {cal['note']}.", ""]
    if cal["global"]:
        g = cal["global"]
        L += [f"- gesamt: λ {g['lambda']:.2f} (roh {g['lambda_raw']:.2f}), Ø Schätzung {g['mean_estimate_bps']:+.1f} bps, "
              f"Ø realisiert {g['mean_realized_bps']:+.1f} bps, Delta {g['delta_bps']:+.1f} bps (n={g['n']})"]
    for cls, v in sorted(cal["by_class"].items()):
        L.append(f"- {cls}: λ {v['lambda']:.2f}, Delta {v['delta_bps']:+.1f} bps, Basis {v['basis']}, n={v.get('n_class', v['n'])}")
    L += ["", "## Schätzung gegen Ergebnis", ""] + calibration_lines(events) + [""]
    return "\n".join(L)


def main() -> int:
    now = datetime.now(timezone.utc)
    try:
        events = load_events(EVENTS_PATH, now - timedelta(days=WINDOW_DAYS))
        cal = calibrate(events)
        cal["updated_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CALIBRATION_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cal, indent=1, sort_keys=True), "utf-8")
        tmp.replace(CALIBRATION_PATH)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        text = render(now, cal, events)
        (REPORT_DIR / f"{now:%Y-%m-%d}.md").write_text(text, "utf-8")
    except Exception as exc:   # noqa: BLE001 - Timer-Lauf soll sauber mit Code 2 enden
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
