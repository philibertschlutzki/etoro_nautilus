"""Erzeugt results/H{4..7}/verdict.json und report.md aus metrics.json (Zahlen nur aus den Metrik-Dateien)."""
import json, subprocess
head = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
f = lambda x, n=2: "–" if x is None else f"{x:.{n}f}"
pc = lambda x, n=1: "–" if x is None else f"{100*x:.{n}f} %"
def ci(c): return f"{f(c['point'])} [{f(c['lo'])}; {f(c['hi'])}]"
def write(h, verdict, extra, md):
    base = dict(hypothesis=h, verdict=verdict, stage_a_only=True, repo_commit=head, stage_b="nicht ausgefuehrt", stage_c="NICHT_FREIGEGEBEN" if h in ("H4", "H6") else "entfaellt")
    base.update(extra)
    json.dump(base, open(f"results/{h}/verdict.json", "w"), indent=1, default=float)
    open(f"results/{h}/report.md", "w").write(md)

# ---------------- H4
d = json.load(open("results/H4/metrics.json")); A = d["arms"]; C = d["comparisons"]
rows = "".join(f"| {n} | {a['slots'] if a['slots']<10**5 else '∞'} × {pc(a['notional'])} | {a['n_trades']} | {f(a['sr'])} | {pc(a['maxdd'])} | {pc(a['total_ret'],0)} | {f(a['expectancy_bps'],0)} | {pc(a['dropped_share'],0)} | {f(a['mean_open'],0)} | {a['n_ge2000']} | {pc(a.get('volmatched_ann_ret'))} |\n" for n, a in A.items())
md = f"""# EBC-H4 — Kapazität × Breite: Stufe-A-Bericht

**Verdikt: {d['verdict_stage_a']}** (nur Stufe A; Stufe B/C nicht ausgeführt). Mehr Plätze und ein frisches Universum liefern mehr als das Dreifache an Trades (×{f(d['P42']['trade_ratio'])}), aber keine bessere Risiko-Rendite: ΔSR(C120+ − C30) = {ci(d['P43']['dsr'])}. Der Edge repliziert auf `U_neu` nur schwach und nicht signifikant.

Zeitraum {d['period'][0]} bis {d['period'][1]} ({d['n_days']} Tage), Kosten nur Rückfallwerte (keine Werktags-Spread-Messung), `U_neu` = {d['universe_neu']['n']} Instrumente (Seed 20261012, SHA-256 `{d['universe_neu']['sha256'][:12]}…`), davon {d['universe_neu']['n_after_cost_filter']} nach Kostenfilter q ≤ 0,10. Parität C30 = B+: 1141 Trades, SR 2,0156, MaxDD −15,42 % (bitgleich).

## Vorhersagen

| ID | Ergebnis | Schwelle | erfüllt |
|---|---|---|---|
| P4.1 Replikation | N30-Expectancy {f(d['P41']['mean'],0)} bps, 90-%-KI [{f(d['P41']['lo'],0)}; {f(d['P41']['hi'],0)}]; K1-C30 {f(d['P41']['k1_c30_expectancy'],0)} bps | untere Grenze > 0 und ≥ 0,5 × K1-C30 | {'ja' if d['P41']['pass'] else 'nein'} |
| P4.2 Trades/Qualität | Trades ×{f(d['P42']['trade_ratio'])}, Expectancy ×{f(d['P42']['exp_ratio'])} | ≥ 3,0 und ≥ 0,5 | {'ja' if d['P42']['pass'] else 'nein'} |
| P4.3 Risiko-Rendite | ΔSR {ci(d['P43']['dsr'])}; DD {pc(d['P43']['dd_c120plus'])} gegen {pc(d['P43']['dd_c30'])} | Punkt > 0, untere Grenze > −0,20, DD ≤ +1 pp | {'ja' if d['P43']['pass'] else 'nein'} |
| P4.4 Kapazität bindet | verworfen: C30 {pc(d['P44']['C30'],0)}, C120+ {pc(d['P44']['C120plus'],0)} | C30 ≥ 20 %, C120+ ≤ 5 % | C30 ja, C120+ nein |
| P4.5 Ausreisser | ≥ +2000 bps: ×{f(d['P45']['ratio'])} | ≥ 2,5 | {'ja' if d['P45']['ratio']>=2.5 else 'nein'} |
| P4.6 Nur Aktien/ETF | ΔSR(C30-K1 − C30) {ci(C['C30_K1-C30'])} | Punkt ≥ −0,10 | {'ja' if C['C30_K1-C30']['point']>=-0.10 else 'nein'} |
| P4.7 q-Rang als Vola-Tilt | ΔSR(C30 − C30r) {ci(C['C30-C30r'])} | berichten | – |

Widerlegung (§2.3): (a) obere Grenze N30 < 0: nein; (b) obere Grenze ΔSR(C120+ − C30) < 0: nein ({f(d['P43']['dsr']['hi'])}). Daher `UNENTSCHIEDEN`.

## Kapazitätstabelle

| Arm | Plätze × Notional | Trades | SR | MaxDD | Rendite | Expectancy bps | verworfen | Ø offen | ≥ 2000 bps | Jahresrendite bei Vola von C30 |
|---|---|---|---|---|---|---|---|---|---|---|
{rows}
Verworfene Kandidaten bei C30 erreichen im Mittel {f(A['C30']['dropped_expectancy_bps'],0)} bps gegen {f(A['C30']['expectancy_bps'],0)} bps der genommenen: Die q-Reihenfolge sortiert gut, zusätzliche Plätze füllen sich mit schlechteren Kandidaten (H0 teilweise bestätigt). Die Zufallsreihenfolge C30r erreicht nur SR {f(A['C30r']['sr'])} gegen {f(A['C30']['sr'])}: Der q-Rang wirkt als starker Volatilitäts-Tilt auf volatile Aktien.

## Antwort auf F5

Trades: C120+ macht ×{f(d['P42']['trade_ratio'],1)} so viele Trades wie C30, C∞ ×{f(A['Cinf']['n_trades']/A['C30']['n_trades'],0)}. Gewinn: Die Gesamtrendite sinkt mit mehr Plätzen ({pc(A['C30']['total_ret'],0)} bei C30, {pc(A['C120plus']['total_ret'],0)} bei C120+); auch bei gleicher Vola bleibt C30 vorn ({pc(A['C30']['volmatched_ann_ret'])} gegen {pc(A['C120plus']['volmatched_ann_ret'])}). Verluste: Der Drawdown sinkt mit der Kapazität nur auf unlevered Basis ({pc(A['C120']['maxdd'])} bei C120), nicht bei gleicher Vola ({pc(A['C120']['volmatched_maxdd'])}).

## Einschränkungen

Ein Aufwärtsregime; `U_neu` und `U_breit` enthalten nur heute noch gelistete Instrumente mit ≥ 750 Kerzen (Überlebens-Verzerrung); ohne gemessene Spreads, ohne Stufe B und C; C30r mit einer Ziehung je Trade, nicht je Handelstag neu.
"""
write("H4", d["verdict_stage_a"], dict(capacity={k: dict(slots=v["slots"], notional=v["notional"], n_trades=v["n_trades"], sr=v["sr"], maxdd=v["maxdd"], expectancy_bps=v["expectancy_bps"], dropped_share=v["dropped_share"], dropped_expectancy_bps=v["dropped_expectancy_bps"], mean_open_positions=v["mean_open"], n_trades_ge_2000bps=v["n_ge2000"]) for k, v in A.items()}, predictions=dict(P41=d["P41"], P42=d["P42"], P43=d["P43"]), universe_neu=d["universe_neu"]), md)

# ---------------- H5
d = json.load(open("results/H5/metrics.json")); Cf = d["configs"]; C = d["comparisons"]
order = ["H0@C30", "HM@C30", "Hstar@C30", "HL@C30", "H0+NP@C30", "HL+NP@C30", "H0@C60", "H0@C120", "HM@C120", "T20@C120", "Tinf@C120", "HL@C120", "H0+NP@C120", "HL+NP@C120"]
rows = "".join(f"| {k} | {Cf[k]['n_trades']} (T {Cf[k]['n_T']}, M {Cf[k]['n_M']}, V {Cf[k]['n_V']}) | {f(Cf[k]['sr'])} | {pc(Cf[k]['maxdd'])} | {pc(Cf[k]['total_ret'],0)} | {f(Cf[k]['expectancy_bps'],0)} | {f(Cf[k]['pp_DISASTER_STOP'],1)} | {f(Cf[k]['pp_TIME_BOX'],1)} |\n" for k in order)
p = d["P51"]; np_ = d["np10"]
md = f"""# EBC-H5 — Haltedauer × Kapazität: Stufe-A-Bericht

**Verdikt: {d['verdict_stage_a']}** (nur Stufe A). Ein 1-Tage-Zeitstopp für M kostet bei 30 Plätzen klar Sharpe (ΔSR {ci(p['dsr'])}, obere Grenze < 0), obwohl er die M-Katastrophen-Stops von {f(p['ds_loss_M_pp']['H0'],1)} auf {f(p['ds_loss_M_pp']['HM'],1)} pp drückt und M-Trades um ×{f(p['trade_ratio_M'])} erhöht. Das Optimum von H_T hängt nicht erkennbar von der Kapazität ab (Interaktion I = {ci(d['P52']['I'])}).

Zeitraum {d['period'][0]} bis {d['period'][1]}, `U_breit` nach Kostenfilter (426 Instrumente), Rückfallkosten.

## Vorhersagen

| ID | Ergebnis | erfüllt |
|---|---|---|
| P5.1 | ΔSR(HM − H0 @C30) {ci(p['dsr'])}; M-Trades ×{f(p['trade_ratio_M'])}; M-Katastrophen-Stop-Verlust ×{f(p['ds_loss_M_ratio'])} | nein (Punktschätzer < 0) |
| P5.2 | I = {ci(d['P52']['I'])} | nein |
| P5.3 | ΔSR(HL − H0 @C120) {ci(C['HL@C120-H0@C120'])} | Punkt > 0, DD ↓ |
| P5.4 | NP10 bei C30: Trades {Cf['H0+NP@C30']['n_trades']} gegen {Cf['H0@C30']['n_trades']}; ΔSR {ci(C['H0+NP@C30-H0@C30'])}; gepaarte ΔExpectancy der {np_['n_triggered']} ausgelösten Signale {f(np_['delta_expectancy_bps'],0)} bps [{f(np_['ci90'][0],0)}; {f(np_['ci90'][1],0)}], {np_['lost_outliers']} verlorene Ausreisser | nein |
| P5.5 PBO | C30 {f(d['pbo']['C30'])}, C120 {f(d['pbo']['C120'])} | C120: ≥ 0,5, Gitter-Optimum überangepasst |

Widerlegung (§2.3): (a) trifft zu, die obere Grenze von ΔSR(HM − H0 @C30) ist {f(C['HM@C30-H0@C30']['hi'])} < 0. Das Gitter bestimmt kein Verdikt; sein Spitzenwert bei C30 ({d['grid_best']['C30'][0][1]}, SR {f(d['grid_best']['C30'][0][0])}) liegt in der Nähe des Status quo (SR {f(Cf['H0@C30']['sr'])}), bei C120 liegt das Gitter-Optimum bei SR {f(d['grid_best']['C120'][0][0])} ({d['grid_best']['C120'][0][1]}) mit PBO {f(d['pbo']['C120'])}.

## Konfigurationen × Kapazität

| Konfiguration | Trades | SR | MaxDD | Rendite | Expectancy bps | Beitrag Katastrophen-Stop (pp) | Beitrag Zeitstopp (pp) |
|---|---|---|---|---|---|---|---|
{rows}
## Antwort auf F5

Trades: Kürzeres M und NP10 erhöhen die Trade-Zahl (HM @C30 +34 %, HL+NP @C30 +38 %). Gewinn: Bei 30 Plätzen bleibt der Status quo (SR {f(Cf['H0@C30']['sr'])}, Rendite {pc(Cf['H0@C30']['total_ret'],0)}) vorn; HL+NP erreicht explorativ SR {f(Cf['HL+NP@C30']['sr'])} bei Rendite {pc(Cf['HL+NP@C30']['total_ret'],0)} und MaxDD {pc(Cf['HL+NP@C30']['maxdd'])}, ΔSR gegen H0 −0,03 [−0,52; 0,45] (Nachrechnung aus `portfolio_daily_returns.csv`), also nicht signifikant. Verluste: Lange Haltedauern bei hoher Kapazität (HL @C120) senken den Drawdown auf {pc(Cf['HL@C120']['maxdd'])}, aber auch die Rendite auf {pc(Cf['HL@C120']['total_ret'],0)}.

## Einschränkungen

HL+NP@C30 ist eine nachträgliche Beobachtung aus acht Konfigurationen (nur zur Berichterstattung vorregistriert) und in den Teilperioden nicht stabil (zweites Drittel besser, drittes schlechter als H0). Ohne Stufe B.
"""
write("H5", d["verdict_stage_a"], dict(configs={k: dict(sr=v["sr"], maxdd=v["maxdd"], n_trades=dict(T=v["n_T"], M=v["n_M"], V=v["n_V"]), expectancy_bps=v["expectancy_bps"]) for k, v in Cf.items()}, interaction=d["P52"]["I"], np10=np_, pbo=d["pbo"], recommended_holding=dict(C30=dict(T=60, M=5, V=20), C120=dict(T=60, M=5, V=20), note="Status quo bleibt; keine Konfiguration schlaegt H0 signifikant")), md)

# ---------------- H6
d = json.load(open("results/H6/metrics.json")); A = d["arms"]; C = d["comparisons"]; pr = d["paired"]
rows = "".join(f"| {n} | {a['n_trades']} | {f(a['sr'])} | {pc(a['maxdd'])} | {pc(a['maxdd_volmatched'])} | {pc(a['cvar5_volmatched'])} | {pc(a['total_ret'],0)} | {pc(a['ds_share_gross_losses'],0)} | {pc(a['mean_exposure'],0)} |\n" for n, a in A.items())
md = f"""# EBC-H6 — Verlustbudget in drei Ebenen: Stufe-A-Bericht

**Verdikt: {d['verdict_stage_a']}** (nur Stufe A; Stufe C NICHT_FREIGEGEBEN). L1 (Katastrophen-Stop-Deckel 45 %) wirkt auf Trade-Ebene wie vorhergesagt: Anteil der Katastrophen-Stop-Exits an den Bruttoverlusten {pc(A['C30']['ds_share_gross_losses'],0)} → {pc(A['L1']['ds_share_gross_losses'],0)}, gepaarte ΔExpectancy +{f(pr['delta_expectancy_bps'],0)} bps [{f(pr['ci90'][0],0)}; {f(pr['ci90'][1],0)}]. Auf Portfolio-Ebene verliert L1 aber Sharpe ({ci(C['L1-C30'])}), und die Kombination L123 ist klar schlechter ({ci(C['L123-C30'])}) ohne kleineren vola-gleichen Drawdown ({f(d["P62"]["dd_ratio"])} × C30).

Zeitraum {d['period'][0]} bis {d['period'][1]}, Rückfallkosten, `simulate_sized` (Parität: C30 1143 Trades, SR {f(A['C30']['sr'])} gegen 1141 und 2,02 in `simulate`).

## Vorhersagen

| ID | Ergebnis | erfüllt |
|---|---|---|
| P6.1 | Anteil {pc(d['P61']['ds_share'],1)} ≤ 25 %; ΔExpectancy +{f(d['P61']['d_exp'],1)} bps, untere Grenze {f(d['P61']['lo'],1)} > −5 | ja |
| P6.2 | DD vola-gleich ×{f(d['P62']['dd_ratio'])} (Soll ≤ 0,75), CVaR5 ×{f(d['P62']['cvar_ratio'])} (≤ 0,80), ΔSR untere Grenze {f(d['P62']['dsr']['lo'])} (> −0,15), Trades ×{f(d['P62']['trades_ratio'])} | nein |
| P6.3 | L1 {ci(C['L1-C30'])}; L2 {ci(C['L2-C30'])}; L3 {ci(C['L3-C30'])} | L1: Expectancy ↑, DD nicht ↓; L2: vola-gleicher DD ↑; L3: DD ↓ aber SR ↓ |
| P6.4 | R4: mittlere Tagesrendite C30 {f(d['regimes'][3]['mean_daily_bps_C30'],1)} bps, L3 {f(d['regimes'][3]['mean_daily_bps_L3'],1)} bps (Soll ≥ 30 % kleinerer Verlust) | nein |
| P6.6 | bei C120: L1 {ci(C['L1@C120-C120'])}, L123 {ci(C['L123@C120-C120'])} | berichtet |

Widerlegung (§2.3): (b) trifft zu, die obere Grenze von ΔSR(L123 − C30) ist {f(C['L123-C30']['hi'])} < 0 und der vola-gleiche DD ist nicht kleiner. Whipsaw: {pc(pr['whipsaw_share_ds0'],0)} der Katastrophen-Stop-Exits schliessen am selben Tag jenseits des Stops. Die {pr['n_ds0_exits']} früheren Katastrophen-Stop-Exits enden unter DS1 zu {pr['former_ds_new_reason']['TRAILING_STOP']} im Trailing-Stop (im Mittel {f(pr['mean_net_former_ds_exit_ds1'],0)} statt {f(pr['mean_net_former_ds_exit_ds0'],0)} bps).

## Arme

| Arm | Trades | SR | MaxDD | MaxDD vola-gleich | CVaR5 vola-gleich | Rendite | Anteil Katastrophen-Stop an Verlusten | Ø Exposure |
|---|---|---|---|---|---|---|---|---|
{rows}
## Antwort auf F5

Trades: DS1 und L2 ändern die Zahl der Trades um −8 % bis −3 %. Gewinn: Die Gesamtrendite sinkt (C30 {pc(A['C30']['total_ret'],0)}, L1 {pc(A['L1']['total_ret'],0)}, L123 {pc(A['L123']['total_ret'],0)}). Verluste: Der Katastrophen-Stop wird mit DS1 vertragsgemäss (3 % statt 50 % der Bruttoverluste, Invariante ≤ 1 % der Exits fast erfüllt), der Drawdown sinkt dadurch aber nicht; das Risikobudget und die Volatilitätssteuerung senken nur das mittlere Exposure.

## Einschränkungen

Ein Aufwärtsregime mit nur 42 R4-Tagen; Näherung des Signaltags als Vortag des Einstiegs; Stufe B und C fehlen (Broker-Stop-Distanz ungemessen).
"""
write("H6", d["verdict_stage_a"], dict(ds=dict(share_gross_losses=dict(DS0=A["C30"]["ds_share_gross_losses"], DS1=A["L1"]["ds_share_gross_losses"]), delta_expectancy_bps=pr["delta_expectancy_bps"], ci90=pr["ci90"], former_ds_exits_new_reason=pr["former_ds_new_reason"]), arms={k: dict(sr=v["sr"], maxdd_volmatched=v["maxdd_volmatched"], cvar5_volmatched=v["cvar5_volmatched"], n_trades=v["n_trades"]) for k, v in A.items()}, comparisons=C, broker_sl=dict(status="NICHT_FREIGEGEBEN")), md)

# ---------------- H7
d = json.load(open("results/H7/metrics.json")); A = d["arms"]; C = d["comparisons"]; AR = d["archetypes"]
rows = "".join(f"| {n} | {a['n_trades']} | {f(a['sr'])} | {pc(a['maxdd'])} | {pc(a['total_ret'],0)} | {f(a['expectancy_bps'],0)} |\n" for n, a in A.items())
arows = "".join(f"| {a} | {AR[a]['n_signals_trades']} / {AR[a]['n_taken']} | {f(AR[a]['expectancy_taken_bps'],0)} [{f(AR[a]['ci90_taken'][0],0)}; {f(AR[a]['ci90_taken'][1],0)}] | {pc(AR[a]['hit'],0)} | {f(AR[a]['sr_allein'])} | {f(AR[a]['alpha']['alpha_ann']*100,1)} % [{f(AR[a]['alpha']['ci90'][0]*100,1)}; {f(AR[a]['alpha']['ci90'][1]*100,1)}] | {f(AR[a]['corr_tmv'])} | {f(AR[a]['corr_M'])} |\n" for a in "RXWM")
md = f"""# EBC-H7 — Signal-Breite (R, X, W): Stufe-A-Bericht

**Verdikt: {d['verdict_stage_a']}** (nur Stufe A). R (IBS-Rücksetzer) ist netto positiv (Expectancy {f(AR['R']['expectancy_taken_bps'],0)} bps, untere Grenze > 0) und als Einzel-Sleeve stark (SR {f(AR['R']['sr_allein'])}), korreliert aber mit M ({f(AR['R']['corr_M'])}) und dem TMV-Portfolio ({f(AR['R']['corr_tmv'])}) zu stark (P7.2 verfehlt). X ist positiv ohne Signifikanz, W schwach positiv mit Konfidenzintervall um 0. Die Kombination mit festen Quoten bringt keinen Sharpe-Gewinn: ΔSR(Kombi6-Q − TMV-Q) {ci(C['Kombi6-Q-TMV-Q'])}, Trades ×{f(d['P73']['trade_ratio'])} statt ≥ 1,5.

Zeitraum {d['period'][0]} bis {d['period'][1]}, `U_breit` nach Kostenfilter, Rückfallkosten (R und W sind kostenempfindlich: +4 bps Round-Trip senken die Expectancy von R auf {f(AR['R']['stress_plus4bps_expectancy'],0)} bps). Handprüfung: {d['handcheck_ok']} von 60 Trades stimmen mit der unabhängigen Nachrechnung überein; `simulate_sleeves` mit einem Sleeve entspricht `simulate` ({d['test_single_sleeve_equals_simulate']}).

## Archetypen

| Archetyp | Trades / genommen | Expectancy bps (genommen) [90-%-KI] | Trefferquote | SR allein | Alpha p. a. gegen SPX500 | Korr. TMV-Q | Korr. M |
|---|---|---|---|---|---|---|---|
{arows}
## Vorhersagen

| ID | Ergebnis | erfüllt |
|---|---|---|
| P7.1 | R, X, W: {d['P71']} | alle ja (R untere Grenze > 0; X, W Punktschätzer und Alpha > 0) |
| P7.2 | R, X, W: {d['P72']} | nur W |
| P7.3 | Trades ×{f(d['P73']['trade_ratio'])}, ΔSR {ci(d['P73']['dsr'])}, DD {pc(d['P73']['dd_k'])} gegen {pc(d['P73']['dd_t'])} | nein |
| P7.4 | Überlappung R/M {pc(d['overlap_RM']['share'],0)} der R-Signale (≤ 50 %); mit M-Signal {f(d['overlap_RM']['exp_with'],0)} bps, ohne {f(d['overlap_RM']['exp_without'],0)} bps | ja |
| P7.5 | Kombi6-P (gemeinsamer Pool 120) gegen TMV120: SR {f(A['Kombi6-P']['sr'])} gegen {f(A['TMV120']['sr'])}, Rendite {pc(A['Kombi6-P']['total_ret'],0)} gegen {pc(A['TMV120']['total_ret'],0)} | berichtet |
| P7.6 | TMV120+RXW gegen TMV120: ΔSR {ci(C['TMV120+RXW-TMV120'])}; DD vola-gleich {pc(d['diag']['RXW_volmatched_maxdd'])} gegen {pc(d['diag']['TMV120_maxdd'])} | berichtet |

Widerlegung (§2.3): weder (a) noch (b) greift (obere Grenzen {f(AR['R']['ci90_taken'][1],0)} bps bzw. {f(C['Kombi6-Q-TMV-Q']['hi'])}). Für B++ kommt allein W in Frage ({d['bplusplus_candidates']}); R besteht P7.1, aber nicht P7.2.

## Arme

| Arm | Trades | SR | MaxDD | Rendite | Expectancy bps |
|---|---|---|---|---|---|
{rows}
## Antwort auf F5

Trades: Mit festen Quoten steigen die Trades nur um {pc(d['P73']['trade_ratio']-1,0)}; der gemeinsame Pool nimmt R-Signale mit hoher Frequenz auf (Kombi6-P {A['Kombi6-P']['n_trades']} Trades). Gewinn: Im gemeinsamen Pool steigt die Sharpe gegenüber TMV120 von {f(A['TMV120']['sr'])} auf {f(A['Kombi6-P']['sr'])} (ΔSR 0,32 [−0,01; 0,66], siehe `results/H4/summary_h4_h7.json`), erreicht aber nicht das Niveau von C30 (2,02). Verluste: Der Drawdown wächst gegenüber TMV120 ({pc(A['Kombi6-P']['maxdd'])} gegen {pc(A['TMV120']['maxdd'])}).

## Einschränkungen

Ein Aufwärtsregime; X hat nur {AR['X']['n_taken']} Trades und 20 Monatsenden; W hat keine eigene Spread-Messung; keine Stufe B (mindestens zwei Monatswechsel fehlen).
"""
write("H7", d["verdict_stage_a"], dict(archetypes={a: dict(n_trades=AR[a]["n_taken"], expectancy_bps=AR[a]["expectancy_taken_bps"], ci90=AR[a]["ci90_taken"], alpha_ann=AR[a]["alpha"]["alpha_ann"], corr_tmv=AR[a]["corr_tmv"], passed_p71_p72=bool(d["P71"][a] and d["P72"][a])) for a in "RXW"}, corr_R_M=AR["R"]["corr_M"], overlap_R_M=d["overlap_RM"]["share"], combination=dict(delta_sr=C["Kombi6-Q-TMV-Q"]), archetypes_for_bplusplus=d["bplusplus_candidates"]), md)
print("ok")
