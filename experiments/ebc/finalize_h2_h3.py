"""verdict.json, metrics.json, report.md fuer H2 und H3 (+ report.md fuer H1)."""
import csv, hashlib, json, os, shutil, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np

def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()
def J(p): return json.load(open(p))
def f(x, n=2): return "n/a" if x is None else (f"{x:.{n}f}" if isinstance(x, (int, float)) else str(x))

for H in ("H2", "H3"):
    for fn in ("preflight.json", "universe.csv", "costs_calibration.json"):
        shutil.copy(f"results/H1/{fn}", f"results/{H}/{fn}")

# ======================= H2 =======================
S2 = "results/H2/stage_a"
p21, p22, p23 = J(f"{S2}/p21.json"), J(f"{S2}/p22.json"), J(f"{S2}/p23.json")
p24, p25, p26 = J(f"{S2}/breadth_regression.json"), J(f"{S2}/sizing_metrics.json"), J(f"{S2}/power_mc.json")
h2p = J(f"{S2}/h2_portfolio.json")
sig = list(csv.DictReader(open(f"{S2}/horizon_signals.csv")))
reg2 = list(csv.DictReader(open(f"{S2}/regimes.csv")))
prereg2 = J("results/H2/preregistration.json")
refuted_a = p21["n_monotone_decreasing"] >= 3
refuted_b = p24["refuted"]
refuted_c = p25["refuted"]
verdict2 = "WIDERLEGT" if (refuted_a or refuted_b or refuted_c) else ("BESTAETIGT" if (p21["passed"] and p22["passed"] and p24["passed"]) else "UNENTSCHIEDEN")
preds2 = [
 dict(id="P2.1", metric="argmax_h SR_netto (Entdeckung) je Klasse; SR(argmax)-SR(1) (Bestaetigung)", value={k: v["argmax_h"] for k, v in p21["classes"].items()},
      ci90={k: [v["delta_vs_h1"]["lo"], v["delta_vs_h1"]["hi"]] for k, v in p21["classes"].items()}, threshold="argmax>=5 in >=3 von 4 Klassen und KI-Untergrenze>0", passed=p21["passed"]),
 dict(id="P2.2", metric="Krypto Netto-Expectancy je Signal/h", value=p22, ci90=None, threshold="h<=5 obere KI-Grenze<0 fuer alle Signale; h in {10,20} Mehrheit<0", passed=p22["passed"]),
 dict(id="P2.3", metric="Stundenachse h=7 Bars Netto-Expectancy", value={k: {s: v["exp_net_bps"] for s, v in c.items()} for k, c in p23["cells"].items()}, ci90=None,
      threshold="<0 fuer FX/Rohstoffe/Krypto; Aktien nur positiv bei IC>=0,10", passed=p23["passed"]),
 dict(id="P2.4", metric="Steigung b in ln SR_P = a + b ln N_eff", value=p24["b"], ci90=p24["b_ci90"], threshold="b in [0,30;0,70], R2>=0,60, Zuwachs N_eff(50->alle Aktien) < 15 %", passed=p24["passed"],
      extra=dict(r2=p24["r2"], n_eff_growth=p24["n_eff_growth_50_to_all_stocks"])),
 dict(id="P2.5", metric="SR(RP)-SR(EN)", value=p25["delta_RP_minus_EN"]["point"], ci90=[p25["delta_RP_minus_EN"]["lo"], p25["delta_RP_minus_EN"]["hi"]],
      threshold=">=0,10, untere KI-Grenze>0, MaxDD(RP)<=MaxDD(EN)", passed=p25["passed"]),
 dict(id="P2.6", metric="Promotionswahrscheinlichkeit n=1 / n=80 bei SR 2,0", value=p26["ratio_n1_over_n80_promotion"]["sr2.0"], ci90=None, threshold=">=10", passed=p26["passed"]),
]
ic = {}
for r in sig:
    ic.setdefault(r["klasse"], {}).setdefault(r["signal"], {})[r["h"]] = float(r["ic"]) if r["ic"] not in ("", "nan") else None
meas = J("results/H1/costs_calibration.json")
v2 = dict(hypothesis="H2", prompt_version="1.0.0", repo_commit="5f737ed495e70eab85b2dbbb7673eb7369c428ce", preregistration_sha256=sha("results/H2/preregistration.json"),
    period={"stage_a_oos": [prereg2["comparison_period"]["start"], prereg2["comparison_period"]["end"]], "discovery_confirmation_split_note": "Entdeckung = erste 2/3 ab Kalendertag 250, Bestaetigung = letztes Drittel", "stage_b": None},
    universe={"n_total": prereg2["universe"]["n"], "n_after_cost_filter": 426, "by_class": {"K1": 353, "K2": 48, "K3": 10, "K4": 15, "K5": 0}}, predictions=preds2,
    stage_c={"status": "NICHT_GESTARTET (wartet auf Freigabe)", "n_fills": 0}, verdict=verdict2, verdict_scope="vorlaeufig, nur Stufe A",
    reasons=[f"Formal erfuellt: Widerlegungskriterium 2.3(b) (b = {p24['b']:.2f} < 0,15 bzw. R2 = {p24['r2']:.2f} < 0,30). Kriterien (a) und (c) nicht erfuellt.",
             "Einschraenkung zu P2.4: Die vorregistrierte Log-Regression laeuft nur ueber Ziehungen mit SR_P > 0; bei mittleren Teilportfolio-Sharpes um 0 (Entdeckungsperiode) erzeugt das eine Auswahlverzerrung (negatives b). Der Mittelwert SR_P steigt dennoch mit N (Aktien: 0,02 bei N=5 auf 0,25 bei N=984). Schwellen wurden nicht veraendert.",
             f"P2.1: argmax (Entdeckung) K1 {p21['classes']['K1']['argmax_h']}, K2 {p21['classes']['K2']['argmax_h']}, K3 {p21['classes']['K3']['argmax_h']}, K4 {p21['classes']['K4']['argmax_h']}; nur K1 >= 5, KI-Untergrenze jeweils <= 0.",
             "Stufe B (Forward-Shadow) und Stufe C (Kostenmessung) nicht ausgefuehrt."],
    protocol_deviations=prereg2["protocol_deviations"],
    optimal_holding_days_by_class={"K1_aktien_etf": p21["classes"]["K1"]["argmax_h"], "K2_fx": p21["classes"]["K2"]["argmax_h"], "K3_rohstoffe": p21["classes"]["K3"]["argmax_h"], "K4_indizes": p21["classes"]["K4"]["argmax_h"], "K5_krypto": None},
    measured_ic_by_class_and_h=ic,
    breadth={"b": p24["b"], "b_ci90": p24["b_ci90"], "r2": p24["r2"], "rho_bar_within": p24["rho_bar_within"], "rho_bar_between": p24["rho_bar_between"]},
    sizing={k: {"sr": v["sr"], "maxdd": v["maxdd"]} for k, v in p25["variants"].items()},
    measured_costs_bps={"spread_p50": "Rueckfallwerte (siehe costs_calibration.json)", "fees_rt": {"CRYPTO": 200.0}, "slippage_side": "Rueckfallwerte", "financing_day": {"long": "0,79 CFD-Klassen", "short": 0.79}})
json.dump(v2, open("results/H2/verdict.json", "w"), indent=1, ensure_ascii=False, default=float)
met2 = dict(P21=p21, P22=p22, P23=p23, P24=p24, P25={k: v for k, v in p25.items()}, P26=p26, H2_portfolio=h2p, regimes=reg2)
json.dump(met2, open("results/H2/metrics.json", "w"), indent=1, default=float)

tab = lambda rows_: "\n".join(rows_)
k1 = p21["classes"]
rep2 = f"""# EBC-H2 — Gewinnmathematik: Verdikt WIDERLEGT (formal, vorläufig: nur Stufe A)

**Verdikt:** WIDERLEGT nach dem vorregistrierten Kriterium 2.3(b) (Breiten-Steigung b = {p24['b']:.2f}, R² = {p24['r2']:.2f}); die primären Vorhersagen P2.1, P2.2 und P2.4 sind **nicht** erfüllt. Die P2.4-Messung ist durch die Regressionsvorschrift (nur Ziehungen mit SR_P > 0) verzerrt – siehe Einschränkungen. Stufe B/C wurden nicht ausgeführt.

| ID | Wert | 90-%-KI | Schwelle | erfüllt |
|---|---|---|---|---|
| P2.1 argmax_h (K1/K2/K3/K4) | {k1['K1']['argmax_h']}/{k1['K2']['argmax_h']}/{k1['K3']['argmax_h']}/{k1['K4']['argmax_h']} Tage; ΔSR vs h=1: {f(k1['K1']['delta_vs_h1']['point'])}/{f(k1['K2']['delta_vs_h1']['point'])}/{f(k1['K3']['delta_vs_h1']['point'])}/{f(k1['K4']['delta_vs_h1']['point'])} | untere Grenzen {f(k1['K1']['delta_vs_h1']['lo'])}/{f(k1['K2']['delta_vs_h1']['lo'])}/{f(k1['K3']['delta_vs_h1']['lo'])}/{f(k1['K4']['delta_vs_h1']['lo'])} | argmax ≥ 5 in ≥ 3 Klassen, KI-Untergrenze > 0 | nein |
| P2.2 Krypto | Obergrenze < 0 bei S_REV und M (h ≤ 5) sowie T/V bei h = 1; nicht bei S_MOM und T/V (h = 3, 5); h 10/20: keine Mehrheit < 0 | siehe crypto.csv | alle Signale | nein |
| P2.3 Stundenachse h=7 | FX/Rohstoffe/Krypto negativ; Aktien S_REV +6,1 bps bei IC 0,02 | – | s. Doku | nein |
| P2.4 Breite | b = {p24['b']:.2f}, R² = {p24['r2']:.2f}, N_eff-Zuwachs 50→alle Aktien {p24['n_eff_growth_50_to_all_stocks']*100:.0f} % | [{p24['b_ci90'][0]:.2f}; {p24['b_ci90'][1]:.2f}] | b ∈ [0,3;0,7], R² ≥ 0,6, < 15 % | nein (Kriterium 2.3(b) erfüllt) |
| P2.5 RP − EN | ΔSR {p25['delta_RP_minus_EN']['point']:.2f}; MaxDD {p25['variants']['RP']['maxdd']*100:.1f} % vs {p25['variants']['EN']['maxdd']*100:.1f} % | [{p25['delta_RP_minus_EN']['lo']:.2f}; {p25['delta_RP_minus_EN']['hi']:.2f}] | ≥ 0,10 und Untergrenze > 0 | nein |
| P2.6 Testmacht | Verhältnis n=1/n=80 bei SR 2,0: {p26['ratio_n1_over_n80_promotion']['sr2.0']:.1f}× | – | ≥ 10× | ja |

## Die vier Hebel (Stufe A, Netto, Fallback-Kosten)
- **Haltedauer:** Nur Aktien/ETF (K1) profitieren von längerer Haltedauer (Brutto-Expectancy 15 → 98 bps von h=1 → 60, Netto 9 → 92 bps); die SR-Verbesserung ist nicht signifikant. FX, Rohstoffe und Indizes sind in allen h netto negativ (Archetypen tragen dort nicht).
- **Breite:** Mittlere Korrelation der Teilportfolios 0,02–0,06 (innerhalb Klassen), 0,006 zwischen Klassen; N_eff wächst mit N weiter (+{p24['n_eff_growth_50_to_all_stocks']*100:.0f} % von 50 auf alle Aktien), das Sättigungs-Versprechen (< 15 %) trifft nicht zu.
- **Risikoparität:** SR {p25['variants']['RP']['sr']:.2f} vs {p25['variants']['EN']['sr']:.2f} (EN), MaxDD {p25['variants']['RP']['maxdd']*100:.1f} % vs {p25['variants']['EN']['maxdd']*100:.1f} %; nicht signifikant. FR {p25['variants']['FR']['sr']:.2f}, QK {p25['variants']['QK']['sr']:.2f}.
- **Portfolio-Test:** Monte-Carlo reproduziert C5 (n=1: 14,6 %, n=80: 0,99 % bei SR 2,0); das H2-Portfolio (Klassen-h, RP) erreicht auf den letzten 26 Wochen keine Promotion (kein `PROMOTE`), 1 von 1010 Teilportfolios.

## Optimale Haltedauer je Klasse (Entdeckungsperiode)
K1 Aktien/ETF: {k1['K1']['argmax_h']} Tage · K2 FX: {k1['K2']['argmax_h']} · K3 Rohstoffe: {k1['K3']['argmax_h']} · K4 Indizes: {k1['K4']['argmax_h']} · K5 Krypto: kein positives Netto-Optimum (200 bps Kommission).

## Antwort auf F2
Ja für Aktien/ETF: längere Haltedauern (10–60 Tage) und breite Teilportfolios bringen netto mehr, aber die Messung kann die Sättigungs- und Optimalitätsbehauptungen nicht bestätigen. Für FX, Rohstoffe, Indizes tragen die drei Archetypen netto nicht; Krypto ist bei kurzer Haltedauer strukturell netto negativ (S_REV, T, M, V h ≤ 5). Risikoparität senkt den MaxDD, ist statistisch aber nicht belegt.

## Einschränkungen
Kosten aus Rückfallwerten (Wochenende), Survivorship, Stichprobe mit starkem Aufwärtsregime in der Bestätigungsperiode (SR(K1) ≈ 1,7–2,0 dort vs 0,5–1,0 in der Entdeckung). P2.4: Log-Regression nur über SR_P > 0 (bei mittlerem SR_P ≈ 0 verzerrt); Details `breadth_draws.csv`.
"""
open("results/H2/report.md", "w").write(rep2)

# ======================= H3 =======================
S3 = "results/H3/stage_a"
m3 = J(f"{S3}/h3_metrics_partial.json")
p31, p32, p33, p35 = m3["P31"], m3["P32"], m3["P33"], m3["P35"]
leaks = J(f"{S3}/leaks_real_data.json")
prereg3 = J("results/H3/preregistration.json")
vbp, fcr, fcrb = leaks["VBP"], leaks["FCR"], leaks["FCR_blockiert"]
p34 = dict(vbp_short_share_original=vbp["original"]["short_share"], vbp_short_share_patched=vbp["gepatcht"]["short_share"], fcr_short_share_original_blocked=fcrb["original"]["short_share"],
           fcr_short_share_original=fcr["original"]["short_share"],
           vbp_std_original=vbp["original"]["std_bps"], vbp_std_patched=vbp["gepatcht"]["std_bps"], vbp_exp_original=vbp["original"]["exp_bps"], vbp_exp_patched=vbp["gepatcht"]["exp_bps"],
           fcr_blocked_exp_original=fcrb["original"]["exp_bps"], fcr_blocked_exp_patched=fcrb["gepatcht"]["exp_bps"])
p34["passed"] = bool(p34["vbp_short_share_original"] > 0 and (p34["vbp_std_patched"] < p34["vbp_std_original"] or p34["vbp_exp_patched"] > p34["vbp_exp_original"]))
refuted3 = bool(p31["refuted"] or p32["refuted"] or p33["refuted"])
verdict3 = "WIDERLEGT" if refuted3 else ("BESTAETIGT" if (p31["passed"] and p32["passed"] and p33["passed"]) else "UNENTSCHIEDEN")
dm = p32["delta_by_archetype"]["P1"]; it = p32["interaction"]["P1"]
preds3 = [
 dict(id="P3.1", metric="CVaR5-Verbesserung S1 vs S0; Delta-Expectancy S1-S0 (bps)", value={"cvar5_improvement": p31["cvar5_improvement"]["point"], "delta_expectancy_bps": p31["delta_expectancy"]["point"]},
      ci90={"cvar5_improvement": [p31["cvar5_improvement"]["lo"], p31["cvar5_improvement"]["hi"]], "delta_expectancy": [p31["delta_expectancy"]["lo"], p31["delta_expectancy"]["hi"]]},
      threshold="CVaR5 >= 20 % besser und untere KI-Grenze Delta-Expectancy > -2 bps", passed=p31["passed"]),
 dict(id="P3.2", metric="TTP P1: Delta je Archetyp (bps) und Interaktion", value={"M": dm["M"]["delta"], "T": dm["T"]["delta"], "V": dm["V"]["delta"], "interaction": it["point"]},
      ci90={"interaction": [it["lo"], it["hi"]], "M": [dm["M"]["lo"], dm["M"]["hi"]], "T": [dm["T"]["lo"], dm["T"]["hi"]], "V": [dm["V"]["lo"], dm["V"]["hi"]]},
      threshold="Delta_M > 0, Delta_T < 0, Delta_V < 0, Interaktion untere Grenze > 0", passed=p32["passed"]),
 dict(id="P3.3", metric="Short-Expectancy Aktien Z1/Z2 (bps); MaxDD-Reduktion Z2 vs Z0; dSR", value={"Z1": p33["exp_Z1_equity_short"], "Z2": p33["exp_Z2_equity_short"], "maxdd_reduction": p33["maxdd_reduction"], "dSR": p33["dSR_Z2_minus_Z0"]["point"]},
      ci90={"Z1": p33["exp_Z1_equity_ci90"], "Z2": p33["exp_Z2_equity_ci90"], "dSR": [p33["dSR_Z2_minus_Z0"]["lo"], p33["dSR_Z2_minus_Z0"]["hi"]]},
      threshold="Z1 < 0 und (Z2 > 0 oder MaxDD-Reduktion >= 15 % bei dSR >= 0)", passed=p33["passed"]),
 dict(id="P3.4", metric="Short-Anteil VBP original/gepatcht; Std Netto-Rendite", value=p34, ci90=None, threshold="Anteil > 0 reproduziert; Patch verbessert Expectancy oder senkt Varianz", passed=p34["passed"]),
 dict(id="P3.5", metric="optimale Zeitbox (Tage)", value=p35["optimal_timebox_days"], ci90=None, threshold="T >= 10, M <= 5", passed=p35["passed"]),
 dict(id="P3.6", metric="nativer TSL-Feldname", value=None, ci90=None, threshold="Demo-Test C1", passed=None, status="nicht gemessen (Stufe C gesperrt)"),
 dict(id="P3.7", metric="Finanzierung gemessen", value=None, ci90=None, threshold="Abweichung > 50 % von 0,79", passed=None, status="nicht gemessen (Stufe C gesperrt)"),
]
v3 = dict(hypothesis="H3", prompt_version="1.0.0", repo_commit="5f737ed495e70eab85b2dbbb7673eb7369c428ce", preregistration_sha256=sha("results/H3/preregistration.json"),
    period={"stage_a_oos": [prereg3["comparison_period"]["start"], prereg3["comparison_period"]["end"]], "stage_b": None},
    universe={"n_total": prereg3["universe"]["n"], "n_after_cost_filter": 426, "by_class": {"K1": 353, "K2": 48, "K3": 10, "K4": 15, "K5": 0}}, predictions=preds3,
    stage_c={"status": "NICHT_GESTARTET (wartet auf Freigabe)", "n_fills": 0}, verdict=verdict3, verdict_scope="vorlaeufig, nur Stufe A",
    reasons=[f"P3.1 nicht erfuellt: S1 verbessert CVaR5 nur um {p31['cvar5_improvement']['point']*100:.1f} % (< 20 %) und verschlechtert die Expectancy um {-p31['delta_expectancy']['point']:.0f} bps (KI [{p31['delta_expectancy']['lo']:.0f}; {p31['delta_expectancy']['hi']:.0f}]).",
             f"P3.2 nicht erfuellt: Delta_M = {dm['M']['delta']:.2f} bps (nicht > 0), Delta_T = {dm['T']['delta']:.0f}, Delta_V = {dm['V']['delta']:.0f}; die Interaktion ist aber positiv ({it['point']:.0f} bps, KI [{it['lo']:.0f}; {it['hi']:.0f}]).",
             f"P3.3 nicht erfuellt: Z1-Aktien-Short {p33['exp_Z1_equity_short']:.0f} bps, Z2 {p33['exp_Z2_equity_short']:.0f} bps (beide negativ); MaxDD-Reduktion {p33['maxdd_reduction']*100:.1f} %, aber dSR(Z2-Z0) {p33['dSR_Z2_minus_Z0']['point']:.2f}.",
             "Kein Widerlegungskriterium 2.3 erfuellt. Stufe C (TSL-Feld, Stop-Slippage, Finanzierung, Leerverkaufbarkeit, Teilschliessung) und Stufe B nicht ausgefuehrt."],
    protocol_deviations=prereg3["protocol_deviations"],
    tsl={"field_effective": None, "distance_fixed_in_price": None, "stop_slippage_bps_p50": None, "status": "nicht gemessen"},
    ttp_delta_bps={"T": dm["T"]["delta"], "M": dm["M"]["delta"], "V": dm["V"]["delta"], "interaction_ci90": [it["lo"], it["hi"]]},
    shorts={"expectancy_bps": {"Z1_equity": p33["exp_Z1_equity_short"], "Z2_equity": p33["exp_Z2_equity_short"]}, "maxdd_change": -p33["maxdd_reduction"], "shortable_share_equity": None},
    financing_bps_per_day="Annahme 0,79 (nicht gemessen)",
    leaks={"vbp_short_share_original": p34["vbp_short_share_original"], "vbp_short_share_patched": p34["vbp_short_share_patched"], "fcr_short_share_original": p34["fcr_short_share_original"],
           "fcr_short_share_original_blocked_entries": p34["fcr_short_share_original_blocked"]},
    optimal_timebox_days=p35["optimal_timebox_days"])
json.dump(v3, open("results/H3/verdict.json", "w"), indent=1, ensure_ascii=False, default=float)
json.dump(dict(P31=p31, P32=p32, P33=p33, P34=p34, P35=p35, short_layer=m3["short_layer"]), open("results/H3/metrics.json", "w"), indent=1, default=float)
zl = {z["variante"]: z for z in m3["short_layer"]}
rep3 = f"""# EBC-H3 — Exits und Shorts: Verdikt UNENTSCHIEDEN (vorläufig: nur Stufe A; Stufe C nicht gestartet)

**Verdikt:** UNENTSCHIEDEN. Die primären Vorhersagen P3.1, P3.2 und P3.3 sind nicht erfüllt; kein Widerlegungskriterium nach §2.3 greift. Die Mechanik-Tests (TSL-Feldname, Stop-Slippage, Finanzierung, Leerverkaufbarkeit, Teilschliessung) warten auf die Freigabe von Stufe C.

| ID | Wert | 90-%-KI | Schwelle | erfüllt |
|---|---|---|---|---|
| P3.1 | CVaR5 +{p31['cvar5_improvement']['point']*100:.1f} %; ΔExpectancy {p31['delta_expectancy']['point']:.1f} bps | [{p31['cvar5_improvement']['lo']*100:.1f}; {p31['cvar5_improvement']['hi']*100:.1f}] %; [{p31['delta_expectancy']['lo']:.1f}; {p31['delta_expectancy']['hi']:.1f}] | ≥ 20 % und Untergrenze > −2 bps | nein |
| P3.2 | ΔM {dm['M']['delta']:.2f}, ΔT {dm['T']['delta']:.1f}, ΔV {dm['V']['delta']:.1f} bps; Interaktion {it['point']:.1f} | [{it['lo']:.1f}; {it['hi']:.1f}] | ΔM > 0, ΔT < 0, ΔV < 0, Untergrenze > 0 | nein (ΔM ≈ 0) |
| P3.3 | Z1-Aktien-Short {p33['exp_Z1_equity_short']:.0f} bps, Z2 {p33['exp_Z2_equity_short']:.0f} bps; MaxDD −{p33['maxdd_reduction']*100:.1f} %, dSR {p33['dSR_Z2_minus_Z0']['point']:.2f} | dSR [{p33['dSR_Z2_minus_Z0']['lo']:.2f}; {p33['dSR_Z2_minus_Z0']['hi']:.2f}] | Z1<0 und (Z2>0 oder MaxDD −15 % bei dSR ≥ 0) | nein |
| P3.4 | VBP {p34['vbp_short_share_original']*100:.1f} % Shorts original → 0 % gepatcht; Std {p34['vbp_std_original']:.0f} → {p34['vbp_std_patched']:.0f} bps | – | Anteil > 0, Patch senkt Varianz/hebt Expectancy | ja |
| P3.5 | optimale Zeitbox T {p35['optimal_timebox_days']['T']}, M {p35['optimal_timebox_days']['M']}, V {p35['optimal_timebox_days']['V']} Tage | – | T ≥ 10, M ≤ 5 | ja |
| P3.6/P3.7 | nicht gemessen | – | Stufe C | – |

## Antwort auf F3 je Hebel
- **Trailing-Stop-Loss (S1, Distanz k·ATR vom Intrabar-Hoch):** verbessert die Verlust-Tails nur um {p31['cvar5_improvement']['point']*100:.0f} % (CVaR5), kostet aber {-p31['delta_expectancy']['point']:.0f} bps Expectancy je Signal (T/M/V über {p31['n_signals']} Signale), weil der enge, intrabar nachgezogene Stop Trends vorzeitig beendet. Die Kombination S2 (Schutzdistanz 1,5·D) liegt bei {p31['S2_vs_S0_expectancy']['point']:.0f} bps gegenüber S0. Ob eToro den Stop real nachzieht (Feld `IsTslEnabled` vs `IsTrailingStop`) ist ungemessen.
- **Trailing-Take-Profit (P1: 0,5/0,3 ATR):** T −{-dm['T']['delta']:.0f} bps, V {dm['V']['delta']:.0f} bps, M {dm['M']['delta']:.2f} bps (neutral); die Archetyp-Interaktion ist positiv und signifikant ({it['point']:.0f} bps). Konsequenz: TTP nicht auf Trend-/Ausbruchs-Archetypen anwenden; für Mean-Reversion neutral (kein Gewinn).
- **Short:** Aktien-Shorts sind unter Z1 ({p33['exp_Z1_equity_short']:.0f} bps) und im Abwärtsregime-Gate Z2 ({p33['exp_Z2_equity_short']:.0f} bps) netto negativ; Z2 senkt den MaxDD um {p33['maxdd_reduction']*100:.0f} %, aber auch die SR ({zl['Z2']['sr']:.2f} vs Z0 {zl['Z0']['sr']:.2f}). Z3 (Shorts nur FX/Rohstoffe/Indizes) SR {zl['Z3']['sr']:.2f}.
- **Short-Leaks (real):** VolatilityBreakoutPump ist zu {p34['vbp_short_share_original']*100:.0f} % Short (reale Stundendaten, 101 Instrumente), FlashCrashReversal bei blockiertem Entry zu {p34['fcr_short_share_original_blocked']*100:.0f} %; der Patch aus Anhang B entfernt beide (0 Shorts).
- **Zeitbox:** optimal T {p35['optimal_timebox_days']['T']} / M {p35['optimal_timebox_days']['M']} / V {p35['optimal_timebox_days']['V']} Tage.

## Einschränkungen
Annahmen: alle Nicht-Krypto-Instrumente leerverkaufbar (Obergrenze für Shorts), Finanzierung 0,79 bps/Tag, Kosten aus Rückfallwerten; Intrabar-Reihenfolge innerhalb einer Kerze unbekannt (Stop-Prüfung vor Hoch-Update, konservativ). Survivorship und Aufwärtsregime wie H1.
"""
open("results/H3/report.md", "w").write(rep3)
shutil.copy("/dev/null", "results/H3/stage_a/.keep")
print(verdict2, verdict3)
