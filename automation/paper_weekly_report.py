"""Wöchentlicher Paper-Auswertungsbericht (nur lesend): Round-Trips, Gebühren und Netto-PnL je Klasse und Instrument.

Quelle sind die Füllungen (``OrderFilled``) in ``logs/incubation_bot_<Datum>.log``. Sie werden je Instrument FIFO zu
Round-Trips gepaart. Der Bericht wertet NUR den Betrieb und die Kosten des Paper-Kontos aus; Paper-Ergebnisse zählen
nie als Evidenz für die Produktions-Gates.

Gebühren: steht im Log eine Kommission > 0, wird sie genommen. Sonst gilt das Modell aus ``backtest.json``
(``commission_bps_by_asset_class``, eToro-Krypto 1 % je Seite = 200 bps Round-Trip). Der Spread steckt in den
Fill-Preisen und ist nicht extra ausgewiesen.

Aufruf: ``python -m automation.paper_weekly_report [--days 7]``. ``paper_report`` ruft ihn bei jedem Lauf auf, schreibt
aber höchstens einmal je 7 Tage eine neue Datei ``paper_weekly_<Jahr>-W<Woche>.md`` nach ``data/paper_reports/``
(der stündliche Push veröffentlicht sie nach ``logs/paper_reports/``).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
REPORT_DIR = PROJECT_ROOT / "data" / "paper_reports"
INSTRUMENT_MAP = PROJECT_ROOT / "automation" / "config" / "instrument_map.json"
BACKTEST_CFG = PROJECT_ROOT / "automation" / "config" / "backtest.json"
ONE_WEEK = timedelta(days=7)

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})")
_FILL_RE = re.compile(r"OrderFilled\(([^)]*)\)")
_KV_RE = re.compile(r"(\w+)=([^,]*)")


@dataclass(frozen=True)
class Fill:
    ts: datetime
    instrument: str
    side: str          # "BUY" | "SELL"
    qty: float
    px: float
    commission: float


@dataclass(frozen=True)
class RoundTrip:
    instrument: str
    qty: float
    gross: float
    fee: float
    closed: datetime

    @property
    def net(self) -> float:
        return self.gross - self.fee


def _float(text: str) -> float | None:
    try:
        return float(str(text).strip().split()[0])
    except (ValueError, IndexError):
        return None


def parse_fills(log_paths: list[Path], since: datetime | None = None) -> list[Fill]:
    """``OrderFilled``-Zeilen aus den Bot-Logs, zeitlich sortiert, ohne doppelte Zeilen (gleiche trade_id)."""
    fills: list[Fill] = []
    seen: set[str] = set()
    for path in log_paths:
        try:
            lines = path.read_text("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _FILL_RE.search(line)
            if not m:
                continue
            fields = {k: v.strip() for k, v in _KV_RE.findall(m.group(1))}
            side, qty, px = fields.get("order_side", "").upper(), _float(fields.get("last_qty", "")), \
                _float(fields.get("last_px", ""))
            instrument = fields.get("instrument_id", "")
            if side not in ("BUY", "SELL") or not qty or px is None or not instrument:
                continue
            tid = fields.get("trade_id")
            if tid:
                if tid in seen:
                    continue
                seen.add(tid)
            t = _TS_RE.match(line)
            if t:
                ts = datetime.fromisoformat(f"{t.group(1)}T{t.group(2)}").replace(tzinfo=timezone.utc)
            else:
                day = re.search(r"(\d{8})", path.name)
                ts = datetime.strptime(day.group(1), "%Y%m%d").replace(tzinfo=timezone.utc) if day else \
                    datetime.fromtimestamp(0, timezone.utc)
            if since is not None and ts < since:
                continue
            fills.append(Fill(ts, instrument, side, qty, px, _float(fields.get("commission", "")) or 0.0))
    return sorted(fills, key=lambda f: f.ts)


def pair_round_trips(fills: list[Fill], fee_bps: dict[str, float]) -> tuple[list[RoundTrip], dict[str, float]]:
    """FIFO-Paarung je Instrument. Gibt abgeschlossene Round-Trips und die noch offene Nettomenge je Instrument.

    ``fee_bps``: Round-Trip-Gebühr in bps je Instrument (Modell); eine geloggte Kommission > 0 hat Vorrang."""
    lots: dict[str, deque] = defaultdict(deque)      # instrument -> deque[[signierte Restmenge, Preis, Kommission je Stück]]
    trips: list[RoundTrip] = []
    for f in fills:
        signed = f.qty if f.side == "BUY" else -f.qty
        book = lots[f.instrument]
        remaining, per_unit_fee = signed, f.commission / f.qty
        while remaining and book and book[0][0] * remaining < 0:
            lot = book[0]
            matched = min(abs(lot[0]), abs(remaining))
            direction = 1.0 if lot[0] > 0 else -1.0                      # Long-Lot wird durch Verkauf geschlossen
            gross = direction * (f.px - lot[1]) * matched
            logged = (lot[2] + per_unit_fee) * matched
            model = (lot[1] + f.px) * matched * fee_bps.get(f.instrument, 0.0) / 2.0 / 10_000.0
            trips.append(RoundTrip(f.instrument, matched, gross, logged if logged > 0 else model, f.ts))
            lot[0] -= direction * matched
            remaining += direction * matched
            if abs(lot[0]) < 1e-12:
                book.popleft()
        if abs(remaining) > 1e-12:
            book.append([remaining, f.px, per_unit_fee])
    open_qty = {k: sum(l[0] for l in v) for k, v in lots.items() if v and abs(sum(l[0] for l in v)) > 1e-12}
    return trips, open_qty


def asset_classes(map_path: Path = INSTRUMENT_MAP) -> dict[str, str]:
    try:
        data = json.loads(map_path.read_text("utf-8"))["instruments"]
    except (OSError, ValueError, KeyError):
        return {}
    return {spec["symbol"]: spec.get("asset_class", "unbekannt") for spec in data.values() if "symbol" in spec}


def fee_model(classes: dict[str, str], cfg_path: Path = BACKTEST_CFG) -> dict[str, float]:
    """Round-Trip-Gebühr (bps) je Instrument aus ``commission_bps_by_asset_class`` (Schlüssel gross) bzw. ``commission_bps``."""
    try:
        cfg = json.loads(cfg_path.read_text("utf-8"))
    except (OSError, ValueError):
        cfg = {}
    by_class = {str(k).upper(): float(v) for k, v in (cfg.get("commission_bps_by_asset_class") or {}).items()}
    default = float(cfg.get("commission_bps", 0.0) or 0.0)
    return {sym: by_class.get(cls.upper(), default) for sym, cls in classes.items()}


def _row(label: str, trips: list[RoundTrip]) -> str:
    n = len(trips)
    wins = sum(1 for t in trips if t.net > 0)
    gross, fee = sum(t.gross for t in trips), sum(t.fee for t in trips)
    net = gross - fee
    return (f"| {label} | {n} | {wins / n * 100:.0f} % | {gross:+.2f} | {fee:.2f} | {net:+.2f} | {net / n:+.3f} |"
            if n else f"| {label} | 0 | - | - | - | - | - |")


def render(trips: list[RoundTrip], open_qty: dict[str, float], classes: dict[str, str], *, start: datetime,
           end: datetime) -> str:
    head = "| | Round-Trips | Gewinner | Brutto USD | Gebühren USD | Netto USD | Netto je Trade |\n|---|---|---|---|---|---|---|"
    by_class: dict[str, list[RoundTrip]] = defaultdict(list)
    by_inst: dict[str, list[RoundTrip]] = defaultdict(list)
    for t in trips:
        by_class[classes.get(t.instrument, "unbekannt")].append(t)
        by_inst[t.instrument].append(t)
    out = [f"# Paper-Wochenbericht {start:%Y-%m-%d} bis {end:%Y-%m-%d} (UTC)", "",
           f"Erstellt {end:%Y-%m-%d %H:%M} UTC, Quelle: Füllungen in den Bot-Logs. Nur Betrieb und Kosten, "
           "keine Evidenz für die Produktions-Gates.", "", "## Gesamt und je Klasse", "", head, _row("**Alle**", trips)]
    out += [_row(cls, ts) for cls, ts in sorted(by_class.items())]
    out += ["", "## Je Instrument", "", head]
    out += [_row(sym, ts) for sym, ts in sorted(by_inst.items(), key=lambda kv: sum(t.net for t in kv[1]))]
    if not trips:
        out += ["", "Keine abgeschlossenen Round-Trips in diesem Zeitraum."]
    out += ["", "## Noch offen (laut Logs)", ""]
    out += [f"- {sym}: {qty:+g}" for sym, qty in sorted(open_qty.items())] or ["- keine"]
    n_enough = sum(1 for ts in by_class.values() if len(ts) >= 30)
    out += ["", "## Aussagekraft", "",
            f"{len(trips)} Round-Trips insgesamt, {n_enough} von {len(by_class)} Klassen mit mindestens 30. "
            "Unter 30 Round-Trips je Klasse ist die Rendite nicht von Zufall zu unterscheiden; Betrieb und Gebühren "
            "sind trotzdem aussagekräftig.", "",
            "Hinweise: Gebühren sind für Krypto geloggt oder modelliert (1 % je Seite), bei Forex, Aktien und Rohstoffen "
            "steckt die Handelskosten nur im Spread der Fill-Preise. Teilfüllungen werden FIFO gepaart; Positionen, "
            "die vor Beginn der Logs eröffnet wurden, erscheinen nur mit ihrem Schliessen."]
    return "\n".join(out) + "\n"


def build(days: int = 7, *, now: datetime | None = None, log_dir: Path = LOG_DIR) -> str:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    paths = sorted(log_dir.glob("incubation_bot_*.log"))
    fills = parse_fills(paths, since=start)
    classes = asset_classes()
    trips, open_qty = pair_round_trips(fills, fee_model(classes))
    return render([t for t in trips if t.closed >= start], open_qty, classes, start=start, end=now)


def maybe_write_weekly(state: dict, now: datetime, *, report_dir: Path | None = None, log_dir: Path = LOG_DIR) -> dict:
    """Schreibt höchstens alle 7 Tage einen Wochenbericht; Fehler brechen den Aufrufer nie ab."""
    last = state.get("last_weekly")
    if last and now - datetime.fromisoformat(last) < ONE_WEEK:
        return state
    try:
        year, week, _ = now.isocalendar()
        target = (report_dir or REPORT_DIR)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"paper_weekly_{year}-W{week:02d}.md").write_text(build(7, now=now, log_dir=log_dir), "utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"HINWEIS: Wochenbericht fehlgeschlagen: {exc}", file=sys.stderr)
        return state
    return dict(state, last_weekly=now.isoformat())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper-Wochenbericht (nur lesend)")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args(argv)
    print(build(args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
