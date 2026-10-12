"""Trade-Ledger aus den eToro-Positionen selbst (nur lesend), unabhängig von Nautilus-Buchungen und Kontostand.

Für das Demo-Konto gibt es keine Trade-Historie über die API, und die Nautilus-Positionen sind bei Teil-Schließungen
unzuverlässig. ``TradeLedger.observe`` bekommt jeden Kontostand, den ``EToroAccountView`` ohnehin alle ~10 s von eToro holt:
verschwindet eine offene Position eines Bot-Instruments, wurde sie geschlossen, und es entsteht ein Eintrag
(``data/state/demo_trades.jsonl``, Schema der eToro-Historie, damit ``paper_weekly_report`` ihn direkt liest). Der PnL ist der
zuletzt gesehene unrealisierte PnL (höchstens ein Abrufintervall alt), die Krypto-Gebühr rechnet der Bericht nach.
Die offenen Positionen werden in ``data/state/demo_trades_open.json`` mitgeführt, damit auch Schließungen während eines
Neustarts erfasst werden. Fremde Positionen (Instrumente ausserhalb des Bots) und Copy-Trading werden ignoriert.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = PROJECT_ROOT / "data" / "state" / "demo_trades.jsonl"
OPEN_PATH = PROJECT_ROOT / "data" / "state" / "demo_trades_open.json"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class TradeLedger:
    def __init__(self, instrument_ids, *, ledger_path: Path | None = None, open_path: Path | None = None,
                 clock=None, log=None) -> None:
        self._ids = {int(i) for i in instrument_ids}
        self._ledger_path = Path(ledger_path or LEDGER_PATH)
        self._open_path = Path(open_path or OPEN_PATH)
        self._clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self._log = log
        self._lock = threading.Lock()
        self._open: dict[str, dict] = self._load()
        self._known_ledger: set[str] | None = None

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self._open_path.read_text("utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self._open_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._open_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._open, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._open_path)

    def _ledger_ids(self) -> set[str]:
        if self._known_ledger is None:
            self._known_ledger = set()
            try:
                for line in self._ledger_path.read_text("utf-8").splitlines():
                    if line.strip():
                        self._known_ledger.add(str(json.loads(line)["positionId"]))
            except (OSError, ValueError, KeyError):
                pass
        return self._known_ledger

    def observe(self, snapshot) -> list[dict]:
        """Vergleicht den Kontostand mit den zuletzt gesehenen offenen Positionen; gibt neu geschlossene Trades zurück."""
        now = self._clock()
        with self._lock:
            current = {p.position_id: p for p in snapshot.positions if p.instrument_id in self._ids}
            closed_rows = []
            for pid, rec in list(self._open.items()):
                if pid in current:
                    continue
                row = {
                    "positionId": pid, "instrumentId": rec["instrumentId"], "isBuy": rec["isBuy"],
                    "investment": rec["investment"], "initialInvestment": rec["investment"],
                    "openRate": rec.get("openRate"), "openTimestamp": rec["openTimestamp"],
                    "closeTimestamp": _iso(now), "netProfit": round(rec.get("lastPnl", 0.0), 4),
                    "fees": 0.0, "source": "snapshot", "pnlAgeS": round(now - rec.get("lastSeen", now), 1),
                    "entryFee": rec.get("entryFee", 0.0)}
                closed_rows.append(row)
                del self._open[pid]
            if closed_rows:
                known = self._ledger_ids()
                self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._ledger_path, "a", encoding="utf-8") as fh:
                    for row in closed_rows:
                        if row["positionId"] not in known:
                            fh.write(json.dumps(row, sort_keys=True) + "\n")
                            known.add(row["positionId"])
            for pid, p in current.items():
                rec = self._open.get(pid)
                if rec is None:
                    rec = {"instrumentId": p.instrument_id, "isBuy": p.is_buy, "investment": p.amount,
                           "openRate": p.open_rate, "openTimestamp": p.opened_iso or _iso(now),
                           "entryFee": p.external_fees}
                rec["lastPnl"], rec["lastSeen"] = p.pnl, now
                self._open[pid] = rec
            self._save()
        if closed_rows and self._log is not None:
            for row in closed_rows:
                self._log.info(f"[TRADE_LEDGER] geschlossen: Position {row['positionId']} Instrument {row['instrumentId']} "
                               f"PnL {row['netProfit']:+.2f} USD (Stand {row['pnlAgeS']} s alt)")
        return closed_rows
