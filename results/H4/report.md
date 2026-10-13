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
