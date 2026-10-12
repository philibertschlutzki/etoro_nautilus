# EBC-H5 — Haltedauer × Kapazität: Stufe-A-Bericht

**Verdikt: WIDERLEGT** (nur Stufe A). Ein 1-Tage-Zeitstopp für M kostet bei 30 Plätzen klar Sharpe (ΔSR -0.52 [-0.95; -0.11], obere Grenze < 0), obwohl er die M-Katastrophen-Stops von -11.6 auf -1.2 pp drückt und M-Trades um ×1.55 erhöht. Das Optimum von H_T hängt nicht erkennbar von der Kapazität ab (Interaktion I = -0.06 [-0.48; 0.35]).

Zeitraum 2024-12-23 bis 2026-10-12, `U_breit` nach Kostenfilter (426 Instrumente), Rückfallkosten.

## Vorhersagen

| ID | Ergebnis | erfüllt |
|---|---|---|
| P5.1 | ΔSR(HM − H0 @C30) -0.52 [-0.95; -0.11]; M-Trades ×1.55; M-Katastrophen-Stop-Verlust ×0.10 | nein (Punktschätzer < 0) |
| P5.2 | I = -0.06 [-0.48; 0.35] | nein |
| P5.3 | ΔSR(HL − H0 @C120) 0.06 [-0.26; 0.41] | Punkt > 0, DD ↓ |
| P5.4 | NP10 bei C30: Trades 1288 gegen 1141; ΔSR -0.13 [-0.48; 0.21]; gepaarte ΔExpectancy der 3516 ausgelösten Signale -81 bps [-136; -28], 131 verlorene Ausreisser | nein |
| P5.5 PBO | C30 0.44, C120 0.92 | C120: ≥ 0,5, Gitter-Optimum überangepasst |

Widerlegung (§2.3): (a) trifft zu, die obere Grenze von ΔSR(HM − H0 @C30) ist -0.11 < 0. Das Gitter bestimmt kein Verdikt; sein Spitzenwert bei C30 (T60/M5/V40@C30, SR 2.03) liegt in der Nähe des Status quo (SR 2.02), bei C120 liegt das Gitter-Optimum bei SR 1.80 (T20/M1/Vinf@C120) mit PBO 0.92.

## Konfigurationen × Kapazität

| Konfiguration | Trades | SR | MaxDD | Rendite | Expectancy bps | Beitrag Katastrophen-Stop (pp) | Beitrag Zeitstopp (pp) |
|---|---|---|---|---|---|---|---|
| H0@C30 | 1141 (T 365, M 624, V 152) | 2.02 | -15.4 % | 83 % | 284 | -44.2 | 37.4 |
| HM@C30 | 1529 (T 395, M 969, V 165) | 1.50 | -13.5 % | 53 % | 150 | -35.9 | 51.6 |
| Hstar@C30 | 1723 (T 571, M 1005, V 147) | 1.58 | -13.5 % | 58 % | 143 | -46.3 | 95.9 |
| HL@C30 | 1396 (T 355, M 897, V 144) | 1.60 | -13.5 % | 62 % | 187 | -32.0 | 11.3 |
| H0+NP@C30 | 1288 (T 449, M 680, V 159) | 1.89 | -14.5 % | 81 % | 245 | -45.9 | 27.3 |
| HL+NP@C30 | 1579 (T 428, M 981, V 170) | 1.99 | -12.5 % | 105 % | 251 | -34.0 | 13.3 |
| H0@C60 | 2237 (T 720, M 1226, V 291) | 1.81 | -12.3 % | 54 % | 203 | -35.2 | 23.4 |
| H0@C120 | 4352 (T 1323, M 2464, V 565) | 1.66 | -8.7 % | 35 % | 142 | -23.2 | 16.1 |
| HM@C120 | 5657 (T 1408, M 3659, V 590) | 1.62 | -6.4 % | 31 % | 100 | -17.4 | 34.0 |
| T20@C120 | 5160 (T 1950, M 2620, V 590) | 1.63 | -9.5 % | 31 % | 109 | -26.5 | 47.1 |
| Tinf@C120 | 4249 (T 1251, M 2437, V 561) | 1.74 | -8.7 % | 40 % | 165 | -22.8 | -0.8 |
| HL@C120 | 5101 (T 1240, M 3385, V 476) | 1.72 | -6.0 % | 36 % | 127 | -16.4 | 7.5 |
| H0+NP@C120 | 4850 (T 1633, M 2593, V 624) | 1.79 | -9.1 % | 40 % | 146 | -22.1 | 18.9 |
| HL+NP@C120 | 5785 (T 1594, M 3644, V 547) | 1.77 | -6.2 % | 40 % | 122 | -15.3 | 7.0 |

## Antwort auf F5

Trades: Kürzeres M und NP10 erhöhen die Trade-Zahl (HM @C30 +34 %, HL+NP @C30 +38 %). Gewinn: Bei 30 Plätzen bleibt der Status quo (SR 2.02, Rendite 83 %) vorn; HL+NP erreicht explorativ SR 1.99 bei Rendite 105 % und MaxDD -12.5 %, ΔSR gegen H0 −0,03 [−0,52; 0,45] (Nachrechnung aus `portfolio_daily_returns.csv`), also nicht signifikant. Verluste: Lange Haltedauern bei hoher Kapazität (HL @C120) senken den Drawdown auf -6.0 %, aber auch die Rendite auf 36 %.

## Einschränkungen

HL+NP@C30 ist eine nachträgliche Beobachtung aus acht Konfigurationen (nur zur Berichterstattung vorregistriert) und in den Teilperioden nicht stabil (zweites Drittel besser, drittes schlechter als H0). Ohne Stufe B.
