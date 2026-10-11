---
ebc_prompt_id: EBC-H1
titel: "Einfache, universell parametrierte Archetypen über das breite eToro-Universum statt Pro-Symbol-Auswahl aus 14 Strategien"
version: 1.0.0
erstellt_am: 2026-10-11
quelle_repository: https://github.com/philibertschlutzki/etoro_nautilus
quelle_commit: 5f737ed495e70eab85b2dbbb7673eb7369c428ce
beantwortet_fragen: [F1, F4]
konto_modus: nur_demo
stufen: [S0_setup, SA_historisch, SB_forward_shadow, SC_demo_kalibrierung]
mindestdauer: {SA: "Rechenzeit, typisch < 1 Tag", SB: ">= 30 Handelstage", SC: "parallel zu SB"}
ergebnisordner: results/H1/
primaere_endpunkte: [P1.1, P1.2]
seed: 20261011
---

# EBC-H1 — Einfach × breit statt Auswahl × tief

## 0. Auftrag

Du prüfst die vorregistrierte Hypothese H1 gegen reale eToro-Marktdaten, real gemessene Kosten und reale Ausführung im eToro-Demo-Konto. Du arbeitest in einem eigenen Experiment-Klon des Repositories, änderst nichts am Produktions-Checkout und handelst ausschliesslich im Demo-Konto. Ergebnis: ein Verdikt `BESTAETIGT | WIDERLEGT | UNENTSCHIEDEN` mit allen Messwerten in `results/H1/`.

Beantwortete Fragen:
- **F1** — Sind die aktuellen Strategien zu komplex für reale Trading-Szenarien, und wie sähen sie für ein möglichst breites Spektrum an eToro-Instrumenten aus?
- **F4** (Teil) — Welche Features fehlen? Hier: achsenkonsistente Validierung, universelle Parametrisierung, Kostenfilter für das Universum.

## 1. Harte Regeln (gelten vor jedem Schritt)

1. **Nur Demo.** Orders ausschliesslich über Demo-Endpunkte (`/api/v1/trading/execution/demo/...`) oder über das von `automation/account_guard.py` verifizierte Paper-Konto. Vor jeder Order `GET https://public-api.etoro.com/api/v1/me` prüfen; Abweichung vom Demo-/Paper-Konto ⇒ sofortiger Abbruch, keine weitere Order.
2. **Konto-Isolation.** Die Paper-Inkubation des Repos übernimmt beim Start offene eToro-Positionen und hält Symbole mit offener Position fest (Commit `d198415`, `daily_orchestrator._open_position_symbols`). Darum gilt in dieser Reihenfolge: (a) eigenes Demo-Konto für H1, sonst (b) disjunkte Instrumentmenge zu jedem anderen laufenden Bot und Verwaltung jeder Position über ihre `positionId`, sonst (c) Stufe C entfällt und wird im Verdikt als `BLOCKIERT` vermerkt. Täglicher Abgleich: jede nicht von H1 eröffnete Position auf einem H1-Instrument ⇒ Stufe C pausieren.
3. **Produktion unberührt.** Arbeite in einem separaten Klon (§3). Im Produktions-Checkout keine Datei ändern, kein `--reset-catalog`, kein Katalog-Rebuild, keine Änderung an `data/`.
4. **Vorregistrierung.** Vor dem ersten Blick auf Ergebnisse von Stufe A schreibst du `results/H1/preregistration.json` (alle Parameter dieses Dokuments, Universumsliste, Seeds, Datumsgrenzen, SHA-256 dieser Datei). Jede spätere Abweichung wird in `protocol_deviations` begründet; Schwellen werden nachträglich nie verändert.
5. **Keine Parametersuche** ausser den in §6 ausdrücklich genannten Varianten.
6. **Rate-Limits** der eToro-API einhalten (Backoff bei HTTP 429, konservativ ≤ 1 Request/s je Endpunkt). API-Keys nie loggen.
7. **Positionsgrösse in Stufe C** ≤ 100 USD Notional je Order, ≤ 20 gleichzeitige H1-Positionen.

## 2. Hypothese

### 2.1 Behauptung (H1)

Die heutigen Strategien sind für reale Bedingungen zu komplex — nicht im Signalcode, sondern im Apparat darum herum: Auswahl einer von 8 bzw. 14 Strategien je Symbol, Parameter in Bar-Einheiten, Optimierung auf rund 1000 Kerzen und ein Achsenbruch zwischen Auswahl (Tagesachse) und Handel (Stundenachse). Ein Portfolio aus **drei vorab festgelegten Archetypen** (Trend-Ausbruch T, Rücksetzer im Trend M, Volatilitäts-Ausbruch V) mit je höchstens zwei **global festen** Parametern in ATR- und Handelstag-Einheiten, **ohne Auswahl je Symbol**, auf der **Tagesachse** über das **kostengefilterte breite eToro-Universum** ausgeführt, ist out-of-sample netto mindestens gleich profitabel wie die Pro-Symbol-Auswahl und deutlich robuster.

**H0:** Die Pro-Symbol-Auswahl (Status quo) erzielt out-of-sample eine höhere Netto-Sharpe als die Archetypen, und ihre Auswahl hat Informationsgehalt (der In-Sample-Rang sagt den Out-of-Sample-Rang vorher).

### 2.2 Vorhersagen (vorregistriert)

| ID | Vorhersage | Messgrösse | Schwelle | Rolle |
|---|---|---|---|---|
| P1.1 | Arm B+ ist netto nicht schlechter als Arm A1 | ΔSR = SR_netto(B+) − SR_netto(A1), Stufe A, Vergleichszeitraum (§7.2) | Punktschätzer > 0 **und** untere Grenze des 90-%-Bootstrap-KI > −0,20 | primär |
| P1.2 | Die Pro-Symbol-Auswahl hat keinen Informationsgehalt | PBO(A1) via CSCV; Spearman ρ(IS-Score, OOS-Netto-Rendite) der gewählten Paare | PBO(A1) ≥ 0,50 **und** ρ ≤ 0,10 | primär |
| P1.3 | Robustheit über Marktregime | SR_netto je Regime R1–R4 (§7.6) | SR(B+) > 0 in ≥ 3 von 4 Regimen; A1 erfüllt das nicht | sekundär |
| P1.4 | Achsenbruch: der Tagesachsen-Auswahlscore sagt das Stunden-Ergebnis nicht vorher | Spearman ρ(Tages-Score, Netto-Rendite derselben Paare und Parameter auf der Stundenachse) | −0,10 ≤ ρ ≤ 0,10 | sekundär |
| P1.5 | Die Paper-Gates trennen nicht von Zufall | Anteil eligibler Paare unter dem Nullmodell / beobachteter Anteil | ≥ 0,80 | sekundär |
| P1.6 | Breitere Wirkung | Anteil Instrumente mit positiver Netto-Expectancy, je Auswertungsklasse K1–K5 (§4.1) | B+ ≥ A1 in der Mehrheit der Klassen, die in B+ nach dem Kostenfilter vertreten sind | sekundär |

### 2.3 Widerlegung

H1 ist **widerlegt**, wenn ΔSR ≤ −0,30 mit oberer 90-%-KI-Grenze < 0 (A1 klar besser) **oder** wenn PBO(A1) < 0,30 **und** ρ ≥ 0,25 (die Auswahl ist informativ). Sind P1.1 oder P1.2 nicht erfüllt, ohne dass ein Widerlegungskriterium greift: `UNENTSCHIEDEN`.

### 2.4 Begründung aus dem Repository (Stand `5f737ed`)

- **B1 Komplexität.** 14 aktive Strategien (`automation/config/strategies.json`). Der Optuna-Suchraum hat je Strategie 5–17 Dimensionen (Median 7), mit dynamischem Take-Profit bis 19 (`automation/optimizer/spaces.py:348–362`, `:407–669`); zusammen 47 reine Signalparameter. ComboTrendVwap allein: 12 Signalparameter.
- **B2 Datenlage.** Die eToro-Candle-API liefert je Intervall nur die jüngsten 1000 Kerzen (README §4, Issue #1372). `logs/summary.csv`: 170 Instrumente, Median 4000 Ticks = 1000 O/H/L/C-Stundenkerzen, Krypto-Spanne 41,7 Tage. Die Walk-Forward-Geometrie verlangt 444 Tage; der letzte Optimizer-Lauf endete `waiting_for_data`, ETA 2027-10-28 (`logs/zusammenfassung_e8d56d83_20261009T182944719125.md`).
- **B3 Achsenbruch.** Die Paper-Auswahl läuft auf der Tagesachse (`automation/papertrading.py:124–133`, `automation/daily_orchestrator.py:1650–1675`), der Demo-Bot handelt dieselben eingefrorenen Parameter auf der Stundenachse (`automation/momentum_ls_run.py:216–219`). `automation/bar_axis.py` skaliert keine Parameter: `ema_period = 100` heisst in der Auswahl 100 Handelstage, im Handel 100 Stunden-Bars (≈ 14 Handelstage bei Aktien, ≈ 4 Tage bei Krypto/FX). Die Zeitbox ist in der Auswahl ≤ 5 Tage (`max_handelstage` 5), im Handel ≤ 7 Stunden-Bars (`automation/optimizer/_contracts.py:53–88`).
- **B4 Auswahl ≈ Zufall.** Paper-Gates (`automation/papertrading.py:181–194`): t(α) ≥ 0,5, PSR ≥ 0,5, ≥ 8 Trades, PF ≥ 1,0, Expectancy ≥ 0. Unter H0 besteht ein einzelnes Paar t(α) ≥ 0,5 mit p = 0,31; das Maximum über 8 Strategien je Symbol bei unabhängigen Teststatistiken mit p = 0,95 (korrelierte Strategien senken den Wert, ändern aber die Grössenordnung nicht). Beobachtet am 2026-10-11: 474 eligible Aktien-Paare und 251 Rund-um-die-Uhr-Paare (Commit `e460996`); am 2026-10-10 blieben von 259 eligiblen Paaren nach Deflated-Sortino-Filter 9 übrig (Commit `13d8ca8`); BTC/AdxAtrMomentum mit OOS-Profit-Faktor 0,39 wurde gehandelt (Commit `e460996`).
- **B5 Inkonsistenzen.** MeanReversion und DynamicBreakout sind `active: true`, tragen aber die Notiz „Deaktiviert: Whipsaw-Anfälligkeit / Overtrading" (`strategies.json:32, :41`). MeanReversion und TrendPullback füttern ihre Indikatoren erst nach dem Exit-Check (`strategies/mean_reversion.py:62–65`, `strategies/trend_pullback.py:61–65`) — an jeder Exit-Bar entsteht eine Indikatorlücke.
- **B6 Nachweisbarkeit.** Mit 60 Holdout-Tagen auf der Stundenachse ist die Mindest-nachweisbare Sharpe 3,95 p. a.; auf der Tagesachse braucht Sharpe 1,0 p. a. rund 685 Handelstage (≈ 2,7 Jahre), Sharpe 1,5 rund 306 Handelstage (`automation/optimizer/deflation.py:183–236`, nachgerechnet). Nur die Tagesachse mit ~4 Jahren Historie kann einfache Strategien statistisch tragen.

## 3. Schritt 0 — Setup und Preflight

```bash
git clone https://github.com/philibertschlutzki/etoro_nautilus ebc_h1_repo
cd ebc_h1_repo && git checkout 5f737ed495e70eab85b2dbbb7673eb7369c428ce && git switch -c ebc/h1
python3 -m venv venv && . venv/bin/activate && pip install -r automation/requirements.txt   # Python ≥ 3.12
cp /pfad/zur/.env .env          # nur Demo-Keys; ETORO_ENV nicht setzen oder "demo"
python -m automation.demo_order_check            # nur lesend; muss bestehen
python -m pytest automation/tests -x -q -k "bar_axis or session or disaster_stop or papertrading"   # Smoke
mkdir -p results/H1 experiments/ebc_h1
```

Preflight-Checks (alle müssen bestehen, Ergebnis nach `results/H1/preflight.json`):
1. `GET /api/v1/me` liefert das Demo-/Paper-Konto.
2. HEAD des Klons = `5f737ed…`, Arbeitsverzeichnis sauber ausser `experiments/`, `results/`, Overlay-Configs.
3. Konto-Isolation nach Regel 2 festgestellt und dokumentiert.
4. Optional: den Produktions-Katalog **nur lesend** in den Klon kopieren (`rsync -a --ignore-existing <prod>/data/nautilus/ data/nautilus/`), um mehr Stundenhistorie zu haben.

## 4. Universum und Daten

### 4.1 Universum `U_breit` (Seed 20261011)

Quelle: eToro-Metadaten `https://api.etorostatic.com/sapi/instrumentsmetadata/V1.1/instruments` (wie `automation/universe_fetcher.py:36`) und `GET /api/v1/market-data/instrument-types`. Filter wie der 24h-Filter des Repos (Commit `1d51506`): nicht intern, kein Verfallsdatum, PriceSource eToro, nicht delisted; Krypto ohne Cross-Paare und Stablecoins; Rohstoffe ohne Währungsvarianten von Gold/Silber.

| Schicht | Umfang |
|---|---|
| US-Aktien | 150 zufällig |
| Nicht-US-Aktien | 50 zufällig |
| ETFs | 40 zufällig |
| Indizes (CFD) | alle verfügbaren |
| Devisen | alle Majors, Minors, Crosses (≤ 50) |
| Rohstoffe | alle Spot-Instrumente (≤ 20) |
| Krypto | Top 30 nach Verfügbarkeit, ohne Stablecoins |
| Status quo | Vereinigung mit allen 170 Instrumenten aus `automation/config/instrument_map.json` |

Aufnahme in Stufe A nur mit ≥ 750 Tageskerzen. Neue Instrumente trägst du nur im Klon in `automation/config/instrument_map.json` ein: ETFs als `equity` (gleiche Session- und Kostenlogik), Indizes als neue Klasse `index` mit eigenen Einträgen in allen `*_by_asset_class`-Tabellen der Klon-`backtest.json` (Session `null`, Kosten aus §5) — sonst greift `unknown_asset_class_policy: "reject"`.

Auswertungsklassen in allen Tabellen: **K1** Aktien + ETFs, **K2** Devisen, **K3** Rohstoffe, **K4** Indizes, **K5** Krypto.

### 4.2 Daten

- **Tageskerzen:** je Instrument 1000 Kerzen `GET https://public-api.etoro.com/api/v1/market-data/instruments/{etoro_id}/history/candles/desc/OneDay/1000`, im Klon gespeichert mit `python -m automation.historical_fetcher --interval OneDay --full-window --symbols <Liste>`.
- **Stundenkerzen** (nur für P1.4): `… --interval OneHour` bzw. der kopierte Produktions-Katalog.
- **Kerzendefinition** der Tagesachse einmal bestimmen: `python -m automation.optimizer.sweep --measurement-run --oneday-definition` (Ergebnis `reports/oneday_definition_<run_id>.json`). Signale entstehen am Kerzenschluss, Ausführung zum Eröffnungskurs der Folgekerze.
- Datenqualität je Instrument nach `results/H1/universe.csv`: Kerzenzahl, älteste Kerze, Lücken > 4 Handelstage, Anteil Kerzen mit High = Low. Instrumente mit > 2 % Nullspannen-Kerzen oder Lücken > 10 Handelstage ausschliessen.

## 5. Kostenmodell (gemessen, nicht angenommen)

Für jedes Instrument in `results/H1/universe.csv`:

| Grösse | Messung | Rückfallwert bis zur Messung |
|---|---|---|
| Spread s (bps) | `GET /api/v1/market-data/instruments/rates?instrumentIds=…`, ≥ 20 Stichproben über ≥ 2 Handelstage innerhalb der Handelszeit; Median und P75 von (ask − bid)/mid | `backtest.json spread_bps_by_asset_class` |
| Kommission c_k (bps, Round Trip) | Stufe C: Gebührenfelder der Demo-Positionen | Krypto 200 (bestätigt am 2026-10-11: 2,00 USD auf 199,56 USD, Commit `d198415`), sonst 0 |
| Slippage (bps je Seite) | Stufe C: (Fill − Quote zum Absendezeitpunkt)/Quote | `backtest.json slippage_bps_by_asset_class` |
| Overnight-Finanzierung (bps/Tag, je Seite) | Stufe C: Gebühr je gehaltener Nacht | Short 0,79 (Repo-Wert); Long-CFD (FX, Rohstoffe, Indizes) 0,79 als konservative Annahme (das Repo setzt 0 und unterschätzt damit); Aktien-/ETF-Long ohne Hebel 0 |

Round-Trip-Kosten: `c_RT = s_P50 + c_k + 2 · slippage` (+ Finanzierung je Haltetag). Kostenquote je Instrument: `q = c_RT / ATR20_bps` (ATR20 der Tageskerzen in bps des Kurses, Median der letzten 250 Tage).

## 6. Versuchsarme

Gemeinsam für alle Arme: Tagesachse, Startkapital 10 000 USD, Notional je Position 2 % der aktuellen Portfolio-Equity (wie `incubation_sizing` bei 80 Plätzen, `momentum_ls_run.py:231–238`), höchstens 30 gleichzeitige Positionen (60-%-Gesamtdeckel, `backtest.json live_risk.max_total_exposure_fraction`), höchstens eine Position je (Instrument, Strategie/Archetyp). Exits nach der Semantik von `HourlyStrategyBase` (Trailing-Stop auf Schlusskurs, Katastrophen-Stop `clamp(3 · Stopdistanz, 2 %, 15 %)` als simulierte Stop-Order). Kosten nach §5.

**Seitenregel** (für A1, B, B+): Aktien, ETFs und Krypto nur long; Devisen, Rohstoffe und Indizes beidseitig. Shorts werden in EBC-H3 getestet, nicht hier.

| Arm | Beschreibung | Freiheitsgrade |
|---|---|---|
| **A0** Status quo exakt | Paper-Auswahl wie implementiert: die 8 auf `OneDay` zugelassenen Strategien (SmaCrossover, MeanReversion, DynamicBreakout, TrendPullback, AdxAtrMomentum, SqueezeBreakout, DonchianRegimeBreakout, Rsi2Reversion) mit `strategy_defaults.json` + `PAPER_TRAILING_TP`; Gates `PAPER_SELECTION_GATES` + `PAPER_REQUIRED_GATES`; je Symbol der Turnier-Gewinner (`per_symbol_winners` aus `backtest_runner.py --momentum`, „Rank first, Gate second"), Platzvergabe nach (deflationierter) OOS-PSR (`incubation.select_incubation_candidates`); 40 Aktien- plus 40 Rund-um-die-Uhr-Plätze; ungültig gewordene Paare werden ersetzt (`retire_ineligible`), solange keine Position offen ist | Wahl 1 aus 8 je Symbol |
| **A1** Status quo, Seitenregel | wie A0, Short-Trades auf Aktien/ETFs/Krypto verworfen | wie A0 |
| **A2** Pro-Symbol-Tuning (sekundär, optional) | wie A1, zusätzlich je Strategie ein 3×3-Gitter über ihre zwei wichtigsten Parameter (Periode, Multiplikator; Gitterpunkte 0,5×/1×/2× Default) ⇒ 72 Konfigurationen je Symbol | Wahl 1 aus 72 je Symbol |
| **B** Archetypen | T, M, V nach §6.1 auf allen Instrumenten, keine Auswahl | 6 globale Zahlen |
| **B+** Archetypen mit Kostenfilter | wie B, nur Instrumente mit q ≤ 0,10 (Sensitivität 0,05 und 0,20 nur berichten) | 6 + 1 |

Kapazitätsregel bei mehr Signalen als freien Plätzen: A0/A1/A2 nach OOS-PSR-Rang (Status quo), B/B+ nach aufsteigender Kostenquote q, bei Gleichstand nach Instrument-ID.

### 6.1 Spezifikation der Archetypen (die Antwort auf F1 „wie sähen sie aus")

Indikatoren auf Tageskerzen: ATR20 (Wilder), SMA_n, RSI2 (Wilder), Bollinger BB(20; 2,0), Keltner KC(20; 1,5 · ATR20) um EMA20.

**T — Trend-Ausbruch** (Parameter: L = 50, k = 3,0)
- Long, wenn `C_t > max(C_{t−50} … C_{t−1})`; Short (nur FX/Rohstoffe/Indizes), wenn `C_t < min(C_{t−50} … C_{t−1})`.
- Exit: ATR-Trailing-Stop `k · ATR20` vom besten Schlusskurs seit Einstieg (`trailing_stop_anchor="price_extreme"`); Signal-Exit `C_t < min(C_{t−20} … C_{t−1})` (Long) bzw. `> max(…)` (Short); Zeitstopp 60 Handelstage.

**M — Rücksetzer im Trend** (Parameter: θ = 10, H = 5)
- Long, wenn `C_t > SMA200_t` und `RSI2_t < 10`; Short (nur FX/Rohstoffe/Indizes), wenn `C_t < SMA200_t` und `RSI2_t > 90`.
- Exit: `C_t > SMA5_t` (Long) bzw. `C_t < SMA5_t` (Short); Zeitstopp 5 Handelstage; Schutz-Trailing-Stop 3,0 · ATR20.

**V — Volatilitäts-Ausbruch nach Kompression** (Parameter: n_sq = 5, k = 2,5)
- Kompression am Tag t: `BB_oben < KC_oben` und `BB_unten > KC_unten`.
- Long, wenn an den 5 Tagen bis t−1 durchgehend Kompression bestand und `C_t > BB_oben_t`; Short (nur FX/Rohstoffe/Indizes) bei `C_t < BB_unten_t`.
- Exit: ATR-Trailing-Stop 2,5 · ATR20; Zeitstopp 20 Handelstage.

Umsetzung im Klon: drei Klassen `automation/strategies/ebc_trend.py`, `ebc_pullback.py`, `ebc_squeeze.py`, abgeleitet von `HourlyStrategyBase` nach dem Vertrag in `automation/AGENTS.md` (Exit-Check zuerst, Indikatoren immer füttern, `tags=self._entry_order_tags(bar)` an jeder Entry-Order, Zeitstopp über `max_bars_in_trade`). Overlay für Arm B: Kopie von `config_papertrading_daily` mit `backtest.json max_handelstage = 60`, ohne `PAPER_TRAILING_TP`.

## 7. Stufe A — Historischer Walk-Forward

### 7.1 Rechenweg
- **Route R (Pflicht):** Nautilus-`BacktestEngine` je (Strategie bzw. Archetyp, Instrument) mit den Repo-Klassen, eingerichtet wie `backtest_runner.run_single_backtest_worker` (`OmsType.NETTING`, `AccountType.MARGIN`, `create_mock_instrument`, Spread-Rekonstruktion via `resolve_spread_bps`, `disaster_stop_mode="simulated_order"`, `session_window` via `session_windows.resolve_session_window`). Arm A über das materialisierte Overlay `config_papertrading_daily` (`python -c "from automation.papertrading import materialize_daily_selection_overlay; …"`), Arm B über das Overlay aus §6.1. Ausgabe je Lauf: Fills und Positionen ⇒ Trade-Liste und tägliche Netto-P&L-Reihe.
- **Route V (Gegenprobe für B):** unabhängige vektorisierte Implementierung der Archetypen auf denselben Kerzen und Kosten für 30 zufällige Instrumente. Toleranz: Trade-Anzahl ±2 %, Netto-Ergebnis je Trade ±5 bps. Abweichung ⇒ Ursache klären, bevor du weitermachst.

### 7.2 Zeitachse
- Arm A0/A1/A2: Neuauswahl alle 5 Handelstage mit der heutigen Paper-Tagesgeometrie (`derive_walk_forward_daily` bei 1456 Tagen Tiefe: IS 473 + Embargo 10 + 3 × 128 OOS = 867 Kalendertage; Rang aus dem IS-Fenster, Gates aus den OOS-Folds), ausschliesslich mit Daten bis zum Auswahltag; gehandelt wird bis zur nächsten Neuauswahl. Erste Auswahl, sobald 867 Kalendertage Historie vorliegen.
- **Vergleichszeitraum (P1.1, P1.3, P1.6):** vom ersten Auswahltag von A0 bis zur letzten Kerze vor Beginn von Stufe B — bei 1000 Aktien-Tageskerzen ≈ 590 Kalendertage, bei Krypto (365 Kerzen/Jahr) ≈ 130 Tage. Arm B/B+ werden im selben Zeitraum bewertet (Aufwärmphase der Indikatoren: 250 Kerzen davor); die längere B-Historie nur zusätzlich berichten.
- Arm B/B+: keine Auswahl; alle Signale im Vergleichszeitraum zählen.

### 7.3 Portfolio-Simulation
Aus den Trade-Listen je Paar baut ein Ereignissimulator die Portfolio-Equity: chronologisch, Kapazitätsregel aus §6, Notional 2 % der Equity zum Entry, alle Kosten nach §5. Ausgabe `stage_a/portfolio_daily_returns.csv` (Spalten `date, A0, A1, A2, B, Bplus`, Werktage, Equity 21:00 UTC, Wochenend-P&L der 24/7-Instrumente am Montag).

### 7.4 Überanpassung (P1.2)
- **PBO je Symbol** für A1 (8 Konfigurationen) und A2 (72): Tagesrenditen der Konfigurationen im OOS-Zeitraum, CSCV mit 12 Gruppen und k = 6 (924 Pfade, `automation/optimizer/cpcv.py: cpcv_group_boundaries, cpcv_paths, probability_of_backtest_overfitting`), Performance = Sharpe je Gruppe. PBO(A1) = Median über alle Symbole mit ≥ 6 auswertbaren Konfigurationen.
- **Rangpersistenz:** an jedem Auswahltag Spearman ρ zwischen IS-Score (die OOS-PSR, nach der die Plätze vergeben werden) und Netto-Rendite des gewählten Paares bis zur nächsten Auswahl, gepoolt über alle Auswahltage.
- **Turnier-Treue:** Für den letzten Auswahltag die emulierte Gewinnerliste gegen einen echten Lauf `backtest_runner.py --momentum` mit dem Overlay `config_papertrading_daily` abgleichen. Übereinstimmung der Gewinner je Symbol < 70 % ⇒ Abweichung im Bericht begründen, Emulation korrigieren, erst dann auswerten.

### 7.5 Nullmodell der Gates (P1.5)
Für jede (Strategie, Instrument)-Reihe 200 Null-Replikate durch zirkuläre Verschiebung der Positionsreihe gegen die Kursreihe um eine Zufallsanzahl von 20–230 Handelstagen (Autokorrelation bleibt erhalten, Timing-Information wird zerstört). Gates wie A0 anwenden. Kennzahl: mittlerer Anteil eligibler Paare unter Null / beobachteter Anteil.

### 7.6 Szenario-Matrix (reale Marktphasen)
Referenz: Index-CFD `SPX500` (über die eToro-Suche auflösen), sonst gleichgewichteter Index der US-Aktien im Universum. Regime je Tag:
- R1 Aufwärts ruhig: Index > SMA200 und Vol20 ≤ Median(Vol20)
- R2 Aufwärts volatil: Index > SMA200 und Vol20 > Median
- R3 Abwärts: Index < SMA200 und SMA200 20 Tage fallend
- R4 Rest (Übergang/Seitwärts)

Zusätzlich Ereignistage: oberste 5 % |Index-Tagesrendite|, Aktien-Gaps |O_t/C_{t−1} − 1| ≥ 3 %, Wochenenden (Krypto). Alle Kennzahlen je Regime und Ereignisklasse berichten.

### 7.7 Achsenbruch (P1.4)
Für jedes von A0 zuletzt gewählte Paar: dieselbe Strategie mit denselben eingefrorenen Parametern (inkl. `PAPER_TRAILING_TP`, `catalog_warmup_bars = 400`) auf der Stundenachse über das gesamte verfügbare Stundenfenster laufen lassen, wie es der Demo-Bot tut (`bar_axis.LIVE_AXIS = "OneHour"`). Spearman ρ zwischen Tages-Auswahlscore und Netto-Rendite auf der Stundenachse; zusätzlich Median der Stunden-Netto-Rendite und Anteil Paare mit negativem Ergebnis.

## 8. Stufe B — Forward-Shadow (≥ 30 Handelstage)

- **Arm B+ (Kandidat):** täglich nach Kerzenschluss Tageskerzen fortschreiben (`count = min(1000, Lücke + 2)`), Archetypen deterministisch auf der verlängerten Historie neu rechnen, neue Trades ab Vorregistrierungsdatum mit Fill zum nächsten Eröffnungskurs und gemessenen Kosten verbuchen.
- **Arm A (Status quo):** falls die Paper-Inkubation des Repos läuft und lesbar ist: Evidenz-Ledger `data/state/incubation/<strategy>_<symbol>.jsonl` (Netto-Rendite je Session-Bar, `incubation.py:178–211`) zu Tagesrenditen aggregieren. Sonst Shadow der aktuellen Inkubations-Whitelist auf der Stundenachse.
- Wöchentlich Zwischenbericht `stage_b/weekly_<KW>.md`, keine Änderung an Regeln oder Schwellen.

## 9. Stufe C — Demo-Kalibrierung (parallel zu B)

- Stratifizierte Zufallsauswahl von ≤ 20 B+-Teilportfolios (Instrument × Archetyp, alle Anlageklassen vertreten).
- Jede Entry-/Exit-Entscheidung aus Stufe B wird zum nächsten Handelsbeginn als Market-Order im Demo-Konto ausgeführt: `POST /api/v1/trading/execution/demo/market-open-orders/by-amount` mit `InstrumentID`, `IsBuy`, `Leverage = 1`, `Amount ≤ 100`, `StopLossRate` (Katastrophen-Stop); Schliessen über `POST …/demo/market-close-orders/positions/{positionId}`. Nach jeder Order den Status über `GET /api/v1/trading/info/demo/orders/{orderId}` prüfen (eToro lehnt teils asynchron ab, Commit `31372e5`).
- Je Fill protokollieren (`stage_c/fills.csv`): `order_id, position_id, instrument, side, submit_ts, bid, ask, fill_px, units, fees_usd, overnight_fees_usd`. Daraus Slippage, Gebühren und Finanzierung je Klasse.
- **Kosten-Rekalibrierung:** weicht die gemessene mittlere Slippage oder Gebühr einer Klasse um mehr als 2 bps bzw. 20 % vom Modell ab, rechnest du Stufe A mit den gemessenen Werten erneut (vorregistrierte Pflicht, keine Abweichung). Das Verdikt stützt sich dann auf die rekalibrierte Rechnung.

## 10. Metriken (Definitionen)

- `r_t`: Netto-Tagesrendite des Portfolios aus Equity inkl. unrealisierter P&L und aller Kosten.
- `SR = mean(r) / std(r) · √252`; `PSR(SR* = 0)` mit Schiefe/Kurtosis über `automation.optimizer.deflation.probabilistic_sharpe_ratio`.
- Netto-Expectancy je Trade (bps auf Notional), Profit-Faktor, Trefferquote, MaxDD, CVaR5 der Tagesrenditen, Turnover (Notional/Equity p. a.), Kostenanteil = Kosten / |Brutto-P&L|.
- Effektive Zahl der Tests je Arm: Anzahl verglichener Konfigurationen je Auswahlentscheidung × Auswahlentscheidungen.

## 11. Statistik und Entscheidungsregel

- ΔSR mit gepaartem Stationary Bootstrap: gemeinsame Indizes für beide Reihen aus `automation.optimizer.bootstrap.stationary_bootstrap_indices`, Blocklänge `optimal_block_length` der Differenzreihe, 5000 Replikate, Seed 20261011, 90-%-Perzentil-KI.
- Primärentscheidung allein auf Stufe A (inkl. Kosten-Rekalibrierung aus C).
- Stufe B darf das Verdikt nur abschwächen: liegt ΔSR_forward (B+ − A) mit oberer 90-%-KI-Grenze < −0,50, wird ein `BESTAETIGT` zu `UNENTSCHIEDEN`.

| Verdikt | Bedingung |
|---|---|
| `BESTAETIGT` | P1.1 und P1.2 erfüllt, Stufe B widerspricht nicht |
| `WIDERLEGT` | Kriterium aus §2.3 erfüllt |
| `UNENTSCHIEDEN` | sonst; nenne die Datenmenge, die entscheiden würde |

## 12. Ausgaben

```
results/H1/
  preregistration.json   preflight.json   universe.csv   costs_calibration.json
  stage_a/ trades/<arm>/<instrument>__<strategy>.parquet
           portfolio_daily_returns.csv   selection_log.csv   pbo.csv
           rank_persistence.csv   null_gate_calibration.json   regimes.csv   axis_break.csv
  stage_b/ forward_daily_returns.csv   weekly_<KW>.md
  stage_c/ fills.csv   cost_recalibration.json
  metrics.json   verdict.json   report.md
```

`verdict.json`:

```json
{
  "hypothesis": "H1",
  "prompt_version": "1.0.0",
  "repo_commit": "5f737ed495e70eab85b2dbbb7673eb7369c428ce",
  "preregistration_sha256": "<sha256>",
  "period": {"stage_a_oos": ["YYYY-MM-DD", "YYYY-MM-DD"], "stage_b": ["YYYY-MM-DD", "YYYY-MM-DD"]},
  "universe": {"n_total": 0, "n_after_cost_filter": 0, "by_class": {}},
  "predictions": [
    {"id": "P1.1", "metric": "delta_sharpe_Bplus_minus_A1", "value": 0.0, "ci90": [0.0, 0.0], "threshold": "point>0 and ci_low>-0.20", "passed": false}
  ],
  "stage_c": {"status": "OK|BLOCKIERT", "n_fills": 0, "slippage_bps_by_class": {}, "fees_bps_by_class": {}},
  "verdict": "BESTAETIGT|WIDERLEGT|UNENTSCHIEDEN",
  "reasons": [],
  "protocol_deviations": []
}
```

`report.md` (Deutsch, ≤ 3 Seiten): Verdikt im ersten Satz, Tabelle der Vorhersagen, Kostenbefund, Regime-Tabelle, Antwort auf F1 in drei Sätzen.

## 13. Folgen je Verdikt

- **BESTAETIGT:** Empfehlung als GitHub-Issues im Katalogformat des Repos (Symptom / Root-Cause / Fix / Akzeptanzkriterien / Betroffene Dateien): (1) Live-Freigabe der Tagesachse (Klausel `bar_axis_live_supported`) und achsenkonsistente Auswahl; (2) Archetyp-Strategien T/M/V mit festen Parametern statt Pro-Symbol-Auswahl; (3) Universums-Kostenfilter q ≤ 0,10 aus gemessenen Spreads; (4) Pro-Symbol-Tuning in `spaces.py` nur noch als Diagnose.
- **WIDERLEGT:** Welche der 8 Strategien tragen den Vorsprung von A1, in welchen Regimen? Diese Paare gezielt in EBC-H2 aufnehmen.
- **UNENTSCHIEDEN:** Stufe B um weitere 30 Handelstage verlängern; Universum um weitere 150 Aktien erweitern, falls die Konfidenzintervalle vom Universumsumfang dominiert werden.

## 14. Ablauf-Checkliste

- [ ] S0 Setup, Preflight bestanden, Isolation dokumentiert
- [ ] Universum gezogen, Daten geladen, Qualitätsfilter angewendet
- [ ] Kosten gemessen (Spread) bzw. Rückfallwerte dokumentiert
- [ ] Archetyp-Klassen im Klon implementiert, Route-V-Gegenprobe bestanden
- [ ] `preregistration.json` geschrieben (vor jeder Auswertung)
- [ ] Stufe A gerechnet: A0, A1, (A2), B, B+
- [ ] PBO, Rangpersistenz, Nullmodell, Regime, Achsenbruch
- [ ] Stufe B gestartet, Stufe C gestartet (oder BLOCKIERT)
- [ ] Kosten-Rekalibrierung geprüft
- [ ] `verdict.json` und `report.md` geschrieben
