---
ebc_prompt_id: EBC-H4
titel: "Kapazität × Breite: mehr gleichzeitige Positionen mit kleinerem Notional über ein erweitertes, frisch gezogenes Aktien-/ETF-Universum"
version: 1.0.0
erstellt_am: 2026-10-11
quelle_repository: https://github.com/philibertschlutzki/etoro_nautilus
quelle_commit: 40671219c3e007ea268c5d48ff5573cafaef88a7
beantwortet_fragen: [F5, F4]
ziele: {gewinn: "+", verluste: "Nebenbedingung", trades: "++"}
konto_modus: nur_demo
stufen: [S0_setup, SA_historisch, SB_forward_shadow, SC_demo_kalibrierung]
mindestdauer: {SA: "Rechenzeit < 1 Tag, davor Spread-Stichproben über >= 2 Handelstage", SB: ">= 30 Handelstage", SC: "parallel zu SB, erst nach Freigabe"}
ergebnisordner: results/H4/
primaere_endpunkte: [P4.1, P4.2, P4.3]
seed: 20261011
seed_universum: 20261012
abhaengig_von: "EBC-H1 §4–§7 (U_breit, Kosten, Archetypen, Vergleichszeitraum) — results/H1 und data/ebc/ wiederverwenden"
---

# EBC-H4 — Kapazität × Breite: mehr Plätze, kleineres Notional, frisches Universum

## 0. Auftrag

Du prüfst die vorregistrierte Hypothese H4 gegen reale eToro-Marktdaten, gemessene Spreads und reale Kleinorders im Demo-/Paper-Konto. Du arbeitest im Experiment-Arbeitsverzeichnis von EBC-H1–H3 auf einem eigenen Branch, änderst nichts an der Produktion und handelst ausschliesslich im Demo-/Paper-Konto. Ergebnis: Verdikt `BESTAETIGT | WIDERLEGT | UNENTSCHIEDEN` mit allen Messwerten in `results/H4/`.

Beantwortete Fragen:
- **F5** — Wie lassen sich die Gewinne maximieren, die Verluste reduzieren und die Anzahl Trades maximal erhöhen? Hier: Trades und Gewinn über Kapazität und Breite, ohne ein Signal zu ändern; Verluste als Nebenbedingung (Drawdown nicht schlechter).
- **F4** (Teil) — Fehlende Features: Kapazitätssteuerung (Plätze × Notional), Zählung verworfener Kandidaten, Universums-Erweiterung mit gemessenem Kostenfilter, Replikation auf disjunkten Instrumenten.

## 1. Harte Regeln (gelten vor jedem Schritt)

1. **Nur Demo/Paper.** Orders ausschliesslich über Demo-Endpunkte oder das von `automation/account_guard.py` verifizierte Paper-Konto (`ETORO_PAPER_ACCOUNT_CID`; `verify_paper_account` vor jeder Order, d. h. `GET https://public-api.etoro.com/api/v1/me` muss genau diese `realCid` liefern). Abweichung ⇒ sofortiger Abbruch, keine weitere Order.
2. **Konto-Isolation** wie EBC-H1 Regel 2: (a) eigenes Demo-Konto, sonst (b) Instrumente disjunkt zu jedem laufenden Bot und zu allen offenen Positionen, Verwaltung jeder Position über ihre `positionId`, sonst (c) Stufe C `BLOCKIERT`.
3. **Produktion unberührt.** Arbeit auf Branch `ebc/h4`; kein Push auf `main`, keine Änderung an `automation/config/*.json` ausser in Overlays des Experiments.
4. **Vorregistrierung** in `results/H4/preregistration.json` vor jeder Auswertung von Stufe A: alle Parameter dieses Dokuments, Liste und SHA-256 von `U_neu`, Kostentabelle, Seeds, Datumsgrenzen, SHA-256 dieser Datei. Schwellen werden nie nachträglich geändert; Abweichungen stehen begründet in `protocol_deviations`.
5. **Keine Parametersuche.** Archetypen exakt nach EBC-H1 §6.1 (`experiments/ebc/sim.py`, `ARCH`), Exits wie B+ (Trailing-Stop auf Schlusskurs, Katastrophen-Stop `clamp(3 · k · ATR20 %, 2 %, 15 %)`, Zeitstopps T 60 / M 5 / V 20). Variiert werden nur die Kapazitätsstufen aus §6.
6. **Rate-Limits:** ≤ 1 Request/s (`experiments/ebc/api.py`), Backoff bei HTTP 429; API-Keys nie loggen.
7. **Stufe C nur nach ausdrücklicher Freigabe** durch den Nutzer; ≤ 50 USD je Order, ≤ 20 gleichzeitige H4-Positionen, jede Testposition nach dem Test schliessen.
8. `manuals/ebc/EBC_H1.md`–`EBC_H3.md` und `results/H1`–`results/H3` nicht ändern (deren Vorregistrierungen referenzieren die SHA-256 dieser Dateien).

## 2. Hypothese

### 2.1 Behauptung (H4)

Arm B+ aus EBC-H1 ist kapazitäts-, nicht signalbegrenzt, und sein Ertrag hängt an wenigen Ausreissern. Werden bei unverändertem Gesamtdeckel (60 % der Equity) mehr Plätze mit kleinerem Notional vergeben — 120 Plätze à 0,5 % statt 30 à 2 % — und wird das Universum um frisch gezogene, kostengefilterte Aktien und ETFs erweitert, dann steigt die Zahl der Trades mindestens auf das Dreifache, es werden mehr Ausreisser gefangen, und die Netto-Sharpe steigt bei nicht tieferem Drawdown, ohne dass ein Signal geändert wird. Der Vorteil der Archetypen ist dabei keine Eigenschaft der 466 Instrumente von `U_breit`: er repliziert auf einem disjunkten, frisch gezogenen Universum `U_neu`.

**H0:** Zusätzliche Plätze füllen sich mit schlechteren Kandidaten; Expectancy je Trade und Sharpe fallen, und auf `U_neu` ist die Netto-Expectancy nicht positiv.

### 2.2 Vorhersagen (vorregistriert)

| ID | Vorhersage | Messgrösse | Schwelle | Rolle |
|---|---|---|---|---|
| P4.1 | Der Edge repliziert ausserhalb von `U_breit` | Netto-Expectancy je Trade von Arm N30 (nur `U_neu`) | > 0 mit unterer 90-%-KI-Grenze > 0 (Cluster-Bootstrap nach Einstiegsmonat) **und** Punktschätzer ≥ 0,5 × Expectancy der K1-Trades von C30 | primär |
| P4.2 | Mehr Trades ohne Qualitätsverlust | Trades(C120+) / Trades(C30); Expectancy(C120+) / Expectancy(C30) | ≥ 3,0 **und** ≥ 0,5 | primär |
| P4.3 | Bessere Risiko-Rendite | ΔSR = SR_netto(C120+) − SR_netto(C30); DD = Betrag des MaxDD | Punktschätzer > 0, untere 90-%-KI-Grenze > −0,20 **und** DD(C120+) ≤ DD(C30) + 1 pp | primär |
| P4.4 | Kapazität ist der Engpass | Anteil verworfener Kandidaten (§7.3) | C30 ≥ 20 %, C120+ ≤ 5 % | sekundär |
| P4.5 | Mehr Ausreisser | Zahl der Trades mit Netto ≥ +2000 bps | C120+ ≥ 2,5 × C30 | sekundär |
| P4.6 | FX, Rohstoffe und Indizes sind entbehrlich | ΔSR(C30-K1 − C30) | Punktschätzer ≥ −0,10 | sekundär |
| P4.7 | Der q-Rang wirkt als Volatilitäts-Tilt | ΔSR(C30 − C30r); Median-ATR20 % der genommenen Trades je Arm | berichten (Diagnose für EBC-H6) | sekundär |
| P4.8 | Kleinorders sind real handelbar | Stufe C: Annahmequote von 50-USD-Orders, Gebühren, Spread je Klasse | ≥ 98 % angenommen, keine Mindestgebühr je Order | sekundär |

### 2.3 Widerlegung

H4 ist **widerlegt**, wenn (a) die obere 90-%-KI-Grenze der Netto-Expectancy von N30 < 0 ist (der Edge kehrt sich auf frischen Instrumenten um) **oder** (b) ΔSR(C120+ − C30) eine obere 90-%-KI-Grenze < 0 hat (mehr Kapazität und Breite sind klar schlechter). Sind primäre Vorhersagen unerfüllt, ohne dass ein Widerlegungskriterium greift: `UNENTSCHIEDEN`.

### 2.4 Begründung aus dem Repository (Stand `4067121`)

Alle Zahlen sind reproduzierbar mit `python experiments/ebc/evidence_h4_h8.py` (rein lesend, schreibt `results/evidence_h4_h8.json`).

- **E1 Ausreisser.** B+ im Vergleichszeitraum 2024-12-23 bis 2026-10-12: 1141 Trades, Expectancy 284 bps, SR 2,02, MaxDD −15,4 % (`results/H1/metrics.json`). Netto-Beitrag aller Trades +64,7 pp Equity (2 % Notional, ohne Zinseszins). Die besten 1 % der Trades (11) liefern 42 %, die besten 5 % (57) liefern 115 % des Netto-Gewinns; 97 Trades schliessen mit ≥ +2000 bps. Ein ausreissergetriebener Ertrag braucht viele Lose: jeder verworfene Kandidat ist ein möglicher verpasster Ausreisser.
- **E2 Kapazität.** `experiments/ebc/portfolio.py:61–92` (`simulate`) vergibt höchstens 30 Plätze à 2 % und verwirft Kandidaten ohne freien Platz, ohne sie zu zählen (`:72–79`). Gleichzeitig offene B+-Positionen (Näherung aus den Trade-Indizes, Feiertage nicht abgeglichen): Median 27, an 46 % der Tage ≥ 28 von 30. Die exakte Zahl verworfener Kandidaten ist bisher nicht gemessen.
- **E3 Breite sättigt die Sharpe, nicht die Trade-Zahl.** Mittlere Korrelation der Teilportfolios in K1 ρ̄ = 0,024, zwischen Klassen 0,006 (`results/H2/stage_a/breadth_regression.json`); N_eff = N / (1 + (N − 1) · ρ̄) nähert sich 1/ρ̄ ≈ 41. Mittlere SR der Aktien-Teilportfolios (H2, Entdeckungsperiode): 0,21 bei N = 50, 0,25 bei allen 984. Mehr Instrumente heben die Sharpe kaum, wohl aber die Zahl der Trades und Ausreisser-Fänge — sofern Plätze frei sind.
- **E4 Klassen.** K1 (Aktien/ETF) trägt den Ertrag: 1064 Trades, +64,5 pp; K2 53 Trades −0,3 pp; K3 16 Trades +0,5 pp; K4 8 Trades 0,0 pp. H2: FX, Rohstoffe und Indizes sind mit den Archetypen in allen Haltedauern netto negativ.
- **E5 Ein einziges Universum.** Alle Ergebnisse von H1–H3 stammen aus `U_breit` (466 Instrumente, 357 K1, Seed 20261011, `experiments/ebc/build_universe.py`) und aus einer Periode mit starkem Aufwärtsregime (H1-Bericht, Einschränkungen). Eine Replikation auf disjunkten Instrumenten fehlt.
- **E6 Live-Grössen.** Gesamtdeckel `automation/config/backtest.json:96` (`max_total_exposure_fraction: 0.60`). 0,5 % Notional sind bei 10 000 USD Equity 50 USD je Position. Ob eToro solche Orders für alle Instrumente annimmt und ohne Mindestgebühr ausführt, ist ungemessen.

## 3. Schritt 0 — Setup und Preflight

```bash
cd <Experiment-Arbeitsverzeichnis von EBC-H1–H3>     # enthält data/ebc/daily/, data/ebc/arch_trades.pkl, data/ebc/spreads.jsonl
git fetch origin && git switch -c ebc/h4 origin/main  # main muss 4067121 und diese Datei enthalten
. venv/bin/activate
python experiments/ebc/evidence_h4_h8.py              # Zahlen aus §2.4 reproduzieren
mkdir -p results/H4/stage_a results/H4/stage_b results/H4/stage_c
```

`experiments/ebc/preflight.py` prüft heute fest gegen `5f737ed`. Erweitere es im Branch: Argument `--expected-ancestor 40671219c3e007ea268c5d48ff5573cafaef88a7`, Prüfung per `git merge-base --is-ancestor`, HEAD-Hash mitschreiben. Dann `python experiments/ebc/preflight.py results/H4/preflight.json`. Alle Prüfungen müssen bestehen:

1. `/me` liefert das angeheftete Demo-/Paper-Konto.
2. HEAD enthält `4067121` als Vorfahren; Arbeitsverzeichnis sauber ausser `experiments/`, `results/`.
3. Konto-Isolation nach Regel 2 festgestellt und dokumentiert.
4. **Paritätstest:** Arm C30 auf `U_breit` mit den Rückfallkosten von H1 und dem Stichtag aus §4.3 reproduziert B+ aus `results/H1/metrics.json`: 1141 Trades, SR 2,0156 ± 0,005, MaxDD −15,42 % ± 0,05 pp. Scheitert die Parität, zuerst die Ursache klären (häufig: von `stage_b_daily.py` nach dem Stichtag angehängte Kerzen), nicht weiterrechnen.

## 4. Universum und Daten

### 4.1 `U_breit` (unverändert)
`results/H1/universe.csv` (466 Instrumente, SHA-256 `ab8863f9…` in `results/H1/preregistration.json`). Nach Kostenfilter q ≤ 0,10: 426 (K1 353, K2 48, K3 10, K4 15).

### 4.2 `U_neu` (Seed 20261012)
Quelle und Filter wie EBC-H1 §4.1 (`build_universe.py`: Metadaten `data/ebc/metadata.json` neu laden, `base_ok`, `spot_ok`, US = ExchangeID 4/5), aber nur Aktien und ETFs und nur Instrumente, die nicht in `results/H1/universe.csv` stehen:

| Schicht | Umfang |
|---|---|
| US-Aktien | 300 |
| Nicht-US-Aktien | 100 |
| ETFs | 60 |

Je Schicht die Kandidaten mit Seed 20261012 mischen, der Reihe nach laden, die ersten n mit bestandener Qualitätsprüfung aufnehmen: ≥ 750 Tageskerzen, ≤ 2 % Kerzen mit High = Low, keine Lücke > 10 Handelstage, letzte Kerze höchstens 5 Kalendertage alt. Umsetzung als `experiments/ebc/build_universe_neu.py` (Kopie von `build_universe.py` mit Ausschlussliste, eigenem Seed und eigenen Schichtgrössen). Ausgabe `results/H4/universe_neu.csv` mit denselben Spalten wie `results/H1/universe.csv`, alle Zeilen Klasse K1; Kerzen nach `data/ebc/daily/<id>.csv`. `universe.load(path)` liest beide Dateien; `common.setup()` erhält dafür einen Parameter `paths`.

### 4.3 Stichtag
Stufe A verwendet nur Kerzen mit Datum ≤ 2026-10-10. H1 hat am 2026-10-11 abgerufen und die unfertige Kerze des Abruftags verworfen; `portfolio.Calendar` rollt Wochenend-Kerzen auf den folgenden Montag, darum endet der Vergleichszeitraum im Kalender am 2026-10-12. Spätere Kerzen gehören zu Stufe B. Den Vergleichszeitraum über Datumsgrenzen bestimmen, nicht über den Index `g_start = 617`: der Kalender ändert sich, sobald `U_neu` hinzukommt.

## 5. Kostenmodell

Wie EBC-H1 §5 und `experiments/ebc/costs.py`, mit gemessenen Spreads:

- **Spread:** an mindestens zwei Handelstagen über die Handelszeiten der europäischen und US-Börsen alle 15 Minuten messen, z. B. ab 07:00 UTC `python experiments/ebc/sample_spreads.py --loop 15 --max-snapshots 57` (bis etwa 21:15 UTC; `sample_spreads.py` dafür um `--universe <csv>` erweitern, damit `U_neu` mitgemessen wird). Gültig sind nur Stichproben von Montag bis Freitag, deren Quote-Zeitstempel `qts` höchstens 5 Minuten vor dem Abrufzeitpunkt `ts` liegt (frische Kurse in der Session; die Wochenend-Stichproben von H1 sind für Nicht-Krypto-Instrumente unbrauchbar). Instrumente mit ≥ 20 gültigen Stichproben über ≥ 2 Handelstage erhalten `s = max(Rückfallwert, gemessener Median)` (`CostTable(rows, spreads=<gefiltert>, use_measured=True)`), alle übrigen den Rückfallwert.
- **Slippage, Kommission, Finanzierung:** Rückfallwerte aus `costs.py` (Slippage je Seite K1 1,5 bps; Krypto-Kommission 200 bps; Finanzierung Short 0,79 bps/Tag, Long-CFD 0,79 bps/Tag, Aktien long 0) bis zur Messung in Stufe C.
- **Referenzarm B+ₘ:** B+ mit derselben, gemessenen Kostentabelle neu gerechnet (= Arm C30). Alle Arme verwenden dieselbe Tabelle. Der Paritätstest (§3) verwendet ausnahmsweise die Rückfallkosten von H1.
- Kostenquote `q = c_RT / ATR20_bps` (Median der letzten 250 Tage bis zum Stichtag), Filter q ≤ 0,10 für alle Arme.

## 6. Versuchsarme

Gemeinsam: Tagesachse; Archetypen T, M, V nach EBC-H1 §6.1, sequenziell (höchstens eine Position je Instrument und Archetyp, `sim.sequential`); Seitenregel von H1 (K1 nur long, K2–K4 beidseitig, `universe.SHORT_OK`); Exits wie B+; Einstieg zum Eröffnungskurs nach dem Signal; Startkapital 10 000 USD; Notional f × Equity beim Einstieg; Plätze × f = 60 % (Gesamtdeckel); bei mehr Kandidaten als freien Plätzen Vorrang nach aufsteigendem q, Gleichstand nach Instrument-ID; Portfolio mit `portfolio.simulate(trades, cal, slots=N, notional_frac=f)`.

| Arm | Universum | Plätze × Notional | Rang bei Engpass | Zweck |
|---|---|---|---|---|
| **C30** (= B+ₘ) | `U_breit` | 30 × 2 % | q | Referenz |
| **C60** | `U_breit` | 60 × 1 % | q | Kapazität |
| **C120** | `U_breit` | 120 × 0,5 % | q | Kapazität |
| **C120+** | `U_breit` ∪ `U_neu` | 120 × 0,5 % | q | Kandidat: Kapazität × Breite |
| **N30** | nur `U_neu` | 30 × 2 % | q | Replikation (P4.1) |
| **C30-K1** | `U_breit`, nur K1 | 30 × 2 % | q | P4.6 |
| **C30r** | `U_breit` | 30 × 2 % | Zufall (Seed 20261011, je Handelstag neu gezogen) | P4.7 |
| **C∞** | `U_breit` ∪ `U_neu` | unbegrenzt × 0,5 % | – | Kapazitätsbedarf, nur berichten |

Die Zerlegung des Effekts von C120+ gegenüber C30 in einen Kapazitätsanteil (C120 − C30) und einen Breitenanteil (C120+ − C120) wird berichtet, ist aber keine Vorhersage.

## 7. Stufe A — Historische Auswertung

### 7.1 Rechenweg
- **Route V** wie H1–H3: `sim.sequential` je (Instrument, Archetyp) ⇒ Trades; `portfolio.trade_vector` ⇒ tägliche Netto-P&L-Vektoren; `portfolio.simulate` ⇒ Portfolio.
- **Gegenprobe Route R** (Nautilus, `experiments/ebc/route_r.py`/`parity.py`) für 30 zufällige `U_neu`-Instrumente: Trade-Anzahl ±2 %, Netto je Trade ±5 bps. Abweichung ⇒ Ursache klären, bevor du weitermachst.

### 7.2 Zeitraum
Vergleichszeitraum 2024-12-23 bis 2026-10-12, Aufwärmphase der Indikatoren 250 Kerzen davor; es zählen nur Trades mit Einstieg im Vergleichszeitraum. Für `U_neu` gelten dieselben Datumsgrenzen.

### 7.3 Verworfene Kandidaten (P4.4)
Erweitere `simulate` um einen optionalen Zähler (`stats: dict | None = None`), der das Ergebnis nicht verändert (Paritätstest danach erneut bitgleich). Je Handelstag: `n_cand` (neue Signale, deren Schlüssel nicht belegt ist), `n_taken`, `n_dropped` (kein freier Platz). Kennzahl: Σ n_dropped / Σ n_cand, je Archetyp und Klasse. Zusätzlich der **Wert der verworfenen Kandidaten**: mittlere Netto-Rendite (bps) der verworfenen Trades aus der Trade-Liste, verglichen mit den genommenen — das prüft H0 direkt.

### 7.4 Ausreisser und Auslastung
Je Arm: Zahl der Trades ≥ +2000 bps, Anteil des Netto-Gewinns der besten 1 % und 5 % der Trades, mittlere und mediane Zahl offener Positionen, mittleres Exposure (Notional / Equity).

### 7.5 Vola-gleiche Rendite
Je Arm die Tagesrenditen auf die annualisierte Vola von C30 skalieren (`r' = r · σ_C30 / σ_Arm`) und Jahresrendite und MaxDD bei gleicher Vola berichten. So bleibt der Vergleich frei vom unterschiedlichen mittleren Exposure der Arme.

### 7.6 Szenario-Matrix
Regime R1–R4 und die 5 % grössten Index-Tage (`common.regime_array`, Referenz SPX500) für C30, C120 und C120+: SR und mittlere Tagesrendite je Regime.

## 8. Stufe B — Forward-Shadow (≥ 30 Handelstage)

- Gemeinsames tägliches Skript `experiments/ebc/stage_b_multi.py` (Verallgemeinerung von `stage_b_daily.py`: mehrere Hypothesen und Arme, Universum `U_breit` ∪ `U_neu`, Kerzen fortschreiben mit `count = min(1000, Lücke + 2)`, nur Trades mit Einstieg ab Startdatum). Aufruf nach 22:30 UTC: `python experiments/ebc/stage_b_multi.py --hyp H4 --arms C30,C120plus,N30 --since <Startdatum>`. Ausgabe `results/H4/stage_b/forward_daily_returns.csv`.
- Wöchentlich `stage_b/weekly_<KW>.md` mit Trades, verworfenen Kandidaten und SR je Arm; keine Änderung an Regeln oder Schwellen.
- Stufe B darf das Verdikt nur abschwächen: hat ΔSR_forward(C120+ − C30) eine obere 90-%-KI-Grenze < −0,50, wird ein `BESTAETIGT` zu `UNENTSCHIEDEN`.

## 9. Stufe C — Kleinorder-Kalibrierung (nach Freigabe, parallel zu B)

- 40 K1-Instrumente, geschichtet (je 10 US-Aktien und 5 Nicht-US-Aktien aus `U_breit` und aus `U_neu`, dazu je 5 ETFs), ohne offene Positionen und, solange der Inkubations-Bot im selben Konto läuft, nicht in `automation/config/instrument_map.json` (der Bot übernimmt beim Start offene Positionen auf diesen Instrumenten, `automation/adapters/etoro_execution.py:243–283`).
- Je Instrument während seiner Handelszeit eine Market-Order über 50 USD: `POST {REST_BASE}/market-open-orders/by-amount` mit `InstrumentID`, `IsBuy: true`, `Leverage: 1`, `Amount: 50`, `StopLossRate` auf dem Katastrophen-Stop-Niveau von B+. `REST_BASE` wie im Adapter (`automation/adapters/etoro_execution.py`, `_REST_BASE[env]` mit `env = account_guard.verify_paper_account(...)`).
- Status nach jeder Order über `GET /api/v1/trading/info/<env>/orders/{orderId}` prüfen (eToro lehnt teils asynchron ab), Fill und Bid/Ask zum Absendezeitpunkt protokollieren, nach mindestens 1 Minute schliessen: `POST {REST_BASE}/market-close-orders/positions/{positionId}` mit `{"InstrumentId": <id>, "UnitsToDeduct": null}`. Gebühren aus `GET /api/v1/trading/info/<env>/pnl`.
- Ausgabe `stage_c/small_orders.csv`: `order_id, position_id, instrument, klasse, quelle_universum, submit_ts, bid, ask, fill_px, units, status, error_code, fees_usd`. Kennzahlen: Annahmequote, Spread beim Fill, Slippage, Gebühren je Order.
- **Kosten-Rekalibrierung:** weicht die gemessene Slippage oder Gebühr einer Klasse um mehr als 2 bps bzw. 20 % vom Modell ab, rechnest du Stufe A mit den gemessenen Werten neu (vorregistrierte Pflicht); das Verdikt stützt sich dann auf die rekalibrierte Rechnung.

## 10. Metriken (Definitionen)

- `r_t`: Netto-Tagesrendite des Portfolios aus Equity inkl. unrealisierter P&L und aller Kosten; `SR = mean(r) / std(r) · √252`.
- `DD`: Betrag des maximalen Drawdowns der Equity; `CVaR5`: Mittel der schlechtesten 5 % der Tagesrenditen.
- Netto-Expectancy je Trade in bps auf das Entry-Notional; Profit-Faktor; Trefferquote; Turnover.
- Anteil verworfener Kandidaten (§7.3); Auslastung = mittlere Zahl offener Positionen / Plätze.
- Netto-Beitrag in pp Equity = Σ Netto-bps / 10⁴ × f × 100 (ohne Zinseszins, wie `evidence_h4_h8.py`).

## 11. Statistik und Entscheidungsregel

- **Portfolio-Ebene:** ΔSR mit gepaartem Stationary Bootstrap (`portfolio.delta_sharpe_ci(r_a, r_b)`: gemeinsame Indizes, Blocklänge `optimal_block_length` der Differenzreihe, 5000 Replikate, Seed 20261011, 90-%-Perzentil-KI).
- **Trade-Ebene:** Cluster-Bootstrap nach Kalendermonat des Einstiegs (5000 Replikate, Seed 20261011, 90-%-KI; Umsetzung wie `month_sums`/`boot_mean_diff` in `experiments/ebc/h3_stage_a.py`), für P4.1 als Ein-Stichproben-KI des Mittelwerts.
- Primärentscheidung auf Stufe A (inkl. Rekalibrierung aus C); Stufe B schwächt nur ab (§8).

| Verdikt | Bedingung |
|---|---|
| `BESTAETIGT` | P4.1, P4.2 und P4.3 erfüllt, Stufe B widerspricht nicht |
| `WIDERLEGT` | Kriterium aus §2.3 erfüllt |
| `UNENTSCHIEDEN` | sonst; nenne die fehlende Evidenz (Forward-Tage, Zahl der `U_neu`-Instrumente, Kostenmessung) |

## 12. Ausgaben

```
results/H4/
  preregistration.json   preflight.json   universe_neu.csv   costs_calibration.json
  stage_a/ trades/<arm>.parquet   portfolio_daily_returns.csv   capacity.csv   dropped_candidates.csv
           outliers.csv   regimes.csv   parity_route_r_v.json
  stage_b/ forward_daily_returns.csv   weekly_<KW>.md
  stage_c/ small_orders.csv   cost_recalibration.json
  metrics.json   verdict.json   report.md
```

`verdict.json` mit denselben Feldern wie EBC-H1 (`hypothesis: "H4"`, `repo_commit` = HEAD des Branches), zusätzlich:

```json
{
  "capacity": {
    "C30": {"slots": 30, "notional": 0.02, "n_trades": 0, "sr": 0.0, "maxdd": 0.0, "expectancy_bps": 0.0,
            "dropped_share": 0.0, "dropped_expectancy_bps": 0.0, "mean_open_positions": 0.0, "n_trades_ge_2000bps": 0},
    "C60": {}, "C120": {}, "C120plus": {}, "N30": {}, "C30_K1": {}, "C30r": {}, "Cinf": {}
  },
  "decomposition_dsr": {"capacity_C120_minus_C30": 0.0, "breadth_C120plus_minus_C120": 0.0},
  "universe_neu": {"n_drawn": 0, "n_quality_ok": 0, "n_after_cost_filter": 0, "sha256": "<sha256>"},
  "costs": {"spread_source": "gemessen|rueckfall", "n_instruments_measured": 0},
  "stage_c": {"status": "OK|BLOCKIERT|NICHT_FREIGEGEBEN", "n_orders": 0, "acceptance_rate": 0.0, "min_fee_usd": 0.0}
}
```

`report.md` (Deutsch, ≤ 3 Seiten): Verdikt im ersten Satz, Tabelle der Vorhersagen, Kapazitätstabelle (Arm × Trades, SR, DD, Expectancy, verworfene Kandidaten, Auslastung), Replikation auf `U_neu`, Antwort auf F5 in drei Sätzen (Trades, Gewinn, Verluste).

## 13. Folgen je Verdikt

- **BESTAETIGT:** Issues im Katalogformat des Repos (Symptom / Root-Cause / Fix / Akzeptanzkriterien / Betroffene Dateien): (1) `automation/config/ebc_archetypes.json` `sizing`: 120 Plätze × 0,5 % bei Gesamtdeckel 0,60; (2) Universum `U_breit` ∪ `U_neu` (nur K1, falls P4.6 erfüllt) mit gemessenem Kostenfilter und vierteljährlicher Neuziehung; (3) Zähler verworfener Signale in der Portfolio-Simulation und als Telemetrie im Live-Allocator; (4) Kapazitätsstufe als Baustein von B++ in EBC-H8.
- **WIDERLEGT:** (a) ⇒ der Edge ist universumsspezifisch: H1-Verdikt einschränken und die `U_breit`-Ergebnisse als in-sample behandeln; (b) ⇒ Kapazität bei 30 Plätzen lassen, Ursache aus dem Wert der verworfenen Kandidaten (§7.3) dokumentieren.
- **UNENTSCHIEDEN:** `U_neu` um weitere 400 Instrumente erweitern (Seed 20261013); Stufe B um 30 Handelstage verlängern.

## 14. Ablauf-Checkliste

- [ ] S0 Setup, Preflight bestanden (inkl. Paritätstest C30 = B+), Isolation dokumentiert
- [ ] `U_neu` gezogen, Daten geladen, Qualitätsfilter angewendet, Route-R-Gegenprobe bestanden
- [ ] Spreads an ≥ 2 Handelstagen gemessen, Kostentabelle festgelegt
- [ ] `preregistration.json` geschrieben (vor jeder Auswertung)
- [ ] Stufe A: alle Arme aus §6, verworfene Kandidaten, Ausreisser, Auslastung, Regime
- [ ] Stufe B gestartet; Stufe C nach Freigabe gestartet (oder `NICHT_FREIGEGEBEN`/`BLOCKIERT`)
- [ ] Kosten-Rekalibrierung geprüft
- [ ] `verdict.json` und `report.md` geschrieben
