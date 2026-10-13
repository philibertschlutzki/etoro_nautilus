# Wöchentlicher Paper-Bericht 2026-W42

Zeitraum: 2026-10-09 19:25 bis 2026-10-12 16:59 UTC (abgeschlossene Trades). Quelle: eToro-Trade-Historie des Paper-Kontos, Krypto-Gebühr nachgerechnet.

## Kontostand (eToro)

| Kennzahl | Wert |
|---|---|
| Freies Guthaben | 9897.21 USD |
| Investiert (offene Positionen) | 50.00 USD |
| Unrealisierter PnL | -0.02 USD |
| Equity | 9947.19 USD |
| Einstiegsgebühr offener Krypto-Positionen (im Guthaben schon abgezogen) | 0.00 USD |

## Gesamt

| Gruppe | Trades | Trefferquote | Brutto USD | Gebühr USD | Netto USD | Ø Netto/Trade | Profit-Faktor | n ≥ 30 |
|---|---|---|---|---|---|---|---|---|
| alle Klassen | 33 | 9 % | -6.51 | 15.90 | -22.41 | -0.679 | 0.22 | ja |

## Aussagekraft

33 Trades (≥ 30): Kennzahlen sind belastbar genug für eine erste Einschätzung. Trefferquote 9 % (95-%-Intervall 3–24 %), t-Wert des mittleren Netto-Ergebnisses -1.25 (|t| > 2 spricht für einen echten Effekt).

## Nach Anlageklasse

| Anlageklasse | Trades | Trefferquote | Brutto USD | Gebühr USD | Netto USD | Ø Netto/Trade | Profit-Faktor | n ≥ 30 |
|---|---|---|---|---|---|---|---|---|
| crypto | 5 | 0 % | -12.09 | 15.90 | -27.99 | -5.598 | 0.00 | nein |
| forex | 10 | 10 % | -0.52 | 0.00 | -0.52 | -0.052 | 0.04 | nein |
| unbekannt | 12 | 0 % | -0.20 | 0.00 | -0.20 | -0.017 | 0.00 | nein |
| equity | 6 | 33 % | +6.30 | 0.00 | +6.30 | +1.050 | 158.50 | nein |

## Nach Strategie

| Strategie | Trades | Trefferquote | Brutto USD | Gebühr USD | Netto USD | Ø Netto/Trade | Profit-Faktor | n ≥ 30 |
|---|---|---|---|---|---|---|---|---|
| AdxAtrMomentum | 3 | 0 % | -2.07 | 8.92 | -10.99 | -3.664 | 0.00 | nein |
| SmaCrossover | 3 | 33 % | -3.70 | 6.98 | -10.68 | -3.559 | 0.37 | nein |
| (nicht mehr in der Whitelist) | 17 | 6 % | -0.22 | 0.00 | -0.22 | -0.013 | 0.08 | nein |
| FlashCrashReversal | 2 | 0 % | -0.20 | 0.00 | -0.20 | -0.100 | 0.00 | nein |
| Rsi2Reversion | 6 | 17 % | -0.16 | 0.00 | -0.16 | -0.027 | 0.11 | nein |
| ComboTrendVwap | 2 | 0 % | -0.16 | 0.00 | -0.16 | -0.080 | 0.00 | nein |

## Schlechteste und beste Instrumente (Netto)

| Instrument | Trades | Trefferquote | Brutto USD | Gebühr USD | Netto USD | Ø Netto/Trade | Profit-Faktor | n ≥ 30 |
|---|---|---|---|---|---|---|---|---|
| INJ.ETORO | 2 | 0 % | -10.02 | 6.98 | -17.00 | -8.498 | 0.00 | nein |
| BTC.ETORO | 3 | 0 % | -2.07 | 8.92 | -10.99 | -3.664 | 0.00 | nein |
| EURNOK.ETORO | 1 | 0 % | -0.19 | 0.00 | -0.19 | -0.190 | 0.00 | nein |
| NZDJPY.ETORO | 2 | 0 % | -0.16 | 0.00 | -0.16 | -0.080 | 0.00 | nein |
| CHFJPY.ETORO | 3 | 0 % | -0.12 | 0.00 | -0.12 | -0.040 | 0.00 | nein |
| ID6368 | 1 | 0 % | +0.00 | 0.00 | +0.00 | +0.000 | – | nein |
| ASX.ETORO | 1 | 0 % | +0.00 | 0.00 | +0.00 | +0.000 | – | nein |
| ID8675 | 1 | 0 % | +0.00 | 0.00 | +0.00 | +0.000 | – | nein |
| FISV.ETORO | 1 | 100 % | +0.02 | 0.00 | +0.02 | +0.020 | – | nein |
| CVNA.ETORO | 1 | 100 % | +6.32 | 0.00 | +6.32 | +6.320 | – | nein |

## Offene Positionen

| Instrument | Richtung | Betrag USD | Einstieg | PnL USD |
|---|---|---|---|---|
| ID3157 | Long | 50.00 | 97.87 | -0.02 |

## Hinweise

- Brutto = `netProfit` der eToro-Historie (Kurs-Differenz × Einheiten). Gebühr = nachgerechnete Krypto-Gebühr (1 % je Seite); Forex, Rohstoffe und Aktien: 0, Spreads stecken in den Kursen.
- Strategie-Zuordnung nach der aktuellen Whitelist (zurückgezogene Paare erscheinen als „nicht mehr in der Whitelist“).
- Neu gesammelte Trades in diesem Lauf: 20; alle Trades liegen in `data/state/paper_trades.jsonl`.
