"""Wöchentlicher Paper-Bericht: abgeschlossene Trades, Gebühren und Netto-Ergebnis direkt aus eToro (nur lesend).

Quelle ist ``GET /api/v1/trading/info/trade/history?minDate=<Datum>`` (abgeschlossene Positionen des angehefteten
Paper-Kontos) und ``.../pnl`` (Kontostand, offene Positionen). Es werden NIE Orders gesendet.

Wichtig zur Gebühr: ``netProfit`` der Historie ist der Bruttogewinn der Position (Kurs-Differenz × Einheiten); die
externe eToro-Krypto-Gebühr (1 % je Seite, ``commission_bps_by_asset_class.CRYPTO`` = 200 bps Round-Trip in
``automation/config/backtest.json``) steht dort nicht. Der Bericht rechnet sie deshalb für Krypto aus
``investment`` und Verkaufswert nach (am echten Kontostand geprüft: 156.10 USD INJ, Verkauf 156.95 USD, Gebühr
1.57 USD = 1 % des Verkaufswerts). Andere Klassen: 0 (Spreads stecken in den Kursen).

Damit die Auswertung nicht vom Zeitfenster der API abhängt, werden alle gesehenen Trades dauerhaft in
``data/state/paper_trades.jsonl`` gesammelt (je ``positionId`` einmal).

Ausgabe: ``data/paper_reports/paper_weekly_<JJJJ-Www>.md`` und ``paper_weekly_latest.md`` (der stündliche
``paper_report_publish`` legt sie unter ``logs/paper_reports/`` ins Repo). Ohne ``--since`` gilt das Fenster der letzten
7 Tage; ``--all`` wertet alles seit dem ersten gesammelten Trade aus.

Aussagekraft: Ab ``MIN_TRADES_FOR_STATEMENT`` (30) Round-Trips je Gruppe nennt der Bericht Kennzahlen als belastbar, davor
ausdrücklich nicht (Stichprobe zu klein).

Aufruf (Cron/systemd-Timer, montags)::

    venv/bin/python -m automation.paper_weekly_report            # letzte 7 Tage
    venv/bin/python -m automation.paper_weekly_report --all      # alles seit Start
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = PROJECT_ROOT / "data" / "paper_reports"
TRADES_PATH = PROJECT_ROOT / "data" / "state" / "paper_trades.jsonl"
WHITELIST_PATH = PROJECT_ROOT / "data" / "state" / "incubation_whitelist.json"
INSTRUMENT_MAP_PATH = PROJECT_ROOT / "automation" / "config" / "instrument_map.json"
BACKTEST_CFG_PATH = PROJECT_ROOT / "automation" / "config" / "backtest.json"
HISTORY_URL = "https://public-api.etoro.com/api/v1/trading/info/trade/history"
PNL_URLS = {"demo": "https://public-api.etoro.com/api/v1/trading/info/demo/pnl",
            "real": "https://public-api.etoro.com/api/v1/trading/info/real/pnl"}   # real: nur das angeheftete Paper-Konto
MIN_TRADES_FOR_STATEMENT = 30
WARMUP_LOOKBACK_DAYS = 30


class ReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class Trade:
    position_id: str
    instrument_id: int
    symbol: str
    asset_class: str
    strategy: str
    is_buy: bool
    opened: datetime
    closed: datetime
    investment: float
    gross: float          # netProfit der eToro-Historie (ohne externe Gebühr)
    fee: float            # nachgerechnete externe Gebühr (Krypto), >= 0

    @property
    def net(self) -> float:
        return self.gross - self.fee

    @property
    def hold_hours(self) -> float:
        return (self.closed - self.opened).total_seconds() / 3600.0


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


# ─── Daten holen ────────────────────────────────────────────────────────────────────────────────────────────

def _get(url: str, api_key: str, user_key: str, *, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, headers={
        "x-api-key": api_key, "x-user-key": user_key, "x-request-id": str(uuid.uuid4()),
        "User-Agent": _user_agent()})
    try:
        with opener(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise ReportError(f"Abruf fehlgeschlagen ({url.split('?')[0]}): {exc}") from exc


def _user_agent() -> str:
    from automation.account_guard import _USER_AGENT
    return _USER_AGENT


def fetch_history(api_key: str, user_key: str, min_date: str, *, opener=urllib.request.urlopen) -> list[dict]:
    data = _get(f"{HISTORY_URL}?minDate={min_date}", api_key, user_key, opener=opener)
    if isinstance(data, dict):
        data = data.get("trades") or data.get("items") or data.get("data") or []
    if not isinstance(data, list):
        raise ReportError("Trade-Historie hat ein unerwartetes Format.")
    return data


def load_ledger(path: Path = TRADES_PATH) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[str(row["positionId"])] = row
    except (OSError, ValueError, KeyError):
        pass
    return out


def merge_ledger(rows: list[dict], path: Path = TRADES_PATH) -> tuple[dict[str, dict], int]:
    """Hängt neue Trades (nach positionId) an die dauerhafte Sammlung an; liefert (alle, neu)."""
    known = load_ledger(path)
    new = [r for r in rows if str(r.get("positionId")) not in known and r.get("closeTimestamp")]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for r in new:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
                known[str(r["positionId"])] = r
    return known, len(new)


# ─── Auswertung ─────────────────────────────────────────────────────────────────────────────────────────────

def crypto_fee_rate(backtest_cfg_path: Path | None = None) -> float:
    """Gebühr je Seite als Anteil (CRYPTO-Round-Trip in bps / 2 / 10000); Standard 1 %."""
    try:
        bps = float((json.loads((backtest_cfg_path or BACKTEST_CFG_PATH).read_text("utf-8")).get("commission_bps_by_asset_class") or {})
                    .get("CRYPTO", 200.0))
    except (OSError, ValueError):
        bps = 200.0
    return bps / 2.0 / 10000.0


def build_trades(ledger: dict[str, dict], *, instruments: dict, strategies: dict[str, str], fee_rate: float) -> list[Trade]:
    by_id = {str(k): v for k, v in instruments.items()}
    trades = []
    for row in ledger.values():
        meta = by_id.get(str(row.get("instrumentId")), {})
        symbol = str(meta.get("symbol") or f"ID{row.get('instrumentId')}")
        cls = str(meta.get("asset_class") or "unbekannt")
        invest = float(row.get("initialInvestment") or row.get("investment") or 0.0)
        gross = float(row.get("netProfit") or 0.0)
        fee = 0.0
        if cls == "crypto":
            fee = fee_rate * invest + fee_rate * max(invest + gross, 0.0)
        trades.append(Trade(
            position_id=str(row["positionId"]), instrument_id=int(row.get("instrumentId") or 0), symbol=symbol,
            asset_class=cls, strategy=strategies.get(symbol, "(nicht mehr in der Whitelist)"),
            is_buy=bool(row.get("isBuy", True)), opened=_ts(row["openTimestamp"]), closed=_ts(row["closeTimestamp"]),
            investment=invest, gross=gross, fee=fee))
    return sorted(trades, key=lambda t: t.closed)


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def summarize(trades: list[Trade]) -> dict:
    n = len(trades)
    nets = [t.net for t in trades]
    wins = sum(1 for x in nets if x > 0)
    mean = sum(nets) / n if n else 0.0
    var = sum((x - mean) ** 2 for x in nets) / (n - 1) if n > 1 else 0.0
    tstat = mean / math.sqrt(var / n) if n > 1 and var > 0 else None
    lo, hi = wilson(wins, n)
    gross_win = sum(x for x in nets if x > 0)
    gross_loss = -sum(x for x in nets if x < 0)
    return {
        "n": n, "wins": wins, "win_rate": wins / n if n else 0.0, "wilson": (lo, hi),
        "gross": sum(t.gross for t in trades), "fees": sum(t.fee for t in trades), "net": sum(nets),
        "mean": mean, "tstat": tstat, "pf": (gross_win / gross_loss) if gross_loss > 0 else None,
        "invest": sum(t.investment for t in trades),
        "hold_h": (sum(t.hold_hours for t in trades) / n) if n else 0.0,
        "enough": n >= MIN_TRADES_FOR_STATEMENT,
    }


def _group(trades: list[Trade], key) -> dict[str, list[Trade]]:
    out: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        out[key(t)].append(t)
    return out


def _fmt_pf(pf) -> str:
    return "–" if pf is None else f"{pf:.2f}"


def _row(name: str, s: dict) -> str:
    flag = "ja" if s["enough"] else "nein"
    return (f"| {name} | {s['n']} | {s['win_rate'] * 100:.0f} % | {s['gross']:+.2f} | {s['fees']:.2f} | "
            f"{s['net']:+.2f} | {s['mean']:+.3f} | {_fmt_pf(s['pf'])} | {flag} |")


_HEAD = ("| {k} | Trades | Trefferquote | Brutto USD | Gebühr USD | Netto USD | Ø Netto/Trade | Profit-Faktor | "
         f"n ≥ {MIN_TRADES_FOR_STATEMENT} |\n|---|---|---|---|---|---|---|---|---|")


def render(trades: list[Trade], *, since: datetime, now: datetime, equity: dict | None, open_positions: list[dict],
           new_trades: int, pairs_total: int | None = None) -> str:
    total = summarize(trades)
    lines = [f"# Wöchentlicher Paper-Bericht {now.strftime('%G-W%V')}", "",
             f"Zeitraum: {since:%Y-%m-%d %H:%M} bis {now:%Y-%m-%d %H:%M} UTC (abgeschlossene Trades). "
             "Quelle: eToro-Trade-Historie des Paper-Kontos, Krypto-Gebühr nachgerechnet.", ""]
    lines += ["## Kontostand (eToro)", ""]
    if equity:
        lines += ["| Kennzahl | Wert |", "|---|---|",
                  f"| Freies Guthaben | {equity['credit']:.2f} USD |",
                  f"| Investiert (offene Positionen) | {equity['invested']:.2f} USD |",
                  f"| Unrealisierter PnL | {equity['unrealized']:+.2f} USD |",
                  f"| Equity | {equity['equity']:.2f} USD |",
                  f"| Einstiegsgebühr offener Krypto-Positionen (im Guthaben schon abgezogen) | {equity.get('open_entry_fees', 0.0):.2f} USD |", ""]
    else:
        lines += ["Kontostand nicht abrufbar.", ""]
    lines += ["## Gesamt", "", _HEAD.format(k="Gruppe"), _row("alle Klassen", total), ""]
    if not trades:
        lines += ["Im Zeitraum wurden keine Trades abgeschlossen.", ""]
    lines += ["## Aussagekraft", ""]
    lo, hi = total["wilson"]
    if total["n"] == 0:
        lines.append("Keine abgeschlossenen Trades: keine Aussage möglich.")
    elif total["enough"]:
        t = "–" if total["tstat"] is None else f"{total['tstat']:+.2f}"
        lines.append(f"{total['n']} Trades (≥ {MIN_TRADES_FOR_STATEMENT}): Kennzahlen sind belastbar genug für eine erste Einschätzung. "
                     f"Trefferquote {total['win_rate'] * 100:.0f} % (95-%-Intervall {lo * 100:.0f}–{hi * 100:.0f} %), "
                     f"t-Wert des mittleren Netto-Ergebnisses {t} (|t| > 2 spricht für einen echten Effekt).")
    else:
        lines.append(f"Nur {total['n']} abgeschlossene Trades (Mindestmenge für eine Aussage: {MIN_TRADES_FOR_STATEMENT}). "
                     f"Trefferquote {total['win_rate'] * 100:.0f} % mit 95-%-Intervall {lo * 100:.0f}–{hi * 100:.0f} %: "
                     "Das Ergebnis lässt sich noch nicht von Zufall und Gebühren trennen; bewertet werden nur Betrieb und Kosten.")
    lines.append("")
    for title, key in (("Nach Anlageklasse", lambda t: t.asset_class), ("Nach Strategie", lambda t: t.strategy)):
        groups = _group(trades, key)
        lines += [f"## {title}", ""]
        if groups:
            lines.append(_HEAD.format(k=title.split()[-1]))
            for name, grp in sorted(groups.items(), key=lambda kv: summarize(kv[1])["net"]):
                lines.append(_row(name, summarize(grp)))
        else:
            lines.append("Keine Trades.")
        lines.append("")
    by_symbol = sorted(_group(trades, lambda t: t.symbol).items(), key=lambda kv: summarize(kv[1])["net"])
    if by_symbol:
        lines += ["## Schlechteste und beste Instrumente (Netto)", "", _HEAD.format(k="Instrument")]
        shown = by_symbol[:5] + [x for x in by_symbol[-5:] if x not in by_symbol[:5]]
        lines += [_row(name, summarize(grp)) for name, grp in shown]
        lines.append("")
    lines += ["## Offene Positionen", ""]
    if open_positions:
        lines += ["| Instrument | Richtung | Betrag USD | Einstieg | PnL USD |", "|---|---|---|---|---|"]
        lines += [f"| {p['symbol']} | {'Long' if p['is_buy'] else 'Short'} | {p['amount']:.2f} | {p['open_rate']} | "
                  f"{p['pnl']:+.2f} |" for p in open_positions]
    else:
        lines.append("Keine offenen Positionen.")
    lines += ["", "## Hinweise", "",
              "- Brutto = `netProfit` der eToro-Historie (Kurs-Differenz × Einheiten). Gebühr = nachgerechnete Krypto-Gebühr "
              "(1 % je Seite); Forex, Rohstoffe und Aktien: 0, Spreads stecken in den Kursen.",
              "- Strategie-Zuordnung nach der aktuellen Whitelist (zurückgezogene Paare erscheinen als „nicht mehr in der Whitelist“).",
              f"- Neu gesammelte Trades in diesem Lauf: {new_trades}; alle Trades liegen in `data/state/paper_trades.jsonl`."]
    return "\n".join(lines) + "\n"


# ─── Lauf ───────────────────────────────────────────────────────────────────────────────────────────────────

def load_strategies(path: Path | None = None) -> dict[str, str]:
    try:
        w = json.loads((path or WHITELIST_PATH).read_text("utf-8")).get("per_symbol_winners") or {}
    except (OSError, ValueError):
        return {}
    return {sym: str(v.get("strategy", "?")).replace("Strategy", "") for sym, v in w.items() if isinstance(v, dict)}


def account_summary(pnl: dict, names: dict, classes: dict | None = None, fee_rate: float = 0.01) -> tuple[dict, list[dict]]:
    from automation.account_view import parse_snapshot
    snap = parse_snapshot(pnl)
    classes = classes or {}
    opens = [{"symbol": names.get(p.instrument_id, f"ID{p.instrument_id}"), "is_buy": p.is_buy, "amount": p.amount,
              "open_rate": "", "pnl": p.pnl,
              "entry_fee": fee_rate * p.amount if classes.get(p.instrument_id) == "crypto" else 0.0}
             for p in snap.positions]
    raw = (pnl.get("clientPortfolio", pnl) or {}).get("positions") or []
    rates = {str(r.get("positionID") or r.get("positionId")): r.get("openRate") for r in raw}
    for p, o in zip(snap.positions, opens):
        o["open_rate"] = rates.get(p.position_id, "")
    return ({"credit": snap.credit, "invested": snap.invested, "unrealized": snap.unrealized_pnl,
             "equity": snap.equity, "open_entry_fees": sum(o["entry_fee"] for o in opens)}, opens)


def run(*, days: int = 7, everything: bool = False, now: datetime | None = None, environ=None, history=fetch_history,
        pnl_fetch=None, report_dir: Path | None = None, trades_path: Path | None = None) -> int:
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
    trades_path = trades_path or TRADES_PATH
    report_dir = report_dir or REPORT_DIR
    since_fetch = (now - timedelta(days=WARMUP_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        rows = history(api_key, user_key, since_fetch)
        pnl = (pnl_fetch or (lambda: _get(PNL_URLS[env_name], api_key, user_key)))()
    except ReportError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    ledger, new = merge_ledger(rows, trades_path)
    try:
        instruments = (json.loads(INSTRUMENT_MAP_PATH.read_text("utf-8")) or {}).get("instruments") or {}
    except (OSError, ValueError):
        instruments = {}
    trades = build_trades(ledger, instruments=instruments, strategies=load_strategies(), fee_rate=crypto_fee_rate())
    since = min((t.closed for t in trades), default=now) if everything else now - timedelta(days=days)
    window = [t for t in trades if t.closed >= since]
    names = {int(k): v.get("symbol") for k, v in instruments.items() if str(k).isdigit()}
    try:
        classes = {int(k): v.get("asset_class") for k, v in instruments.items() if str(k).isdigit()}
        equity, opens = account_summary(pnl, names, classes, crypto_fee_rate())
    except Exception:  # Konto-Block ist optional, der Trade-Teil bleibt gültig
        equity, opens = None, []
    text = render(window, since=since, now=now, equity=equity, open_positions=opens, new_trades=new)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"paper_weekly_{now.strftime('%G-W%V')}.md").write_text(text, encoding="utf-8")
    (report_dir / "paper_weekly_latest.md").write_text(text, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wöchentlicher Paper-Bericht (nur lesend)")
    parser.add_argument("--days", type=int, default=7, help="Zeitfenster in Tagen (Standard 7)")
    parser.add_argument("--all", action="store_true", help="alles seit dem ersten gesammelten Trade auswerten")
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(str(PROJECT_ROOT / ".env"))
    return run(days=args.days, everything=args.all)


if __name__ == "__main__":
    sys.exit(main())
