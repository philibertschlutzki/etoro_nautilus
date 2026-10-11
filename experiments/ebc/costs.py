"""Kostenmodell (EBC-H1 §5). c_RT = s_P50 + c_k + 2*slip (+ Finanzierung je Haltetag).
Regel (vorregistriert): s = max(Klassen-Rueckfallwert aus backtest.json, gemessener Median); Slippage/Kommission/Finanzierung
bis Stufe C aus Rueckfallwerten."""
import json, os
import numpy as np

FALLBACK_SPREAD = {"EQUITY": 3.0, "FOREX": 1.5, "COMMODITY": 5.0, "INDEX": 4.0, "CRYPTO": 15.0}
FALLBACK_SLIP = {"EQUITY": 1.5, "FOREX": 0.75, "COMMODITY": 2.5, "INDEX": 2.0, "CRYPTO": 7.5}   # bps je Seite
COMMISSION_RT = {"EQUITY": 0.0, "FOREX": 0.0, "COMMODITY": 0.0, "INDEX": 0.0, "CRYPTO": 200.0}
FIN_SHORT = 0.79
FIN_LONG = {"EQUITY": 0.0, "FOREX": 0.79, "COMMODITY": 0.79, "INDEX": 0.79, "CRYPTO": 0.0}   # §5: Long-CFD konservativ 0,79


def measured_spreads(path="data/ebc/spreads.jsonl"):
    """id -> (p50, p75, n) in bps aus den Rates-Snapshots."""
    d = {}
    if not os.path.exists(path):
        return {}
    for line in open(path):
        x = json.loads(line)
        mid = 0.5 * (x["bid"] + x["ask"])
        d.setdefault(x["id"], []).append((x["ask"] - x["bid"]) / mid * 1e4)
    return {k: (float(np.percentile(v, 50)), float(np.percentile(v, 75)), len(v)) for k, v in d.items()}


class CostTable:
    def __init__(self, universe_rows, spreads=None, overrides=None, use_measured=False):
        # Stufe A (Wochenend-Snapshot, Maerkte ausser Krypto geschlossen): Rueckfallwerte; Messung nur mit use_measured=True
        self.spreads = (spreads if spreads is not None else measured_spreads()) if use_measured else {}
        self.all_measured = measured_spreads()
        self.ov = overrides or {}
        self.p = {}
        for r in universe_rows:
            c = r["cost_class"]; i = r["id"]
            m = self.spreads.get(i)
            s = max(FALLBACK_SPREAD[c], m[0]) if m else FALLBACK_SPREAD[c]
            slip = FALLBACK_SLIP[c]
            self.p[i] = dict(cls=c, spread=s, spread_measured=(m[0] if m else None), n_samples=(m[2] if m else 0),
                             slip=slip, comm=COMMISSION_RT[c], fin_long=FIN_LONG[c], fin_short=FIN_SHORT)
            self.p[i].update(self.ov.get(c, {}))

    def c_rt(self, i):
        p = self.p[i]
        return p["spread"] + p["comm"] + 2 * p["slip"]

    def fin_bps(self, i, side, cal_days):
        p = self.p[i]
        return cal_days * (p["fin_long"] if side > 0 else p["fin_short"])

    def net_bps(self, i, gross_bps, side, cal_days):
        return gross_bps - self.c_rt(i) - self.fin_bps(i, side, cal_days)
