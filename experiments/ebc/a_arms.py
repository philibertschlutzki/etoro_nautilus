"""Arm A0/A1 (Status quo): Emulation der Paper-Auswahl auf den Trade-Listen der 8 festen Tagesachsen-Strategien.
Vereinfachungen (siehe protocol_deviations): feste Default-Parameter => eine durchgehende Route-R-Laufzeit je (Instrument, Strategie)
ersetzt die 5-Tage-Neuauswahl-Laeufe; IS-Score = t-Statistik der Netto-Trade-Rendite im IS-Fenster, Gates im OOS-Fenster."""
import math
import numpy as np
import portfolio as pf

IS_DAYS, EMB_DAYS, OOS_DAYS = 473, 10, 3 * 128      # derive_walk_forward_daily bei 1456 Tagen Tiefe
HIST_NEED = IS_DAYS + EMB_DAYS + OOS_DAYS            # 867
REBAL = 5
SLOTS_EQ, SLOTS_RTC = 40, 40
RTC_CLASSES = {"K2", "K3", "K5"}
MIN_TRADES, MIN_PF, MIN_EXP, MIN_T, MIN_PSR = 8, 1.0, 0.0, 0.5, 0.5


def convert_r(S, trades):
    out = []
    day = pf.DAY_NS
    for t in trades:
        e = int(np.searchsorted(S.ts, (t["entry_ts"] // day) * day))
        if e >= S.n:
            continue
        x = int(np.searchsorted(S.ts, (t["exit_ts"] // day) * day))
        x = min(x, S.n - 1)
        side = 1 if t["side"] == "BUY" else -1
        out.append(dict(entry_i=e, exit_i=x, side=side, entry_px=t["px_open"], exit_px=t["px_close"], reason=t["exit_reason"],
                        gross_bps=side * (t["px_close"] / t["px_open"] - 1) * 1e4,
                        hold_days=int((S.ts[x] - S.ts[e]) // day), signal_i=e - 1))
    return out


def tstat(x):
    x = np.asarray(x, float)
    if len(x) < 2 or x.std(ddof=1) == 0: return -9.0
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def profit_factor(x):
    x = np.asarray(x, float)
    gp, gl = x[x > 0].sum(), -x[x < 0].sum()
    return float("inf") if gl == 0 and gp > 0 else (gp / gl if gl > 0 else 0.0)


class Pair:
    """Trades + kumulierte Statistiken eines (Instrument, Strategie)-Paares fuer O(log n)-Fensterabfragen."""

    def __init__(self, inst, strat, trades, S, cal, cost, i):
        self.inst, self.strat, self.id = inst, strat, i
        self.S = S
        trades = sorted(trades, key=lambda t: t["exit_i"])
        self.trades = trades
        n = len(trades)
        self.exit_day = np.array([int(S.ts[t["exit_i"]] // pf.DAY_NS) for t in trades], dtype=np.int64)
        self.entry_g = np.array([cal.g(S.ts[t["entry_i"]]) for t in trades], dtype=np.int64)
        self.exit_g = np.array([cal.g(S.ts[t["exit_i"]]) for t in trades], dtype=np.int64)
        net = np.array([cost.net_bps(i, t["gross_bps"], t["side"], t["hold_days"]) for t in trades])
        self.net = net
        for t, v in zip(trades, net):
            t["net_bps"] = float(v)
        self.c1 = np.concatenate([[0.0], np.cumsum(net)])
        self.c2 = np.concatenate([[0.0], np.cumsum(net ** 2)])
        self.cpos = np.concatenate([[0.0], np.cumsum(np.where(net > 0, net, 0.0))])
        self.cneg = np.concatenate([[0.0], np.cumsum(np.where(net < 0, -net, 0.0))])
        # tagesreihe
        self.daily = np.zeros(cal.n)
        self.vecs = []
        for t in trades:
            g0, v = pf.trade_vector(S, t, cal, cost, i)
            t["g0"], t["vec"] = g0, v
            self.daily[g0:g0 + len(v)] += v
        self.d1 = np.concatenate([[0.0], np.cumsum(self.daily)])
        self.d2 = np.concatenate([[0.0], np.cumsum(self.daily ** 2)])
        self.d3 = np.concatenate([[0.0], np.cumsum(self.daily ** 3)])
        self.d4 = np.concatenate([[0.0], np.cumsum(self.daily ** 4)])

    def window(self, lo_day, hi_day):
        """Trades mit Exit-Tag in [lo_day, hi_day): Indexbereich."""
        a = int(np.searchsorted(self.exit_day, lo_day, "left")); b = int(np.searchsorted(self.exit_day, hi_day, "left"))
        return a, b

    def stats(self, lo_day, hi_day):
        a, b = self.window(lo_day, hi_day)
        n = b - a
        if n == 0:
            return dict(n=0)
        m = (self.c1[b] - self.c1[a]) / n
        var = max((self.c2[b] - self.c2[a]) / n - m * m, 0.0) * n / max(n - 1, 1)
        sd = math.sqrt(var)
        gl = self.cneg[b] - self.cneg[a]; gp = self.cpos[b] - self.cpos[a]
        return dict(n=n, mean=m, t=(m / (sd / math.sqrt(n)) if sd > 0 and n > 1 else -9.0),
                    pf=(float("inf") if gl == 0 and gp > 0 else (gp / gl if gl > 0 else 0.0)))

    def psr_daily(self, g_lo, g_hi):
        """PSR (SR*=0) der Tagesreihe auf Kalender-Index [g_lo, g_hi)."""
        T = g_hi - g_lo
        if T < 5: return float("nan")
        s1 = self.d1[g_hi] - self.d1[g_lo]; s2 = self.d2[g_hi] - self.d2[g_lo]
        s3 = self.d3[g_hi] - self.d3[g_lo]; s4 = self.d4[g_hi] - self.d4[g_lo]
        m = s1 / T; var = s2 / T - m * m
        if var <= 1e-18: return float("nan")
        sd = math.sqrt(var)
        m3 = s3 / T - 3 * m * s2 / T + 2 * m ** 3
        m4 = s4 / T - 4 * m * s3 / T + 6 * m * m * s2 / T - 3 * m ** 4
        sk = m3 / sd ** 3; ku = m4 / var ** 2
        from automation.optimizer.deflation import probabilistic_sharpe_ratio
        v = probabilistic_sharpe_ratio(m / sd, T, skew=sk, kurtosis=ku)
        return float("nan") if v is None else float(v)


def run_selection(pairs_by_inst, insts, cal, first_day_of, g_start, strategies):
    """Rueckgabe: selection_log (Liste), active_trades (Liste dict mit rank), pair_scores (fuer Rangpersistenz)."""
    active = {}           # (id, strat) -> dict(score, group)
    retired = set()
    log = []; taken = []; persistence = []
    sel_days = list(range(g_start, cal.n, REBAL))
    for si, d in enumerate(sel_days):
        d_day = cal.days[d]
        d_next = sel_days[si + 1] if si + 1 < len(sel_days) else cal.n - 1
        # --- Auswahl je Instrument
        cands = {}
        scores_now = {}
        for r in insts:
            i = r["id"]
            if d_day - first_day_of[i] < HIST_NEED: continue
            is_lo, is_hi = d_day - HIST_NEED, d_day - OOS_DAYS - EMB_DAYS
            oo_lo = d_day - OOS_DAYS
            best = None
            for s in strategies:
                P = pairs_by_inst[i].get(s)
                if P is None: continue
                st = P.stats(is_lo, is_hi)
                if st["n"] < 3: continue
                if best is None or st["t"] > best[0]:
                    best = (st["t"], s)
            if best is None: continue
            P = pairs_by_inst[i][best[1]]
            oo = P.stats(oo_lo, d_day + 1)
            if oo["n"] < MIN_TRADES or oo["pf"] < MIN_PF or oo["mean"] < MIN_EXP or oo["t"] < MIN_T: continue
            g_lo = max(0, d - int(OOS_DAYS * 5 / 7))
            p = P.psr_daily(g_lo, d + 1)
            if not (p == p) or p < MIN_PSR: continue
            cands[(i, best[1])] = dict(psr=p, group="rtc" if r["klasse"] in RTC_CLASSES else "eq", inst=r)
        # --- Retire: ungueltig gewordene Paare ohne offene Position
        for key in list(active):
            if key in cands: 
                active[key]["psr"] = cands[key]["psr"]
                continue
            P = pairs_by_inst[key[0]][key[1]]
            k = int(np.searchsorted(P.entry_g, d, "right")) - 1
            # offene Position: ein Trade mit entry_g <= d < exit_g (nach Entry sortiert nicht garantiert -> Scan)
            open_pos = bool(((P.entry_g <= d) & (P.exit_g > d)).any())
            if not open_pos:
                retired.add(key); del active[key]
        # --- Auffuellen nach OOS-PSR
        used = {"eq": sum(1 for v in active.values() if v["group"] == "eq"),
                "rtc": sum(1 for v in active.values() if v["group"] == "rtc")}
        cap = {"eq": SLOTS_EQ, "rtc": SLOTS_RTC}
        order = sorted(((-v["psr"], k[1], k[0], k) for k, v in cands.items() if k not in active and k not in retired))
        for _, _, _, k in order:
            g = cands[k]["group"]
            if used[g] < cap[g]:
                used[g] += 1
                active[k] = dict(psr=cands[k]["psr"], group=g)
        log.append(dict(g=d, date=int(d_day), n_cand=len(cands), n_active=len(active), n_retired=len(retired)))
        # --- Handel bis zur naechsten Auswahl: Trades mit Entry in [d+1, d_next]; Rangpersistenz
        for key, v in active.items():
            P = pairs_by_inst[key[0]][key[1]]
            mask = (P.entry_g >= d + 1) & (P.entry_g <= d_next)
            for j in np.nonzero(mask)[0]:
                t = P.trades[j]
                taken.append(dict(t, key=key, rank=-v["psr"], tie=int(key[0]), strat=key[1]))
            persistence.append((key, v["psr"], float(P.daily[d + 1:d_next + 1].sum())))
    return log, taken, persistence, active
