"""Schreibt results/<H>/preregistration.json (vor der Auswertung) mit SHA-256 der Protokolldokumente, Universum, Seeds, Fenstern."""
import datetime as dt, hashlib, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np, common, universe, costs, portfolio as pf

def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

H = sys.argv[1]
rows, ct, series, cal = common.setup()
first_day = {r["id"]: int(series[r["id"]].ts[0] // pf.DAY_NS) for r in rows}
HIST_NEED = 867
g_start = next(g for g in range(cal.n) if any(cal.days[g] - first_day[i] >= HIST_NEED for i in first_day))
last = dt.date(1970, 1, 1) + dt.timedelta(days=cal.days[-1])
start = dt.date(1970, 1, 1) + dt.timedelta(days=cal.days[g_start])
common_doc = dict(
    hypothesis=H, protocol_doc=f"manuals/ebc/EBC_{H}.md", protocol_doc_sha256=sha(f"manuals/ebc/EBC_{H}.md"),
    protocol_docs_sha256={h: sha(f"manuals/ebc/EBC_{h}.md") for h in ("H1", "H2", "H3")},
    repo_commit="5f737ed495e70eab85b2dbbb7673eb7369c428ce", seed=20261011,
    written_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
    universe=dict(n=len(rows), sha256=sha("results/H1/universe.csv"), by_class={k: sum(1 for r in rows if r["klasse"] == k) for k in ("K1", "K2", "K3", "K4", "K5")}),
    data=dict(source="eToro API candles/desc/OneDay/1000 (direkt, CSV)", last_candle_utc=str(last), n_calendar_days=cal.n),
    comparison_period=dict(start=str(start), end=str(last), g_start=g_start, rule="erster Tag, an dem mind. ein Instrument 867 Kalendertage Historie hat"),
    cost_model=dict(rule="Spread/Slippage je Klasse aus backtest.json-Rueckfallwerten (Wochenende, Maerkte geschlossen: keine repraesentative Messung); Krypto 200 bps Kommission",
                    spread_bps=costs.FALLBACK_SPREAD, slippage_bps_per_side=costs.FALLBACK_SLIP, commission_rt_bps=costs.COMMISSION_RT,
                    financing_bps_per_day={"short": costs.FIN_SHORT, "long": costs.FIN_LONG}),
    results_viewed_before_registration="nein; ausser Route-R/V-Paritaet (Trade-Ebene) und Anzahl der Trades je Strategie",
    git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
)
dev = [
 "ACCOUNT: Der API-Key traegt nur Real-Scopes; es wird kein /demo/-Endpunkt genutzt. Stufe C (falls freigegeben) liefe ueber die per automation/account_guard.py abgesicherten /real/-Pfade des Paper-Kontos (realCid = ETORO_PAPER_ACCOUNT_CID, Pruefung per GET /api/v1/me vor jeder Order). In Stufe A werden keine Orders gesendet.",
 "ISOLATION: Konto-Isolation Variante (b) (disjunkte Instrumente zum laufenden Bot + Verwaltung je positionId); Stufe C bis zur Freigabe nicht gestartet.",
 "DATEN: Tageskerzen direkt per API gelesen und als CSV gespeichert (experiments/ebc/api.py, build_universe.py) statt historical_fetcher/Nautilus-Katalog; Kerzendefinition = UTC-Tag (fromDate), unfertige Kerze des Abrufdatums verworfen; oneday_definition-Messlauf nicht ausgefuehrt. Rundungsfehler (Low > Open etc.) durch Klemmen bereinigt.",
 "UNIVERSUM: Ziehung = Kandidaten je Schicht zufaellig mischen (Seed 20261011), der Reihe nach laden, die ersten n mit bestandener Qualitaetspruefung; PriceSource-Filter nur fuer Nicht-Aktien/ETF (bei Aktien traegt PriceSource den Boersennamen); US = ExchangeID 4/5; Krypto 'Top 30' = aelteste Listings (kleinste ID); nur 10 Spot-Rohstoffe und 16 Indizes verfuegbar; 13 Instrumente mit Letztkerze vor 2026-09-25 (veraltet/delistet) nach der Ziehung entfernt, nicht nachgezogen.",
 "KOSTEN: Stichprobe der Spreads am Wochenende (Maerkte ausser Krypto geschlossen) nicht repraesentativ -> Rueckfallwerte der backtest.json; Indizes mit DEFAULT (Spread 4, Slippage 2 bps). Slippage/Finanzierung/Kommission nicht aus Stufe C gemessen. Hintergrund-Sampler sammelt Spreads fuer spaetere Rekalibrierung.",
 "ROUTE R: eigener Harness (Anhang-A-Stil, experiments/ebc/route_r.py) statt backtest_runner.run_single_backtest_worker: EXTERNAL-Tagesbars (Bar-Zeitstempel = Folgekerze + 1 ns) plus O/L/H/C-Quote-Ticks, bar_execution=False, Spread im Tick 0 (Kosten explizit in costs.py), Sizing-Cap-Korrektur deaktiviert, Katastrophen-Stop-Fill auf das Stop-Niveau (min/max mit Open) korrigiert, Notional 2 % von 1 Mio. USD je Lauf (Rendite je Trade unabhaengig von der Groesse).",
 "ARCHETYPEN: ATR20 als Wilder-Rekursion (alpha = 1/20, mit der ersten True Range gesaet) in den Klassen und in Route V identisch; Stop-/Zeitstopp-Mechanik der Basisklasse (Anker = Entry-Fill, Ratsche mit trailing_min_atr_frac 0,5, Zeitstopp ueber Bar-Zaehler: Exit nach H Schlusskursen, Ausfuehrung zum naechsten Open).",
 "ARM A0/A1: Emulation statt 5-Tage-Neuauswahl-Laeufen: feste Default-Parameter (strategy_defaults.json + PAPER_TRAILING_TP, max_handelstage 5) => eine durchgehende Route-R-Laufzeit je (Instrument, Strategie); IS-Score = t-Statistik der Netto-Trade-Rendite (Exit-Tag im IS-Fenster, mind. 3 Trades), Gates im OOS-Fenster (>= 8 Trades, PF >= 1, Expectancy >= 0, t >= 0,5, PSR(Tagesreihe) >= 0,5), Platzvergabe nach OOS-PSR (40 Aktien/Index- plus 40 Rund-um-die-Uhr-Plaetze), Retire ungueltig gewordener Paare ohne offene Position (nie wieder gewaehlt). Max-Drawdown-Gate (0,3) und Deflated-Selection nicht abgebildet (PAPER_SELECTION_GATES: deflated_selection false). Turnier-Treue gegen echten backtest_runner --momentum-Lauf NICHT geprueft.",
 "ARM A2: nicht gerechnet (optional laut Protokoll; 72 Konfigurationen/Symbol nicht im Zeitrahmen).",
 "ARM A: Kapazitaet 30 gleichzeitige Positionen (wie B), Notional 2 % der Equity; A-Shorts auf Aktien/ETF/Krypto in A1 verworfen, in A0 beibehalten (Krypto-Shorts blockt die Basisklasse).",
 "KOSTENFILTER q = c_RT / ATR20_bps (Median der letzten 250 Tage, statisch zum Datenende); q <= 0,10.",
 "NULLMODELL (P1.5): zirkulaere Verschiebung der Trade-Positionsreihe um 20-230 Handelstage, Auswertung der Gates zum letzten Auswahltag; Replikate-Zahl siehe null_gate_calibration.json.",
 "ACHSENBRUCH (P1.4): Stundendaten nur API-Fenster (1000 Stundenkerzen), siehe axis_break.csv.",
 "STUFE B/C: nicht ausgefuehrt (Stufe B >= 30 Handelstage, Stufe C erst nach Freigabe); Folgearbeit.",
]
hyp = dict(H1=dict(
    primary=["P1.1: dSR = SR_netto(B+) - SR_netto(A1), Punkt > 0 und untere 90%-KI-Grenze > -0,20",
             "P1.2: PBO(A1) >= 0,50 und Spearman(IS-Score, OOS-Netto-Rendite) <= 0,10"],
    widerlegung="dSR <= -0,30 mit oberer KI-Grenze < 0, oder PBO(A1) < 0,30 und rho >= 0,25",
    windows=dict(is_days=473, embargo_days=10, oos_days=384, rebalance_trading_days=5, hist_need_days=867, slots=[40, 40], max_positions=30, notional=0.02),
    arms=["A0", "A1", "B", "Bplus (q<=0.10; Sensitivitaet 0.05/0.20)"], bootstrap=dict(B=5000, level=0.90, seed=20261011, method="stationary, optimal_block_length der Differenzreihe"),
    pbo=dict(groups=12, k=6, paths=924), regimes="R1-R4 laut §7.6 mit SPX500 als Referenz",
))
hyp["H2"] = dict(
    primary=["P2.1: argmax_h SR_netto(h) (Entdeckung) >= 5 in >= 3 der 4 Klassen K1-K4 und untere 90%-KI-Grenze von SR(argmax)-SR(1) (Bestaetigung) > 0 in diesen Klassen",
             "P2.2: Krypto-Expectancy: h<=5 obere 90%-KI-Grenze < 0 fuer alle 5 Signale; h in {10,20} Punktschaetzer < 0 fuer die Mehrheit",
             "P2.4: b in [0,30; 0,70], R2 >= 0,60, Zuwachs N_eff 50 Aktien -> alle Aktien < 15 %"],
    refutation="SR_netto(h) in >= 3 der Klassen K1-K4 monoton fallend in h, oder b < 0,15 oder R2 < 0,30, oder SR(EN)-SR(RP) mit unterer KI-Grenze > 0",
    horizons_archetypes=[1, 3, 5, 10, 20, 40, 60], horizons_diagnostic=[1, 2, 3, 5, 10, 20, 40],
    oos=dict(warmup_calendar_days=250, discovery_share=2 / 3, confirmation_share=1 / 3),
    sizing=dict(EN="2 % Equity", RP="Notional = 0,0010 * E / sigma20 (tgl.)", FR="0,005 * E / (k * ATR20%)", QK="1/4-Kelly je Klasse (252 Tage, mu/sigma2 der EN-Klassenrendite) / 30 Plaetze, gedeckelt auf 2 x RP",
                cap_per_position=0.10, cap_total=0.60),
    breadth=dict(N=[5, 10, 20, 50, 100, 200, "alle"], draws=200, schemes=["nur Aktien (K1)", "alle Klassen proportional"], weights="inverse Volatilitaet (gleiches Risiko)", period="Entdeckungsperiode"),
    power=dict(sr_reference=2.0, ratio_threshold=10, mc_paths_n1=10000, mc_paths_n80=3000, k_max=26),
    crypto=dict(signals=["S_MOM", "S_REV", "T", "M", "V"], horizons=[1, 3, 5, 10, 20], commission_rt_bps=200),
    bootstrap=dict(B=5000, level=0.90, seed=20261011),
)
hyp["H3"] = dict(
    primary=["P3.1: CVaR5(S1) um >= 20 % besser als S0 und untere 90%-KI-Grenze von Expectancy(S1)-Expectancy(S0) > -2 bps",
             "P3.2: Delta_M > 0, Delta_T < 0, Delta_V < 0 und Interaktion Delta_M - (Delta_T+Delta_V)/2 mit unterer 90%-KI-Grenze > 0",
             "P3.3: Expectancy_Z1,Aktien < 0 und (Expectancy_Z2 > 0 oder MaxDD-Reduktion >= 15 % bei dSR >= 0)"],
    refutation="S0 bei CVaR5 und Expectancy besser als S1 (untere KI-Grenzen > 0), oder TTP-Interaktion umgekehrt (obere KI-Grenze < 0), oder Z1 > Z2 (untere KI-Grenze > 0)",
    factors=dict(S=["S0", "S1", "S2"], P=["P0", "P1", "P2", "P3"], Z=["Z0", "Z1", "Z2", "Z3"], H=[1, 3, 5, 10, 20, 40]),
    pairing="Eintrags-gepaart: Signale = steigende Flanke der Entry-Bedingung, jedes Signal unabhaengig unter jeder Variante verfolgt",
    s1_semantics="Distanz D = k*ATR20 beim Einstieg fix; Long: Stop = hoechstes Hoch seit Einstieg - D, Trigger bei Low <= Stop (Pruefung vor dem Hoch-Update des Tages), Fill = min(Open, Stop)",
    p3_semantics="50 % bei +1R (R = k*ATR20 beim Einstieg), Limit-Fill zum Level bzw. Open bei Gap",
    shortability="Stufe C4 nicht gelaufen: alle nicht-Krypto-Instrumente als leerverkaufbar angenommen (Obergrenze fuer Z1/Z2)",
    bootstrap=dict(cluster="Kalendermonat der Einstiege", B=5000, level=0.90, seed=20261011),
)
dev_h2 = [
 "H2 §7.2: Klassenportfolio = Mittel der drei Archetypen-Teilportfolios (je Archetyp ein Event-Portfolio, 2 % Notional, <= 30 Positionen); Zeitstopp h wirkt als Obergrenze fuer alle drei Archetypen, die uebrigen Exits unveraendert.",
 "H2 §7.2 Diagnose: S_MOM/S_REV mit Open-to-Open-Renditen und gestaffelten Teilportfolios (Gewicht 1/h), Kosten je Positionsaenderung (c_RT/2 je Einheit), Fenster-Erwartung = Brutto minus anteilige Umschichtungskosten; h*_theo mit mittlerem IC ueber h.",
 "H2 §7.4 (P2.3): Stundenachse nur auf einer geschichteten Stichprobe mit Stundenhistorie (API-Fenster); siehe hourly_axis.csv.",
 "H2 §7.5: Teilportfolio = (Instrument, Archetyp); Gewichtung invers zur Volatilitaet der Entdeckungsperiode; log-Regression nur ueber Ziehungen mit SR_P > 0.",
 "H2 §7.6 QK: 1/4-Kelly je Klasse aus der trailing-252-Tage-EN-Klassenrendite, verteilt auf 30 Plaetze, gedeckelt auf 2xRP (die Vorschrift laesst die Verteilung offen).",
 "H2 §7.7: Monte-Carlo mit n_paths=10000 (n=1) bzw. 3000 (n=80, Speicher); empirisch: wöchentliche sequential_decision ueber die letzten 26 Wochen der Bestaetigungsperiode auf Tagesrenditen (Look = 5 Bars).",
 "H2 §8/§9: H2-Portfolio-Forward-Shadow und Stufe C nicht ausgefuehrt.",
]
dev_h3 = [
 "H3 §7.1: Stunden-Zusatzauswertung der 14 Repo-Strategien (S0 vs S1 auf Intrabar-Ticks) nur auf einer Stichprobe, siehe stop_summary_hourly.csv, falls vorhanden.",
 "H3 §7.3-7.4: siehe timebox.csv / leaks_real_data.csv; Leerverkaufbarkeit (C4) und Finanzierung (C3) nicht gemessen -> Annahmen: alle Nicht-Krypto-Instrumente leerverkaufbar, Finanzierung 0,79 bps/Tag.",
 "H3 S1/P3: Intrabar-Reihenfolge innerhalb einer Kerze unbekannt: Stop-Pruefung vor dem Hoch/Tief-Update des Tages (konservativ), Teilschliessung zum Zielniveau (Limit).",
 "H3 Stufen B/C (TSL-Feldtest, Stop-Slippage, Finanzierung, Leerverkaufbarkeit, Teilschliessung) nicht ausgefuehrt: P3.1 kann nach Protokoll nur durch Stufe C abgeschwaecht werden.",
]
if H == "H2": dev = dev + dev_h2
if H == "H3": dev = dev + dev_h3
doc = dict(common_doc, parameters=hyp.get(H, {}), protocol_deviations=dev)
os.makedirs(f"results/{H}", exist_ok=True)
if os.path.exists(f"results/{H}/preregistration.json"):
    sys.exit(f"{H}: preregistration.json existiert bereits - nicht ueberschreiben")
json.dump(doc, open(f"results/{H}/preregistration.json", "w"), indent=1, ensure_ascii=False)
print(H, "prereg geschrieben; Vergleichszeitraum", start, "->", last, "g_start", g_start)
