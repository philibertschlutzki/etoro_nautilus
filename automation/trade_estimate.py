"""Backtest-Ertragsschätzung je Trade samt Konfidenz und Abgleich mit dem effektiven Netto-Ertrag.

Die Schätzung ist keine neue Rechnung, sondern die bereits im Auswahlpfad vorhandene Holdout-/OOS-Round-Trip-Statistik
des Gewinners (``trade_return_bps_mean/std/n``, optional Trefferquote und Profit-Faktor). Die Konfidenz ist die
Kalibrierung aus dem Backtest: Unsicherheitsband der Erwartung (Normalnäherung, ``mean ± 1.96·std/√n``) und
``p_positiv = Φ(mean / (std/√n))``, also die Wahrscheinlichkeit, dass die wahre Erwartung je Trade über 0 liegt. Kein Modell-Score.

``build_backtest_estimate`` wird bei der Auswahl gerechnet und im Inkubations-Record eingefroren; ``TradeLedger`` schreibt
beim Öffnen die Schätzung in USD (``mean_bps`` × Notional) und beim Schliessen das Delta zum Netto-Ertrag.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

Z95 = 1.959964


def _num(value: Any) -> float | None:
    try:
        out = None if value is None else float(value)
    except (TypeError, ValueError):
        return None
    return out if out is not None and math.isfinite(out) else None


def _first(winner: Mapping[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        node: Any = winner
        for key in path:
            node = node.get(key) if isinstance(node, Mapping) else None
        if node is not None:
            return node
    return None


def build_backtest_estimate(winner: Mapping[str, Any] | None) -> dict | None:
    """Schätzung je Trade in bps (netto der Backtest-Kosten) aus einem Turnier-/Whitelist-Gewinner; ``None``, wenn
    die Round-Trip-Statistik fehlt. Felder fehlender Grössen bleiben ``None`` (nichts wird erfunden)."""
    if not winner:
        return None
    mean = _num(_first(winner, ("holdout_trade_return_bps_mean",), ("oos_trade_return_bps_mean",),
                       ("oos_metrics", "trade_return_bps_mean")))
    std = _num(_first(winner, ("holdout_trade_return_bps_std",), ("oos_trade_return_bps_std",),
                      ("oos_metrics", "trade_return_bps_std")))
    n = _num(_first(winner, ("holdout_trade_return_bps_n",), ("oos_trade_return_bps_n",),
                    ("oos_metrics", "trade_return_bps_n")))
    if mean is None:
        return None
    n_int = int(n) if n else 0
    est = {
        "mean_bps": round(mean, 3), "std_bps": None if std is None else round(std, 3), "n_trades": n_int,
        "win_rate": _num(_first(winner, ("holdout_win_rate",), ("oos_win_rate",), ("oos_metrics", "win_rate"))),
        "profit_factor": _num(_first(winner, ("holdout_profit_factor",), ("oos_profit_factor",),
                                     ("oos_metrics", "profit_factor"))),
        "ci95_low_bps": None, "ci95_high_bps": None, "p_positive": None,
        "strategy": winner.get("strategy"),
    }
    if std is not None and std > 0 and n_int >= 2:
        se = std / math.sqrt(n_int)
        est["ci95_low_bps"] = round(mean - Z95 * se, 3)
        est["ci95_high_bps"] = round(mean + Z95 * se, 3)
        est["p_positive"] = round(0.5 * (1.0 + math.erf(mean / se / math.sqrt(2.0))), 4)
    return est


def estimate_usd(estimate: Mapping[str, Any] | None, notional: float) -> dict | None:
    """Schätzung in USD für ein konkretes Notional (Erwartung und 95-%-Band)."""
    if not estimate:
        return None
    def usd(bps):
        return None if bps is None else round(float(bps) / 1e4 * notional, 4)
    return {"expected_usd": usd(estimate.get("mean_bps")), "ci95_low_usd": usd(estimate.get("ci95_low_bps")),
            "ci95_high_usd": usd(estimate.get("ci95_high_bps"))}


def net_result_usd(gross_pnl: float, investment: float, leverage: float, entry_fee: float,
                   commission_rt_bps: float | None) -> dict:
    """Netto-Ertrag: letzter PnL minus Eröffnungs- und Schlussgebühr. Die Eröffnungsgebühr ist die von eToro gemeldete
    (``totalExternalFees``), sonst die halbe Round-Trip-Kommission des Kostenmodells; die Schlussgebühr ist immer
    die halbe Kommission auf den Wert beim Schliessen (Modell, eToro meldet sie erst im Kontostand danach)."""
    notional = investment * max(leverage, 1.0)
    half = (commission_rt_bps or 0.0) / 2.0 / 1e4
    open_fee = entry_fee if entry_fee and entry_fee > 0 else half * notional
    close_fee = half * max(notional + gross_pnl, 0.0)
    return {"gross_usd": round(gross_pnl, 4), "open_fee_usd": round(open_fee, 4), "close_fee_usd": round(close_fee, 4),
            "net_usd": round(gross_pnl - open_fee - close_fee, 4),
            "fee_source": "etoro+modell" if entry_fee and entry_fee > 0 else "modell"}


def infer_exit_reason(is_buy: bool, open_rate: float | None, stop_rate: float | None, investment: float,
                      leverage: float, last_pnl: float, tolerance: float = 0.25) -> str:
    """Austrittsgrund aus dem letzten gesehenen Stand (nur abgeleitet, daher ``*_vermutet``): lag der aus dem PnL
    zurückgerechnete Kurs im untersten ``tolerance``-Anteil der Distanz Einstieg→Stop (oder dahinter; auch ein nachgezogener Stop im Gewinn), war es der Broker-Stop;
    sonst ein Ausstieg des Bots (Signal, Trailing-TP, Zeit, Risikostopp) bzw. manuell."""
    notional = investment * max(leverage, 1.0)
    if not (open_rate and stop_rate and notional > 0):
        return "unbekannt"
    sign = 1.0 if is_buy else -1.0
    last_rate = open_rate * (1.0 + sign * last_pnl / notional)
    remaining = sign * (last_rate - stop_rate)       # Abstand des letzten Kurses zum Stop (<= 0: Stop erreicht)
    reach = tolerance * abs(open_rate - stop_rate)
    return "broker_stop_vermutet" if remaining <= reach else "bot_ausstieg_vermutet"


def calibration_lines(events: list[Mapping[str, Any]], min_trades: int = 10) -> list[str]:
    """Markdown-Zeilen für die systemd-Berichte: Schätzung gegen Ergebnis über alle geschlossenen Trades mit Schätzung
    (Anzahl, Ø Schätzung, Ø Netto, Ø Delta, Anteil im 95-%-Band, Aufschlüsselung nach Austrittsgrund und Strategie)."""
    closed = [e for e in events if e.get("event") == "close" and e.get("deltaUsd") is not None]
    if not closed:
        return ["- noch keine geschlossenen Trades mit Backtest-Schätzung"]
    def avg(rows, f):
        vals = [f(r) for r in rows]
        return sum(vals) / len(vals)
    inside = [e["insideCi95"] for e in closed if e.get("insideCi95") is not None]
    out = [f"- {len(closed)} Trades: Ø Schätzung {avg(closed, lambda e: e['estimateUsd']['expected_usd']):+.2f} USD, "
           f"Ø Netto {avg(closed, lambda e: e['result']['net_usd']):+.2f} USD, Ø Delta {avg(closed, lambda e: e['deltaUsd']):+.2f} USD"
           + (f", {sum(inside) / len(inside):.0%} im 95%-Band (Soll ca. 95 %)" if inside else "")]
    if len(closed) < min_trades:
        out.append(f"- unter {min_trades} Trades: noch keine Aussage zur Kalibrierung")
    for key, label in (("exitReason", "Austritt"), ("strategy", "Strategie")):
        groups: dict[str, list] = {}
        for e in closed:
            groups.setdefault(str(e.get(key)), []).append(e)
        for name, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            out.append(f"  - {label} {name}: {len(rows)} Trades, Ø Delta {avg(rows, lambda e: e['deltaUsd']):+.2f} USD")
    return out


# ─── Kalibrierung: Backtest-Erwartung gegen realisierten Netto-Ertrag ──────────────────────────────

MIN_TRADES_CLASS = 30       # darunter gilt die Gesamt-Kalibrierung, darunter wieder keine (λ = 1)
MIN_TRADES_GLOBAL = 30


def realized_bps(event: Mapping[str, Any]) -> float | None:
    """Netto-Ertrag eines ``close``-Events in bps auf das Notional (gleiche Basis wie ``trade_return_bps`` im Backtest)."""
    inv = _num(event.get("investment"))
    if not inv or inv <= 0:
        return None
    notional = inv * max(_num(event.get("leverage")) or 1.0, 1.0)
    return float(event["result"]["net_usd"]) / notional * 1e4


def _lambda(pairs: list[tuple[float, float]]) -> dict:
    """λ = Regression durch den Ursprung realisiert ~ λ·geschätzt, begrenzt auf [0, 1]: die Schätzung wird nur
    verkleinert, nie vergrössert (ein Backtest-Gewinner ist nach der Auswahl eher zu optimistisch als zu pessimistisch)."""
    sxx = sum(e * e for e, _ in pairs)
    raw = sum(e * r for e, r in pairs) / sxx if sxx > 0 else 1.0
    mean_e = sum(e for e, _ in pairs) / len(pairs)
    mean_r = sum(r for _, r in pairs) / len(pairs)
    return {"n": len(pairs), "lambda": round(min(max(raw, 0.0), 1.0), 4), "lambda_raw": round(raw, 4),
            "mean_estimate_bps": round(mean_e, 2), "mean_realized_bps": round(mean_r, 2),
            "delta_bps": round(mean_r - mean_e, 2)}


def calibrate(events: list[Mapping[str, Any]], *, min_class: int = MIN_TRADES_CLASS, min_global: int = MIN_TRADES_GLOBAL) -> dict:
    """Kalibrierung aus ``close``-Events mit Schätzung: je Anlageklasse und gesamt. Eine Klasse unter ``min_class`` Trades erbt
    die Gesamt-Kalibrierung; unter ``min_global`` Trades bleibt alles bei λ = 1 (unkalibriert), damit wenige Trades
    keine Schätzung verbiegen."""
    pairs: dict[str, list[tuple[float, float]]] = {}
    for e in events:
        if e.get("event") != "close":
            continue
        r, est = realized_bps(e), (e.get("estimate") or {}).get("mean_bps")
        if r is None or est is None:
            continue
        pairs.setdefault(str(e.get("assetClass") or "DEFAULT"), []).append((float(est), r))
    allp = [p for v in pairs.values() for p in v]
    out: dict[str, Any] = {"global": None, "by_class": {}}
    if len(allp) >= min_global:
        out["global"] = _lambda(allp)
    for cls, v in pairs.items():
        if len(v) >= min_class:
            out["by_class"][cls] = {**_lambda(v), "basis": "klasse"}
        elif out["global"]:
            out["by_class"][cls] = {**out["global"], "n_class": len(v), "basis": "gesamt"}
    out["n_trades"] = len(allp)
    out["note"] = "λ=1 ohne Eintrag (zu wenige Trades)" if not out["global"] else "λ aus Regression durch den Ursprung, [0,1]"
    return out


def calibrated_fields(estimate: Mapping[str, Any], calibration_path, asset_class: str | None) -> dict:
    """``calibrated_mean_bps`` = λ · ``mean_bps`` aus der Kalibrierungsdatei; ohne Datei/Eintrag λ = 1."""
    import json
    lam, basis = 1.0, "unkalibriert"
    try:
        with open(calibration_path, "r", encoding="utf-8") as fh:
            cal = json.load(fh) or {}
        entry = (cal.get("by_class") or {}).get(str(asset_class or "DEFAULT")) or cal.get("global")
        if entry and entry.get("lambda") is not None:
            lam, basis = float(entry["lambda"]), f"{entry.get('basis', 'gesamt')}, n={entry.get('n')}"
    except (OSError, ValueError):
        pass
    mean = _num(estimate.get("mean_bps"))
    return {"calibration_lambda": lam, "calibration_basis": basis,
            "calibrated_mean_bps": None if mean is None else round(lam * mean, 3)}
