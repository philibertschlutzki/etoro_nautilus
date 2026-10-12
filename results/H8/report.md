# EBC-H8 — Realer Durchsatz von B++: nicht ausgeführt

**Verdikt: UNENTSCHIEDEN (nicht ausgeführt).** H8 verlangt Paper-Orders (Stufe C, ≥ 40 Handelstage, ≥ 150 Round-Trips) und eine ausdrückliche schriftliche Freigabe des Nutzers mit Konto-Variante, Budget und Startdatum (§1.1). Diese Freigabe liegt nicht vor; es wurden keine Orders gesendet und kein Executor gebaut.

## Zusammensetzung von B++ nach den Verdikten H4–H7 (Stufe A)

| Baustein | Verdikt | Aufnahme in B++ |
|---|---|---|
| H4 120 × 0,5 % und `U_neu` | UNENTSCHIEDEN | nein: C120+ hat ×4,1 Trades, aber SR 1,53 gegen 2,02 und weniger Rendite |
| H5 kürzeres M, längere T/V, NP10 | WIDERLEGT | nein: Zeitstopps T 60 / M 5 / V 20 bleiben |
| H6 Katastrophen-Stop 45 %, Risikobudget, Volatilitätssteuerung | WIDERLEGT | nein |
| H7 Archetypen R, X, W | UNENTSCHIEDEN | W besteht P7.1 und P7.2; R nur P7.1; kein Beitrag zur Sharpe bei festen Quoten |

Ohne bestätigten Hebel gilt B+ (EBC-H1, 30 × 2 %, Kostenfilter q ≤ 0,10) als Regelsatz; der Paritätstest und die Rückfallkosten sind in `results/H4` bis `results/H7` dokumentiert.

## Was vor einer Freigabe zu entscheiden bleibt

1. Konto-Variante nach H8 §1.3: (a) eigenes Demo-Konto, (b) Inkubations-Bot pausiert, (c) nur Instrumente ausserhalb von `automation/config/instrument_map.json`.
2. Budget E₀ (Standard 10 000 USD Paper-Geld) und Startdatum.
3. Eine Randbedingung aus den Stufe-A-Läufen: Die Ergebnisse gelten für Tageskerzen und einen Aufwärtsmarkt (2024-12-23 bis 2026-10-12). Der Paper-Bot handelt die Stundenachse mit der Inkubations-Auswahl; die Übertragung von B+ auf den Live-Pfad ist durch kein Experiment belegt.
