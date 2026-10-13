# EBC-H7 — Signal-Breite (R, X, W): Stufe-A-Bericht

**Verdikt: UNENTSCHIEDEN** (nur Stufe A). R (IBS-Rücksetzer) ist netto positiv (Expectancy 70 bps, untere Grenze > 0) und als Einzel-Sleeve stark (SR 1.94), korreliert aber mit M (0.65) und dem TMV-Portfolio (0.70) zu stark (P7.2 verfehlt). X ist positiv ohne Signifikanz, W schwach positiv mit Konfidenzintervall um 0. Die Kombination mit festen Quoten bringt keinen Sharpe-Gewinn: ΔSR(Kombi6-Q − TMV-Q) 0.00 [-0.49; 0.51], Trades ×1.17 statt ≥ 1,5.

Zeitraum 2024-12-23 bis 2026-10-12, `U_breit` nach Kostenfilter, Rückfallkosten (R und W sind kostenempfindlich: +4 bps Round-Trip senken die Expectancy von R auf 42 bps). Handprüfung: 60 von 60 Trades stimmen mit der unabhängigen Nachrechnung überein; `simulate_sleeves` mit einem Sleeve entspricht `simulate` (True).

## Archetypen

| Archetyp | Trades / genommen | Expectancy bps (genommen) [90-%-KI] | Trefferquote | SR allein | Alpha p. a. gegen SPX500 | Korr. TMV-Q | Korr. M |
|---|---|---|---|---|---|---|---|
| R | 8886 / 2461 | 70 [29; 108] | 57 % | 1.94 | 3.4 % [0.8; 5.8] | 0.70 | 0.65 |
| X | 201 / 184 | 463 [-40; 1013] | 37 % | 0.61 | 0.9 % [-3.1; 4.4] | 0.62 | 0.47 |
| W | 352 / 352 | 34 [-22; 90] | 55 % | 0.75 | 0.2 % [-0.3; 0.6] | 0.33 | 0.27 |
| M | 5324 / 1952 | 41 [5; 72] | 63 % | 1.01 | 1.2 % [-0.8; 3.0] | 0.74 | 1.00 |

## Vorhersagen

| ID | Ergebnis | erfüllt |
|---|---|---|
| P7.1 | R, X, W: {'R': True, 'X': True, 'W': True} | alle ja (R untere Grenze > 0; X, W Punktschätzer und Alpha > 0) |
| P7.2 | R, X, W: {'R': False, 'X': False, 'W': True} | nur W |
| P7.3 | Trades ×1.17, ΔSR 0.00 [-0.49; 0.51], DD 9.9 % gegen 8.0 % | nein |
| P7.4 | Überlappung R/M 20 % der R-Signale (≤ 50 %); mit M-Signal -13 bps, ohne 62 bps | ja |
| P7.5 | Kombi6-P (gemeinsamer Pool 120) gegen TMV120: SR 1.98 gegen 1.66, Rendite 54 % gegen 35 % | berichtet |
| P7.6 | TMV120+RXW gegen TMV120: ΔSR -0.02 [-0.31; 0.28]; DD vola-gleich -9.5 % gegen -8.7 % | berichtet |

Widerlegung (§2.3): weder (a) noch (b) greift (obere Grenzen 108 bps bzw. 0.51). Für B++ kommt allein W in Frage (['W']); R besteht P7.1, aber nicht P7.2.

## Arme

| Arm | Trades | SR | MaxDD | Rendite | Expectancy bps |
|---|---|---|---|---|---|
| TMV-Q | 5005 | 1.31 | -8.0 % | 19 % | 71 |
| Kombi6-Q | 5860 | 1.31 | -9.9 % | 25 % | 79 |
| R-allein | 2461 | 1.94 | -2.2 % | 9 % | 70 |
| X-allein | 184 | 0.61 | -3.0 % | 4 % | 463 |
| W-allein | 352 | 0.75 | -0.3 % | 1 % | 34 |
| M-allein | 1952 | 1.01 | -2.5 % | 4 % | 41 |
| Kombi6-P | 6807 | 1.98 | -11.0 % | 54 % | 132 |
| TMV120 | 4352 | 1.66 | -8.7 % | 35 % | 142 |
| TMV120+RXW | 7349 | 1.64 | -13.4 % | 52 % | 121 |
| Kombi5-Q | 6648 | 1.46 | -8.4 % | 22 % | 61 |
| TMV+R-Q | 7640 | 1.51 | -9.7 % | 27 % | 64 |
| TMV+W-Q | 4414 | 1.11 | -7.8 % | 14 % | 59 |
| TMV+X-Q | 4263 | 1.04 | -9.6 % | 19 % | 84 |

## Antwort auf F5

Trades: Mit festen Quoten steigen die Trades nur um 17 %; der gemeinsame Pool nimmt R-Signale mit hoher Frequenz auf (Kombi6-P 6807 Trades). Gewinn: Im gemeinsamen Pool steigt die Sharpe gegenüber TMV120 von 1.66 auf 1.98 (ΔSR 0,32 [−0,01; 0,66], siehe `results/H4/summary_h4_h7.json`), erreicht aber nicht das Niveau von C30 (2,02). Verluste: Der Drawdown wächst gegenüber TMV120 (-11.0 % gegen -8.7 %).

## Einschränkungen

Ein Aufwärtsregime; X hat nur 184 Trades und 20 Monatsenden; W hat keine eigene Spread-Messung; keine Stufe B (mindestens zwei Monatswechsel fehlen).
