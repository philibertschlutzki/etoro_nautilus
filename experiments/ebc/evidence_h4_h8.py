"""Belege fuer EBC-H4 bis EBC-H8 aus den committeten Ergebnissen von H1-H3 (rein lesend: keine API, keine Rohdaten).

Aufruf im Repo-Root:  python experiments/ebc/evidence_h4_h8.py
Ausgabe: results/evidence_h4_h8.json und eine Kurzfassung auf stdout.

Quellen (alle im Repository):
  results/H1/stage_a/trades/Bplus.parquet        Trades des Arms B+ im Vergleichszeitraum 2024-12-23 .. 2026-10-12
  results/H1/stage_a/portfolio_daily_returns.csv Tagesrenditen der Arme
  results/H1/universe.csv                        Kerzenzahl je Instrument (Ausrichtung der Trade-Indizes)
  results/H2/stage_a/{breadth_draws.csv, breadth_regression.json, sizing.csv}
  results/H3/stage_a/{stop_summary.csv, timebox.csv}

Im Arm B+ hat jede Position 2 % Notional (results/H1/preregistration.json); der Beitrag eines Trades zur Equity in
Prozentpunkten ist daher net_bps / 1e4 * 2 * 100 (ohne Zinseszins).
"""
import csv
import json
import os

import numpy as np
import pandas as pd

ROOT = os.getcwd()
OUT = os.path.join(ROOT, "results", "evidence_h4_h8.json")
NOTIONAL = 0.02
SLOTS = 30


def pct_equity(bps):
    """Summe von Trade-Renditen (bps auf Notional) als Prozentpunkte der Equity bei 2 % Notional."""
    return float(np.sum(bps) / 1e4 * NOTIONAL * 100.0)


def trade_stats(g):
    n = g["net_bps"].to_numpy()
    wins, losses = n[n > 0], n[n <= 0]
    return dict(n=int(len(n)), mean_bps=float(n.mean()), median_bps=float(np.median(n)), hit=float((n > 0).mean()),
                avg_win_bps=float(wins.mean()) if len(wins) else None,
                avg_loss_bps=float(losses.mean()) if len(losses) else None,
                profit_factor=float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
                equity_pp=pct_equity(n), median_hold_days=float(g["hold_days"].median()))


def main():
    tr = pd.read_parquet(os.path.join(ROOT, "results/H1/stage_a/trades/Bplus.parquet"))
    tr["instrument"] = tr["instrument"].astype(str)
    uni = pd.read_csv(os.path.join(ROOT, "results/H1/universe.csv"), dtype={"etoro_id": str})
    klasse = dict(zip(uni["etoro_id"], uni["klasse"]))
    tr["klasse"] = tr["instrument"].map(klasse)
    ev = {"quelle": "results/H1 (Arm B+), results/H2, results/H3", "notional": NOTIONAL, "slots": SLOTS}

    # --- 1. Trades je Archetyp
    ev["bplus_trades"] = {"alle": trade_stats(tr)}
    for a, g in tr.groupby("strategy"):
        ev["bplus_trades"][a] = trade_stats(g)

    # --- 2. Ausreisser-Abhaengigkeit des Gewinns
    net = np.sort(tr["net_bps"].to_numpy())[::-1]
    total = net.sum()
    tails = {}
    for share in (0.01, 0.02, 0.05, 0.10):
        k = int(round(share * len(net)))
        tails[f"top_{int(share * 100)}pct_trades_share_of_net"] = float(net[:k].sum() / total)
    tails["n_trades_net_ge_2000bps"] = int((net >= 2000).sum())
    tails["share_of_net_from_trades_ge_2000bps"] = float(net[net >= 2000].sum() / total)
    ev["ausreisser"] = tails

    # --- 3. Zerlegung der Verluste nach Ausstiegsgrund
    losing = tr[tr["net_bps"] < 0]
    gross_loss = losing["net_bps"].sum()
    by_reason = (losing.groupby(["strategy", "reason"])["net_bps"].agg(["count", "sum"])
                 .assign(share_of_gross_losses=lambda d: d["sum"] / gross_loss).sort_values("sum"))
    ev["verluste"] = {
        "gross_losses_equity_pp": pct_equity(losing["net_bps"]),
        "net_total_equity_pp": pct_equity(tr["net_bps"]),
        "by_strategy_reason": [dict(strategy=s, reason=r, n=int(row["count"]), equity_pp=pct_equity([row["sum"]]),
                                    share_of_gross_losses=float(row["share_of_gross_losses"]))
                               for (s, r), row in by_reason.iterrows()],
    }
    ds = tr[tr["reason"] == "DISASTER_STOP"]
    ev["katastrophen_stop"] = dict(
        n=int(len(ds)), share_of_trades=float(len(ds) / len(tr)),
        share_of_gross_losses=float(ds["net_bps"].sum() / gross_loss),
        equity_pp=pct_equity(ds["net_bps"]), median_net_bps=float(ds["net_bps"].median()),
        median_hold_days_by_strategy={a: float(g["hold_days"].median()) for a, g in ds.groupby("strategy")},
        n_by_strategy={a: int(len(g)) for a, g in ds.groupby("strategy")},
        hinweis="Median ~ -1506 bps = Deckel disaster_stop_max_pct 0,15 plus Kosten (experiments/ebc/sim.py DS_MAX)")

    # --- 3b. Mechanik des Katastrophen-Stops auf der Tagesachse
    # ATR20 je Instrument (bps, Median der letzten 250 Tage) = c_rt_bps / q aus results/H1/universe_q.csv; Naeherung fuer
    # die ATR beim Einstieg. DS-Distanz = clamp(K_DISASTER * k * ATR%, DS_MIN, DS_MAX) wie experiments/ebc/sim.py disaster_pct.
    uq = pd.read_csv(os.path.join(ROOT, "results/H1/universe_q.csv"), dtype={"etoro_id": str})
    atr_pct = tr["instrument"].map(dict(zip(uq["etoro_id"], uq["c_rt_bps"] / uq["q"]))) / 1e4
    k_arch = tr["strategy"].map({"T": 3.0, "M": 3.0, "V": 2.5})
    ds_raw = 3.0 * k_arch * atr_pct
    ds_in_atr = ds_raw.clip(0.02, 0.15) / atr_pct
    is_ds = tr["reason"] == "DISASTER_STOP"
    quint = pd.qcut(atr_pct, 5, labels=False)
    # H3 (S0P0, K1): im Modus S0 wird nur der Katastrophen-Stop intrabar ausgefuehrt; der Trailing-Stop fuellt zum
    # naechsten Open und kann kein Whipsaw sein (sim.py _trade). Whipsaw-Quote der DS-Exits = whipsaw_share * n_stop_exits / n_DS.
    ex = pd.read_parquet(os.path.join(ROOT, "results/H3/stage_a/exits_paired.parquet"))
    ex = ex[(ex["variante"] == "S0P0") & (ex["klasse"] == "K1")]
    ss = pd.read_csv(os.path.join(ROOT, "results/H3/stage_a/stop_summary.csv"))
    ss = ss[(ss["variante"] == "S0P0") & (ss["klasse"] == "K1") & ss["archetyp"].isin(["T", "M", "V"])]
    whip = {}
    for _, row in ss.iterrows():
        n_ds = int(((ex["archetyp"] == row["archetyp"]) & (ex["exit_grund"] == "DISASTER_STOP")).sum())
        whip[row["archetyp"]] = dict(n_ds_exits=n_ds,
                                     ds_whipsaw_rate=float(row["whipsaw_share"] * row["n_stop_exits"] / n_ds) if n_ds else None)
    ev["katastrophen_stop_mechanik"] = dict(
        share_trades_cap_binds=float((ds_raw > 0.15).mean()),
        median_atr20_bps=dict(alle=float(atr_pct.median() * 1e4), ds_exits=float(atr_pct[is_ds].median() * 1e4),
                              andere=float(atr_pct[~is_ds].median() * 1e4)),
        median_ds_distance_in_atr=dict(alle=float(ds_in_atr.median()), ds_exits=float(ds_in_atr[is_ds].median())),
        trailing_distance_in_atr={"T": 3.0, "M": 3.0, "V": 2.5},
        by_atr_quintile=[dict(quintil=int(qq), n=int(len(g)), median_atr20_bps=float(atr_pct[g.index].median() * 1e4),
                              ds_rate=float((g["reason"] == "DISASTER_STOP").mean()), mean_net_bps=float(g["net_bps"].mean()),
                              equity_pp=pct_equity(g["net_bps"]))
                         for qq, g in tr.groupby(quint)],
        h3_ds_whipsaw_K1=whip,
        hinweis="Der Deckel 15 % stammt aus der Stundenachse (3 x Stopdistanz); auf Tageskerzen bindet er fast immer und "
                "legt den intrabar ausgefuehrten Katastrophen-Stop naeher an den Einstieg als den Trailing-Stop (k x ATR).")

    # --- 4. Zeitstopp: Gewinner am T-Zeitstopp, Verlierer am M-Zeitstopp
    ev["zeitstopp"] = {f"{a}_{r}": trade_stats(g) for (a, r), g in tr.groupby(["strategy", "reason"])
                       if r in ("TIME_BOX", "SIGNAL")}

    # --- 5. Anlageklassen
    ev["klassen"] = {k: trade_stats(g) for k, g in tr.groupby("klasse")}

    # --- 6. Kapazitaet (Naeherung): gleichzeitig offene Positionen je Handelstag
    n_candles = dict(zip(uni["etoro_id"], uni["n_candles"]))
    n_max = int(uni["n_candles"].max())
    shift = n_max - tr["instrument"].map(n_candles)          # Indizes am gemeinsamen Datenende ausrichten
    g0 = (tr["entry_i"] + shift).astype(int).to_numpy()
    g1 = (tr["exit_i"] + shift).astype(int).to_numpy()
    occ = np.zeros(g1.max() + 2)
    for a, b in zip(g0, g1):
        occ[a:b] += 1
    occ = occ[g0.min():g1.max()]
    ev["kapazitaet_naeherung"] = dict(
        mean=float(occ.mean()), median=float(np.median(occ)), p90=float(np.percentile(occ, 90)),
        share_days_ge_28=float((occ >= 28).mean()), share_days_ge_30=float((occ >= SLOTS).mean()),
        hinweis="Trade-Indizes je Instrument, am Datenende ausgerichtet; Feiertagskalender nicht abgeglichen. "
                "Exakte Zahl verworfener Kandidaten liefert erst EBC-H4 P4.4.")

    # --- 7. Tagesrenditen B+: Drawdown und Tail-Tage
    d = pd.read_csv(os.path.join(ROOT, "results/H1/stage_a/portfolio_daily_returns.csv"))
    r = d["Bplus"].astype(float).to_numpy()
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    i_trough = int(dd.argmin())
    i_peak = int(np.argmax(eq[:i_trough + 1]))
    k = int(np.ceil(0.05 * len(r)))
    srt = np.sort(r)
    ev["tagesrenditen"] = dict(
        n_days=int(len(r)), ann_vol=float(r.std(ddof=1) * np.sqrt(252)), maxdd=float(dd.min()),
        maxdd_from=str(d["date"].iloc[i_peak]), maxdd_to=str(d["date"].iloc[i_trough]),
        sum_worst_5pct_days_pp=float(srt[:k].sum() * 100), sum_best_5pct_days_pp=float(srt[-k:].sum() * 100),
        sum_all_days_pp=float(r.sum() * 100),
        corr_A1_Bplus=float(np.corrcoef(d["A1"].astype(float), d["Bplus"].astype(float))[0, 1]))

    # --- 8. H3: Stop-Zusammenfassung (S0P0, K1; Whipsaw-Anteil bezogen auf alle Stop-Exits) und Zeitbox-Tabelle
    stops = list(csv.DictReader(open(os.path.join(ROOT, "results/H3/stage_a/stop_summary.csv"))))
    ev["h3_stop_summary_S0P0_K1"] = {row["archetyp"]: dict(whipsaw_share_all_stop_exits=float(row["whipsaw_share"]),
                                                          n_stop_exits=int(float(row["n_stop_exits"])),
                                                          expectancy_bps=float(row["expectancy_bps"]))
                                     for row in stops if row["variante"] == "S0P0" and row["klasse"] == "K1"}
    tb = list(csv.DictReader(open(os.path.join(ROOT, "results/H3/stage_a/timebox.csv"))))
    ev["h3_zeitbox"] = [dict(archetyp=row["archetyp"], h=int(row["h"]), sr_netto=float(row["sr_netto"]),
                             n_trades=int(row["n_trades"]), expectancy_bps=float(row["expectancy_bps"])) for row in tb]

    # --- 9. H2: Breite (Saettigung) und Sizing
    br = json.load(open(os.path.join(ROOT, "results/H2/stage_a/breadth_regression.json")))
    draws = list(csv.DictReader(open(os.path.join(ROOT, "results/H2/stage_a/breadth_draws.csv"))))
    mean_sr = {}
    for row in draws:
        mean_sr.setdefault((row["schema"], int(row["N"])), []).append(float(row["sr_p"]))
    rho_k1 = br["rho_bar_within"]["K1"]
    ev["h2_breite"] = dict(
        rho_bar_within=br["rho_bar_within"], rho_bar_between=br["rho_bar_between"],
        n_eff_ceiling_K1=float(1.0 / rho_k1),
        mean_sr_p_aktien={str(n): float(np.mean(v)) for (s, n), v in sorted(mean_sr.items()) if s == "aktien"},
        mean_sr_p_alle={str(n): float(np.mean(v)) for (s, n), v in sorted(mean_sr.items()) if s == "alle"},
        hinweis="N_eff = N/(1+(N-1)*rho) naehert sich 1/rho: mehr Aktien mit denselben Archetypen heben die Sharpe "
                "kaum ueber N_eff ~ 1/rho hinaus; neue, wenig korrelierte Signale heben die Decke.")
    ev["h2_sizing"] = [dict(variante=row["variante"], sr=float(row["sr"]), maxdd=float(row["maxdd"]),
                            n_trades=int(row["n_trades"])) for row in csv.DictReader(
                                open(os.path.join(ROOT, "results/H2/stage_a/sizing.csv")))]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(ev, open(OUT, "w"), indent=1, ensure_ascii=False)

    a = ev["bplus_trades"]
    print(f"B+: {a['alle']['n']} Trades, Netto {a['alle']['equity_pp']:.1f} pp Equity; "
          f"T {a['T']['n']}, M {a['M']['n']}, V {a['V']['n']}")
    print(f"Ausreisser: Top 1 % = {tails['top_1pct_trades_share_of_net']:.0%}, Top 5 % = "
          f"{tails['top_5pct_trades_share_of_net']:.0%} des Netto-Gewinns; {tails['n_trades_net_ge_2000bps']} Trades >= +2000 bps")
    k_ = ev["katastrophen_stop"]
    print(f"Katastrophen-Stop: {k_['n']} Trades ({k_['share_of_trades']:.1%}), {k_['share_of_gross_losses']:.1%} der "
          f"Bruttoverluste, Median {k_['median_net_bps']:.0f} bps, Haltedauer-Median {k_['median_hold_days_by_strategy']}")
    c_ = ev["kapazitaet_naeherung"]
    print(f"Kapazitaet (Naeherung): Median {c_['median']:.0f} von {SLOTS} Plaetzen, >= 28 belegt an "
          f"{c_['share_days_ge_28']:.0%} der Tage")
    t_ = ev["tagesrenditen"]
    print(f"MaxDD {t_['maxdd']:.1%} ({t_['maxdd_from']} -> {t_['maxdd_to']}); schlechteste 5 % Tage "
          f"{t_['sum_worst_5pct_days_pp']:.1f} pp, alle Tage {t_['sum_all_days_pp']:.1f} pp")
    m_ = ev["katastrophen_stop_mechanik"]
    print(f"DS-Deckel 15 % bindet bei {m_['share_trades_cap_binds']:.0%} der Trades; DS-Distanz Median "
          f"{m_['median_ds_distance_in_atr']['alle']:.2f} ATR (DS-Exits {m_['median_ds_distance_in_atr']['ds_exits']:.2f}) "
          f"gegen Trailing 2,5-3 ATR; DS-Whipsaw-Quote H3 K1: " +
          ", ".join(f"{k}: {v['ds_whipsaw_rate']:.0%}" for k, v in m_["h3_ds_whipsaw_K1"].items()))
    print(f"N_eff-Decke K1: 1/rho = {ev['h2_breite']['n_eff_ceiling_K1']:.0f}; mittlere SR_P (Aktien) "
          f"{ev['h2_breite']['mean_sr_p_aktien']}")
    print("geschrieben:", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
