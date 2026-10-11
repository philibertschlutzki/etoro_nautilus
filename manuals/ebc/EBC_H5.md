---
ebc_prompt_id: EBC-H5
titel: "Haltedauer nach Archetyp und Kapazität: Rücksetzer kurz halten, Trend- und Ausbruchsgewinner laufen lassen, Positionen ohne Fortschritt freigeben"
version: 1.0.0
erstellt_am: 2026-10-11
quelle_repository: https://github.com/philibertschlutzki/etoro_nautilus
quelle_commit: 40671219c3e007ea268c5d48ff5573cafaef88a7
beantwortet_fragen: [F5, F4]
ziele: {gewinn: "+", verluste: "+", trades: "+"}
konto_modus: nur_demo
stufen: [S0_setup, SA_historisch, SB_forward_shadow]
mindestdauer: {SA: "Rechenzeit, typisch < 1 Tag", SB: ">= 30 Handelstage"}
ergebnisordner: results/H5/
primaere_endpunkte: [P5.1, P5.2]
seed: 20261011
abhaengig_von: "EBC-H1 §4–§7 (Archetypen, Kosten, Vergleichszeitraum), EBC-H3 §7.3 (Zeitbox), Kapazitätsstufen aus EBC-H4 §6 (ohne H4-Ergebnis rechenbar)"
---

# EBC-H5 — Haltedauer × Kapazität

## 0. Auftrag

Du prüfst die vorregistrierte Hypothese H5 gegen reale eToro-Tagesdaten mit der Kostentabelle von EBC-H4 (gemessene Spreads, sonst Rückfallwerte). Es werden keine Orders gesendet. Ergebnis: Verdikt `BESTAETIGT | WIDERLEGT | UNENTSCHIEDEN` mit allen Messwerten in `results/H5/`.

Beantwortete Fragen:
- **F5** — Gewinne maximieren, Verluste reduzieren, Trades erhöhen. Hier: über die Haltedauer je Archetyp, abgestimmt auf die Kapazität.
- **F4** (Teil) — Fehlende Features: Zeitstopp je Archetyp und Kapazität statt eines festen Werts, Ausstieg bei fehlendem Fortschritt.

## 1. Harte Regeln

1. **Keine Orders.** H5 ist eine reine Konfigurationsfrage; Stufe C entfällt. Lesende API-Aufrufe nur für Stufe B (Kerzen), ≤ 1 Request/s, Keys nie loggen.
2. **Produktion unberührt.** Arbeit auf Branch `ebc/h5` im Experiment-Arbeitsverzeichnis von H1–H4; keine Änderung an `automation/config/*.json`.
3. **Vorregistrierung** in `results/H5/preregistration.json` vor jeder Auswertung (Konfigurationen aus §6, Gitter, Kapazitätsstufen, Kostentabelle, Seeds, SHA-256 dieser Datei). Schwellen nie nachträglich ändern.
4. **Keine Parametersuche über §6 hinaus.** Entry-Signale und Stops exakt wie B+ (EBC-H1 §6.1); variiert werden nur die Zeitstopps, der Ausstieg ohne Fortschritt (NP10) und die Kapazität. Das Gitter aus §7.4 dient nur der Überanpassungs-Diagnose; das Verdikt stützt sich ausschliesslich auf die vorregistrierten Konfigurationen.
5. `manuals/ebc/EBC_H1.md`–`EBC_H4.md` und `results/H1`–`results/H4` nicht ändern.

## 2. Hypothese

### 2.1 Behauptung (H5)

Die Haltedauer ist im B+-Portfolio ein Kapazitätshebel, kein reiner Signalhebel. Zwei Mechanismen:

1. **Rücksetzer (M) halten Verlierer zu lange.** Die Gewinner von M verlassen den Markt über den Signal-Exit nach wenigen Tagen; was nach 5 Handelstagen noch offen ist, ist überwiegend ein Verlierer, und die Katastrophen-Stops von M fallen im Median auf den dritten Tag. Ein Zeitstopp von 1 Handelstag kostet einen Teil der Signal-Exit-Gewinne, begrenzt aber Zeitstopp- und Katastrophen-Stop-Verluste und gibt Plätze frei. Bei knapper Kapazität überwiegt der Gewinn an Plätzen: mehr Trades, weniger Verlust, mindestens gleiche Sharpe.
2. **Trend und Ausbruch (T, V) verdienen am rechten Rand.** Ihr Ertrag kommt aus wenigen langen Haltedauern. Ob ein langer Zeitstopp hilft, hängt von der Kapazität ab: Bei 30 Plätzen verdrängen lange T-Positionen neue Kandidaten, kürzere Haltedauern sind besser; bei 120 Plätzen kostet langes Halten keine verdrängten Kandidaten, und T ohne Zeitstopp ist besser. Das optimale H_T steigt mit der Kapazität (Interaktion).

**H0:** Die optimale Haltedauer ist unabhängig von der Kapazität, und ein kürzerer Zeitstopp für M ist der Status quo (5 Tage) nicht überlegen.

### 2.2 Vorhersagen (vorregistriert)

| ID | Vorhersage | Messgrösse | Schwelle | Rolle |
|---|---|---|---|---|
| P5.1 | M kurz halten verbessert das Portfolio bei knapper Kapazität | Konfiguration HM gegen H0 bei C30: ΔSR; Zahl der M-Trades; Netto-Beitrag der M-Katastrophen-Stops (pp Equity) | ΔSR-Punktschätzer > 0, untere 90-%-KI-Grenze > −0,20 **und** M-Trades ≥ 1,5 × **und** Verlust aus M-Katastrophen-Stops ≤ 0,5 × | primär |
| P5.2 | Optimales H_T steigt mit der Kapazität | Interaktionskontrast I = [SR(C120, T∞) − SR(C120, T20)] − [SR(C30, T∞) − SR(C30, T20)], M und V wie H0 | I > 0 mit unterer 90-%-KI-Grenze > 0 | primär |
| P5.3 | Gewinner laufen lassen bei reichlicher Kapazität | HL gegen H0 bei C120: ΔSR, Netto-Beitrag (pp), DD | ΔSR-Punktschätzer > 0, Netto-Beitrag höher, DD ≤ DD(H0) + 2 pp | sekundär |
| P5.4 | Ausstieg ohne Fortschritt (NP10) gibt Plätze frei, ohne Gewinner zu kosten | H0+NP gegen H0 bei C30 und C120: Trades, gepaarte ΔExpectancy der betroffenen Trades, ΔSR | bei C30 Trades ≥ 1,10 ×, ΔExpectancy-Punktschätzer ≥ 0, ΔSR-Punktschätzer ≥ 0 | sekundär |
| P5.5 | Das Gitter-Optimum ist überangepasst | PBO der Auswahl der besten von 27 Konfigurationen (CSCV) je Kapazität | berichten; PBO ≥ 0,50 ⇒ nur vorregistrierte Konfigurationen empfehlen | sekundär (Diagnose) |

### 2.3 Widerlegung

H5 ist **widerlegt**, wenn (a) bei C30 ΔSR(HM − H0) eine obere 90-%-KI-Grenze < 0 hat (5 Tage für M klar besser) **oder** (b) der Interaktionskontrast I eine obere 90-%-KI-Grenze < 0 hat (langes Halten von T nützt bei knapper Kapazität mehr als bei reichlicher). Sonst, bei unerfüllten primären Vorhersagen: `UNENTSCHIEDEN`.

### 2.4 Begründung aus dem Repository (Stand `4067121`)

Reproduzierbar mit `python experiments/ebc/evidence_h4_h8.py` (`results/evidence_h4_h8.json`); Haltedauern in Kalendertagen.

- **D1 M-Verlierer am Zeitstopp.** Von 624 M-Trades in B+ enden 435 über den Signal-Exit (`C > SMA5`): Mittel +470 bps, Trefferquote 80 %, Median-Haltedauer 4 Tage, +40,9 pp Equity. 139 enden am 5-Tage-Zeitstopp: Mittel −363 bps, Trefferquote 20 %, Median-Haltedauer 7 Tage, −10,1 pp. Dazu 37 M-Katastrophen-Stops mit −11,6 pp (13,2 % der Bruttoverluste von B+).
- **D2 Rechter Rand von T und V.** T: 365 Trades, Median −375 bps, Trefferquote 39,7 %, Mittel +492 bps. Die 30 T-Trades am 60-Tage-Zeitstopp sind alle Gewinner: Mittel +4510 bps, Median-Haltedauer 91 Tage, +27,1 pp = 42 % des Netto-Gewinns von B+. V: Median −260 bps, Trefferquote 40,1 %; die 58 V-Trades am 20-Tage-Zeitstopp haben 80 % Trefferquote, Mittel +1763 bps, +20,5 pp. Der Zeitstopp schneidet laufende Gewinner ab.
- **D3 Zeitbox in H3 (je Archetyp allein, 30 Plätze, `experiments/ebc/h3_stage_a.py:229–240`, Periode ab Kerze 251 und damit länger als der Vergleichszeitraum).** T: SR 1,21 / 1,25 / 1,07 bei h = 10 / 20 / 40, Expectancy steigt von 5 auf 213 bps (h = 1 → 40), die Trade-Zahl fällt von 10 309 auf 1014. M: SR 1,09 bei h = 1 (7842 Trades) gegen 0,96 bei h = 5 (4301 Trades). V: nicht monoton (SR 0,51 / 0,74 / 0,26 / 0,68 / 0,38 / 1,01 für h = 1 … 40) — durch Rauschen dominiert. Die Expectancy je Trade belohnt langes Halten, die knappe Kapazität bestraft es.
- **D4 Kapazität bindet.** Gleichzeitig offene B+-Positionen (Näherung): Median 27 von 30, an 46 % der Tage ≥ 28 (EBC-H4 §2.4 E2).
- **D5 Positionen ohne Fortschritt.** Die 98 T-Trades, die über den Trailing-Stop mit Verlust enden, kosten −15,5 pp (17,6 % der Bruttoverluste); solche Positionen belegen Plätze, bis der Stop greift.
- **D6 Zeitstopp im Code.** Ein Zeitstopp je Archetyp existiert bereits (`automation/config/ebc_archetypes.json`, `max_bars_in_trade` T 60 / M 5 / V 20; `experiments/ebc/sim.py` `ARCH`, Option `H`); ein Ausstieg ohne Fortschritt fehlt.

## 3. Schritt 0 — Setup

Wie EBC-H4 §3 (Branch `ebc/h5`, Preflight mit Vorfahren-Prüfung `4067121`, Paritätstest C30 = B+ mit H1-Rückfallkosten, `mkdir -p results/H5/stage_a results/H5/stage_b`). Konto-Prüfungen entfallen bis auf den Nachweis, dass keine Order-Funktion aufgerufen wird.

## 4. Universum und Daten

`U_breit` nach Kostenfilter q ≤ 0,10 (426 Instrumente), Stichtag der Kerzen nach EBC-H4 §4.3. `U_neu` wird nicht benötigt; liegt es aus H4 vor, rechnest du P5.1 und P5.2 zusätzlich auf `U_breit` ∪ `U_neu` (nur berichten).

## 5. Kostenmodell

Die vorregistrierte Kostentabelle von EBC-H4 (`results/H4/costs_calibration.json`: gemessene Spreads nach H4 §5, sonst Rückfallwerte). Liegt sie noch nicht vor, gilt H4 §5 unverändert: zuerst Spreads messen, dann rechnen. Finanzierung je Haltetag nach `costs.py`; lange Haltedauern tragen sie voll.

## 6. Konfigurationen und Kapazität

Haltedauern in Handelstagen; ∞ = kein Zeitstopp (`opts["H"] = 10**6`): T endet dann über Trailing-Stop, Signal-Exit (20-Tage-Tief) oder Katastrophen-Stop, V über Trailing-Stop oder Katastrophen-Stop. H_M = 1 heisst: Ausstieg zum Eröffnungskurs nach dem ersten Schlusskurs der Position (`walk`: `bars >= H`).

| Konfiguration | H_T | H_M | H_V | NP10 | Zweck |
|---|---|---|---|---|---|
| **H0** | 60 | 5 | 20 | nein | Status quo B+ |
| **HM** | 60 | 1 | 20 | nein | P5.1 |
| **T20** | 20 | 5 | 20 | nein | P5.2 |
| **T∞** | ∞ | 5 | 20 | nein | P5.2 |
| **H\*** | 20 | 1 | 40 | nein | H3-Optimum (Referenz für C30) |
| **HL** | ∞ | 1 | ∞ | nein | P5.3 |
| **H0+NP** | 60 | 5 | 20 | ja (T, V) | P5.4 |
| **HL+NP** | ∞ | 1 | ∞ | ja (T, V) | P5.4, nur berichten |

**NP10 — Ausstieg ohne Fortschritt** (nur T und V): Ist nach dem Schlusskurs des 10. Handelstags der Position der höchste Schlusskurs seit Einstieg (Short: der tiefste) nicht mindestens 1 · ATR20 (Wert am Signaltag) über (unter) dem Einstiegskurs, Ausstieg zum nächsten Eröffnungskurs, Grund `NO_PROGRESS`. Umsetzung als Option `opts["np"] = (10, 1.0)` in `sim.walk`, geprüft nach Stops und vor Zeitstopp und Signal-Exit.

**Kapazität** wie EBC-H4 §6: C30 = 30 Plätze × 2 %, C60 = 60 × 1 %, C120 = 120 × 0,5 %, jeweils Gesamtdeckel 60 %, Vorrang nach aufsteigendem q. Jede Konfiguration wird bei C30, C60 und C120 gerechnet; C60 nur berichten.

Alles Übrige wie B+: sequenziell, Seitenregel H1, Trailing-Stop auf Schlusskurs (S0), Katastrophen-Stop `clamp(3 · k · ATR20 %, 2 %, 15 %)`, kein TTP.

## 7. Stufe A — Historische Auswertung

### 7.1 Portfolio
Je (Konfiguration, Kapazität): `sim.sequential` mit `opts={"H": …, "np": …}` je Archetyp — `walk` kennt heute nur ein `H` je Aufruf; die Archetypen werden getrennt mit ihrem eigenen H erzeugt —, dann `portfolio.trade_vector` und `portfolio.simulate(..., slots=N, notional_frac=f)` über den Vergleichszeitraum 2024-12-23 bis 2026-10-12. Kennzahlen: SR, DD, CVaR5, Trades je Archetyp, Expectancy, Netto-Beitrag (pp) je Archetyp und Exit-Grund, verworfene Kandidaten (Zähler aus EBC-H4 §7.3), Auslastung.

### 7.2 Interaktionskontrast (P5.2)
Vier Tagesrenditen-Reihen r₁ = (C120, T∞), r₂ = (C120, T20), r₃ = (C30, T∞), r₄ = (C30, T20). Kontrast `I = SR(r₁) − SR(r₂) − SR(r₃) + SR(r₄)`. KI: gemeinsamer Stationary Bootstrap für alle vier Reihen (Indizes aus `portfolio.boot_indices`, Blocklänge `optimal_block_length` der Kontrastreihe `r₁ − r₂ − r₃ + r₄`, 5000 Replikate, Seed 20261011), 90-%-Perzentil-KI. Umsetzung als `portfolio.contrast_sharpe_ci(series, weights)`, das für zwei Reihen mit Gewichten (+1, −1) exakt `delta_sharpe_ci` reproduziert (Test).

### 7.3 Gepaarte Wirkung von NP10 (P5.4)
Eintragsgepaart wie EBC-H3 §7.1: jedes T- und V-Signal (steigende Flanke, `sim.independent`) einmal mit und einmal ohne NP10 bis zum Ausstieg verfolgen; nur Signale, bei denen NP10 auslöst, gehen in die Differenz ein. Kennzahlen: Anteil ausgelöster Signale, gepaarte ΔExpectancy (bps) mit Cluster-Bootstrap nach Einstiegsmonat, Anteil der NP10-Ausstiege, die ohne NP10 später ≥ +2000 bps erreicht hätten (verlorene Ausreisser).

### 7.4 Gitter und Überanpassung (P5.5)
Gitter H_T ∈ {20, 60, ∞} × H_M ∈ {1, 2, 5} × H_V ∈ {20, 40, ∞} (27 Konfigurationen) bei C30 und C120. Tagesrenditen der 27 Konfigurationen, CSCV mit 12 Gruppen und k = 6 (`automation/optimizer/cpcv.py`: `cpcv_paths`, `probability_of_backtest_overfitting`), Performance = Sharpe je Gruppe. Ausgabe: SR-Karte je Kapazität und PBO. Das Gitter bestimmt kein Verdikt.

### 7.5 Szenario-Matrix
Regime R1–R4 und Top-5-%-Index-Tage (`common.regime_array`) für H0, HM, H\* (C30) und HL (C120).

## 8. Stufe B — Forward-Shadow (≥ 30 Handelstage)

Über `experiments/ebc/stage_b_multi.py` (EBC-H4 §8): Arme `H0@C30`, `HM@C30`, `Hstar@C30`, `H0@C120`, `HL@C120`, täglich nach Kerzenschluss, Ausgabe `results/H5/stage_b/forward_daily_returns.csv`, wöchentlicher Zwischenbericht ohne Regeländerung. Stufe B schwächt nur ab: hat ΔSR_forward(HM − H0) bei C30 eine obere 90-%-KI-Grenze < −0,50, wird ein `BESTAETIGT` zu `UNENTSCHIEDEN`.

## 9. Stufe C

Entfällt. Die Umsetzung des Zeitstopps je Archetyp in der Live-Ausführung prüft EBC-H8.

## 10. Metriken (Definitionen)

Wie EBC-H4 §10. Zusätzlich: Netto-Beitrag (pp Equity) je Archetyp und Exit-Grund (`SIGNAL`, `TIME_BOX`, `TRAILING_STOP`, `DISASTER_STOP`, `NO_PROGRESS`, `DATA_END`); mittlere Haltedauer in Handelstagen; Plätze-Tage je Archetyp (Σ Haltedauer der genommenen Trades) als Mass der Kapazitätsbindung.

## 11. Statistik und Entscheidungsregel

- Portfolio: `portfolio.delta_sharpe_ci` (gepaart, stationär, 5000, Seed 20261011, 90 %); Kontrast nach §7.2.
- Trade-Ebene: Cluster-Bootstrap nach Einstiegsmonat (5000, Seed 20261011, 90 %).
- Mehrfachvergleiche: Die Primärentscheidung stützt sich auf genau zwei Tests (P5.1, P5.2); alle übrigen Vergleiche sind sekundär oder diagnostisch und werden unkorrigiert berichtet.

| Verdikt | Bedingung |
|---|---|
| `BESTAETIGT` | P5.1 und P5.2 erfüllt, Stufe B widerspricht nicht |
| `WIDERLEGT` | Kriterium aus §2.3 erfüllt |
| `UNENTSCHIEDEN` | sonst; nenne die fehlende Evidenz |

## 12. Ausgaben

```
results/H5/
  preregistration.json   preflight.json
  stage_a/ portfolio_daily_returns.csv   configs.csv   exit_reasons.csv   interaction.json
           np10_paired.parquet   grid_sr.csv   pbo.json   regimes.csv
  stage_b/ forward_daily_returns.csv   weekly_<KW>.md
  metrics.json   verdict.json   report.md
```

`verdict.json` mit denselben Feldern wie EBC-H1 (`hypothesis: "H5"`), zusätzlich:

```json
{
  "configs": {"H0@C30": {"sr": 0.0, "maxdd": 0.0, "n_trades": {"T": 0, "M": 0, "V": 0}, "expectancy_bps": 0.0},
              "HM@C30": {}, "Hstar@C30": {}, "H0@C120": {}, "HL@C120": {}, "T20@C30": {}, "Tinf@C30": {},
              "T20@C120": {}, "Tinf@C120": {}, "H0+NP@C30": {}, "H0+NP@C120": {}},
  "interaction": {"I": 0.0, "ci90": [0.0, 0.0]},
  "np10": {"share_triggered": 0.0, "delta_expectancy_bps": 0.0, "ci90": [0.0, 0.0], "lost_outliers": 0},
  "pbo": {"C30": 0.0, "C120": 0.0},
  "recommended_holding": {"C30": {"T": 0, "M": 0, "V": 0}, "C120": {"T": "inf", "M": 0, "V": "inf"}}
}
```

`report.md` (Deutsch, ≤ 3 Seiten): Verdikt, Vorhersagetabelle, Tabelle Konfiguration × Kapazität (SR, DD, Trades, Netto-Beitrag), Exit-Gründe vorher/nachher, Antwort auf F5 in drei Sätzen.

## 13. Folgen je Verdikt

- **BESTAETIGT:** Issues: (1) `max_bars_in_trade` je Archetyp abhängig von der Kapazitätsstufe (bei 30 Plätzen H\*, bei 120 Plätzen HL) in `automation/config/ebc_archetypes.json`; (2) M mit Zeitstopp 1 Tag; (3) NP10 als Exit-Option der Basisklasse, falls P5.4 erfüllt; (4) Baustein von B++ in EBC-H8.
- **WIDERLEGT:** Zeitstopps T 60 / M 5 / V 20 beibehalten; dokumentieren, ob der Verlust von HM aus verlorenen Signal-Exit-Gewinnern stammt.
- **UNENTSCHIEDEN:** Mit `U_breit` ∪ `U_neu` wiederholen (mehr Trades je Konfiguration); Stufe B verlängern.

## 14. Ablauf-Checkliste

- [ ] Setup, Preflight, Paritätstest C30 = B+
- [ ] `sim.walk` um `np` erweitert, `contrast_sharpe_ci` mit Test gegen `delta_sharpe_ci`
- [ ] `preregistration.json` geschrieben (vor jeder Auswertung)
- [ ] §7.1 Konfigurationen × Kapazität, §7.2 Interaktion, §7.3 NP10 gepaart, §7.4 Gitter/PBO, §7.5 Regime
- [ ] Stufe B gestartet
- [ ] `verdict.json` und `report.md` geschrieben
