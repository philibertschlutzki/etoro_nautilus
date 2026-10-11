# EBC-H2 — Gewinnmathematik: Verdikt WIDERLEGT (formal, vorläufig: nur Stufe A)

**Verdikt:** WIDERLEGT nach dem vorregistrierten Kriterium 2.3(b) (Breiten-Steigung b = -0.34, R² = 0.05); die primären Vorhersagen P2.1, P2.2 und P2.4 sind **nicht** erfüllt. Die P2.4-Messung ist durch die Regressionsvorschrift (nur Ziehungen mit SR_P > 0) verzerrt – siehe Einschränkungen. Stufe B/C wurden nicht ausgeführt.

| ID | Wert | 90-%-KI | Schwelle | erfüllt |
|---|---|---|---|---|
| P2.1 argmax_h (K1/K2/K3/K4) | 10/3/1/3 Tage; ΔSR vs h=1: 0.02/-0.02/0.00/-0.30 | untere Grenzen -0.90/-0.97/0.00/-1.30 | argmax ≥ 5 in ≥ 3 Klassen, KI-Untergrenze > 0 | nein |
| P2.2 Krypto | h ≤ 5: S_REV Obergrenze < 0, S_MOM/T/V nicht; h 10/20 keine Mehrheit < 0 | siehe crypto.csv | alle Signale | nein |
| P2.3 Stundenachse h=7 | FX/Rohstoffe/Krypto negativ; Aktien S_REV +6,1 bps bei IC 0,02 | – | s. Doku | nein |
| P2.4 Breite | b = -0.34, R² = 0.05, N_eff-Zuwachs 50→alle Aktien 71 % | [-0.41; -0.27] | b ∈ [0,3;0,7], R² ≥ 0,6, < 15 % | nein (Kriterium 2.3(b) erfüllt) |
| P2.5 RP − EN | ΔSR 0.16; MaxDD -15.8 % vs -20.4 % | [-0.16; 0.46] | ≥ 0,10 und Untergrenze > 0 | nein |
| P2.6 Testmacht | Verhältnis n=1/n=80 bei SR 2,0: 14.7× | – | ≥ 10× | ja |

## Die vier Hebel (Stufe A, Netto, Fallback-Kosten)
- **Haltedauer:** Nur Aktien/ETF (K1) profitieren von längerer Haltedauer (Brutto-Expectancy 15 → 98 bps von h=1 → 60, Netto 9 → 92 bps); die SR-Verbesserung ist nicht signifikant. FX, Rohstoffe und Indizes sind in allen h netto negativ (Archetypen tragen dort nicht).
- **Breite:** Mittlere Korrelation der Teilportfolios 0,02–0,06 (innerhalb Klassen), 0,006 zwischen Klassen; N_eff wächst mit N weiter (+71 % von 50 auf alle Aktien), das Sättigungs-Versprechen (< 15 %) trifft nicht zu.
- **Risikoparität:** SR 1.55 vs 1.39 (EN), MaxDD -15.8 % vs -20.4 %; nicht signifikant. FR 1.32, QK 1.31.
- **Portfolio-Test:** Monte-Carlo reproduziert C5 (n=1: 14,6 %, n=80: 0,99 % bei SR 2,0); das H2-Portfolio (Klassen-h, RP) erreicht auf den letzten 26 Wochen keine Promotion (kein `PROMOTE`), 1 von 1010 Teilportfolios.

## Optimale Haltedauer je Klasse (Entdeckungsperiode)
K1 Aktien/ETF: 10 Tage · K2 FX: 3 · K3 Rohstoffe: 1 · K4 Indizes: 3 · K5 Krypto: kein positives Netto-Optimum (200 bps Kommission).

## Antwort auf F2
Ja für Aktien/ETF: längere Haltedauern (10–60 Tage) und breite Teilportfolios bringen netto mehr, aber die Messung kann die Sättigungs- und Optimalitätsbehauptungen nicht bestätigen. Für FX, Rohstoffe, Indizes tragen die drei Archetypen netto nicht; Krypto ist bei kurzer Haltedauer strukturell netto negativ (S_REV, T, M, V h ≤ 5). Risikoparität senkt den MaxDD, ist statistisch aber nicht belegt.

## Einschränkungen
Kosten aus Rückfallwerten (Wochenende), Survivorship, Stichprobe mit starkem Aufwärtsregime in der Bestätigungsperiode (SR(K1) ≈ 1,7–2,0 dort vs 0,5–1,0 in der Entdeckung). P2.4: Log-Regression nur über SR_P > 0 (bei mittlerem SR_P ≈ 0 verzerrt); Details `breadth_draws.csv`.
