# Hypothesen E1–E4 / R1–R3: Prüfung gegen main und Live-Daten (Stand 2026-10-14)

Quellen: Code auf main (alle genannten Tests und Module existieren), Bot-Logs 13.–14.10. (nur ~1,5 Tage, kleine Stichprobe),
`data/state/{paper,demo}_trades.jsonl`, `data/state/incubation_equity_state.json`, `results/H1…H8`, `results/weekly/`.

| ID | Befund | Verdikt | Folge |
|----|--------|---------|-------|
| R1 Spread-Gate | Live seit #715: Schwelle = `k_spread(2.0) × spread_bps_model` (EQUITY 7 → 14 bps), geprüft gegen den echten WS-Bid/Ask. 71 `SPREAD_GATE_REJECT` in 1,5 Tagen, 23 Symbole, Median 19,5 bps, fast nur zu Stundenöffnungen. Exoten-FX mit 3 bps Schwelle (USDBRL, USDTWD) werden dauerhaft blockiert. | schon umgesetzt | **Kopplung beachten:** Hebt man nach dem Freitagsbericht das Modell-EQUITY-Spread auf ~26 bps, steigt die Gate-Schwelle automatisch auf ~52 bps und das Gate wird fast wirkungslos. Entweder `spread_gate_bps` explizit pinnen oder Gate und Kostenmodell trennen. |
| R2 Short-Leaks / Rejects | Krypto-Shorts blockiert (`SHORT_BLOCKED`), Short-Leak-Patch (#allow_short=False) in main, Mindestbetrag wird aus Rejects gelernt. 0 `OrderRejected` (kein 746/747/768) in den Logs. Offen: nicht-shortbare Aktien (747) werden nicht gelernt, sind aber bisher nie aufgetreten. | schon umgesetzt, Ziel 0 % erreicht | Kein PR. Erst bei einem ersten 747/746 ein gelerntes Richtungs-Verbot nachrüsten. |
| R3 Persistenter CB-State / Post-Fill | HWM/Tagesbasis überleben Neustarts (`incubation_equity_state.json`, Umgebung gebunden). 13 `SIZING_CAP_CORRECTION`, max. Faktor 1,13 (NVO, 223 USD), alle korrigiert. Die 3 `HARD_EXIT` (WS) am 13.10. liegen vor dem Soft-Reconnect; seither kein Neustart. `REJECT_DATA_STALE`/`incomplete`: 0. | schon umgesetzt | Kein PR. Ursache des 1,13 bei Gelegenheit klären (Preissprung zwischen Sizing und Fill). |
| E1 Kosten-ATR-Floor | Kostengekoppelter Floor (#1096) und Klassen-Floor wirken nur im Backtest (`backtest_runner` setzt `params["atr_floor_bps"]`). Der Live-Bot bekommt den Wert nicht (weder `strategy_defaults.json` noch `resolve_live_params`) und fällt auf 2,0 bps zurück. Selektion und Live laufen also mit unterschiedlichen Stopdistanzen. | **Lücke bestätigt (Code)**, Wirkung noch nicht gemessen | PR nach dem Freitagsbericht (Parameter im Bot bleiben diese Woche eingefroren): Live-Floor wie im Backtest auflösen. Vorher Exit-Gründe und Stopdistanz aus dem Ledger messen. |
| E2 Haltedauer / Financing | Ungehebelte Aktien-Longs und Krypto-Spot zahlen keinen Overnight-Satz, Aktien-Shorts und FX nur Spread (Stage C). Ø Haltedauer 1,6 h (Paper) / 9,4 h (Demo). Die Stage-C-Testpositionen messen die FX-Finanzierung, Auswertung Mo 19.10. Entry-Sperre für FX am Wochenende existiert, ein Zwangs-Close vor Freitag-Close nicht. | offen (wartet auf Stage C) | Nach Mo 19.10. entscheiden, ob FX/Short-Positionen vor Rollover/Freitag geschlossen werden. Vorher kein PR. |
| E3 Multi-Archetyp | Die Archetypen (#1326) laufen nur auf der Daily-Achse im Backtest, nicht im Bot. Das ist die Frage H1 (bestätigt, vorläufig) / H3 (unentschieden). Gewichtung nach Grenzbeitrag und Fractional Kelly sind als Module vorhanden. | offen, deckungsgleich mit H1/H3 | Entscheidung Fr 16.10. (B+ in den Bot?). Kein eigener PR. |
| E4 Alpha-t-Gate (HC3) | HC3, Kalibrierung und Erreichbarkeit sind umgesetzt (#1250/#1255/#1282/#1340). Das Gate gilt für die Produktion, das Paper-Overlay umgeht es. Produktions-Gates (Sharpe 1.5, DD 0.3) bleiben bewusst unverändert. | schon umgesetzt | Nichts zu tun; erst relevant, wenn echtes Geld in Sicht ist. |

Zusammenhang mit H1–H8: E3 = H1/H3, E1 und R1 hängen an H4 (Kosten/Spread, widerlegt als Hebel, aber Spread 26 vs 7 bps offen), E2 an H3 (Stage C).

## Wochen-Todos (Änderungen gegenüber `logs/paper_reports/KW_42_plan.md`)
1. Fr 16.10.: Zusätzlich zur Frage „Bot-Spread anheben?" entscheiden, wie das Spread-Gate danach festgelegt wird (siehe R1).
2. Fr 16.10.: Exit-Gründe und Stopdistanz aus dem Ledger auswerten und den Live-ATR-Floor (E1) als PR vorbereiten.
3. Mo 19.10.: Stage-C-Auswertung entscheidet auch E2 (FX-Finanzierung).
4. Logs mindestens 7 Tage aufbewahren (jetzt nur ab 13.10.).
