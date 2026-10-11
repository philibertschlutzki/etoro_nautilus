# EBC-H1 — Einfach × breit: Verdikt BESTAETIGT (vorläufig: nur Stufe A)

**Verdikt:** BESTAETIGT – die Archetypen T/M/V mit Kostenfilter (B+) sind out-of-sample netto klar profitabler als die Pro-Symbol-Auswahl (A1) und die Auswahl hat keinen Informationsgehalt (P1.1, P1.2 erfüllt, kein Widerlegungskriterium). Das Verdikt ist vorläufig: Stufe B (30 Tage Forward) und Stufe C (Demo-Kalibrierung) wurden nicht ausgeführt, Survivorship und ein überwiegend steigender Markt (~21 Monate) schränken die Aussage ein.

| ID | Wert | 90-%-KI | Schwelle | erfüllt |
|---|---|---|---|---|
| P1.1 ΔSR (B+ − A1) | 1.53 (SR 2.02 vs 0.49) | [0.68; 2.38] | > 0 und Untergrenze > −0,20 | ja |
| P1.2 PBO(A1) / ρ | 0.526 / 0.009 | ρ: [-0.017; 0.035] | PBO ≥ 0,50 und ρ ≤ 0,10 | ja |
| P1.3 SR>0 je Regime | B+ 3/4, A1 3/4 | – | B+ ≥ 3/4 und A1 nicht | nein (A1 ebenfalls 3/4) |
| P1.4 ρ(Tages-Score, Stunden-Netto) | 0.068 (n=45) | – | −0,10 ≤ ρ ≤ 0,10 | ja |
| P1.5 eligible Null/beobachtet | 1.17 (20.4 % vs 17.4 %) | Null [17.6; 23.4] % | ≥ 0,80 | ja |
| P1.6 Klassen B+ ≥ A1 | 2 von 4 | – | Mehrheit | nein |

## Kennzahlen (Vergleichszeitraum 2024-12-23 bis 2026-10-12, 471 Tage, Netto)
| Arm | SR | Jahresrendite | Vol | MaxDD | Trades | Expectancy (bps) | PF | Kostenanteil |
|---|---|---|---|---|---|---|---|---|
| A0 | 0.60 | 3.9 % | 6.5 % | -6.5 % | 1915 | 19 | 1.09 | 30 % |
| A1 | 0.49 | 2.9 % | 6.0 % | -5.9 % | 1605 | 17 | 1.09 | 31 % |
| B | 1.97 | 33.2 % | 16.8 % | -15.5 % | 1159 | 273 | 1.71 | 4 % |
| Bplus | 2.02 | 33.8 % | 16.8 % | -15.4 % | 1141 | 284 | 1.73 | 2 % |

ΔSR B+ − A0 = 1.42 [0.16; 2.72]; Kostenfilter q ≤ 0,05/0,10/0,20: SR 1,78/2,02/2,02. Gleichgewichteter Aktien-Benchmark: SR 1.17; B+ Beta 0.70, Alpha 20 % p.a. (A1: Beta 0.22, Alpha -1.4 % p.a.).

## Kostenbefund
Round-Trip-Kosten (Rückfallwerte): Aktien/ETF 6 bps, FX 3, Rohstoffe 10, Indizes 8, Krypto 230 bps (200 bps Kommission). Der Kostenfilter q ≤ 0,10 entfernt 40 von 466 Instrumenten (alle 33 Krypto-Instrumente: q ≈ 0,28–1,40, dazu 7 weitere: Anleihen-ETFs, USDHKD/USDCNH, USDOLLAR); übrig K1 353, K2 48, K3 10, K4 15, K5 0. Die Spread-Messung am Wochenende ist für Nicht-Krypto-Klassen nicht repräsentativ (geschlossene Märkte); die Rekalibrierung mit gemessenen Spreads/Slippage/Finanzierung steht aus.

## Regime (SPX500)
| Regime | Tage | SR A1 | SR B+ |
|---|---|---|---|
| R1 | 223 | 1.06 | 3.60 |
| R2 | 187 | 0.39 | 1.36 |
| R3 | 19 | 9.41 | 2.64 |
| R4 | 42 | -2.07 | -2.53 |
| top5pct_abs_index_ret | 24 | -3.31 | -2.95 |

B+ verliert in Übergangsphasen (R4) und an den 5 % größten Index-Tagen (Tagesmittel −18 bzw. −34 bps), ist in ruhigen Aufwärts- und volatilen Aufwärtsphasen stark.

## Antwort auf F1
Ja – der Apparat um die Signale ist der Kostentreiber, nicht die Signale: drei globale Archetypen (T: 50-Tage-Ausbruch, M: RSI2-Rücksetzer im SMA200-Trend, V: Squeeze-Ausbruch) mit je zwei festen Parametern in ATR-/Handelstag-Einheiten erreichen auf 426 kostengefilterten Instrumenten netto SR 2.02 gegen 0.49 der Pro-Symbol-Auswahl. Der Informationsgehalt der Auswahl ist null (ρ = 0.009, PBO 0.53, Gates nicht besser als Nullmodell), und die Auswahl-Scores der Tagesachse sagen die Stunden-Ergebnisse nicht voraus (ρ = 0.07; 76 % der Paare auf der Stundenachse negativ). Breite Instrumente heißt: Aktien/ETF tragen den Ertrag, FX ist netto leicht negativ, Krypto ist ohne Haltedauer ≫ 5 Tage durch 200 bps Kommission ausgeschlossen.
