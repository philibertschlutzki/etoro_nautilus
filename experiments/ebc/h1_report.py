import json, csv
M = json.load(open("results/H1/metrics.json")); V = json.load(open("results/H1/verdict.json"))
C = json.load(open("results/H1/costs_calibration.json"))
a = M["arms"]; reg = M["regimes"]; cl = M["P1.6_classes"]; n = M["null"] if "null" in M else M["P1.5"]
ctx = M["context_benchmark"]; u = M["universe"]
p11, p12 = M["P1.1"], M["P1.2"]
def r(x, d=2): return "n/a" if x is None else f"{x:.{d}f}"
rg = {x["regime"]: x for x in reg}
txt = f"""# EBC-H1 — Einfach × breit: Verdikt BESTAETIGT (vorläufig: nur Stufe A)

**Verdikt:** BESTAETIGT – die Archetypen T/M/V mit Kostenfilter (B+) sind out-of-sample netto klar profitabler als die Pro-Symbol-Auswahl (A1) und die Auswahl hat keinen Informationsgehalt (P1.1, P1.2 erfüllt, kein Widerlegungskriterium). Das Verdikt ist vorläufig: Stufe B (30 Tage Forward) und Stufe C (Demo-Kalibrierung) wurden nicht ausgeführt, Survivorship und ein überwiegend steigender Markt (~21 Monate) schränken die Aussage ein.

| ID | Wert | 90-%-KI | Schwelle | erfüllt |
|---|---|---|---|---|
| P1.1 ΔSR (B+ − A1) | {r(p11['point'])} (SR {r(a['Bplus']['sr'])} vs {r(a['A1']['sr'])}) | [{r(p11['lo'])}; {r(p11['hi'])}] | > 0 und Untergrenze > −0,20 | ja |
| P1.2 PBO(A1) / ρ | {r(p12['pbo'],3)} / {r(p12['rho'],3)} | ρ: [{r(M['rank_persistence']['ci90_iid'][0],3)}; {r(M['rank_persistence']['ci90_iid'][1],3)}] | PBO ≥ 0,50 und ρ ≤ 0,10 | ja |
| P1.3 SR>0 je Regime | B+ {M['P1.3']['n_regimes_SR_pos_Bplus']}/4, A1 {M['P1.3']['n_regimes_SR_pos_A1']}/4 | – | B+ ≥ 3/4 und A1 nicht | nein (A1 ebenfalls 3/4) |
| P1.4 ρ(Tages-Score, Stunden-Netto) | {r(M['P1.4']['spearman_daily_score_vs_hourly_net'],3)} (n={M['P1.4']['n_evaluable']}) | – | −0,10 ≤ ρ ≤ 0,10 | ja |
| P1.5 eligible Null/beobachtet | {r(n['ratio_null_over_observed'])} ({r(n['null_fraction_mean']*100,1)} % vs {r(n['observed_fraction']*100,1)} %) | Null [{r(n['null_fraction_ci90'][0]*100,1)}; {r(n['null_fraction_ci90'][1]*100,1)}] % | ≥ 0,80 | ja |
| P1.6 Klassen B+ ≥ A1 | {M['P1.6']['classes_Bplus_ge_A1']} von {M['P1.6']['classes_represented']} | – | Mehrheit | nein |

## Kennzahlen (Vergleichszeitraum {M['period'][0]} bis {M['period'][1]}, {M['n_days']} Tage, Netto)
| Arm | SR | Jahresrendite | Vol | MaxDD | Trades | Expectancy (bps) | PF | Kostenanteil |
|---|---|---|---|---|---|---|---|---|
""" + "\n".join(f"| {k} | {r(v['sr'])} | {v['ann_ret']*100:.1f} % | {v['ann_vol']*100:.1f} % | {v['maxdd']*100:.1f} % | {v['n_trades']} | {v['expectancy_bps']:.0f} | {v['pf']:.2f} | {(v['cost_share'] or 0)*100:.0f} % |" for k, v in a.items()) + f"""

ΔSR B+ − A0 = {r(M['dSR_Bplus_minus_A0']['point'])} [{r(M['dSR_Bplus_minus_A0']['lo'])}; {r(M['dSR_Bplus_minus_A0']['hi'])}]; Kostenfilter q ≤ 0,05/0,10/0,20: SR 1,78/2,02/2,02. Gleichgewichteter Aktien-Benchmark: SR {r(ctx['benchmark_ew_K1_sharpe'])}; B+ Beta {r(ctx['Bplus']['beta'])}, Alpha {ctx['Bplus']['alpha_ann']*100:.0f} % p.a. (A1: Beta {r(ctx['A1']['beta'])}, Alpha {ctx['A1']['alpha_ann']*100:.1f} % p.a.).

## Kostenbefund
Round-Trip-Kosten (Rückfallwerte): Aktien/ETF 6 bps, FX 3, Rohstoffe 10, Indizes 8, Krypto 230 bps (200 bps Kommission). Der Kostenfilter q ≤ 0,10 entfernt {u['n_total']-u['n_after_cost_filter']} von {u['n_total']} Instrumenten (alle 33 Krypto-Instrumente: q ≈ 0,28–1,40, dazu 7 weitere: Anleihen-ETFs, USDHKD/USDCNH, USDOLLAR); übrig K1 {u['by_class_after_filter']['K1']}, K2 {u['by_class_after_filter']['K2']}, K3 {u['by_class_after_filter']['K3']}, K4 {u['by_class_after_filter']['K4']}, K5 0. Die Spread-Messung am Wochenende ist für Nicht-Krypto-Klassen nicht repräsentativ (geschlossene Märkte); die Rekalibrierung mit gemessenen Spreads/Slippage/Finanzierung steht aus.

## Regime (SPX500)
| Regime | Tage | SR A1 | SR B+ |
|---|---|---|---|
""" + "\n".join(f"| {k} | {rg[k]['n_days']} | {r(rg[k]['SR_A1'])} | {r(rg[k]['SR_Bplus'])} |" for k in ("R1", "R2", "R3", "R4", "top5pct_abs_index_ret")) + f"""

B+ verliert in Übergangsphasen (R4) und an den 5 % größten Index-Tagen (Tagesmittel −18 bzw. −34 bps), ist in ruhigen Aufwärts- und volatilen Aufwärtsphasen stark.

## Antwort auf F1
Ja – der Apparat um die Signale ist der Kostentreiber, nicht die Signale: drei globale Archetypen (T: 50-Tage-Ausbruch, M: RSI2-Rücksetzer im SMA200-Trend, V: Squeeze-Ausbruch) mit je zwei festen Parametern in ATR-/Handelstag-Einheiten erreichen auf 426 kostengefilterten Instrumenten netto SR {r(a['Bplus']['sr'])} gegen {r(a['A1']['sr'])} der Pro-Symbol-Auswahl. Der Informationsgehalt der Auswahl ist null (ρ = {r(p12['rho'],3)}, PBO {r(p12['pbo'],2)}, Gates nicht besser als Nullmodell), und die Auswahl-Scores der Tagesachse sagen die Stunden-Ergebnisse nicht voraus (ρ = {r(M['P1.4']['spearman_daily_score_vs_hourly_net'],2)}; 76 % der Paare auf der Stundenachse negativ). Breite Instrumente heißt: Aktien/ETF tragen den Ertrag, FX ist netto leicht negativ, Krypto ist ohne Haltedauer ≫ 5 Tage durch 200 bps Kommission ausgeschlossen.
"""
open("results/H1/report.md", "w").write(txt)
print(len(txt.split()), "Wörter")
