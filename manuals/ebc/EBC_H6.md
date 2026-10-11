---
ebc_prompt_id: EBC-H6
titel: "Verlustbudget in drei Ebenen: Katastrophen-Stop auf die Tagesachse skalieren, Risikobudget je Trade, Volatilitätssteuerung neuer Positionen"
version: 1.0.0
erstellt_am: 2026-10-11
quelle_repository: https://github.com/philibertschlutzki/etoro_nautilus
quelle_commit: 40671219c3e007ea268c5d48ff5573cafaef88a7
beantwortet_fragen: [F5, F4]
ziele: {gewinn: "+", verluste: "++", trades: "Nebenbedingung"}
konto_modus: nur_demo
stufen: [S0_setup, SA_historisch, SB_forward_shadow, SC_demo_mechanik]
mindestdauer: {SA: "Rechenzeit, typisch < 1 Tag", SB: ">= 30 Handelstage", SC: "1 Handelstag, nach Freigabe"}
ergebnisordner: results/H6/
primaere_endpunkte: [P6.1, P6.2]
seed: 20261011
abhaengig_von: "EBC-H1 §4–§7 (Archetypen, Vergleichszeitraum), EBC-H3 §7.1 (eintragsgepaarte Bewertung), Kostentabelle aus EBC-H4 §5"
---

# EBC-H6 — Verlustbudget: Katastrophen-Stop, Risikobudget, Volatilitätssteuerung

## 0. Auftrag

Du prüfst die vorregistrierte Hypothese H6 gegen reale eToro-Tagesdaten und, nach Freigabe, gegen die reale Stop-Annahme des eToro-Demo-/Paper-Kontos. Ergebnis: Verdikt `BESTAETIGT | WIDERLEGT | UNENTSCHIEDEN` mit allen Messwerten in `results/H6/`.

Beantwortete Fragen:
- **F5** — Gewinne maximieren, Verluste reduzieren, Trades erhöhen. Hier: Verluste reduzieren, ohne Gewinn und Trade-Zahl zu opfern.
- **F4** (Teil) — Fehlende Features: achsengerechter Katastrophen-Stop, Positionsgrösse nach Risiko statt nach Notional, Volatilitätssteuerung des Portfolios.

## 1. Harte Regeln

1. **Nur Demo/Paper** wie EBC-H4 Regel 1 (`automation/account_guard.py`, `GET /api/v1/me` vor jeder Order; Abweichung ⇒ Abbruch).
2. **Konto-Isolation** wie EBC-H1 Regel 2.
3. **Produktion unberührt.** Branch `ebc/h6`; `strategy_defaults.json` (`_disaster_stop`) und `automation/disaster_stop.py` nicht ändern — Varianten nur als Option der Simulation (§6).
4. **Vorregistrierung** in `results/H6/preregistration.json` vor jeder Auswertung (Ebenen-Parameter aus §6, Kostentabelle, Seeds, Datumsgrenzen, SHA-256 dieser Datei).
5. **Keine Parametersuche.** Je Ebene genau die Stufen aus §6; Archetypen, Trailing-Stop, Zeitstopps wie B+.
6. **Stufe C nur nach Freigabe**, ≤ 20 USD je Order, `Leverage = 1`, jede Testposition sofort nach dem Lesen des gespeicherten Stop-Niveaus schliessen.
7. Rate-Limits ≤ 1 Request/s; Keys nie loggen. `manuals/ebc/EBC_H1.md`–`EBC_H5.md` und `results/H1`–`results/H5` nicht ändern.

## 2. Hypothese

### 2.1 Behauptung (H6)

Die Hälfte der Bruttoverluste von B+ ist kein Marktrisiko, sondern ein Parametrierungsfehler aus der Stundenachse: Der Katastrophen-Stop `clamp(3 · Stopdistanz, 2 %, 15 %)` liegt auf Tageskerzen fast immer am 15-%-Deckel, damit **näher** am Einstieg als der Trailing-Stop, und wird **intrabar** ausgelöst. Er wirkt als enger Primär-Stop mit hoher Whipsaw-Quote, nicht als Schutz gegen Katastrophen. Drei Ebenen senken die Verluste, ohne Gewinn und Trade-Zahl zu opfern:

- **L1 Katastrophen-Stop achsengerecht (DS1):** Deckel 45 % statt 15 %. Der Trailing-Stop auf Schlusskurs wird wieder der bindende Stop; der Broker-Stop schützt nur noch gegen Gaps und Bot-Ausfall.
- **L2 Risikobudget je Trade (RB):** Notional umgekehrt proportional zur ATR, damit ein Stop auf einem volatilen Titel nicht ein Mehrfaches eines ruhigen kostet.
- **L3 Volatilitätssteuerung (VT):** Neue Positionen werden mit dem Verhältnis von Referenz- zu aktueller Index-Volatilität skaliert; in Stressphasen kommt weniger neues Risiko hinzu, die Zahl der Trades bleibt.

**H0:** Der 15-%-Deckel schützt: Die gestoppten Trades wären weiter gefallen, ein weiterer Stop erhöht die Verluste; RB und VT senken die Rendite stärker als das Risiko.

### 2.2 Vorhersagen (vorregistriert)

| ID | Vorhersage | Messgrösse | Schwelle | Rolle |
|---|---|---|---|---|
| P6.1 | DS1 macht den Katastrophen-Stop wieder zum Katastrophen-Stop, ohne Expectancy zu kosten | Anteil der Katastrophen-Stop-Exits an den Bruttoverlusten im Arm L1; eintragsgepaarte ΔExpectancy (DS1 − DS0) über alle Signale (§7.1) | Anteil ≤ 25 % **und** ΔExpectancy-Punktschätzer > 0 mit unterer 90-%-KI-Grenze > −5 bps | primär |
| P6.2 | L1 + L2 + L3 senken Drawdown und Tail-Verluste bei gleicher Trade-Zahl | Arm L123 gegen C30, alle Risikogrössen vola-gleich (§7.4): DD, CVaR5 der Tagesrenditen, ΔSR, Trades | DD ≤ 0,75 × DD(C30) **und** Betrag CVaR5 ≤ 0,80 × **und** untere 90-%-KI-Grenze von ΔSR > −0,15 **und** Trades ≥ 0,9 × | primär |
| P6.3 | Jede Ebene wirkt in die erwartete Richtung | L1, L2, L3 einzeln gegen C30: ΔSR, ΔExpectancy, DD und CVaR5 vola-gleich | berichten; Erwartung L1: Expectancy ↑; L2, L3: DD ↓ | sekundär |
| P6.4 | VT schützt in Stressphasen | mittlere Tagesrendite (vola-gleich) in Regime R4 und an den Top-5-%-Index-Tagen, L3 gegen C30 | Verlust je Tag um ≥ 30 % kleiner | sekundär |
| P6.5 | DS1 ist beim Broker setzbar | Stufe C: grösste akzeptierte `StopLossRate`-Distanz je Klasse bei `Leverage = 1` | ≥ 45 % für K1; sonst gilt der grösste akzeptierte Wert als Live-Deckel | sekundär |
| P6.6 | Gleiche Richtung bei mehr Kapazität | P6.1 und P6.2 bei C120 (120 × 0,5 %) | berichten | sekundär |

### 2.3 Widerlegung

H6 ist **widerlegt**, wenn (a) die eintragsgepaarte ΔExpectancy (DS1 − DS0) eine obere 90-%-KI-Grenze < 0 hat (der weite Deckel kostet klar) **oder** (b) ΔSR(L123 − C30) eine obere 90-%-KI-Grenze < 0 hat **und** der vola-gleiche DD von L123 nicht kleiner ist als der von C30. Sonst, bei unerfüllten primären Vorhersagen: `UNENTSCHIEDEN`.

### 2.4 Begründung aus dem Repository (Stand `4067121`)

Reproduzierbar mit `python experiments/ebc/evidence_h4_h8.py` (`results/evidence_h4_h8.json`, Abschnitte `verluste`, `katastrophen_stop`, `katastrophen_stop_mechanik`, `tagesrenditen`).

- **V1 Verlustzerlegung B+.** Bruttoverluste −88,3 pp Equity bei Netto +64,7 pp. 142 Katastrophen-Stop-Exits (12,4 % der Trades) tragen −44,2 pp = 50,1 % der Bruttoverluste; Median je Exit −1506 bps (= 15-%-Deckel plus Kosten); Median-Haltedauer M 3, T 6, V 8 Kalendertage. Grösste Posten: T-Katastrophen-Stop −22,4 pp (25,4 %), T-Trailing-Stop-Verlierer −15,5 pp (17,6 %), M-Katastrophen-Stop −11,6 pp (13,2 %), M-Zeitstopp-Verlierer −11,6 pp (13,1 %), V-Katastrophen-Stop −10,2 pp (11,5 %).
- **V2 Vertrag verletzt.** Der Katastrophen-Stop ist laut `automation/disaster_stop.py` (Modul-Docstring) ein weiter Broker-Stop gegen Ausfälle, der „im Normalbetrieb NIE binden soll"; die Invariante `check_disaster_stop_non_binding` (`automation/optimizer/invariants.py:643`) verlangt einen Anteil der `DISASTER_STOP`-Exits ≤ 1 %, häufigere Treffer heissen dort: die Klemmen verändern die Strategie. B+ hat 12,4 % Katastrophen-Stop-Exits — das Zwölffache.
- **V2a Mechanik.** Katastrophen-Stop = `clamp(k_disaster · Stopdistanz, 2 %, 15 %)` mit `k_disaster = 3` (`automation/config/strategy_defaults.json` `_disaster_stop`, dort als Default „für 1h-Candle-Daten" beschrieben; `automation/disaster_stop.py:82–105`; Order-Tag in `automation/strategies/hourly_strategy_base.py:1427–1442`; Simulation `experiments/ebc/sim.py:12` und `:149–151`). Auf der Tagesachse ist die Stopdistanz k · ATR20 der Tageskerzen: Median-ATR20 der B+-Trades 5,9 % ⇒ 3 · 3 · 5,9 % ≈ 53 % ⇒ gedeckelt auf 15 %. Der Deckel bindet bei 94 % der B+-Trades; die Distanz des Katastrophen-Stops beträgt im Median 2,5 ATR (bei den Katastrophen-Stop-Exits 1,95 ATR) gegenüber 3 ATR (T, M) bzw. 2,5 ATR (V) beim Trailing-Stop. Er wird intrabar geprüft (`sim.walk`), der Trailing-Stop nur auf dem Schlusskurs mit Ausführung zum nächsten Open.
- **V3 Whipsaw.** EBC-H3, Variante S0P0, Klasse K1: 48 % (T), 58 % (M) und 55 % (V) der Katastrophen-Stop-Exits schliessen am selben Tag wieder jenseits des Stop-Niveaus (in S0 wird nur der Katastrophen-Stop intrabar ausgeführt; abgeleitet aus `results/H3/stage_a/stop_summary.csv` und `exits_paired.parquet`).
- **V4 Volatilität konzentriert Stops, nicht Verluste je Ertrag.** Quote der Katastrophen-Stop-Exits je ATR-Quintil der B+-Trades: 2 % im untersten (Median-ATR20 3,0 %) bis 32 % im obersten Quintil (9,7 %). Gleichzeitig liefern das mittlere und das oberste Quintil den grössten Netto-Beitrag (+23,2 pp bzw. +15,8 pp, unterstes +3,6 pp): volatile Titel nicht ausschliessen, sondern nach Risiko gewichten.
- **V5 Tail-Tage.** Die schlechtesten 5 % der Tage summieren sich auf −57,8 pp, alle Tage auf +63,1 pp; MaxDD −15,4 % von 2025-01-03 bis 2025-04-09. SR von B+ in Regime R4 −2,53, an den 5 % grössten Index-Tagen −2,95 (`results/H1/report.md`).
- **V6 Sizing in H2.** Risikoparität gegen gleiches Notional: SR 1,55 gegen 1,39, MaxDD −15,8 % gegen −20,4 % (nicht signifikant, H2-Portfolio mit Klassen-Haltedauern; `results/H2/stage_a/sizing.csv`).
- **V7 Betrieb.** Der Paper-Bot lief am 2026-10-11 ab etwa 13:15 UTC nicht (`logs/paper_reports/paper_report_latest.md`). Der Broker-Stop ist bei Ausfällen der einzige Schutz und muss bleiben — als Katastrophen-Stop, nicht als Primär-Stop.

## 3. Schritt 0 — Setup

Wie EBC-H4 §3 (Branch `ebc/h6`, Preflight mit Vorfahren-Prüfung `4067121`, Paritätstest C30 = B+ mit H1-Rückfallkosten, `mkdir -p results/H6/stage_a results/H6/stage_b results/H6/stage_c`).

## 4. Universum und Daten

`U_breit` nach Kostenfilter q ≤ 0,10 (426 Instrumente), Stichtag der Kerzen nach EBC-H4 §4.3; `SPX500` aus `U_breit` als Referenz für L3 und die Regime.

## 5. Kostenmodell

Kostentabelle von EBC-H4 (`results/H4/costs_calibration.json`), sonst EBC-H4 §5 zuerst ausführen. Längere Haltedauern durch DS1 tragen die Finanzierung nach `costs.py`.

## 6. Ebenen und Arme

**L1 — Katastrophen-Stop.** DS0: `clamp(3 · k · ATR20 %, 2 %, 15 %)` (Status quo). DS1: Deckel 45 %, sonst gleich. DS∞: kein Katastrophen-Stop (nur Diagnose, nie empfehlbar). Umsetzung: `sim.disaster_pct(S, t, k, ds_max=DS_MAX)` und `walk` liest `opts["ds_max"]`; DS∞ über `opts["disaster"] = False`.

**L2 — Risikobudget je Trade.** EN: Notional f = 2 % der Equity (Status quo). RB: `f_i,t = clip(f · ATR̃_t / ATR20 %_i,t, 0,5 · f, 3 · f)`, ATR20 % des Instruments am Signaltag t, ATR̃_t = Median von ATR20 % über alle Instrumente des kostengefilterten Universums am Tag t (kein Blick nach vorn, keine Konstante). Ein Titel mit Median-Volatilität erhält 2 %, einer mit doppelter ATR 1 %.

**L3 — Volatilitätssteuerung neuer Positionen.** `s_t = clip(σ_ref,t / σ̂_t, 0,25, 1,5)`; σ̂_t = annualisierte EWMA-Volatilität (λ = 0,94) der SPX500-Tagesrenditen bis zum Schlusskurs des Signaltags; σ_ref,t = Median von σ̂ über die vorangehenden 252 Handelstage. Notional neuer Positionen = f_i,t · s_t; bestehende Positionen bleiben unverändert.

Portfolio: alle H6-Arme einschliesslich C30 mit derselben Maschine `portfolio.simulate_sized(trades, cal, slots=30, cap_total=0.60, cap_pos=3 · f, frac_key="frac")`, `frac` = Notional-Anteil nach L2 und L3. Für EN ohne VT weicht `simulate_sized` von `simulate` nur ab, wenn der Gesamtdeckel nach Equity-Verlusten vor dem 30. Platz greift; die Abweichung von C30 gegenüber B+ (Trades, SR) berichten.

| Arm | L1 | L2 | L3 | Zweck |
|---|---|---|---|---|
| **C30** (= B+ₘ) | DS0 | EN | – | Referenz |
| **L1** | DS1 | EN | – | P6.1, P6.3 |
| **L2** | DS0 | RB | – | P6.3 |
| **L3** | DS0 | EN | VT | P6.3, P6.4 |
| **L123** | DS1 | RB | VT | P6.2 (Kandidat) |
| **DS∞** | aus | EN | – | Diagnose |
| **C120**, **L1@C120**, **L123@C120** | wie oben bei 120 × 0,5 %, `cap_pos = 3 · 0,5 %` | | | P6.6 |

Alles Übrige wie B+: sequenziell, Seitenregel H1, Trailing-Stop auf Schlusskurs, Zeitstopps T 60 / M 5 / V 20, kein TTP, Vorrang nach q.

## 7. Stufe A — Historische Auswertung

### 7.1 Eintragsgepaarte Bewertung von L1 (P6.1)
Wie EBC-H3 §7.1: jedes Signal (steigende Flanke, `sim.independent`, Einstieg im Vergleichszeitraum 2024-12-23 bis 2026-10-12) unabhängig unter DS0, DS1 und DS∞ bis zum Ausstieg verfolgen. Je Signal die Netto-Differenz DS1 − DS0. Kennzahlen je Archetyp und Klasse: ΔExpectancy, CVaR5 der Trade-Renditen, Anteil der Signale, deren Ausgang sich ändert, Verteilung der neuen Exit-Gründe der früheren Katastrophen-Stop-Exits (Erholung bis Signal-/Zeit-Exit, Trailing-Stop, weiter Katastrophen-Stop), Verlust je früherem Katastrophen-Stop-Exit unter DS1, Gap-Verluste (Exit-Kurs jenseits des Stop-Niveaus).

### 7.2 Portfolio der Arme
Alle Arme aus §6 über den Vergleichszeitraum: SR, DD, CVaR5, Trades, Expectancy, Netto-Beitrag (pp) je Archetyp und Exit-Grund, Anteil der Katastrophen-Stop-Exits an den Bruttoverlusten, mittleres Exposure, Verteilung von f_i,t und s_t.

### 7.3 Verluste je ATR-Quintil
Für C30, L1, L2 und L123: Quote der Katastrophen-Stop-Exits, Netto-Beitrag und Bruttoverluste je ATR20-Quintil der genommenen Trades (gleiche Quintilsgrenzen wie C30).

### 7.4 Vola-gleiche Risikogrössen
Für P6.2–P6.4 die Tagesrenditen jedes Arms auf die annualisierte Vola von C30 skalieren (`r' = r · σ_C30 / σ_Arm`, σ über den ganzen Vergleichszeitraum), dann DD, CVaR5 und Regime-Mittel berechnen. So zählt nur die Form des Risikos, nicht ein kleineres mittleres Exposure.

### 7.5 Szenario-Matrix
Regime R1–R4 und Top-5-%-Index-Tage (`common.regime_array`) für alle Arme; zusätzlich der Zeitraum des MaxDD von C30 (2025-01-03 bis 2025-04-09) als Ereignisfenster.

## 8. Stufe B — Forward-Shadow (≥ 30 Handelstage)

Über `experiments/ebc/stage_b_multi.py` (EBC-H4 §8): Arme `C30`, `L1`, `L123`; Ausgabe `results/H6/stage_b/forward_daily_returns.csv`, wöchentlicher Zwischenbericht ohne Regeländerung. Stufe B schwächt nur ab: hat ΔSR_forward(L123 − C30) eine obere 90-%-KI-Grenze < −0,50, wird ein `BESTAETIGT` zu `UNENTSCHIEDEN`.

## 9. Stufe C — Stop-Distanz beim Broker (nach Freigabe)

- 10 K1-Instrumente (5 US-Aktien, 3 Nicht-US-Aktien, 2 ETFs) und je 2 aus K2, K3, K4, ohne offene Positionen und, solange der Inkubations-Bot im selben Konto läuft, nicht in `automation/config/instrument_map.json` (EBC-H4 §9), jeweils in ihrer Handelszeit.
- Je Instrument nacheinander drei Market-Orders über 20 USD (`POST {REST_BASE}/market-open-orders/by-amount`, `IsBuy: true`, `Leverage: 1`) mit `StopLossRate` im Abstand 45 %, 30 % und 20 % unter dem Ask. Status über `GET /api/v1/trading/info/<env>/orders/{orderId}`; das tatsächlich gespeicherte Stop-Niveau aus `GET /api/v1/trading/info/<env>/pnl` lesen; Position sofort schliessen (`POST {REST_BASE}/market-close-orders/positions/{positionId}`, `{"InstrumentId": <id>, "UnitsToDeduct": null}`).
- Ausgabe `stage_c/sl_distance.csv`: `instrument, klasse, requested_distance, status, error_code, stored_stop_rate, stored_distance`. Ergebnis je Klasse: grösste akzeptierte Distanz und ob eToro das Niveau verändert.

## 10. Metriken (Definitionen)

Wie EBC-H4 §10. Zusätzlich: Anteil der Katastrophen-Stop-Exits an den Bruttoverlusten = Σ Netto-bps der Exits mit Grund `DISASTER_STOP` / Σ Netto-bps aller Verlust-Trades; Whipsaw wie EBC-H3 §7.1; Gap-Verlust = seitenbereinigte Differenz zwischen Stop-Niveau und Exit-Kurs in bps auf den Einstieg.

## 11. Statistik und Entscheidungsregel

- Trade-Ebene: Cluster-Bootstrap nach Einstiegsmonat (5000, Seed 20261011, 90 %) für gepaarte Differenzen.
- Portfolio-Ebene: `portfolio.delta_sharpe_ci` (gepaart, stationär, 5000, Seed 20261011, 90 %). DD und CVaR5 als Punktschätzer der vola-gleichen Reihen.
- Stufe C schwächt nur ab: akzeptiert eToro für K1 keine Distanz ≥ 30 %, wird P6.1 als für die Live-Umsetzung nicht erfüllt vermerkt (Stufe-A-Ergebnis bleibt bestehen, Empfehlung auf die grösste akzeptierte Distanz begrenzt).

| Verdikt | Bedingung |
|---|---|
| `BESTAETIGT` | P6.1 und P6.2 erfüllt, Stufe B widerspricht nicht |
| `WIDERLEGT` | Kriterium aus §2.3 erfüllt |
| `UNENTSCHIEDEN` | sonst; nenne die fehlende Evidenz |

## 12. Ausgaben

```
results/H6/
  preregistration.json   preflight.json
  stage_a/ ds_paired.parquet   (signal_id, archetyp, klasse, variante, rendite_bps, exit_grund, haltedauer)
           portfolio_daily_returns.csv   arms.csv   loss_decomposition.csv   atr_quintiles.csv
           sizing_scale.csv   regimes.csv
  stage_b/ forward_daily_returns.csv   weekly_<KW>.md
  stage_c/ sl_distance.csv
  metrics.json   verdict.json   report.md
```

`verdict.json` mit denselben Feldern wie EBC-H1 (`hypothesis: "H6"`), zusätzlich:

```json
{
  "ds": {"share_gross_losses": {"DS0": 0.0, "DS1": 0.0}, "delta_expectancy_bps": 0.0, "ci90": [0.0, 0.0],
         "former_ds_exits_new_reason": {"SIGNAL": 0, "TIME_BOX": 0, "TRAILING_STOP": 0, "DISASTER_STOP": 0}},
  "arms": {"C30": {"sr": 0.0, "maxdd_volmatched": 0.0, "cvar5_volmatched": 0.0, "n_trades": 0},
           "L1": {}, "L2": {}, "L3": {}, "L123": {}, "DSinf": {}, "C120": {}, "L1@C120": {}, "L123@C120": {}},
  "stress": {"R4_mean_daily_bps": {"C30": 0.0, "L3": 0.0}, "top5pct_index_days_mean_bps": {"C30": 0.0, "L3": 0.0}},
  "broker_sl": {"max_accepted_distance": {"K1": 0.0, "K2": 0.0, "K3": 0.0, "K4": 0.0}, "status": "OK|BLOCKIERT|NICHT_FREIGEGEBEN"}
}
```

`report.md` (Deutsch, ≤ 3 Seiten): Verdikt, Vorhersagetabelle, Verlustzerlegung vorher/nachher, Ebenen-Tabelle, Ergebnis der Broker-Prüfung, Antwort auf F5 in drei Sätzen.

## 13. Folgen je Verdikt

- **BESTAETIGT:** Issues: (1) `disaster_stop_max_pct` je Achse: Tagesachsen-Archetypen 0,45 (bzw. die grösste von eToro akzeptierte Distanz aus §9), Stundenachse unverändert; (2) Positionsgrösse nach Risikobudget in `automation/config/ebc_archetypes.json` (`sizing`), Umsetzung im Allocator über `account_view`; (3) Volatilitätsskalierung neuer Positionen als Allocator-Option; (4) Baustein von B++ in EBC-H8.
- **WIDERLEGT:** (a) ⇒ der 15-%-Deckel schützt; stattdessen prüfen, ob ein Stop auf Schlusskurs-Basis mit kleinerem k die Whipsaws vermeidet. (b) ⇒ RB/VT verwerfen; nur L1 weiterverfolgen, falls P6.1 erfüllt.
- **UNENTSCHIEDEN:** Mit `U_breit` ∪ `U_neu` (EBC-H4) wiederholen; Stufe B verlängern.

## 14. Ablauf-Checkliste

- [ ] Setup, Preflight, Paritätstest C30 = B+
- [ ] `sim.py` um `ds_max` erweitert; Abweichung C30 (`simulate_sized`) gegenüber B+ (`simulate`) berichtet
- [ ] `preregistration.json` geschrieben (vor jeder Auswertung)
- [ ] §7.1 gepaarte DS-Bewertung, §7.2 Arme, §7.3 ATR-Quintile, §7.4 vola-gleiche Risikogrössen, §7.5 Regime
- [ ] Stufe B gestartet; Stufe C nach Freigabe (oder `NICHT_FREIGEGEBEN`/`BLOCKIERT`)
- [ ] `verdict.json` und `report.md` geschrieben
