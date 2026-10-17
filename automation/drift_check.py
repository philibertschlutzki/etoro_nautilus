"""Wöchentlicher Drift-Check der Backtests: Haben sich Paare (Instrument x Strategie) in den letzten Wochen anders verhalten als in der Historie?

Liest die Roh-Trades der Tages-Spur (``<ebc_exp>/data/ebc/a_trades/<etoro_id>.pkl``, geschrieben von ``experiments/ebc/gen_a.py``;
je Strategie eine Liste von Trades mit ``side, px_open, px_close, exit_ts``) und vergleicht je Paar die Brutto-Rendite pro Trade im
jungen Fenster (``--recent-days``, Standard 90) mit der Zeit davor (Welch-t). Es ändert weder Whitelist noch Bot, es schreibt nur
``results/drift/<Datum>.md`` und ``data/state/drift_flags.json`` (Pro Paar: Flag, t-Wert, Mittelwerte).

Ehrlichkeit gegen Mehrfachtests: bei ~1.300 Paaren sind bei reinem Zufall ~5 % geflaggt. Der Bericht nennt deshalb die erwartete Zufallszahl
neben der gefundenen, und je Anlageklasse den Anteil der Paare, deren junge Rendite unter der alten liegt (Regime statt Einzelpaar).

``--validate`` prüft, ob das Verfahren die Prognose überhaupt verbessert (Walk-forward, kein Blick in die Zukunft): an monatlichen
Stichtagen wird die Rendite der folgenden ``--horizon-days`` je Paar vorhergesagt mit (a) Mittel der ganzen Historie, (b) Mittel des jungen
Fensters, (c) Mischung beider (Schrumpfung, Gewicht n_jung/(n_jung+K)). Gemessen wird die Rang-Korrelation Vorhersage ~ Realisierung je
Stichtag (Mittel über Stichtage) und, ob geflaggte Paare danach schlechter laufen als nicht geflaggte. Nur was hier besser ist als (a),
gehört in die Auswahl.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRADES = Path.home() / "ebc_exp" / "data" / "ebc" / "a_trades"
REPORT_DIR = PROJECT_ROOT / "results" / "drift"
FLAGS_PATH = PROJECT_ROOT / "data" / "state" / "drift_flags.json"
INSTRUMENT_MAP = PROJECT_ROOT / "automation" / "config" / "instrument_map.json"
METADATA = Path.home() / "ebc_exp" / "data" / "ebc" / "metadata.json"
DAY_NS = 86_400 * 10**9
MIN_OLD, MIN_RECENT = 15, 5          # Mindestzahl Trades im alten bzw. jungen Fenster
T_FLAG = -1.65                       # einseitig 5 %
SHRINK_K = 10.0


def trade_returns(trades: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """(exit_ts_ns, Brutto-Rendite in bps) je Trade, nach Austritt sortiert. Unvollständige Trades fallen weg."""
    ts, r = [], []
    for t in trades:
        try:
            po, pc, te = float(t["px_open"]), float(t["px_close"]), int(t["exit_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if po <= 0 or pc <= 0:
            continue
        sign = 1.0 if "BUY" in str(t.get("side", "")).upper() else -1.0
        ts.append(te)
        r.append(sign * (pc / po - 1.0) * 1e4)
    order = np.argsort(ts)
    return np.asarray(ts, dtype=np.int64)[order], np.asarray(r, dtype=float)[order]


def load_pairs(trades_dir: Path) -> dict[tuple[str, str], tuple[np.ndarray, np.ndarray]]:
    pairs = {}
    for f in sorted(trades_dir.glob("*.pkl")):
        try:
            data = pickle.loads(f.read_bytes())
        except Exception:                                    # defekte Datei darf den Lauf nicht kippen
            continue
        for strat, trades in data.items():
            if isinstance(trades, list) and trades:
                ts, r = trade_returns(trades)
                if len(r):
                    pairs[(f.stem, strat)] = (ts, r)
    return pairs


def welch_t(old: np.ndarray, new: np.ndarray) -> float:
    """t-Wert (jung - alt); 0.0 wenn die Streuung null ist."""
    se = np.sqrt(old.var(ddof=1) / len(old) + new.var(ddof=1) / len(new))
    return float((new.mean() - old.mean()) / se) if se > 0 else 0.0


def split_at(ts: np.ndarray, r: np.ndarray, cut_ns: int, recent_days: float) -> tuple[np.ndarray, np.ndarray]:
    """Trades vor dem jungen Fenster [cut-recent, cut) und im jungen Fenster (nur Austritte bis cut)."""
    lo = cut_ns - int(recent_days * DAY_NS)
    return r[ts < lo], r[(ts >= lo) & (ts < cut_ns)]


def analyse(pairs: dict, now_ns: int, recent_days: float) -> list[dict]:
    rows = []
    for (iid, strat), (ts, r) in pairs.items():
        old, new = split_at(ts, r, now_ns, recent_days)
        if len(old) < MIN_OLD or len(new) < MIN_RECENT:
            continue
        t = welch_t(old, new)
        rows.append({"id": iid, "strategy": strat, "n_old": len(old), "n_recent": len(new), "mean_old_bps": float(old.mean()),
                     "mean_recent_bps": float(new.mean()), "t": t, "flag": bool(t <= T_FLAG and new.mean() < 0.0)})
    return rows


TYPE_CLASS = {1: "forex", 2: "commodity", 4: "index", 5: "equity", 6: "etf", 10: "crypto"}   # eToro InstrumentTypeID


def classes(metadata: Path = METADATA) -> dict[str, str]:
    """Anlageklasse je Instrument-ID: instrument_map.json zuerst, sonst eToro-Metadaten (InstrumentTypeID), damit keine Paare in '?' landen."""
    out: dict[str, str] = {}
    try:
        for d in json.loads(metadata.read_text("utf-8"))["InstrumentDisplayDatas"]:
            c = TYPE_CLASS.get(d.get("InstrumentTypeID"))
            if c:
                out[str(d["InstrumentID"])] = c
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        m = json.loads(INSTRUMENT_MAP.read_text("utf-8"))["instruments"]
    except (OSError, ValueError, KeyError):
        m = {}
    out.update({k: v["asset_class"] for k, v in m.items() if v.get("asset_class")})
    return out


def by_class(rows: list[dict], cls: dict[str, str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        c = out.setdefault(cls.get(r["id"], "?"), {"n": 0, "below": 0, "flagged": 0, "d_sum": 0.0})
        c["n"] += 1
        c["below"] += r["mean_recent_bps"] < r["mean_old_bps"]
        c["flagged"] += r["flag"]
        c["d_sum"] += r["mean_recent_bps"] - r["mean_old_bps"]
    return out


def ranks(x: np.ndarray) -> np.ndarray:
    order = x.argsort(kind="mergesort")
    rk = np.empty(len(x))
    rk[order] = np.arange(len(x))
    for v in np.unique(x):                                   # Bindungen mitteln
        m = x == v
        if m.sum() > 1:
            rk[m] = rk[m].mean()
    return rk


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 10 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    return float(np.corrcoef(ranks(a), ranks(b))[0, 1])


def validate(pairs: dict, last_ns: int, recent_days: float, horizon_days: float, n_cuts: int = 24) -> dict:
    """Walk-forward-Prüfung der Prognose; Stichtage monatlich rückwärts ab ``last_ns - horizon``."""
    res = {"full": [], "recent": [], "blend": [], "n_pairs": [], "flag_next": [], "noflag_next": []}
    for k in range(n_cuts):
        cut = last_ns - int((horizon_days + 30.0 * k) * DAY_NS)
        end = cut + int(horizon_days * DAY_NS)
        pf, pr, pb, real, flags = [], [], [], [], []
        for ts, r in pairs.values():
            old, new = split_at(ts, r, cut, recent_days)
            nxt = r[(ts >= cut) & (ts < end)]
            if len(old) < MIN_OLD or len(new) < MIN_RECENT or len(nxt) < 3:
                continue
            full = np.concatenate([old, new])
            w = len(new) / (len(new) + SHRINK_K)
            pf.append(full.mean())
            pr.append(new.mean())
            pb.append(w * new.mean() + (1 - w) * full.mean())
            real.append(nxt.mean())
            flags.append(welch_t(old, new) <= T_FLAG and new.mean() < 0.0)
        if len(real) < 30:
            continue
        real_a, fl = np.asarray(real), np.asarray(flags)
        for key, p in (("full", pf), ("recent", pr), ("blend", pb)):
            ic = spearman(np.asarray(p), real_a)
            if ic is not None:
                res[key].append(ic)
        res["n_pairs"].append(len(real))
        if fl.any() and (~fl).any():
            res["flag_next"].append(float(real_a[fl].mean()))
            res["noflag_next"].append(float(real_a[~fl].mean()))
    def agg(v: list[float]) -> dict:
        a = np.asarray(v)
        return {"mean": float(a.mean()), "se": float(a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else None, "n_cuts": len(a)} if len(a) else {}
    return {k: agg(res[k]) for k in ("full", "recent", "blend")} | {
        "flagged_next_bps": agg(res["flag_next"]), "unflagged_next_bps": agg(res["noflag_next"]),
        "avg_pairs": float(np.mean(res["n_pairs"])) if res["n_pairs"] else 0.0}


def verdict(v: dict) -> list[str]:
    """Klartext, was die Validierung hergibt; ohne Daten keine Aussage."""
    if not v.get("full"):
        return ["Validierung: zu wenig Daten (weniger als 30 Paare je Stichtag)."]
    out = []
    full, blend = v["full"], v.get("blend", {})
    if blend:
        gain = blend["mean"] - full["mean"]
        out.append(f"Mischung (Schrumpfung) gegen ganze Historie: Rang-IC {blend['mean']:+.3f} gegen {full['mean']:+.3f} (Differenz {gain:+.3f}); "
                   + ("Mischung hilft" if gain > 2 * float(np.hypot(blend.get("se") or 0.0, full.get("se") or 0.0)) else "kein belegter Vorteil, bei der ganzen Historie bleiben"))
    f, u = v.get("flagged_next_bps", {}), v.get("unflagged_next_bps", {})
    if f and u:
        d = f["mean"] - u["mean"]
        se = float(np.hypot(f.get("se") or 0.0, u.get("se") or 0.0))
        out.append(f"Geflaggte Paare danach {f['mean']:+.1f} bps, nicht geflaggte {u['mean']:+.1f} bps (Differenz {d:+.1f}); "
                   + ("Flag ist ein belegtes Warnsignal (mehr als 2 Standardfehler)" if d < -2 * se and se > 0
                      else "Flag sagt nichts Belegtes voraus (Differenz innerhalb von 2 Standardfehlern oder positiv)"))
    return out


def render(now: datetime, rows: list[dict], cls: dict[str, str], recent_days: float, val: dict | None) -> str:
    flagged = sorted((r for r in rows if r["flag"]), key=lambda r: r["t"])
    expected = 0.05 * len(rows)
    L = [f"# Drift-Check {now:%Y-%m-%d}", "",
         f"Stand {now:%Y-%m-%d %H:%M} UTC. Junges Fenster {recent_days:.0f} Tage gegen Historie davor; {len(rows)} Paare mit genug Trades "
         f"(mind. {MIN_OLD} alt, {MIN_RECENT} jung). Brutto-Rendite pro Trade, Tages-Spur.", ""]
    L += [f"Geflaggt (t <= {T_FLAG} und junges Mittel < 0): **{len(flagged)}**, bei reinem Zufall wären ~{expected:.0f} zu erwarten. "
          + ("Deutlich mehr als Zufall: echte Veränderung wahrscheinlich." if len(flagged) > 1.5 * expected + 3 else "Im Bereich des Zufalls: kein belegter Regimewechsel."), "",
          "## Anteil Paare mit jungem Mittel unter altem, je Anlageklasse", "", "| Klasse | Paare | darunter | Anteil | Ø Differenz bps | geflaggt |", "|---|---|---|---|---|---|"]
    for c, d in sorted(by_class(rows, cls).items()):
        L.append(f"| {c} | {d['n']} | {d['below']} | {d['below'] / d['n']:.0%} | {d['d_sum'] / d['n']:+.1f} | {d['flagged']} |")
    L += ["", "Bei Zufall liegen ~50 % darunter; deutlich mehr in einer Klasse spricht für einen Regimewechsel dort.", "", "## Auffälligste Paare", "",
          "| Instrument | Strategie | alt bps (n) | jung bps (n) | t |", "|---|---|---|---|---|"]
    for r in flagged[:25]:
        L.append(f"| {r['id']} | {r['strategy']} | {r['mean_old_bps']:+.1f} ({r['n_old']}) | {r['mean_recent_bps']:+.1f} ({r['n_recent']}) | {r['t']:.2f} |")
    if val is not None:
        L += ["", "## Validierung (Walk-forward)", ""] + [f"- {x}" for x in verdict(val)]
        L += ["", "```json", json.dumps(val, indent=1), "```"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trades-dir", type=Path, default=DEFAULT_TRADES)
    ap.add_argument("--recent-days", type=float, default=90.0)
    ap.add_argument("--horizon-days", type=float, default=60.0)
    ap.add_argument("--validate", action="store_true", help="zusätzlich Walk-forward-Validierung (rechnet länger)")
    ap.add_argument("--out", type=Path, default=REPORT_DIR)
    ap.add_argument("--flags", type=Path, default=FLAGS_PATH)
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        pairs = load_pairs(a.trades_dir)
        if not pairs:
            print(f"FEHLER: keine Trades in {a.trades_dir}", file=sys.stderr)
            return 2
        last_ns = max(int(ts[-1]) for ts, _ in pairs.values())
        rows = analyse(pairs, last_ns, a.recent_days)
        val = validate(pairs, last_ns, a.recent_days, a.horizon_days) if a.validate else None
        text = render(now, rows, classes(), a.recent_days, val)
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / f"{now:%Y-%m-%d}.md").write_text(text, "utf-8")
        a.flags.parent.mkdir(parents=True, exist_ok=True)
        tmp = a.flags.with_suffix(".tmp")
        tmp.write_text(json.dumps({"updated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "recent_days": a.recent_days, "pairs": rows}, indent=1), "utf-8")
        tmp.replace(a.flags)
    except Exception as exc:   # noqa: BLE001 - Timer-Lauf soll sauber mit Code 2 enden
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
