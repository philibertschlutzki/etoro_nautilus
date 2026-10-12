# EBC Stufe C — Start (Paper-Konto 48264806, /real/-Pfad per account_guard)

Alle Befehle im Klon-Root `~/ebc_exp` (Interpreter: `~/etoro_nautilus/venv/bin/python3`). Standard ist **Dry-Run** (nur GET, nie POST).

0. Vorher: Bot-Thread informieren (Bot übernimmt nach Neustart alle offenen eToro-Positionen; 3-%-Tagesverlust-, 10-%-Drawdown- und 60-%-Exposure-Stopp zählen das Gesamtkonto). `results/stage_c/test_symbols.json` ausschließen lassen (Plan: `plan`).
1. `python experiments/ebc/stage_c/run_stage_c.py plan` – Kandidaten und `test_symbols.json`, keine Orders.
2. Montag nach US-Marktöffnung (>= 15:45 UTC, Mo): `... c4 --live --confirm-account 48264806` (30 Aktien-Shorts je <= 20 USD, sofort geschlossen), danach `c5` (Teilschließung, <= 50 USD) und `c1` (zwei Orders <= 50 USD, FX nach Sonntag 22:00 UTC möglich).
3. Während c1/fin: `monitor` alle 5 Min (cron) bzw. täglich, rein lesend.
4. Ende: `closeall --live --confirm-account 48264806` schließt alle eigenen Testpositionen per positionId.
5. `python experiments/ebc/stage_c/evaluate_stage_c.py` – Kosten-Rekalibrierung (`results/stage_c/cost_recalibration.json`).
Schutz: /me gegen CID vor jeder Order, kein Bot-Instrument, keine fremde Position, Beträge <= 20/50/100 USD, <= 20 Testpositionen, Handelszeit-Prüfung, Krypto nur long, Verwaltung je positionId. Tests: `pytest experiments/ebc/stage_c/test_stage_c.py`.

## FX/Rohstoffe/Indizes
eToro lehnt FX-Positionen < 1000 USD ab (Bot-Beobachtung 2026-10-11). Standard der Live-Phasen daher nur K1 (Aktien/ETF) und K5 (Krypto, nur long); K2 ist erst mit `--classes K1,K2,K5` und Betrag >= 1000 USD möglich (Caps nicht erhöht -> verweigert). Abgelehnte Orders landen mit statusID/errorCode in `results/stage_c/rejections.csv`, nie in `state.json`.
