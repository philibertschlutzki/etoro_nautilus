---
ebc_prompt_id: EBC-H2
titel: "Gewinnmathematik: Kosten-Haltedauer, effektive Breite, Risikoparität und ein einziger Portfolio-Test"
version: 1.0.0
erstellt_am: 2026-10-11
quelle_repository: https://github.com/philibertschlutzki/etoro_nautilus
quelle_commit: 5f737ed495e70eab85b2dbbb7673eb7369c428ce
beantwortet_fragen: [F2, F4]
konto_modus: nur_demo
stufen: [S0_setup, SA_historisch, SB_forward_shadow, SC_demo_kostenmessung]
mindestdauer: {SA: "Rechenzeit, typisch < 1 Tag", SB: ">= 30 Handelstage", SC: "parallel zu SB, >= 10 Wochenenden-Nächte je Klasse"}
ergebnisordner: results/H2/
primaere_endpunkte: [P2.1, P2.2, P2.4]
seed: 20261011
abhaengig_von: "EBC-H1 §4–§6 (Universum, Kosten, Archetypen) — wiederverwenden, falls vorhanden"
---

# EBC-H2 — Wie sich der Netto-Gewinn mathematisch maximieren lässt

## 0. Auftrag

Du prüfst die vorregistrierte Hypothese H2 gegen reale eToro-Daten, real gemessene Kosten und reale Demo-Ausführung. Ergebnis: Verdikt `BESTAETIGT | WIDERLEGT | UNENTSCHIEDEN` mit allen Messwerten in `results/H2/`.

Beantwortete Fragen:
- **F2** — Wie lassen sich die Gewinne mathematisch optimieren? Sollen möglichst einfache Strategien gegen möglichst viele Instrumente laufen?
- **F4** (Teil) — Fehlende Features: Haltedauer aus der Kostenquote, Risikoparität und Korrelationsbudget im Allocator, Kostenfilter, Portfolio-Forward-Test, Kostenkalibrierung aus echten Fills.

## 1. Harte Regeln

1. **Nur Demo.** Orders ausschliesslich über Demo-Endpunkte (`/api/v1/trading/execution/demo/...`) oder das von `automation/account_guard.py` verifizierte Paper-Konto; vor jeder Order `GET https://public-api.etoro.com/api/v1/me` prüfen, Abweichung ⇒ Abbruch.
2. **Konto-Isolation** wie EBC-H1 Regel 2: eigenes Demo-Konto, sonst disjunkte Instrumente und Verwaltung je `positionId`, sonst Stufe C `BLOCKIERT`. Die Paper-Inkubation des Repos übernimmt offene Positionen beim Start (Commit `d198415`).
3. **Produktion unberührt.** Arbeit nur im Experiment-Klon `ebc_h2_repo` (oder im Klon von EBC-H1 auf eigenem Branch `ebc/h2`).
4. **Vorregistrierung** in `results/H2/preregistration.json` vor jeder Auswertung (Parameter, Seeds, Datumsgrenzen, Aufteilung der OOS-Periode, SHA-256 dieser Datei). Schwellen werden nachträglich nie verändert.
5. Keine Parametersuche ausser den in §7 genannten Gittern (Haltedauer, Universumsgrösse, Sizing-Varianten).
6. Rate-Limits der eToro-API einhalten; Keys nie loggen.
7. Stufe C: ≤ 100 USD Notional je Order, ≤ 20 gleichzeitige H2-Positionen.

## 2. Hypothese

### 2.1 Behauptung (H2)

Der erzielbare Netto-Ertrag folgt näherungsweise dem Fundamentalgesetz mit Kostenabzug:

```
IR ≈ (IC − c/σ_h) · √(Trades_pro_Jahr · N_eff)
     σ_h = σ_1 · √h                      (Volatilität über die Haltedauer h)
     N_eff = N / (1 + (N − 1) · ρ̄)        (effektive Zahl unabhängiger Wetten)
```

Bei konstantem IC maximiert die Haltedauer `h* = 4c² / (IC² · σ_1²)` das IR; dort verbrauchen die Kosten genau die Hälfte der Bruttokante. Daraus folgt die Antwort auf F2: **Ja, möglichst einfache Strategien gegen möglichst viele Instrumente — aber nur (1) mit einer Haltedauer nahe h\* statt der heutigen Ein-Tages-Zeitbox, (2) nach effektiver statt nomineller Breite (zusätzliche Anlageklassen statt zusätzlicher Aktien), (3) mit Risikoparität statt gleichem Notional und (4) als ein einziges vorab festgelegtes Portfolio getestet statt als 80 Einzelpaare.**

**H0:** Kurze Haltedauern (≤ 1 Handelstag) sind netto mindestens so gut wie längere; die Portfolio-Sharpe skaliert mit √N statt √N_eff; gleiches Notional ist nicht schlechter als Risikoparität.

### 2.2 Vorhersagen (vorregistriert)

| ID | Vorhersage | Messgrösse | Schwelle | Rolle |
|---|---|---|---|---|
| P2.1 | Netto-Optimum der Haltedauer liegt bei mehreren Tagen | Archetypen-Portfolio T+M+V (B+-Universum) je Klasse: `argmax_h SR_netto(h)` über die maximale Haltedauer h ∈ {1, 3, 5, 10, 20, 40, 60} Handelstage (§7.2) | argmax ≥ 5 in ≥ 3 der 4 Klassen K1–K4 (EBC-H1 §4.1) **und** SR_netto(argmax) − SR_netto(1) mit unterer 90-%-KI-Grenze > 0 in diesen Klassen | primär |
| P2.2 | Krypto ist auf eToro bei kurzer Haltedauer strukturell netto negativ | Netto-Expectancy je Trade, K5, alle Signale aus §6 | h ≤ 5: obere 90-%-KI-Grenze < 0 für alle Signale; h ∈ {10, 20}: Punktschätzer < 0 für die Mehrheit der Signale | primär |
| P2.3 | Die Status-quo-Zeitbox (≤ 7 Stunden-Bars) ist für FX, Rohstoffe und Krypto netto negativ | Netto-Expectancy der Signale aus §7.4 bei h = 7 Stunden-Bars | < 0 für FX, Rohstoffe, Krypto; Aktien nur positiv, wenn gemessener IC(7) ≥ 0,10 | sekundär |
| P2.4 | Breite zählt effektiv, nicht nominell | Steigung b in `ln SR_P = a + b · ln N_eff` über Zufalls-Teiluniversen (§7.5) | b ∈ [0,30; 0,70] und R² ≥ 0,60; Zuwachs N_eff von 50 Aktien auf alle Aktien < 15 % | primär |
| P2.5 | Risikoparität schlägt gleiches Notional | SR(RP) − SR(EN), MaxDD (§7.6) | Differenz ≥ 0,10 mit unterer 90-%-KI-Grenze > 0 und MaxDD(RP) ≤ MaxDD(EN) | sekundär |
| P2.6 | Ein Portfolio-Test hat ein Vielfaches der Testmacht von 80 Paar-Tests | Entscheidungswahrscheinlichkeit (§7.7) | ≥ 10× bei gleicher Paar-Sharpe | sekundär |

### 2.3 Widerlegung

H2 ist **widerlegt**, wenn (a) SR_netto(h) des Archetypen-Portfolios in ≥ 3 der Klassen K1–K4 monoton mit h fällt (kürzeste Haltedauer am besten) **oder** (b) b < 0,15 oder R² < 0,30 **oder** (c) SR(EN) − SR(RP) eine untere 90-%-KI-Grenze > 0 hat. Sind primäre Vorhersagen unerfüllt, ohne dass ein Widerlegungskriterium greift: `UNENTSCHIEDEN`.

### 2.4 Begründung aus dem Repository (Stand `5f737ed`)

- **C1 Kostenmodell** (`automation/config/backtest.json`): Round Trip `c_RT = Spread + Kommission + 2 · Slippage` = Aktien 7 bps, FX 4 bps, Rohstoffe 11 bps, Krypto 230 bps (Kommission 200 bps, bestätigt durch den realen BTC-Fill am 2026-10-11: 2,00 USD auf 199,56 USD, Commit `d198415`). Short-Finanzierung pauschal 0,79 bps/Tag für alle Klassen.
- **C2 Zeitbox:** `MAX_BARS_IN_TRADE_HARD_CAP = ceil(max_handelstage · BARS_PER_TRADING_DAY) = 7`, abgeleitet aus der NYSE-Session — für alle Klassen (`automation/optimizer/_contracts.py:28, 53, 76, 88`). Für 24/7-Krypto und 24/5-FX heisst das: Ausstieg nach spätestens 7 Stunden.
- **C3 Kostenquote** (Rechnung, Annahmen σ_1 je Stunden-Bar: Aktien 30–70 bps, FX 6–12, Rohstoffe 15–30, Krypto 50–90; in §7.2/§7.4 zu messen): c/σ_h bei h = 7 Stunden-Bars: Aktien 0,04–0,09, FX 0,13–0,25, Rohstoffe 0,14–0,28, Krypto 0,97–1,74. Optimum h\* bei IC = 0,05: Aktien 2–12 Handelstage, FX 7–30, Rohstoffe 31–123, Krypto > 1 Jahr. Die heutige Zeitbox liegt ausser bei Aktien mit hohem IC weit unter h\*.
- **C4 Sizing und Breite:** Der Allocator vergibt gleiches Notional (10 % je Symbol, 60 % gesamt, `automation/momentum_ls_allocator.py:15–16`; Inkubation 2 % je Paar, `automation/momentum_ls_run.py:231–238`) ohne Volatilitäts- oder Korrelationsbezug. Fraktionales Kelly existiert nur als Funktion, die ausschliesslich Tests aufrufen (`automation/fractional_trading.py:51`). Roadmap B1, B2, E1 sind offen (`manuals/feature_roadmap.md`). Zahlenbeispiel N_eff bei ρ̄ = 0,15: 50 Aktien 5,99; 154 Aktien 6,43 (+7 %).
- **C5 Testmacht:** Die Paper-Inkubation testet bis zu 80 Paare parallel (40 + 40 Plätze, `automation/papertrading.py:231–235`); Bonferroni-Schwelle `1 − 0,05/(26 · 80) = 0,999976` (`automation/optimizer/sequential.py:35–38`, `automation/incubation.py:103–105`). Monte-Carlo mit `sequential.simulate_sequential` (Repo-Code, 26 Wochen à 35 Bars): Promotionswahrscheinlichkeit je Paar bei wahrer Sharpe 2,0 p. a. 0,95 %, bei 3,0 4,6 %. Ein einzelner vorab festgelegter Portfolio-Test (n = 1): Sharpe 2,0 ⇒ 14,6 %, 3,0 ⇒ 33,4 %, 4,0 ⇒ 59,5 %. Ein Portfolio aus N_eff = 9 unabhängigen Wetten mit je Sharpe 1,0 hat Sharpe 3,0.
- **C6 Nachweisbarkeit** (`deflation.min_detectable_sharpe`): Stundenachse, 60 Tage Holdout ⇒ 3,95 p. a.; Tagesachse 1 Jahr ⇒ 1,65; 3 Jahre ⇒ 0,95.

## 3. Schritt 0 — Setup und Preflight

Wie EBC-H1 §3 (Klon auf `5f737ed`, venv, `.env` nur Demo, `python -m automation.demo_order_check`, Preflight-Datei `results/H2/preflight.json`). Existieren Ergebnisse von EBC-H1, übernimmst du `results/H1/universe.csv` und `results/H1/costs_calibration.json` unverändert (SHA-256 in `preregistration.json` eintragen).

## 4. Universum und Daten

Universum `U_breit` und Datenregeln identisch zu EBC-H1 §4 (Seed 20261011; US-Aktien 150, Nicht-US-Aktien 50, ETFs 40, Indizes alle, FX ≤ 50, Rohstoffe ≤ 20, Krypto 30, plus alle 170 Instrumente aus `instrument_map.json`; ≥ 750 Tageskerzen). Zusätzlich für §7.4: alle Instrumente mit ≥ 60 Handelstagen Stundenhistorie (API-Fenster 1000 Kerzen, optional nur lesend kopierter Produktions-Katalog).

Endpunkte: Tageskerzen `GET https://public-api.etoro.com/api/v1/market-data/instruments/{etoro_id}/history/candles/desc/OneDay/1000`, Stundenkerzen `…/OneHour/1000`, Quotes `GET /api/v1/market-data/instruments/rates?instrumentIds=…`.

## 5. Kostenmodell

Wie EBC-H1 §5: Spread gemessen (Median/P75 aus ≥ 20 Quote-Stichproben je Instrument über ≥ 2 Handelstage), Kommission (Krypto 200 bps Round Trip, sonst 0 bis zur Messung), Slippage und Overnight-Finanzierung aus Stufe C, bis dahin die Werte aus `backtest.json`. Zusätzlich hier: **Finanzierung je Seite und Klasse** wird in Stufe C über ≥ 10 Positions-Nächte je Klasse inkl. ≥ 1 Wochenende gemessen (eToro belastet Wochenenden je Klasse an unterschiedlichen Wochentagen — empirisch aus den Gebührenfeldern ablesen, nicht annehmen).

## 6. Signale (fest, ohne Tuning)

- **S_MOM** Zeitreihen-Momentum: `s_t = sign(ln(C_t / C_{t−60}))`.
- **S_REV** Kurzfrist-Umkehr: `s_t = −sign(ln(C_t / C_{t−5}))`.
- **Archetypen T, M, V** exakt nach EBC-H1 §6.1 (Parameter L = 50, k = 3,0; θ = 10, H = 5; n_sq = 5, k = 2,5).
- Seitenregel wie EBC-H1: Aktien, ETFs, Krypto nur long (s = −1 ⇒ flach); FX, Rohstoffe, Indizes beidseitig.

## 7. Stufe A — Historische Experimente

### 7.1 Zeitachse und Ausführung
Tagesachse. Kerzen 1–250 Aufwärmphase, danach OOS-Periode bis zum Start von Stufe B. Die OOS-Periode wird vorregistriert in **Entdeckung** (erste 2/3) und **Bestätigung** (letztes 1/3) geteilt. Signal am Kerzenschluss t, Ausführung zum Eröffnungskurs t+1; Kosten nach §5, Finanzierung je gehaltenem Kalendertag nach Seite und Klasse.

### 7.2 Horizont — Kostenquote gegen Haltedauer (P2.1, P2.2)
**Primär (P2.1):** Archetypen T, M, V nach EBC-H1 §6.1 auf dem B+-Universum, maximale Haltedauer h ∈ {1, 3, 5, 10, 20, 40, 60} Handelstage (Zeitstopp; übrige Exit-Regeln unverändert, d. h. ein Archetyp kann früher aussteigen). Je Klasse K1–K4 das gleichgewichtete Archetypen-Portfolio (gleiches Risiko je Teilportfolio) und `SR_netto(h)`. Die Entdeckungsperiode bestimmt `argmax_h`; die Bestätigungsperiode prüft SR_netto(argmax) − SR_netto(1) mit gepaartem Bootstrap.

**Diagnose (Theorieabgleich, kein Kriterium):** Für S_MOM und S_REV und jede Haltedauer h ∈ {1, 2, 3, 5, 10, 20, 40}:
1. h gestaffelte Teilportfolios (Start an Tag t0 + j, j = 0 … h−1), jedes hält die Position aus `s` genau h Tage und wechselt dann; Gesamtportfolio = Mittel der Teilportfolios (äquivalent zu überlappenden Positionen mit Gewicht 1/h).
2. Je Klasse: `IC(h) = corr(s_t, r_{t+1→t+1+h} / (σ_{1,t} · √h))` (gepoolt über Instrumente und Tage, σ_1 = Std. der Tagesrendite über 60 Tage), Brutto- und Netto-Mittelwert je Trade, `SR_netto(h)` annualisiert.
3. `h*_theo = 4 c_RT² / (IC² · σ_1²)` mit den gemessenen Werten neben das empirische argmax stellen. Erwartung: S_MOM folgt der Theorie; S_REV hat wegen schnell zerfallendem IC ein kürzeres Optimum — das ist mit H2 vereinbar (IC ist dort nicht konstant).

Für Teilportfolios (Instrument × Archetyp) aus EBC-H1-Arm B+: liegen sie nicht vor, erzeugst du sie nach EBC-H1 §6.1 und §7.1 (Route R).

### 7.3 Krypto (P2.2)
Für alle Krypto-Instrumente: Netto-Expectancy je Trade und SR_netto für alle Signale und Haltedauern aus §7.2, mit 200 bps Round-Trip-Kommission; diagnostisch zusätzlich ohne Kommission und gegen Buy-and-Hold. Krypto-Shorts gibt es nicht (eToro errorCode 747, Commit `31372e5`).

### 7.4 Stundenachse (P2.3)
Auf Instrumenten mit ≥ 60 Handelstagen Stundenhistorie: S_MOM mit Lookback 60 Bars und S_REV mit Lookback 5 Bars, h ∈ {1, 3, 7, 14, 35} Stunden-Bars (Aktien/Rohstoffe: nur Session-Bars wie der Session-Gate von `HourlyStrategyBase`; FX/Krypto: alle Bars, FX ohne Wochenend-Entries wie `forex_weekend_closed`). Netto-Expectancy und IC je Klasse bei h = 7.

### 7.5 Effektive Breite (P2.4)
1. Teilportfolios aus EBC-H1-Arm B+ (Archetypen, Kostenfilter q ≤ 0,10), je Teilportfolio (Instrument × Archetyp) die tägliche Netto-Rendite (flache Tage = 0) aus der Entdeckungsperiode.
2. Korrelationsmatrix der Teilportfolio-Renditen; ρ̄ innerhalb jeder Klasse und zwischen den Klassen.
3. Für N ∈ {5, 10, 20, 50, 100, 200, alle} je 200 Zufallsziehungen (Seed 20261011) in zwei Schemata: (i) nur Aktien, (ii) über alle Klassen proportional. Portfolio mit gleichem Risiko je Teilportfolio; SR_P und `N_eff = N / (1 + (N − 1) · ρ̄_Teilmenge)`.
4. OLS `ln SR_P = a + b · ln N_eff` und `ln SR_P = a' + b' · ln N` über alle Ziehungen; Bootstrap-KI für b über die Ziehungen. Zuwachs von N_eff zwischen N = 50 und allen Aktien (Schema i).

### 7.6 Sizing (P2.5)
Gleiche Signale (B+), gleiche Exits, gleiche Deckel (≤ 10 % Notional je Position, ≤ 60 % gesamt; bindet der Gesamtdeckel, werden alle neuen Positionen proportional skaliert — in allen Varianten gleich):
- **EN** gleiches Notional 2 % der Equity (Status quo der Inkubation),
- **RP** Volatilitäts-Parität: `Notional_i = 0,0010 · E / σ_{i,20}` (σ = Std. der Tagesrendite über 20 Tage, kausal),
- **FR** Fixed-Fractional: Verlust am Trailing-Stop beim Einstieg = 0,5 % der Equity (`Notional = 0,005 · E / (k · ATR20%)`),
- **QK** ¼-Kelly je Klasse aus rückwärtigen 252 Tagen μ/σ², gedeckelt auf 2× RP.
Kennzahlen: SR, MaxDD, CVaR5, Turnover, Kostenanteil, mittlere Bruttoauslastung.

### 7.7 Testmacht (P2.6)
1. Reproduziere die Monte-Carlo-Werte aus C5 mit `automation.optimizer.sequential.simulate_sequential(n_paths=10000, k_max=26, n_concurrent ∈ {1, 80}, sr_annual ∈ {1, 2, 3, 4})`.
2. Empirisch: auf den letzten 26 Wochen der Bestätigungsperiode `sequential.sequential_decision` wöchentlich anwenden (a) auf die Tagesrenditen des vorregistrierten H2-Portfolios (§8, n_concurrent = 1), (b) auf jedes Teilportfolio einzeln (n_concurrent = 80). Anzahl und Zeitpunkt der Entscheidungen berichten.

### 7.8 Szenario-Matrix
Alle Kennzahlen zusätzlich je Regime R1–R4 und Ereignisklasse wie EBC-H1 §7.6 (Referenz `SPX500` bzw. gleichgewichteter US-Aktien-Index).

## 8. Stufe B — Forward-Shadow (≥ 30 Handelstage)

Vor Start vorregistriert und eingefroren: das **H2-Portfolio** = Archetypen B+ mit RP-Sizing, Haltedauer je Klasse = argmax aus der Entdeckungsperiode (§7.2), Krypto ausgeschlossen, falls P2.2 in der Entdeckungsperiode hält. Vergleich: **Status-quo-Mathematik** = dieselben Signale mit EN-Sizing und Zeitstopp 1 Handelstag.

Täglich nach Kerzenschluss: Kerzen fortschreiben, beide Portfolios deterministisch neu rechnen, Trades ab Vorregistrierungsdatum mit Fill zum nächsten Eröffnungskurs und gemessenen Kosten verbuchen. Wöchentlich: `sequential_decision` auf die Tagesrenditen des H2-Portfolios (n_concurrent = 1, k_max = 26) — Entscheidung protokollieren, nichts ändern.

## 9. Stufe C — Demo-Kostenmessung (parallel zu B)

- Stratifizierte Auswahl von ≤ 20 Positionen des H2-Portfolios, alle Klassen, je Klasse mindestens eine Long- und (wo erlaubt) eine Short-Position; ≤ 100 USD je Order.
- Order: `POST /api/v1/trading/execution/demo/market-open-orders/by-amount` (`InstrumentID`, `IsBuy`, `Leverage = 1`, `Amount`, `StopLossRate`); Status je Order über `GET /api/v1/trading/info/demo/orders/{orderId}` (asynchrone Ablehnungen); Schliessen über `POST /api/v1/trading/execution/demo/market-close-orders/positions/{positionId}`.
- Täglich `GET /api/v1/trading/info/demo/pnl`: Gebühren- und Finanzierungsfelder je Position protokollieren (`stage_c/position_fees_daily.csv`). Ziel: ≥ 10 Positions-Nächte je Klasse und Seite, davon ≥ 1 Wochenende.
- Kosten-Rekalibrierung wie EBC-H1 §9: Abweichung > 2 bps bzw. > 20 % vom Modell ⇒ §7 mit den gemessenen Kosten neu rechnen; das Verdikt stützt sich auf die rekalibrierte Rechnung.

## 10. Metriken (Definitionen)

- `σ_1`: Std. der Bar-Rendite (Tages- bzw. Stunden-Bar) über 60 Bars; `σ_h = σ_1 · √h`.
- `IC(h)`: Pearson-Korrelation zwischen Signal `s_t ∈ {−1, 0, +1}` und standardisierter Forward-Rendite `r_{t+1→t+1+h} / σ_h`.
- `SR_netto(h) = mean(r_netto) / std(r_netto) · √252` auf Tagesrenditen des Portfolios.
- `N_eff` wie in §2.1; ρ̄ = mittlere paarweise Korrelation (ohne Diagonale).
- Kostenanteil = Kosten / |Brutto-P&L|; Turnover = gehandeltes Notional / mittlere Equity p. a.

## 11. Statistik und Entscheidungsregel

- Differenzen von Sharpe-Werten: gepaarter Stationary Bootstrap (`automation.optimizer.bootstrap.stationary_bootstrap_indices`, Blocklänge `optimal_block_length` der Differenzreihe, 5000 Replikate, Seed 20261011), 90-%-Perzentil-KI.
- P2.1 wird auf der Bestätigungsperiode entschieden, argmax auf der Entdeckungsperiode bestimmt (keine Doppelnutzung).
- Stufe B darf nur abschwächen: liegt SR_forward(H2) − SR_forward(Status quo) mit oberer 90-%-KI-Grenze < −0,50, wird `BESTAETIGT` zu `UNENTSCHIEDEN`.

| Verdikt | Bedingung |
|---|---|
| `BESTAETIGT` | P2.1, P2.2 und P2.4 erfüllt, Stufe B widerspricht nicht |
| `WIDERLEGT` | Kriterium aus §2.3 erfüllt |
| `UNENTSCHIEDEN` | sonst; nenne die fehlende Evidenz |

## 12. Ausgaben

```
results/H2/
  preregistration.json   preflight.json   universe.csv   costs_calibration.json
  stage_a/ horizon_signals.csv      (klasse, signal, h, ic, brutto_bps, netto_bps, sr_netto, n_trades)
           horizon_archetypes.csv   crypto.csv   hourly_axis.csv
           breadth_draws.csv        (schema, N, ziehung, sr_p, n_eff, rho_bar)
           breadth_regression.json  sizing.csv   power_mc.json   power_empirical.csv   regimes.csv
  stage_b/ forward_daily_returns.csv   sequential_log.csv   weekly_<KW>.md
  stage_c/ fills.csv   position_fees_daily.csv   cost_recalibration.json
  metrics.json   verdict.json   report.md
```

`verdict.json` mit denselben Feldern wie EBC-H1 (`hypothesis: "H2"`), zusätzlich:

```json
{
  "optimal_holding_days_by_class": {"K1_aktien_etf": 0, "K2_fx": 0, "K3_rohstoffe": 0, "K4_indizes": 0, "K5_krypto": null},
  "measured_ic_by_class_and_h": {},
  "breadth": {"b": 0.0, "b_ci90": [0.0, 0.0], "r2": 0.0, "rho_bar_within": {}, "rho_bar_between": 0.0},
  "sizing": {"EN": {"sr": 0.0, "maxdd": 0.0}, "RP": {}, "FR": {}, "QK": {}},
  "measured_costs_bps": {"spread_p50": {}, "fees_rt": {}, "slippage_side": {}, "financing_day": {"long": {}, "short": {}}}
}
```

`report.md` (Deutsch, ≤ 3 Seiten): Verdikt, die vier Hebel mit gemessenem Effekt in bps bzw. Sharpe, Antwort auf F2 in drei Sätzen, Tabelle „optimale Haltedauer je Klasse".

## 13. Folgen je Verdikt

- **BESTAETIGT:** Issues im Katalogformat des Repos: (1) Zeitbox je Anlageklasse aus der gemessenen Kostenquote ableiten statt aus der NYSE-Session (`_contracts.py`); (2) Risikoparität und Klassen-/Korrelationsbudget in `momentum_ls_allocator.py`; (3) Universums-Kostenfilter und Krypto-Regel (Ausschluss oder Haltedauer ≥ gemessenes Optimum); (4) Forward-Evidenz auf Portfolio-Ebene: ein vorregistriertes Portfolio, `n_concurrent = 1`, statt Pro-Paar-Bonferroni über 80 Plätze; (5) Kostenmodell aus echten Fills nachführen (Spread, Slippage, Gebühren, Finanzierung je Instrument).
- **WIDERLEGT:** Klassen, in denen kurze Haltedauern tragen, für EBC-H3 (Exit-Mechanik) priorisieren.
- **UNENTSCHIEDEN:** Universum vergrössern (Konfidenzintervalle von b) oder Stufe B um 30 Handelstage verlängern.

## 14. Ablauf-Checkliste

- [ ] S0 Setup, Preflight, Isolation; Übernahme der H1-Dateien geprüft
- [ ] Universum, Daten, Kostenmessung
- [ ] `preregistration.json` inkl. Entdeckungs-/Bestätigungsgrenze
- [ ] §7.2 Horizont, §7.3 Krypto, §7.4 Stundenachse
- [ ] §7.5 Breite, §7.6 Sizing, §7.7 Testmacht, §7.8 Regime
- [ ] H2-Portfolio eingefroren, Stufe B und C gestartet
- [ ] Kosten-Rekalibrierung geprüft
- [ ] `verdict.json`, `report.md`
