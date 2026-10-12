"""Route V: unabhaengiger, ereignisgetriebener Tagesachsen-Simulator der Archetypen T/M/V (EBC-H1 §6.1, §7.1).
Bildet die Exit-Semantik von HourlyStrategyBase nach (Stop auf Schlusskurs, Ausfuehrung zum naechsten Open, Katastrophen-Stop
intrabar, Zeitstopp ueber Bar-Zaehler) und kennt die Exit-Varianten aus EBC-H3 (S0/S1/S2, P0..P3) sowie Zeitbox H.
Renditen sind BRUTTO (Mid-Preise); Kosten kommen aus costs.py."""
import math
import numpy as np

ATR_N = 20
FLOOR_BPS = 2.0          # atr_floor_bps der Basisklasse
MIN_ATR_FRAC = 0.5       # trailing_min_atr_frac
EWMA_ALPHA = 2.0 / (5.0 * ATR_N + 1.0)
K_DISASTER, DS_MIN, DS_MAX = 3.0, 0.02, 0.15

ARCH = {  # Parameter nach EBC-H1 §6.1
    "T": dict(k=3.0, H=60, L=50, Lx=20),
    "M": dict(k=3.0, H=5, theta=10.0, n_exit=5),
    "V": dict(k=2.5, H=20, n_sq=5),
    "R": dict(k=3.0, H=3, theta=0.15),      # EBC-H7 §6.1: IBS-Ruecksetzer
    "W": dict(k=3.0, H=4),                  # EBC-H7 §6.1: Monatswechsel (Signale und Einzel-H in h7_arch.py)
    "X": dict(k=4.0, H=10**6),              # EBC-H7 §6.1: 12-1-Momentum (Signale in h7_arch.py)
}


def wilder_atr(h, l, c):
    n = len(c)
    atr = np.zeros(n); tr = np.zeros(n)
    for i in range(n):
        tr[i] = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        atr[i] = tr[i] if i == 0 else atr[i - 1] + (tr[i] - atr[i - 1]) / ATR_N
    return atr


def wilder_rsi(c, p=2):
    n = len(c); out = np.full(n, 50.0)
    g = []; ls = []; ag = al = 0.0; init = False
    for i in range(1, n):
        ch = c[i] - c[i - 1]; gi, li = max(ch, 0.0), max(-ch, 0.0)
        if not init:
            g.append(gi); ls.append(li)
            if len(g) == p:
                ag, al = sum(g) / p, sum(ls) / p; init = True
            else:
                continue
        else:
            ag = (ag * (p - 1) + gi) / p; al = (al * (p - 1) + li) / p
        out[i] = (100.0 if ag > 0 else 50.0) if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    rsi_ok = np.zeros(n, bool); rsi_ok[p + 1 - 1:] = True   # initialisiert nach p Differenzen (Index >= p)
    return out, rsi_ok


def roll_mean(x, n):
    out = np.full(len(x), np.nan)
    cs = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def roll_std(x, n):
    m = roll_mean(x, n); m2 = roll_mean(x * x, n)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def prior_max(x, n):   # max der n VORHERIGEN Werte (ohne aktuellen)
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        out[i] = x[i - n:i].max()
    return out


def prior_min(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        out[i] = x[i - n:i].min()
    return out


class Series:
    """Vorberechnete Indikatoren einer Instrument-Tagesreihe."""

    def __init__(self, ts, o, h, l, c):
        self.ts, self.o, self.h, self.l, self.c = ts, o, h, l, c
        n = self.n = len(c)
        self.atr = wilder_atr(h, l, c)
        self.atr_ok = np.arange(n) >= ATR_N - 1
        floor = FLOOR_BPS / 1e4 * c
        self.atr_eff = np.maximum(self.atr, floor)
        ew = np.full(n, np.nan); cur = None
        for i in range(n):
            if self.atr_ok[i]:
                cur = self.atr[i] if cur is None else cur + EWMA_ALPHA * (self.atr[i] - cur)
                ew[i] = cur
        self.atr_ratchet = np.where(np.isnan(ew), self.atr_eff, np.maximum(self.atr_eff, MIN_ATR_FRAC * np.nan_to_num(ew)))
        self.sma200 = roll_mean(c, 200)
        self.sma200_ago = np.full(n, np.nan); self.sma200_ago[20:] = self.sma200[:-20]
        self.sma5 = roll_mean(c, 5)
        self.rsi2, self.rsi_ok = wilder_rsi(c, 2)
        self.pmax50, self.pmin50 = prior_max(c, 50), prior_min(c, 50)
        self.pmax20, self.pmin20 = prior_max(c, 20), prior_min(c, 20)
        mid = roll_mean(c, 20); sd = roll_std(c, 20)
        self.bbu, self.bbl = mid + 2.0 * sd, mid - 2.0 * sd
        ema = np.zeros(n)
        for i in range(n):
            ema[i] = c[i] if i == 0 else ema[i - 1] + (2.0 / 21.0) * (c[i] - ema[i - 1])
        ready = (np.arange(n) >= 19) & self.atr_ok
        kcu, kcl = ema + 1.5 * self.atr, ema - 1.5 * self.atr
        sq = ready & (self.bbu < kcu) & (self.bbl > kcl)
        self.sq = sq
        self.prior_sq5 = np.zeros(n, bool)
        for i in range(5, n):
            self.prior_sq5[i] = sq[i - 5:i].all()
        down = (c < self.sma200) & (self.sma200 < self.sma200_ago)
        self.down_regime = np.nan_to_num(down.astype(float)).astype(bool)

    # ---- Entry-Signale (je Tag t, Signal am Schlusskurs) ----
    def signals(self, arch):
        n, c = self.n, self.c
        long_ = np.zeros(n, bool); short = np.zeros(n, bool)
        if arch == "T":
            ok = ~np.isnan(self.pmax50) & self.atr_ok
            long_ = ok & (c > self.pmax50)
            short = ok & (c < self.pmin50)
        elif arch == "M":
            ok = ~np.isnan(self.sma200) & self.rsi_ok & self.atr_ok
            long_ = ok & (c > self.sma200) & (self.rsi2 < ARCH["M"]["theta"])
            short = ok & (c < self.sma200) & (self.rsi2 > 100.0 - ARCH["M"]["theta"])
        elif arch == "R":
            rng_ = self.h - self.l
            ibs = np.where(rng_ > 0, (c - self.l) / np.where(rng_ > 0, rng_, 1.0), np.nan)
            ok = ~np.isnan(self.sma200) & self.atr_ok & (rng_ > 0)
            long_ = ok & (c > self.sma200) & (np.nan_to_num(ibs, nan=1.0) < ARCH["R"]["theta"])
        else:
            ok = self.prior_sq5 & (np.arange(n) >= 19) & self.atr_ok
            long_ = ok & (c > self.bbu)
            short = ok & (c < self.bbl)
        return long_, short

    def signal_exit(self, arch, j, side):
        c = self.c[j]
        if arch == "T":
            if np.isnan(self.pmin20[j]): return False
            return (c < self.pmin20[j]) if side > 0 else (c > self.pmax20[j])
        if arch == "M":
            if np.isnan(self.sma200[j]) or not self.rsi_ok[j] or not self.atr_ok[j]: return False
            return (c > self.sma5[j]) if side > 0 else (c < self.sma5[j])
        if arch == "R":
            return j >= 1 and c > self.h[j - 1]
        return False


def compute_ttp_lock(entry, peak, atr, side, act, trail, min_lock_bps):
    if entry <= 0 or atr <= 0: return None
    sign = 1.0 if side > 0 else -1.0
    gain = sign * (peak - entry)
    floor = entry * min_lock_bps / 1e4
    if gain < act * atr or gain < 2.0 * floor: return None
    return max(peak - trail * atr, entry + floor) if side > 0 else min(peak + trail * atr, entry - floor)


def disaster_pct(S, t, k, ds_max=None):
    ds_max = DS_MAX if ds_max is None else ds_max
    bps = k * S.atr_eff[t] / S.c[t] * 1e4 if S.atr_ok[t] else k * FLOOR_BPS / 1e4 * 1e4 * 0 + k * FLOOR_BPS
    return min(max(K_DISASTER * bps / 1e4, DS_MIN), ds_max)


def walk(S, arch, t, side, opts=None):
    """Verfolgt ein Signal vom Schlusskurs-Tag t (Fill zum Open t+1) bis zum Ausstieg. Rueckgabe dict oder None (kein Folgetag)."""
    o = opts or {}
    p = ARCH[arch]
    k = o.get("k", p["k"]); H = o.get("H", p["H"])
    stop_mode = o.get("stop", "S0"); ttp = o.get("ttp", "P0")
    use_disaster = o.get("disaster", True)
    use_signal_exit = o.get("signal_exit", True)
    n = S.n
    if t + 1 >= n: return None
    e = t + 1
    entry = S.o[e]
    pct = disaster_pct(S, t, k, o.get("ds_max"))
    np_cfg = o.get("np"); xf = o.get("exit_flags"); trailing = o.get("trailing", True)
    t_atr = S.atr_eff[t]
    lvl = entry * (1 - pct) if side > 0 else entry * (1 + pct)
    D = k * S.atr_eff[t] if stop_mode in ("S1", "S2") else None   # Distanz in Preis, beim Einstieg fixiert
    Dp = D * (1.5 if stop_mode == "S2" else 1.0) if D else None
    R = k * S.atr_eff[t]
    ext = entry; stop = None; ttp_peak = None   # Anker startet am Entry-Fill (on_position_opened)
    tsl = (entry - side * Dp) if Dp else None      # S1/S2: Stop folgt dem besten Hoch/Tief
    best_hl = entry
    partial = None   # (price) wenn P3 ausgeloest
    bars = 0
    for j in range(e, n):
        bars += 1
        hj, lj, cj, oj = S.h[j], S.l[j], S.c[j], S.o[j]
        # -- intrabar: Katastrophen-Stop, nativer TSL
        if use_disaster:
            if side > 0 and lj <= lvl:
                px = min(oj, lvl) if j > e else lvl
                return _trade(S, arch, e, j, side, entry, px, "DISASTER_STOP", bars, partial, stop_level=lvl)
            if side < 0 and hj >= lvl:
                px = max(oj, lvl) if j > e else lvl
                return _trade(S, arch, e, j, side, entry, px, "DISASTER_STOP", bars, partial, stop_level=lvl)
        if tsl is not None:
            if side > 0 and lj <= tsl:
                px = min(oj, tsl) if j > e else tsl
                return _trade(S, arch, e, j, side, entry, px, "TSL", bars, partial, stop_level=tsl)
            if side < 0 and hj >= tsl:
                px = max(oj, tsl) if j > e else tsl
                return _trade(S, arch, e, j, side, entry, px, "TSL", bars, partial, stop_level=tsl)
            best_hl = max(best_hl, hj) if side > 0 else min(best_hl, lj)
            tsl = max(tsl, best_hl - Dp) if side > 0 else min(tsl, best_hl + Dp)
        # -- P3: Teilschliessung bei +1R (50 %)
        if ttp == "P3" and partial is None:
            tgt = entry + side * R
            if (side > 0 and hj >= tgt) or (side < 0 and lj <= tgt):
                partial = max(oj, tgt) if side > 0 else min(oj, tgt)
                if j == e: partial = tgt
        # -- Schlusskurs-Logik (Basisklasse, S0/S2)
        if stop_mode in ("S0", "S2"):
            ext = max(ext, cj) if side > 0 else min(ext, cj)
            if not trailing:
                stop = None
            if trailing and stop is None and bars == 1:
                stop = ext - side * k * S.atr_eff[j] if S.atr_ok[j] else None
            if trailing and stop is not None and S.atr_ok[j]:
                raw = ext - side * k * S.atr_ratchet[j]
                stop = max(stop, raw) if side > 0 else min(stop, raw)
            if stop is not None and ((side > 0 and cj <= stop) or (side < 0 and cj >= stop)):
                return _next_open_exit(S, arch, e, j, side, entry, "TRAILING_STOP", bars, partial, stop_level=stop)
        # -- TTP (P1/P2) auf Schlusskurs
        if ttp in ("P1", "P2") and S.atr_ok[j]:
            act, trail = (0.5, 0.3) if ttp == "P1" else (1.5, 1.0)
            ttp_peak = cj if ttp_peak is None else (max(ttp_peak, cj) if side > 0 else min(ttp_peak, cj))
            lock = compute_ttp_lock(entry, ttp_peak, S.atr_eff[j], side, act, trail, o.get("min_lock_bps", 0.0))
            if lock is not None and ((side > 0 and cj <= lock) or (side < 0 and cj >= lock)):
                return _next_open_exit(S, arch, e, j, side, entry, "PROFIT_TARGET", bars, partial)
        # -- Ausstieg ohne Fortschritt (EBC-H5 NP): nach np_n Handelstagen kein Fortschritt von np_m * ATR20 (Signaltag)
        if np_cfg is not None and bars == np_cfg[0]:
            ext_np = ext if stop_mode in ("S0", "S2") else (max(entry, cj) if side > 0 else min(entry, cj))
            if side * (ext_np - entry) < np_cfg[1] * t_atr:
                return _next_open_exit(S, arch, e, j, side, entry, "NO_PROGRESS", bars, partial)
        # -- Zeitstopp
        if bars >= H:
            return _next_open_exit(S, arch, e, j, side, entry, "TIME_BOX", bars, partial)
        # -- Signal-Exit
        if xf is not None and xf[j]:
            return _next_open_exit(S, arch, e, j, side, entry, "SIGNAL", bars, partial)
        if use_signal_exit and S.signal_exit(arch, j, side):
            return _next_open_exit(S, arch, e, j, side, entry, "SIGNAL", bars, partial)
    return _trade(S, arch, e, n - 1, side, entry, S.c[n - 1], "DATA_END", bars, partial)


def _next_open_exit(S, arch, e, j, side, entry, reason, bars, partial, stop_level=None):
    if j + 1 >= S.n:
        return _trade(S, arch, e, j, side, entry, S.c[j], "DATA_END", bars, partial)
    return _trade(S, arch, e, j + 1, side, entry, S.o[j + 1], reason, bars + 0, partial, exit_open=True, stop_level=stop_level)


def _trade(S, arch, e, x, side, entry, px, reason, bars, partial, exit_open=False, stop_level=None):
    r_full = side * (px / entry - 1.0) * 1e4
    if partial is not None:
        r_part = side * (partial / entry - 1.0) * 1e4
        r = 0.5 * r_part + 0.5 * r_full
    else:
        r = r_full
    gap = None; whip = False
    if stop_level is not None:
        gap = side * (stop_level - px) / entry * 1e4          # positiv = ungünstig
        if not exit_open:
            whip = bool((S.c[x] > stop_level) if side > 0 else (S.c[x] < stop_level))   # intrabar ausgeloest, Schluss wieder jenseits des Stops
    return dict(entry_i=e, exit_i=x, side=side, entry_px=entry, exit_px=px, gross_bps=r, reason=reason, stop_gap_bps=gap, whipsaw=whip,
                hold_days=int((S.ts[x] - S.ts[e]) // 86_400_000_000_000), bars=x - e + (0 if exit_open else 1) )


def sequential(S, arch, allow_short=False, short_gate=False, opts=None):
    """Eine Position je Instrument (wie die Nautilus-Klasse): erstes Signal bei flacher Position, naechstes Signal fruehestens
    am Schlusskurs des Tages, an dem die Position (Open-Fill bzw. intrabar) beendet wurde."""
    lg, sh = S.signals(arch)
    if not allow_short: sh = np.zeros(S.n, bool)
    elif short_gate: sh = sh & S.down_regime
    trades = []
    t = 0
    n = S.n
    while t < n - 1:
        if lg[t]: side = 1
        elif sh[t]: side = -1
        else:
            t += 1; continue
        tr = walk(S, arch, t, side, opts)
        if tr is None: break
        tr["signal_i"] = t
        trades.append(tr)
        t = tr["exit_i"]   # naechstes Signal am Schluss des Exit-Tags (Open-Exit: Tag x; intrabar: Tag x)
        if tr["reason"] == "DATA_END": break
        if t <= tr["signal_i"]: t = tr["signal_i"] + 1
    return trades


def independent(S, arch, allow_short=False, short_gate=False, opts=None):
    """Alle Signale unabhaengig verfolgt (steigende Flanke der Entry-Bedingung), Ueberlappung erlaubt (EBC-H3 §7.1)."""
    lg, sh = S.signals(arch)
    if not allow_short: sh = np.zeros(S.n, bool)
    elif short_gate: sh = sh & S.down_regime
    out = []
    for t in range(1, S.n - 1):
        for side, arr in ((1, lg), (-1, sh)):
            if arr[t] and not arr[t - 1]:
                tr = walk(S, arch, t, side, opts)
                if tr is not None:
                    tr["signal_i"] = t; out.append(tr)
    return out
