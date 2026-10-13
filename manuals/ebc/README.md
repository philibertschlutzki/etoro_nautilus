# EBC-Hypothesen für den Etoro Bot Commander

Jede Datei `EBC_Hx.md` ist ein ausführbarer, vorregistrierter Prüfauftrag für den Etoro Bot Commander: Hypothese, Vorhersagen mit Schwellen, Widerlegungskriterien, Versuchsarme, Statistik, Ausgabeformat. Ergebnisse landen in `results/Hx/` (`preregistration.json`, `verdict.json`, `report.md`). Die Prompts EBC-H1 bis EBC-H3 sind eingefroren — ihre SHA-256 stehen in den Vorregistrierungen von `results/H1` bis `results/H3`.

## Fragen

| ID | Frage | Hypothesen |
|---|---|---|
| F1 | Sind die aktuellen Strategien zu komplex für reale Trading-Szenarien, und wie sähen sie für ein möglichst breites eToro-Universum aus? | H1 |
| F2 | Wie lassen sich die Gewinne mathematisch optimieren — einfache Strategien auf möglichst vielen Instrumenten? | H2 |
| F3 | Was lässt sich mit Short-Positionen, Trailing-Take-Profit und Trailing-Stop-Loss optimieren? | H3 |
| F4 | Welche Features fehlen für Gewinnmaximierung im realen Trading? | H1–H8 (je Teil) |
| F5 | Wie lassen sich die Gewinne maximieren, die Verluste reduzieren und die Anzahl Trades maximal erhöhen? | H4–H8 |

## Stand (2026-10-11, Commit `4067121`)

| Prompt | Thema | Verdikt | Ausgeführt |
|---|---|---|---|
| [EBC_H1](EBC_H1.md) | Drei Archetypen T/M/V über das breite Universum statt Pro-Symbol-Auswahl | `BESTAETIGT` (vorläufig) | Stufe A; B+ SR 2,02 gegen 0,49, 1141 Trades |
| [EBC_H2](EBC_H2.md) | Haltedauer, Breite, Sizing | `WIDERLEGT` (formal, Kriterium 2.3(b)) | Stufe A |
| [EBC_H3](EBC_H3.md) | Trailing-Stop, Trailing-Take-Profit, Shorts | `UNENTSCHIEDEN` | Stufe A |
| [EBC_H4](EBC_H4.md) | Kapazität × Breite | `WIDERLEGT` (nach Rekalibrierung) | Stufe A (2026-10-12): C120+ ×4,1 Trades, aber SR 1,53 gegen 2,02; Replikation auf `U_neu` schwach (SR 0,82), [Bericht](../../results/H4/report.md) |
| [EBC_H5](EBC_H5.md) | Haltedauer × Kapazität | `WIDERLEGT` | Stufe A: M 1 Tag ΔSR −0,52, NP10 −81 bps je ausgelöstem Signal, [Bericht](../../results/H5/report.md) |
| [EBC_H6](EBC_H6.md) | Verlustbudget in drei Ebenen | `WIDERLEGT` | Stufe A: DS 45 % hebt Trade-Expectancy (+12 bps), L123 senkt Portfolio-SR um 0,49, [Bericht](../../results/H6/report.md) |
| [EBC_H7](EBC_H7.md) | Signal-Breite (drei neue Archetypen) | `UNENTSCHIEDEN` | Stufe A: R netto positiv (SR allein 1,94), aber ρ(R, TMV) 0,70; feste Quoten ohne Sharpe-Gewinn, [Bericht](../../results/H7/report.md) |
| [EBC_H8](EBC_H8.md) | Realer Durchsatz von B++ im Paper-Konto | nicht ausgeführt | keine Freigabe für Paper-Orders, [Bericht](../../results/H8/report.md) |

Stufe C (Demo-/Paper-Orders) wurde bisher für keine Hypothese freigegeben. Stufe B von H1 (B+ Forward-Shadow) ist mit `experiments/ebc/stage_b_daily.py` vorbereitet (Einstiege ab 2026-10-12), aber noch ohne Ergebnis.

Stufe-A-Ergebnisse H4–H7 (2026-10-12, lokaler Katalog, Rückfallkosten, ohne Stufe B/C): Keine Variante schlägt B+ (C30) bei Sharpe und Rendite signifikant; vola-gleich bleibt B+ vorn (33,8 % p. a. gegen 25–31 %). Querschnitt: `results/H4/summary_h4_h7.json`.

## Zielmatrix H4–H8

| Prompt | Hebel | Gewinn | Verluste | Trades | Stufe C | Primäre Endpunkte |
|---|---|---|---|---|---|---|
| H4 | 120 Plätze × 0,5 % statt 30 × 2 %; frisch gezogenes Universum `U_neu` | ↑ SR, mehr Ausreisser | Nebenbedingung: DD ≤ +1 pp | ↑↑ ≥ 3 × | Kleinorders 50 USD | P4.1 Replikation, P4.2 Trades, P4.3 ΔSR |
| H5 | M 1 Tag; T/V ohne Zeitstopp bei 120 Plätzen; Ausstieg ohne Fortschritt | ↑ | ↓ M-Verlierer am Zeitstopp | ↑ M ≥ 1,5 × | – | P5.1 M kurz, P5.2 Interaktion H_T × Kapazität |
| H6 | Katastrophen-Stop-Deckel 45 % statt 15 %; Risikobudget je Trade; Vola-Steuerung | ↑ Expectancy | ↓↓ DD ≤ 0,75 × (vola-gleich) | Nebenbedingung ≥ 0,9 × | Stop-Distanz beim Broker | P6.1 DS1, P6.2 Kombination |
| H7 | Archetypen R (IBS), X (12-1-Momentum), W (Monatswechsel) mit Platzquoten | ↑ SR | Nebenbedingung: DD ≤ +1 pp | ↑↑ ≥ 1,5 × | – | P7.1 netto positiv, P7.2 Korrelation, P7.3 Kombination |
| H8 | B++ (bestätigte Hebel) als täglicher REST-Executor | real erreichbar? | real erreichbar? | ≥ 0,9 × Schatten | Paper-Ausführung, Freigabe | P8.1 Durchsatz, P8.2 Kosten, P8.3 Tracking |

## Kernbelege

Reproduzierbar mit `python experiments/ebc/evidence_h4_h8.py` (rein lesend, ohne API und ohne `data/ebc/`; schreibt `results/evidence_h4_h8.json`). Grundlage: Arm B+ aus `results/H1` im Vergleichszeitraum 2024-12-23 bis 2026-10-12 sowie `results/H2` und `results/H3`.

| Befund | Wert | Hypothese |
|---|---|---|
| Ertrag hängt an Ausreissern | beste 1 % der Trades = 42 %, beste 5 % = 115 % des Netto-Gewinns; 97 Trades ≥ +2000 bps | H4, H5 |
| Kapazität bindet | gleichzeitig offen (Näherung): Median 27 von 30, an 46 % der Tage ≥ 28 | H4, H5 |
| Katastrophen-Stop ist ein Primär-Stop | 142 Exits (12,4 % der Trades, Repo-Invariante `check_disaster_stop_non_binding`: ≤ 1 %) = 50 % der Bruttoverluste; 15-%-Deckel bindet bei 94 % der Trades; Distanz im Median 2,5 ATR, enger als der Trailing-Stop (2,5–3 ATR); 48–58 % der Exits (K1, H3) schliessen am selben Tag wieder jenseits des Stops | H6 |
| Zeitstopps schneiden Gewinner und halten Verlierer | T am 60-Tage-Stopp: 30 Trades, alle Gewinner, +27,1 pp (42 % des Netto-Gewinns); M am 5-Tage-Stopp: 139 Trades, Trefferquote 20 %, −10,1 pp | H5 |
| Nur Aktien/ETF tragen | K1 +64,5 pp; K2 −0,3, K3 +0,5, K4 0,0 pp | H4 |
| Korrelationsdecke | ρ̄ in K1 0,024 ⇒ N_eff → 41; mittlere SR der Aktien-Teilportfolios 0,21 (N = 50) → 0,25 (alle 984) | H4, H7 |
| Tail-Tage | schlechteste 5 % der Tage −57,8 pp gegen +63,1 pp über alle Tage; MaxDD −15,4 % (2025-01-03 bis 2025-04-09) | H6 |

## Reihenfolge und Abhängigkeiten

```
H4  Kostentabelle mit gemessenen Spreads, U_neu, Kandidaten-Zähler, stage_b_multi.py, Preflight-Erweiterung
 ├─ H5  Haltedauer × Kapazität        (nur Rechnung)
 ├─ H6  Verlustbudget                 (Rechnung + Stop-Distanz beim Broker)
 └─ H7  Signal-Breite                 (nur Rechnung)
      └─ H8  B++ real im Paper-Konto  (Zusammensetzung aus den Verdikten H4–H7, Freigabe nötig)
```

H4 zuerst: Es baut die gemeinsame Infrastruktur (Kostentabelle aus H4 §5, `U_neu`, Zähler verworfener Kandidaten, `experiments/ebc/stage_b_multi.py`, Vorfahren-Prüfung in `preflight.py`). H5, H6 und H7 sind voneinander unabhängig und können nach H4 §5 parallel laufen. H8 kommt zuletzt; ohne bestätigten Hebel führt es B+ aus.

## Gemeinsame Konventionen

- **Repo-Stand:** Prompts auf Commit `4067121`; je Hypothese ein Branch `ebc/hx`; `EBC_H1.md`–`EBC_H3.md` und `results/H1`–`results/H3` bleiben unverändert.
- **Universen:** `U_breit` = `results/H1/universe.csv` (466 Instrumente; 426 nach Kostenfilter q ≤ 0,10). `U_neu` = `results/H4/universe_neu.csv` (nur Aktien/ETF, disjunkt zu `U_breit`, Seed 20261012).
- **Zeitraum Stufe A:** 2024-12-23 bis 2026-10-12 (Kalender, Wochenenden auf Montag gerollt); Kerzen bis einschliesslich 2026-10-10. Spätere Kerzen gehören zu Stufe B.
- **Kosten:** EBC-H4 §5 — gemessene Spreads nur aus Werktags-Stichproben mit frischem Quote-Zeitstempel, `s = max(Rückfallwert, gemessener Median)`; Slippage, Kommission, Finanzierung aus `experiments/ebc/costs.py` bis zur Messung in Stufe C. Referenzarm B+ₘ = C30 mit derselben Kostentabelle.
- **Kapazitätsstufen:** C30 = 30 Plätze × 2 %, C60 = 60 × 1 %, C120 = 120 × 0,5 %, Gesamtdeckel 60 % (`automation/config/backtest.json`, `max_total_exposure_fraction`).
- **Statistik:** Portfolio-ΔSR mit `experiments/ebc/portfolio.py` `delta_sharpe_ci` (gepaarter Stationary Bootstrap, 5000 Replikate, Seed 20261011, 90-%-KI); Trade-Ebene mit Cluster-Bootstrap nach Einstiegsmonat. Stufe B schwächt ein Verdikt nur ab (ΔSR_forward mit oberer 90-%-KI-Grenze < −0,50 ⇒ `UNENTSCHIEDEN`).
- **Verdikte:** `BESTAETIGT` (alle primären Vorhersagen erfüllt), `WIDERLEGT` (vorregistriertes Kriterium greift), `UNENTSCHIEDEN` (sonst, mit Angabe der fehlenden Evidenz). `verdict.json` wie EBC-H1 §12 plus die Felder der jeweiligen Hypothese.
- **Konto:** Orders nur im Demo-/Paper-Konto über `automation/account_guard.py`; Stufe C nur nach Freigabe.

## Entscheidungen des Nutzers vor dem Start

1. **Freigabe von Stufe C** für H4 (Kleinorders), H6 (Stop-Distanz) und H8 (Paper-Ausführung); die Stufe C von H1–H3 steht ebenfalls noch aus.
2. **Konto-Variante für H8:** (a) eigenes Demo-Konto, (b) Inkubations-Bot pausieren oder (c) nur Instrumente ausserhalb von `automation/config/instrument_map.json` mit Teilbudget. Der Allocator des Inkubations-Bots rechnet mit allen Positionen des Kontos (`automation/momentum_ls_allocator.py:144`, `:171–172`, `:189`), und der Adapter übernimmt beim Start alle offenen Positionen auf Instrumenten seines Universums (`automation/adapters/etoro_execution.py:243–283`). H8-Positionen im selben Konto verbrauchen also sein Budget, sperren seine Instrumente und können nach einem Neustart von ihm geschlossen werden. Bei Variante (c) fallen 146 der 466 Instrumente von `U_breit` weg (K1 bleibt mit 223 von 357).
3. **Inkubations-Bot:** läuft laut Paper-Bericht vom 2026-10-11 17:00 UTC seit etwa 13:15 UTC nicht. Neustart oder Pause ist unabhängig von H4–H7, bestimmt aber die Konto-Variante von H8.

## Befunde am aktuellen Stand ausserhalb der Hypothesen

- Die Archetypen T/M/V werden von keinem Produktionspfad gelesen (`automation/config/ebc_archetypes.json`); der Paper-Bot handelt die Stundenachse (`automation/bar_axis.py`, `LIVE_AXIS = "OneHour"`) mit der Inkubations-Auswahl, am 2026-10-11 zwei Krypto-Positionen (BTC, INJ).
- Die Short-Leaks aus EBC-H3 Anhang B sind seit Commit `21be66a` in `main` geschlossen.
- Broker-Trailing-Stop: Der Adapter setzt `IsTrailingStop` (`automation/adapters/etoro_execution.py:852–853`), eToro dokumentiert `IsTslEnabled`; `ETORO_ENABLE_TSL` ist standardmässig `"0"`. Ungeprüft bis zur Stufe C von H3.
- `experiments/ebc/prereg.py` und `preflight.py` sind fest auf Commit `5f737ed` und H1–H3 verdrahtet; EBC-H4 §3 erweitert sie.
