---
ebc_prompt_id: EBC-H7
titel: "Signal-Breite: drei zusätzliche, wenig korrelierte Archetypen (IBS-Rücksetzer, 12-1-Momentum, Monatswechsel) mit eigenen Platzquoten"
version: 1.0.0
erstellt_am: 2026-10-11
quelle_repository: https://github.com/philibertschlutzki/etoro_nautilus
quelle_commit: 40671219c3e007ea268c5d48ff5573cafaef88a7
beantwortet_fragen: [F5, F4]
ziele: {gewinn: "+", verluste: "Nebenbedingung", trades: "++"}
konto_modus: nur_demo
stufen: [S0_setup, SA_historisch, SB_forward_shadow]
mindestdauer: {SA: "Rechenzeit, typisch < 1 Tag", SB: ">= 30 Handelstage (mindestens 2 Monatswechsel)"}
ergebnisordner: results/H7/
primaere_endpunkte: [P7.1, P7.2, P7.3]
seed: 20261011
abhaengig_von: "EBC-H1 (T/M/V, Vergleichszeitraum), Kostentabelle EBC-H4 §5; U_neu aus EBC-H4 optional"
---

# EBC-H7 — Signal-Breite statt Instrument-Breite

## 0. Auftrag

Du prüfst die vorregistrierte Hypothese H7 gegen reale eToro-Tagesdaten. Es werden keine Orders gesendet (die neuen Archetypen handeln wie B+ nur Market-Orders long; die Ausführungsmechanik prüft EBC-H8). Ergebnis: Verdikt `BESTAETIGT | WIDERLEGT | UNENTSCHIEDEN` mit allen Messwerten in `results/H7/`.

Beantwortete Fragen:
- **F5** — Gewinne maximieren, Verluste reduzieren, Trades erhöhen. Hier: Trades und Gewinn über neue, wenig korrelierte Signale bei gleicher Kapazität; Verluste als Nebenbedingung.
- **F4** (Teil) — Fehlende Features: Signale auf anderen Zeitskalen (1–3 Tage, Monate), Querschnitts-Ranking, Kalendersignale, Platzquoten je Signalart.

## 1. Harte Regeln

1. **Keine Orders.** Lesende API-Aufrufe nur für Stufe B, ≤ 1 Request/s, Keys nie loggen.
2. **Produktion unberührt.** Branch `ebc/h7`; neue Archetypen nur in `experiments/ebc/` (keine Strategieklassen unter `automation/strategies/`).
3. **Vorregistrierung** in `results/H7/preregistration.json` vor jeder Auswertung: Spezifikation aus §6.1, Universumslisten je Archetyp (inkl. der ETF-Liste für W), Quoten, Kostentabelle, Seeds, SHA-256 dieser Datei.
4. **Keine Parametersuche.** Die Parameter der neuen Archetypen stehen in §6.1 fest; keine Varianten ausser den Diagnose-Armen aus §6.2.
5. `manuals/ebc/EBC_H1.md`–`EBC_H6.md` und `results/H1`–`results/H6` nicht ändern.

## 2. Hypothese

### 2.1 Behauptung (H7)

Die Sharpe von B+ ist durch die Korrelationsdecke seiner drei Archetypen begrenzt, nicht durch die Zahl der Instrumente: T und V sind beide Ausbrüche, M ist der einzige Rücksetzer, alle drei sind Preis-Muster auf denselben Aktien. Drei einfache, in der Literatur vielfach replizierte Signale mit anderem Auslöser und anderer Zeitskala — **R** (Schluss nahe am Tagestief im Aufwärtstrend, 1–3 Tage), **X** (12-1-Monats-Momentum im Querschnitt, Monate), **W** (Monatswechsel, 4 Tage, kalendergetrieben) — sind netto positiv, korrelieren schwach mit T/M/V und nutzen einen Platz besser als eine weitere T/M/V-Position. Bei gleicher Kapazität (120 Plätze × 0,5 %) erhöht die Kombination aus sechs Archetypen die Zahl der Trades um mindestens die Hälfte und die Netto-Sharpe, ohne den Drawdown zu vertiefen.

**H0:** Die neuen Archetypen sind nach Kosten nicht positiv oder so stark mit T/M/V korreliert, dass die Kombination die Sharpe nicht erhöht.

### 2.2 Vorhersagen (vorregistriert)

| ID | Vorhersage | Messgrösse | Schwelle | Rolle |
|---|---|---|---|---|
| P7.1 | Die neuen Archetypen sind netto positiv | Netto-Expectancy je Trade je Archetyp (Sleeve allein, §6.2); Alpha der Sleeve-Tagesrenditen gegen SPX500 | R: Punktschätzer > 0 mit unterer 90-%-KI-Grenze > 0; X und W: Punktschätzer > 0 **und** Alpha-Punktschätzer > 0 | primär |
| P7.2 | Sie diversifizieren | Korrelation der Sleeve-Tagesrenditen mit dem Portfolio TMV-Q; Korrelation R mit dem M-Sleeve | R, X, W je ≤ 0,5 **und** corr(R, M) ≤ 0,6 | primär |
| P7.3 | Sechs Archetypen nutzen dieselben Plätze besser als drei | Kombi6-Q gegen TMV-Q: Trades, ΔSR, DD | Trades ≥ 1,5 × **und** ΔSR-Punktschätzer > 0 mit unterer 90-%-KI-Grenze > −0,10 **und** DD ≤ DD(TMV-Q) + 1 pp | primär |
| P7.4 | R ist kein M-Duplikat | Anteil der R-Signale mit einem M-Signal auf demselben Instrument innerhalb ±1 Handelstag | ≤ 50 % | sekundär |
| P7.5 | Quoten schützen den rechten Rand | Kombi6-P (gemeinsamer Pool, Vorrang nach q) gegen Kombi6-Q: Zahl der T-Trades, Netto-Beitrag von T, SR | berichten (Verdrängung langer T-Positionen durch kurzlebige R-Signale) | sekundär (Diagnose) |
| P7.6 | Additiver Ausbau | TMV120 (EBC-H4 Arm C120) plus R/X/W mit je 20 zusätzlichen Plätzen × 0,5 %: SR und vola-gleicher DD gegen TMV120 | berichten | sekundär |

### 2.3 Widerlegung

H7 ist **widerlegt**, wenn (a) die Netto-Expectancy von R eine obere 90-%-KI-Grenze < 0 hat (der Archetyp mit den meisten zusätzlichen Trades verliert klar) **oder** (b) ΔSR(Kombi6-Q − TMV-Q) eine obere 90-%-KI-Grenze < 0 hat. Sonst, bei unerfüllten primären Vorhersagen: `UNENTSCHIEDEN`.

### 2.4 Begründung

Aus dem Repository (Stand `4067121`, reproduzierbar mit `python experiments/ebc/evidence_h4_h8.py`):

- **G1 Korrelationsdecke.** Mittlere Korrelation der Teilportfolios (Instrument × Archetyp) in K1 ρ̄ = 0,024 ⇒ N_eff → 1/ρ̄ ≈ 41. Mittlere SR der Aktien-Teilportfolios in H2: 0,21 bei N = 50, 0,25 bei allen 984 — mehr Instrumente mit denselben Signalen heben die Sharpe kaum. Neue Signalarten senken die mittlere Korrelation und heben die Decke.
- **G2 Gleichförmige Signale.** T (Schluss über dem 50-Tage-Hoch) und V (Schluss über dem oberen Bollinger-Band nach Kompression) sind beide Ausbruchssignale; M (RSI2 < 10 über SMA200) ist der einzige Rücksetzer. Median-Haltedauer M 5, V 24, T 26 Kalendertage; kein Signal mit Querschnitts-Ranking, keines mit Kalenderauslöser.
- **G3 Marktabhängigkeit.** B+ hat gegen den gleichgewichteten Aktien-Benchmark Beta 0,70 und Alpha 20 % p. a.; Verluste konzentrieren sich in Regime R4 (SR −2,53) und an den 5 % grössten Index-Tagen (SR −2,95) (`results/H1/report.md`). Die Korrelation der Tagesrenditen von B+ und der Status-quo-Auswahl A1 beträgt 0,68.
- **G4 Trade-Engpass auf der Signalseite.** B+ macht 1141 Trades in 471 Handelstagen auf 426 Instrumenten; Ausbrüche (T, V) sind konstruktionsbedingt selten (365 bzw. 152 Trades).

Ausserhalb des Repositorys (nur zur Begründung der Auswahl, keine Evidenz für eToro): IBS-Effekt auf Aktienindex-ETFs (Pagonidis 2014, NAAIM); Querschnitts-Momentum 12-1 (Jegadeesh/Titman 1993; Asness/Moskowitz/Pedersen 2013); Monatswechsel-Effekt (Ariel 1987; Lakonishok/Smidt 1988; McConnell/Xu 2008).

## 3. Schritt 0 — Setup

Wie EBC-H4 §3 (Branch `ebc/h7`, Preflight mit Vorfahren-Prüfung `4067121`, Paritätstest C30 = B+, `mkdir -p results/H7/stage_a results/H7/stage_b`). Konto-Prüfungen entfallen bis auf den Nachweis, dass keine Order-Funktion aufgerufen wird.

## 4. Universum und Daten

- `U_breit` nach Kostenfilter q ≤ 0,10 (426 Instrumente), Stichtag der Kerzen nach EBC-H4 §4.3. Liegt `U_neu` aus EBC-H4 vor, rechnest du P7.1–P7.3 zusätzlich auf `U_breit` ∪ `U_neu` (nur berichten).
- Universum je Archetyp: T, M, V wie B+; R: K1 und K4; X: K1; W: K4 sowie die breiten Aktienindex-ETFs des Universums (Liste aus den Symbolen vorregistrieren, z. B. SPY, IVV, VOO, QQQ, IWM, DIA, VTI, EFA, EEM, VEA, VWO — nur die vorhandenen).
- Kostentabelle von EBC-H4 (`results/H4/costs_calibration.json`), sonst EBC-H4 §5 zuerst ausführen.

## 5. Kostenmodell

Wie EBC-H4 §5. Kurze Haltedauern (R, W) machen die Spread-Messung entscheidend: R und W werden nur auf Instrumenten mit gemessenem Spread gerechnet; fehlt die Messung, gilt der Rückfallwert mit dem Zuschlag P75 − P50 der gemessenen Instrumente derselben Klasse (vorregistriert).

## 6. Archetypen, Sleeves und Arme

### 6.1 Spezifikation der neuen Archetypen

Indikatoren auf Tageskerzen wie EBC-H1 §6.1 (ATR20 Wilder, SMA_n). Alle drei nur long. Exits nach der Semantik von B+ (Trailing-Stop auf Schlusskurs, Ausführung zum nächsten Open; Katastrophen-Stop `clamp(3 · k · ATR20 %, 2 %, 15 %)` intrabar), soweit unten nichts anderes steht.

**R — IBS-Rücksetzer** (Parameter: θ = 0,15, H = 3)
- `IBS_t = (C_t − L_t) / (H_t − L_t)`; Kerzen mit H_t = L_t erzeugen kein Signal.
- Long, wenn `C_t > SMA200_t` und `IBS_t < 0,15`; Einstieg zum nächsten Open.
- Exit: `C_t > H_{t−1}` (Schluss über dem Vortageshoch) ⇒ nächster Open; Zeitstopp 3 Handelstage; Trailing-Stop 3 · ATR20; Katastrophen-Stop mit k = 3.

**X — 12-1-Momentum im Querschnitt** (Parameter: Top 20, Puffer 40)
- Monatsende = letzter Handelstag des Kalendermonats im gemeinsamen Kalender (`portfolio.Calendar`, Tag mit SPX500-Kerze). Rang aller X-Instrumente mit Schlusskurs an diesem Tag und ≥ 253 Kerzen nach `MOM_t = C_{t−21} / C_{t−252} − 1` (Handelstage des Instruments).
- Einstieg zum nächsten Open für die Ränge 1–20, die nicht schon gehalten werden — nur wenn SPX500 am Monatsende über seiner SMA200 schliesst (Marktfilter); sonst in diesem Monat keine neuen X-Positionen.
- Exit zum nächsten Open nach einem Monatsende, an dem der Rang > 40 ist (Puffer); Trailing-Stop 4 · ATR20; Katastrophen-Stop mit k = 4; kein Zeitstopp.

**W — Monatswechsel** (Parameter: Einstieg letzter Handelstag, H = 4)
- Signal am Schlusskurs des vorletzten Handelstags des Monats (Kalender des Instruments), Einstieg zum Open des letzten Handelstags, Exit zum Open des 4. Handelstags des Folgemonats (Haltedauer 4 Handelstage).
- Kein Trailing-Stop (Option `opts["trailing"] = False`), kein Signal-Exit; Katastrophen-Stop mit k = 3.

Umsetzung: R und W als Einträge in `experiments/ebc/sim.py` (`ARCH`, `Series.signals`, `Series.signal_exit`, Option `trailing`), X als eigenes Modul `experiments/ebc/h7_momentum.py` (Querschnitt über alle Serien, Ausgabe im Trade-Format von `sim.walk`). Gegenprobe: je neuem Archetyp 20 zufällige Trades von Hand aus den Kerzen nachrechnen (Signal, Einstieg, Ausstieg, Grund, Brutto-bps) und in `stage_a/handcheck.csv` dokumentieren; jede Abweichung klären, bevor du auswertest.

### 6.2 Sleeves und Arme

Ein Sleeve ist ein Archetyp mit eigener Platzquote. Alle Arme teilen eine gemeinsame Equity (Startkapital 10 000 USD), Notional 0,5 % der Equity je Position, Gesamtdeckel 60 %; innerhalb eines Sleeves Vorrang nach aufsteigendem q (X: nach `MOM`, W: nach q). Umsetzung als `portfolio.simulate_sleeves(trades_by_sleeve, quotas, notional_frac, cap_total)` mit gemeinsamer Equity; für einen einzigen Sleeve mit Quote N muss das Ergebnis `simulate(..., slots=N)` entsprechen (Test).

| Arm | Sleeves × Quote | Zweck |
|---|---|---|
| **TMV-Q** | T 40, M 40, V 40 | Referenz |
| **Kombi6-Q** | T 20, M 20, V 20, R 20, X 20, W 20 | Kandidat (P7.3) |
| **R-allein**, **X-allein**, **W-allein** | je 20 | P7.1, P7.2 |
| **M-allein** | 20 | P7.2 (corr R/M), P7.4 |
| **Kombi6-P** | alle sechs, gemeinsamer Pool 120 | P7.5 |
| **TMV120** und **TMV120+RXW** | Pool 120 (wie EBC-H4 C120) bzw. Pool 120 plus R/X/W je 20, Gesamtdeckel 90 % | P7.6 (SR skalenfrei, DD vola-gleich) |

## 7. Stufe A — Historische Auswertung

### 7.1 Trades und Sleeves
Trades je Archetyp über den Vergleichszeitraum 2024-12-23 bis 2026-10-12 (Einstieg im Zeitraum, Aufwärmphase davor). Je Archetyp: Trades, Netto-Expectancy, Trefferquote, Profit-Faktor, Median-Haltedauer, Kostenanteil, Netto-Beitrag (pp), Expectancy je Klasse.

### 7.2 Alpha und Korrelation (P7.1, P7.2)
- Alpha: OLS der Sleeve-Tagesrenditen auf die SPX500-Tagesrenditen (Achsenabschnitt × 252); KI per Stationary Bootstrap der Residuen (5000, Seed 20261011).
- Korrelation der Tagesrenditen: jeder neue Sleeve mit TMV-Q und mit M-allein; zusätzlich in den 10 % schlechtesten Tagen von TMV-Q (Absicherungswirkung).

### 7.3 Kombination (P7.3)
Kombi6-Q gegen TMV-Q: SR, ΔSR mit KI, DD, CVaR5, Trades je Sleeve, Auslastung je Sleeve, Netto-Beitrag je Sleeve. Zerlegung: Welche Sleeves tragen die Differenz, und was verliert T/M/V durch die halbierte Quote (verworfene Kandidaten je Sleeve, Zähler aus EBC-H4 §7.3)?

### 7.4 Überlappung R/M (P7.4)
Anteil der R-Signale mit einem M-Signal auf demselben Instrument im Fenster ±1 Handelstag; Netto-Expectancy der R-Signale mit und ohne M-Überlappung getrennt.

### 7.5 Szenario-Matrix
Regime R1–R4, Top-5-%-Index-Tage und Monatswechsel-Tage für TMV-Q und Kombi6-Q; je Sleeve die mittlere Tagesrendite je Regime.

## 8. Stufe B — Forward-Shadow (≥ 30 Handelstage)

Über `experiments/ebc/stage_b_multi.py` (EBC-H4 §8): Arme `TMV-Q` und `Kombi6-Q`; Ausgabe `results/H7/stage_b/forward_daily_returns.csv`, wöchentlicher Zwischenbericht ohne Regeländerung. Der Zeitraum muss mindestens zwei Monatswechsel enthalten (X, W). Stufe B schwächt nur ab: hat ΔSR_forward(Kombi6-Q − TMV-Q) eine obere 90-%-KI-Grenze < −0,50, wird ein `BESTAETIGT` zu `UNENTSCHIEDEN`.

## 9. Stufe C

Entfällt; reale Ausführung der Kombination in EBC-H8.

## 10. Metriken (Definitionen)

Wie EBC-H4 §10. Zusätzlich: Alpha (annualisierter Achsenabschnitt der Regression auf SPX500), Korrelation der Tagesrenditen, Auslastung je Sleeve, verworfene Kandidaten je Sleeve.

## 11. Statistik und Entscheidungsregel

- Trade-Ebene: Cluster-Bootstrap nach Einstiegsmonat (5000, Seed 20261011, 90 %).
- Portfolio-Ebene: `portfolio.delta_sharpe_ci` (gepaart, stationär, 5000, Seed 20261011, 90 %).
- P7.1 und P7.2 werden je Archetyp beurteilt; für `BESTAETIGT` müssen alle drei neuen Archetypen P7.1 und P7.2 erfüllen. Erfüllt nur ein Teil, ist das Verdikt `UNENTSCHIEDEN`, und `verdict.json` nennt die Archetypen, die einzeln bestehen (Eingang in B++ nach EBC-H8 §6).

| Verdikt | Bedingung |
|---|---|
| `BESTAETIGT` | P7.1, P7.2 (für R, X, W) und P7.3 erfüllt, Stufe B widerspricht nicht |
| `WIDERLEGT` | Kriterium aus §2.3 erfüllt |
| `UNENTSCHIEDEN` | sonst; nenne die fehlende Evidenz und die einzeln bestehenden Archetypen |

## 12. Ausgaben

```
results/H7/
  preregistration.json   preflight.json   universe_by_archetype.json
  stage_a/ trades/<archetyp>.parquet   handcheck.csv   sleeves_daily_returns.csv
           archetypes.csv   alpha_corr.json   combination.json   overlap_rm.csv   regimes.csv
  stage_b/ forward_daily_returns.csv   weekly_<KW>.md
  metrics.json   verdict.json   report.md
```

`verdict.json` mit denselben Feldern wie EBC-H1 (`hypothesis: "H7"`), zusätzlich:

```json
{
  "archetypes": {"R": {"n_trades": 0, "expectancy_bps": 0.0, "ci90": [0.0, 0.0], "alpha_ann": 0.0, "corr_tmv": 0.0, "passed_p71_p72": false},
                 "X": {}, "W": {}},
  "corr_R_M": 0.0,
  "overlap_R_M": 0.0,
  "combination": {"TMV-Q": {"sr": 0.0, "maxdd": 0.0, "n_trades": 0}, "Kombi6-Q": {"sr": 0.0, "maxdd": 0.0, "n_trades": 0},
                  "delta_sr": 0.0, "ci90": [0.0, 0.0]},
  "diagnostics": {"Kombi6-P": {"sr": 0.0, "n_trades_T": 0}, "TMV120": {"sr": 0.0}, "TMV120+RXW": {"sr": 0.0, "maxdd_volmatched": 0.0}},
  "archetypes_for_bplusplus": []
}
```

`report.md` (Deutsch, ≤ 3 Seiten): Verdikt, Vorhersagetabelle, Steckbrief je neuem Archetyp (Trades, Expectancy, Alpha, Korrelation), Kombinationstabelle, Antwort auf F5 in drei Sätzen.

## 13. Folgen je Verdikt

- **BESTAETIGT:** Issues: (1) Archetypen R, X, W als Tagesachsen-Strategien nach dem Vertrag in `automation/AGENTS.md` (Exit-Check zuerst, Indikatoren immer füttern, `tags=self._entry_order_tags(bar)`), Spezifikation in `automation/config/ebc_archetypes.json`; (2) Platzquoten je Archetyp im Allocator; (3) Baustein von B++ in EBC-H8.
- **WIDERLEGT:** (a) ⇒ R verwerfen, X und W nur bei eigenem Nachweis weiterverfolgen; (b) ⇒ Plätze bleiben bei T/M/V; dokumentieren, welcher Sleeve die Sharpe senkt.
- **UNENTSCHIEDEN:** einzeln bestehende Archetypen in B++ aufnehmen (EBC-H8 §6), übrige mit `U_breit` ∪ `U_neu` und längerer Stufe B erneut prüfen.

## 14. Ablauf-Checkliste

- [ ] Setup, Preflight, Paritätstest C30 = B+
- [ ] R, W in `sim.py`, X in `h7_momentum.py`; Handprüfung von je 20 Trades ohne offene Abweichung
- [ ] `simulate_sleeves` mit Test gegen `simulate`
- [ ] `preregistration.json` geschrieben (vor jeder Auswertung)
- [ ] §7.1 Trades, §7.2 Alpha/Korrelation, §7.3 Kombination, §7.4 Überlappung, §7.5 Regime
- [ ] Stufe B gestartet (mindestens zwei Monatswechsel)
- [ ] `verdict.json` und `report.md` geschrieben
