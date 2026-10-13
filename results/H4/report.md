# EBC-H4 — Kapazität × Breite: Stufe-A-Bericht

**Verdikt: UNENTSCHIEDEN** (nur Stufe A; Stufe B/C nicht ausgeführt). Mehr Plätze und ein frisches Universum liefern mehr als das Dreifache an Trades (×4.12), aber keine bessere Risiko-Rendite: ΔSR(C120+ − C30) = -0.49 [-1.08; 0.11]. Der Edge repliziert auf `U_neu` nur schwach und nicht signifikant.

Zeitraum 2024-12-23 bis 2026-10-12 (471 Tage), Kosten nur Rückfallwerte (keine Werktags-Spread-Messung), `U_neu` = 460 Instrumente (Seed 20261012, SHA-256 `6ca894aded83…`), davon 452 nach Kostenfilter q ≤ 0,10. Parität C30 = B+: 1141 Trades, SR 2,0156, MaxDD −15,42 % (bitgleich).

## Vorhersagen

| ID | Ergebnis | Schwelle | erfüllt |
|---|---|---|---|
| P4.1 Replikation | N30-Expectancy 107 bps, 90-%-KI [-26; 267]; K1-C30 303 bps | untere Grenze > 0 und ≥ 0,5 × K1-C30 | nein |
| P4.2 Trades/Qualität | Trades ×4.12, Expectancy ×0.58 | ≥ 3,0 und ≥ 0,5 | ja |
| P4.3 Risiko-Rendite | ΔSR -0.49 [-1.08; 0.11]; DD 12.6 % gegen 15.4 % | Punkt > 0, untere Grenze > −0,20, DD ≤ +1 pp | nein |
| P4.4 Kapazität bindet | verworfen: C30 88 %, C120+ 74 % | C30 ≥ 20 %, C120+ ≤ 5 % | C30 ja, C120+ nein |
| P4.5 Ausreisser | ≥ +2000 bps: ×2.98 | ≥ 2,5 | ja |
| P4.6 Nur Aktien/ETF | ΔSR(C30-K1 − C30) -0.18 [-0.45; 0.06] | Punkt ≥ −0,10 | nein |
| P4.7 q-Rang als Vola-Tilt | ΔSR(C30 − C30r) 0.69 [-0.02; 1.43] | berichten | – |

Widerlegung (§2.3): (a) obere Grenze N30 < 0: nein; (b) obere Grenze ΔSR(C120+ − C30) < 0: nein (0.11). Daher `UNENTSCHIEDEN`.

## Kapazitätstabelle

| Arm | Plätze × Notional | Trades | SR | MaxDD | Rendite | Expectancy bps | verworfen | Ø offen | ≥ 2000 bps | Jahresrendite bei Vola von C30 |
|---|---|---|---|---|---|---|---|---|---|---|
| C30 | 30 × 2.0 % | 1141 | 2.02 | -15.4 % | 83 % | 284 | 88 % | 27 | 97 | 33.8 % |
| C60 | 60 × 1.0 % | 2237 | 1.81 | -12.3 % | 54 % | 203 | 76 % | 54 | 152 | – |
| C120 | 120 × 0.5 % | 4352 | 1.66 | -8.7 % | 35 % | 142 | 53 % | 105 | 215 | 27.7 % |
| C120plus | 120 × 0.5 % | 4706 | 1.53 | -12.6 % | 43 % | 165 | 74 % | 107 | 289 | 25.6 % |
| N30 | 30 × 2.0 % | 1312 | 0.82 | -16.6 % | 27 % | 107 | 85 % | 27 | 75 | 13.7 % |
| C30_K1 | 30 × 2.0 % | 1165 | 1.83 | -15.3 % | 76 % | 262 | 82 % | 27 | 94 | – |
| C30r | 30 × 2.0 % | 1032 | 1.33 | -9.1 % | 22 % | 99 | 89 % | 27 | 38 | – |
| Cinf | ∞ × 0.5 % | 17812 | 1.28 | -24.5 % | 85 % | 78 | 0 % | 435 | 566 | 21.4 % |
| N120 | 120 × 0.5 % | 4351 | 0.83 | -10.7 % | 16 % | 71 | 49 % | 102 | 188 | – |
| C30plus | 30 × 2.0 % | 1298 | 1.63 | -17.9 % | 90 % | 284 | 93 % | 27 | 109 | – |

Verworfene Kandidaten bei C30 erreichen im Mittel 74 bps gegen 284 bps der genommenen: Die q-Reihenfolge sortiert gut, zusätzliche Plätze füllen sich mit schlechteren Kandidaten (H0 teilweise bestätigt). Die Zufallsreihenfolge C30r erreicht nur SR 1.33 gegen 2.02: Der q-Rang wirkt als starker Volatilitäts-Tilt auf volatile Aktien.

## Antwort auf F5

Trades: C120+ macht ×4.1 so viele Trades wie C30, C∞ ×16. Gewinn: Die Gesamtrendite sinkt mit mehr Plätzen (83 % bei C30, 43 % bei C120+); auch bei gleicher Vola bleibt C30 vorn (33.8 % gegen 25.6 %). Verluste: Der Drawdown sinkt mit der Kapazität nur auf unlevered Basis (-8.7 % bei C120), nicht bei gleicher Vola (-14.3 %).

## Einschränkungen

Ein Aufwärtsregime; `U_neu` und `U_breit` enthalten nur heute noch gelistete Instrumente mit ≥ 750 Kerzen (Überlebens-Verzerrung); ohne gemessene Spreads, ohne Stufe B und C; C30r mit einer Ziehung je Trade, nicht je Handelstag neu.


## Kosten-Rekalibrierung aus Stufe C (2026-10-12, Demo-Konto) — Verdikt nach Rekalibrierung: WIDERLEGT

eToro berechnet eine **Pauschalgebühr von 1,00 USD je Order** (23 von 24 `h4`-Orders; TSLA-Feeprobe 200/500/1000 USD je 1,00 USD beim Öffnen, ca. 1 USD beim Schliessen). Bei 50-USD-Orders sind das 200 bps je Seite. Pflicht nach §9: Stufe A mit gemessenen Werten neu rechnen. Annahme: Gebühr in bps = 2 USD / (Notional-Anteil × 17 388 USD Equity) als Kommission für Aktien/ETF; Filter und Rang weiter mit den Rückfallkosten (`experiments/ebc/h4_fee_recalc.py`, `cost_recalibration_fee.json`).

| Arm | Gebühr Round-Trip bps | Trades | SR | MaxDD | Rendite | Expectancy bps |
|---|---|---|---|---|---|---|
| C10 | 19 | 373 | 1.34 | -22.5 % | 73 % | 284 |
| C15 | 29 | 588 | 1.33 | -19.3 % | 63 % | 233 |
| C20 | 38 | 777 | 1.46 | -19.4 % | 62 % | 226 |
| C30 | 58 | 1141 | 1.62 | -17.4 % | 62 % | 230 |
| C60 | 115 | 2237 | 0.88 | -16.0 % | 22 % | 100 |
| C120 | 230 | 4352 | -0.56 | -15.0 % | -11 % | -49 |
| C120plus | 230 | 4706 | -0.54 | -20.4 % | -14 % | -54 |
| N30 | 58 | 1312 | 0.36 | -18.5 % | 10 % | 50 |
| C30plus | 58 | 1298 | 1.29 | -20.2 % | 65 % | 228 |

ΔSR gegen C30: C60 -0.74 [-1.15; -0.37], C120 -2.18 [-2.72; -1.69], C120+ -2.16 [-2.83; -1.54]. Die obere Grenze für C120+ liegt klar unter 0: Widerlegungskriterium §2.3(b) trifft zu. Kleine Positionen sind mit der Pauschalgebühr netto negativ; C30 bleibt das Optimum (weniger, aber grössere Positionen C10 bis C20 sind nicht besser: SR 1,34 bis 1,46 gegen 1,62). Folge für den Live-Bot: Positionen unter ca. 150 USD zahlen mehr als 130 bps Round-Trip an Gebühr.
