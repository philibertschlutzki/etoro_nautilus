# EBC-H6 — Verlustbudget in drei Ebenen: Stufe-A-Bericht

**Verdikt: WIDERLEGT** (nur Stufe A; Stufe C NICHT_FREIGEGEBEN). L1 (Katastrophen-Stop-Deckel 45 %) wirkt auf Trade-Ebene wie vorhergesagt: Anteil der Katastrophen-Stop-Exits an den Bruttoverlusten 51 % → 3 %, gepaarte ΔExpectancy +12 bps [4; 21]. Auf Portfolio-Ebene verliert L1 aber Sharpe (-0.21 [-0.54; 0.12]), und die Kombination L123 ist klar schlechter (-0.49 [-0.86; -0.12]) ohne kleineren vola-gleichen Drawdown (0.99 × C30).

Zeitraum 2024-12-23 bis 2026-10-12, Rückfallkosten, `simulate_sized` (Parität: C30 1143 Trades, SR 1.97 gegen 1141 und 2,02 in `simulate`).

## Vorhersagen

| ID | Ergebnis | erfüllt |
|---|---|---|
| P6.1 | Anteil 3.4 % ≤ 25 %; ΔExpectancy +12.1 bps, untere Grenze 4.2 > −5 | ja |
| P6.2 | DD vola-gleich ×0.99 (Soll ≤ 0,75), CVaR5 ×1.08 (≤ 0,80), ΔSR untere Grenze -0.86 (> −0,15), Trades ×0.92 | nein |
| P6.3 | L1 -0.21 [-0.54; 0.12]; L2 -0.04 [-0.38; 0.33]; L3 -0.34 [-0.64; -0.06] | L1: Expectancy ↑, DD nicht ↓; L2: vola-gleicher DD ↑; L3: DD ↓ aber SR ↓ |
| P6.4 | R4: mittlere Tagesrendite C30 -18.1 bps, L3 -17.3 bps (Soll ≥ 30 % kleinerer Verlust) | nein |
| P6.6 | bei C120: L1 -0.08 [-0.20; 0.05], L123 -0.30 [-0.60; 0.00] | berichtet |

Widerlegung (§2.3): (b) trifft zu, die obere Grenze von ΔSR(L123 − C30) ist -0.12 < 0 und der vola-gleiche DD ist nicht kleiner. Whipsaw: 54 % der Katastrophen-Stop-Exits schliessen am selben Tag jenseits des Stops. Die 732 früheren Katastrophen-Stop-Exits enden unter DS1 zu 554 im Trailing-Stop (im Mittel -1272 statt -1556 bps).

## Arme

| Arm | Trades | SR | MaxDD | MaxDD vola-gleich | CVaR5 vola-gleich | Rendite | Anteil Katastrophen-Stop an Verlusten | Ø Exposure |
|---|---|---|---|---|---|---|---|---|
| C30 | 1143 | 1.97 | -15.2 % | -15.2 % | -2.4 % | 81 % | 51 % | 58 % |
| L1 | 1056 | 1.76 | -16.2 % | -15.4 % | -2.5 % | 74 % | 3 % | 58 % |
| L2 | 1114 | 1.93 | -11.7 % | -18.2 % | -2.5 % | 45 % | 43 % | 48 % |
| L3 | 1127 | 1.63 | -13.2 % | -13.4 % | -2.5 % | 61 % | 51 % | 50 % |
| L123 | 1056 | 1.49 | -10.1 % | -15.0 % | -2.6 % | 34 % | 3 % | 42 % |
| DSinf | 1040 | 1.80 | -16.2 % | -15.4 % | -2.5 % | 77 % | -0 % | 58 % |
| L12 | 1039 | 1.81 | -11.5 % | -17.4 % | -2.5 % | 43 % | 3 % | 47 % |
| L13 | 1037 | 1.57 | -13.8 % | -13.4 % | -2.6 % | 62 % | 2 % | 50 % |
| C120 | 4347 | 1.63 | -8.8 % | -14.5 % | -2.4 % | 34 % | 36 % | 56 % |
| L1@C120 | 4162 | 1.56 | -9.1 % | -14.8 % | -2.5 % | 33 % | 1 % | 56 % |
| L123@C120 | 4037 | 1.33 | -6.9 % | -15.3 % | -2.5 % | 19 % | 1 % | 52 % |

## Antwort auf F5

Trades: DS1 und L2 ändern die Zahl der Trades um −8 % bis −3 %. Gewinn: Die Gesamtrendite sinkt (C30 81 %, L1 74 %, L123 34 %). Verluste: Der Katastrophen-Stop wird mit DS1 vertragsgemäss (3 % statt 50 % der Bruttoverluste, Invariante ≤ 1 % der Exits fast erfüllt), der Drawdown sinkt dadurch aber nicht; das Risikobudget und die Volatilitätssteuerung senken nur das mittlere Exposure.

## Einschränkungen

Ein Aufwärtsregime mit nur 42 R4-Tagen; Näherung des Signaltags als Vortag des Einstiegs; Stufe B und C fehlen (Broker-Stop-Distanz ungemessen).
