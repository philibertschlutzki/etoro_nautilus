# EBC-H3 — Exits und Shorts: Verdikt UNENTSCHIEDEN (vorläufig: nur Stufe A; Stufe C nicht gestartet)

**Verdikt:** UNENTSCHIEDEN. Die primären Vorhersagen P3.1, P3.2 und P3.3 sind nicht erfüllt; kein Widerlegungskriterium nach §2.3 greift. Die Mechanik-Tests (TSL-Feldname, Stop-Slippage, Finanzierung, Leerverkaufbarkeit, Teilschliessung) warten auf die Freigabe von Stufe C.

| ID | Wert | 90-%-KI | Schwelle | erfüllt |
|---|---|---|---|---|
| P3.1 | CVaR5 +10.6 %; ΔExpectancy -54.8 bps | [9.1; 12.3] %; [-78.6; -32.5] | ≥ 20 % und Untergrenze > −2 bps | nein |
| P3.2 | ΔM -0.08, ΔT -100.6, ΔV -10.2 bps; Interaktion 55.3 | [15.7; 95.0] | ΔM > 0, ΔT < 0, ΔV < 0, Untergrenze > 0 | nein (ΔM ≈ 0) |
| P3.3 | Z1-Aktien-Short -81 bps, Z2 -62 bps; MaxDD −16.5 %, dSR -0.17 | dSR [-1.04; 0.63] | Z1<0 und (Z2>0 oder MaxDD −15 % bei dSR ≥ 0) | nein |
| P3.4 | VBP 41.1 % Shorts original → 0 % gepatcht; Std 251 → 239 bps | – | Anteil > 0, Patch senkt Varianz/hebt Expectancy | ja |
| P3.5 | optimale Zeitbox T 20, M 1, V 40 Tage | – | T ≥ 10, M ≤ 5 | ja |
| P3.6/P3.7 | nicht gemessen | – | Stufe C | – |

## Antwort auf F3 je Hebel
- **Trailing-Stop-Loss (S1, Distanz k·ATR vom Intrabar-Hoch):** verbessert die Verlust-Tails nur um 11 % (CVaR5), kostet aber 55 bps Expectancy je Signal (T/M/V über 28549 Signale), weil der enge, intrabar nachgezogene Stop Trends vorzeitig beendet. Die Kombination S2 (Schutzdistanz 1,5·D) liegt bei -18 bps gegenüber S0. Ob eToro den Stop real nachzieht (Feld `IsTslEnabled` vs `IsTrailingStop`) ist ungemessen.
- **Trailing-Take-Profit (P1: 0,5/0,3 ATR):** T −101 bps, V -10 bps, M -0.08 bps (neutral); die Archetyp-Interaktion ist positiv und signifikant (55 bps). Konsequenz: TTP nicht auf Trend-/Ausbruchs-Archetypen anwenden; für Mean-Reversion neutral (kein Gewinn).
- **Short:** Aktien-Shorts sind unter Z1 (-81 bps) und im Abwärtsregime-Gate Z2 (-62 bps) netto negativ; Z2 senkt den MaxDD um 16 %, aber auch die SR (1.46 vs Z0 1.63). Z3 (Shorts nur FX/Rohstoffe/Indizes) SR 1.56.
- **Short-Leaks (real):** VolatilityBreakoutPump ist zu 41 % Short (reale Stundendaten, 101 Instrumente), FlashCrashReversal bei blockiertem Entry zu 13 %; der Patch aus Anhang B entfernt beide (0 Shorts).
- **Zeitbox:** optimal T 20 / M 1 / V 40 Tage.

## Einschränkungen
Annahmen: alle Nicht-Krypto-Instrumente leerverkaufbar (Obergrenze für Shorts), Finanzierung 0,79 bps/Tag, Kosten aus Rückfallwerten; Intrabar-Reihenfolge innerhalb einer Kerze unbekannt (Stop-Prüfung vor Hoch-Update, konservativ). Survivorship und Aufwärtsregime wie H1.
