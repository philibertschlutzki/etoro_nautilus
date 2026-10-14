# Hypothesen E1–E4 / R1–R3: Prüfung gegen Live-Daten (Stand 2026-10-14)

Datenbasis: `logs/incubation_bot_20261013.log`, `logs/nautilus_mls_2026-10-13_*.log` (nur ab 2026-10-13 04:28 UTC, also ~1,5 Tage, nicht 7),
`data/state/{paper,demo}_trades.jsonl`, `data/state/incubation_equity_state.json`. Stichprobe klein → Befunde sind Indizien, keine Signifikanz.

| ID | Befund aus Live-Daten | Urteil | Folge |
|----|----------------------|--------|-------|
| R1 Spread-Gate | 71 `SPREAD_GATE_REJECT`, 23 Symbole, Median 19,5 bps (max 74), Schwellen 14/10/3 bps. Gate greift an Stundenöffnungen. | bestätigt, bereits umgesetzt (#715/#1366) | Beobachten: Ist-vs-Erwartet-Slippage (#1055/#1204) wöchentlich prüfen; Schwelle 3 bps für Exoten-FX (USDTWD, GBPILS, USDBRL) blockiert dauerhaft → prüfen, ob Symbole aus dem Universum fallen sollten |
| R2 Short-Leaks/Rejects | 0 `OrderRejected`, kein Code 746/747/768 | erfüllt (Ziel 0 % erreicht) | keine Änderung |
| R3 Persistenter CB-State / Sizing | HWM/day_key überleben Neustarts (`incubation_equity_state.json`). 13 `SIZING_CAP_CORRECTION` (12 Instrumente), max. Überschreitung Faktor 1,13 / 224 USD, alle korrigiert. 3 `HARD_EXIT` (WS) am 13.10., danach 14 Reconnect-Meldungen mit Phantom-Positionen (GR-04), kein Datenfehler (`REJECT_DATA_STALE` 0). | bestätigt, Mechanismen greifen | Überschreitung 13 % ist hoch: Ursache (Fill-Preis/Rundung vs. Cap) analysieren; WS-Instabilität (3 Exits in 1 h) beobachten |
| E2 Financing/Time-Box | Ø Haltedauer Paper 1,6 h, Demo 9,4 h (max 28 h); 0 Einträge mit „financ"; ledger-`fees`=0,0. Einzige Wochenend-Haltungen: 2 Krypto-Trades (24/7, kein Weekend-Carry). | derzeit nicht relevant (Carry-Drag nicht messbar) | Priorität senken; erst bei Haltedauern >1 Tag aktiv machen. Ledger enthält keine Overnight-Gebühren → Messung erst nach Feld-Erweiterung möglich |
| E1 Kosten-ATR-Floor | Spread-Rejects bei 19,5 bps Median deuten auf hohe Kosten gegenüber Stops; Stop-Distanz-Statistik nicht im Log | unbelegt | Backtest-Validierung nötig (`stop_loss_share`, `winner_stop_ratio`); m_cost∈[3,5] nur als Sweep-Achse testen |
| E3 Multi-Archetyp | Nicht aus Live-Logs prüfbar | unbelegt | Backtest/Paper-Breadth |
| E4 Alpha-t-Gate | Nicht aus Live-Logs prüfbar | unbelegt | `promotion_confidence_reachability` im nächsten Optimizer-Lauf prüfen |

## Wöchentliche Todos (angepasst)
1. Wochenreport: Anzahl `SPREAD_GATE_REJECT` je Symbol; Dauer-Rejects (>50 % der Gate-Prüfungen) → Universe-Kandidaten zum Entfernen.
2. Sizing-Cap-Überschreitungen: Ursache des Faktors 1,13 klären (NVO, 18:00 UTC) und Auftragsgröße vor Submit begrenzen.
3. WS-Stabilität: `HARD_EXIT`-Zähler und Phantom-Positionen nach Reconnect auswerten.
4. Ledger um Overnight-/Financing-Gebühren erweitern, danach E2 neu bewerten.
5. E1/E3/E4 per Backtest validieren (keine Live-Evidenz vorhanden).
6. Logaufbewahrung: Logs ≥7 Tage vorhalten, sonst sind 7-Tage-Auswertungen nicht möglich.
