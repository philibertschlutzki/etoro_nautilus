"""Trade-Ledger aus den eToro-Positionen selbst (nur lesend), unabhängig von Nautilus-Buchungen und Kontostand.

Für das Demo-Konto gibt es keine Trade-Historie über die API, und die Nautilus-Positionen sind bei Teil-Schließungen
unzuverlässig. ``TradeLedger.observe`` bekommt jeden Kontostand, den ``EToroAccountView`` ohnehin alle ~10 s von eToro holt:
verschwindet eine offene Position eines Bot-Instruments, wurde sie geschlossen, und es entsteht ein Eintrag
(``data/state/demo_trades.jsonl``, Schema der eToro-Historie, damit ``paper_weekly_report`` ihn direkt liest). Der PnL ist der
zuletzt gesehene unrealisierte PnL (höchstens ein Abrufintervall alt), die Krypto-Gebühr rechnet der Bericht nach.
Die offenen Positionen werden in ``data/state/demo_trades_open.json`` mitgeführt, damit auch Schließungen während eines
Neustarts erfasst werden. Zusätzlich schreibt ``TradeLedger`` je Trade die Backtest-Schätzung (``automation/trade_estimate.py``)
beim Öffnen und das Delta zum Netto-Ertrag beim Schliessen als Log-Zeilen ``[TRADE_START]``/``[TRADE_CLOSE]`` und als JSONL
(``data/state/trade_estimates.jsonl``, Schlüssel ``positionId``) für die systemd-Berichte. Fremde Positionen (Instrumente ausserhalb des Bots) und Copy-Trading werden ignoriert.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from automation.trade_estimate import estimate_usd, infer_exit_reason, net_result_usd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = PROJECT_ROOT / "data" / "state" / "demo_trades.jsonl"
OPEN_PATH = PROJECT_ROOT / "data" / "state" / "demo_trades_open.json"
EVENTS_PATH = PROJECT_ROOT / "data" / "state" / "trade_estimates.jsonl"    # je Trade ein ``open``- und ein ``close``-Eintrag


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class TradeLedger:
    def __init__(self, instrument_ids, *, ledger_path: Path | None = None, open_path: Path | None = None,
                 clock=None, log=None, meta: dict | None = None, commission_rt_bps_fn=None,
                 events_path: Path | None = None) -> None:
        self._ids = {int(i) for i in instrument_ids}
        self._ledger_path = Path(ledger_path or LEDGER_PATH)
        self._open_path = Path(open_path or OPEN_PATH)
        self._clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self._log = log
        # ``meta[instrument_id] = {symbol, strategy, estimate}``: Backtest-Schätzung je Bot-Instrument (frozen aus der Auswahl).
        self._meta = {str(k): v for k, v in (meta or {}).items()}
        self._commission_rt_bps = commission_rt_bps_fn or (lambda _iid: None)
        self._events_path = Path(events_path or EVENTS_PATH)
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

    def _append_events(self, events: list[dict]) -> None:
        if not events:
            return
        try:
            self._events_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._events_path, "a", encoding="utf-8") as fh:
                for ev in events:
                    fh.write(json.dumps(ev, sort_keys=True) + "\n")
        except OSError as exc:       # Schätz-Log darf den Ledger nie blockieren
            if self._log is not None:
                self._log.warning(f"[TRADE_ESTIMATE] JSONL nicht schreibbar: {exc}")
        if self._log is None:
            return
        for ev in events:
            self._log.info(_format_event(ev))

    def _open_event(self, pid: str, p, rec: dict, now: float) -> dict:
        meta = self._meta.get(str(p.instrument_id)) or {}
        est = meta.get("estimate")
        rec["symbol"], rec["strategy"], rec["estimate"] = meta.get("symbol"), meta.get("strategy"), est
        rec["estimateUsd"] = estimate_usd(est, p.notional)
        return {"event": "open", "positionId": pid, "ts": _iso(now), "instrumentId": p.instrument_id,
                "symbol": meta.get("symbol"), "strategy": meta.get("strategy"), "isBuy": p.is_buy,
                "investment": p.amount, "notional": round(p.notional, 4), "openRate": p.open_rate,
                "stopLossRate": p.stop_loss_rate, "estimate": est, "estimateUsd": rec["estimateUsd"],
                "estimateNote": None if est else "keine Backtest-Schätzung hinterlegt (Paar vor Einführung gestartet)"}

    def _close_event(self, pid: str, rec: dict, row: dict, now: float) -> dict:
        lev = float(rec.get("leverage") or 1.0)
        res = net_result_usd(float(rec.get("lastPnl", 0.0)), float(rec["investment"]), lev,
                             float(rec.get("entryFee") or 0.0), self._commission_rt_bps(rec["instrumentId"]))
        est_usd = rec.get("estimateUsd") or {}
        expected = est_usd.get("expected_usd")
        low, high = est_usd.get("ci95_low_usd"), est_usd.get("ci95_high_usd")
        delta = None if expected is None else round(res["net_usd"] - expected, 4)
        inside = None if None in (low, high) else bool(low <= res["net_usd"] <= high)
        ev = {"event": "close", "positionId": pid, "ts": _iso(now), "instrumentId": rec["instrumentId"],
              "symbol": rec.get("symbol"), "strategy": rec.get("strategy"), "isBuy": rec["isBuy"],
              "openTimestamp": rec["openTimestamp"], "estimate": rec.get("estimate"), "estimateUsd": est_usd or None,
              "result": res, "deltaUsd": delta, "insideCi95": inside, "stopLossRate": rec.get("stopLossRate"),
              "exitReason": infer_exit_reason(rec["isBuy"], rec.get("openRate"), rec.get("stopLossRate"),
                                              float(rec["investment"]), lev, float(rec.get("lastPnl", 0.0))),
              "exitReasonSource": "abgeleitet", "pnlAgeS": row["pnlAgeS"]}
        row.update({"netAfterFees": res["net_usd"], "exitReason": ev["exitReason"], "stopLossRate": rec.get("stopLossRate"),
                    "estimatedUsd": expected, "deltaUsd": delta})
        return ev

    def observe(self, snapshot) -> list[dict]:
        """Vergleicht den Kontostand mit den zuletzt gesehenen offenen Positionen; gibt neu geschlossene Trades zurück."""
        now = self._clock()
        with self._lock:
            current = {p.position_id: p for p in snapshot.positions if p.instrument_id in self._ids}
            closed_rows = []
            close_events: list[dict] = []
            open_events: list[dict] = []
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
                close_events.append(self._close_event(pid, rec, row, now))
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
                           "entryFee": p.external_fees, "leverage": p.leverage}
                    open_events.append(self._open_event(pid, p, rec, now))
                rec["lastPnl"], rec["lastSeen"] = p.pnl, now
                rec["stopLossRate"] = p.stop_loss_rate
                self._open[pid] = rec
            self._save()
            self._append_events(open_events + close_events)
        if closed_rows and self._log is not None:
            for row in closed_rows:
                self._log.info(f"[TRADE_LEDGER] geschlossen: Position {row['positionId']} Instrument {row['instrumentId']} "
                               f"PnL {row['netProfit']:+.2f} USD (Stand {row['pnlAgeS']} s alt)")
        return closed_rows


def _fmt(value, spec="+.2f", none="n/a"):
    return none if value is None else format(value, spec)


def _format_event(ev: dict) -> str:
    est = ev.get("estimate") or {}
    if ev["event"] == "open":
        usd = ev.get("estimateUsd") or {}
        if not est:
            return (f"[TRADE_START] Position {ev['positionId']} {ev.get('symbol') or ev['instrumentId']} "
                    f"{'long' if ev['isBuy'] else 'short'} {ev['investment']:.0f} USD: {ev['estimateNote']}")
        return (f"[TRADE_START] Position {ev['positionId']} {ev.get('symbol') or ev['instrumentId']} "
                f"{'long' if ev['isBuy'] else 'short'} {ev['investment']:.0f} USD ({ev.get('strategy')}): Backtest-Erwartung "
                f"{_fmt(est.get('mean_bps'))} bps = {_fmt(usd.get('expected_usd'))} USD, 95%-Band "
                f"{_fmt(est.get('ci95_low_bps'))}..{_fmt(est.get('ci95_high_bps'))} bps, P(Erwartung>0) "
                f"{_fmt(est.get('p_positive'), '.2f')}, Trefferquote {_fmt(est.get('win_rate'), '.2f')}, "
                f"PF {_fmt(est.get('profit_factor'), '.2f')}, n={est.get('n_trades')}, Stop {ev.get('stopLossRate')}")
    res = ev["result"]
    return (f"[TRADE_CLOSE] Position {ev['positionId']} {ev.get('symbol') or ev['instrumentId']}: Netto {res['net_usd']:+.2f} USD "
            f"(brutto {res['gross_usd']:+.2f}, Gebühren {res['open_fee_usd'] + res['close_fee_usd']:.2f}, {res['fee_source']}), "
            f"Schätzung {_fmt((ev.get('estimateUsd') or {}).get('expected_usd'))} USD, Delta {_fmt(ev['deltaUsd'])} USD, "
            f"im 95%-Band: {ev['insideCi95'] if ev['insideCi95'] is not None else 'n/a'}, Austritt {ev['exitReason']}")
