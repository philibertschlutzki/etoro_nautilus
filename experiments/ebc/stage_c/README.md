# EBC Stufe C — Start (Standard seit 2026-10-12: Demo-Konto, /demo/-Pfade)

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

### FX-Finanzierung (Entscheidung Phili 2026-10-12: 1010 USD)
Nur Phase `fin`, Betrag genau 1010 USD, höchstens 4 FX-Testpositionen (z. B. 1 Long + 1 Short je Paar, 2 Paare), Handelszeit-/Wochenend-Prüfung unverändert. Aufruf: `run_stage_c.py fin --live --confirm-account 48264806 --classes K1,K2,K5 --fx-max-exposure <Anteil>`.
**Achtung:** Die Exposure-Grenze der Testpositionen ist standardmäßig 4 % des Kontos – eine 1010-USD-Position sind aber ~10 %, 2 Positionen ~20 %, 4 Positionen ~40 % (brutto; Long/Short-Paare netto ~0). Mit dem Standard wird FX deshalb verweigert, bis `--fx-max-exposure` bewusst gesetzt wird (z. B. 0.20 für 1 Long + 1 Short).

## W-8BEN-Befund (2026-10-12)
Long-Eröffnungen auf US-Aktien werden mit `errorCode 768` („User is blocked by W-8BEN“) asynchron abgelehnt, Shorts auf US-Aktien werden angenommen (Details `results/stage_c/w8ben_finding.md`). Daher laufen `c1`/`c5` standardmäßig auf Krypto (K5, long, <= 50 USD) bzw. Nicht-US-Aktien (`--symbols`), nie auf US-Aktien. Probe der Long-Sperre: `c4 --side long`.
Beispiele: `run_stage_c.py c5 --live --confirm-account 48264806` (wählt Krypto), `run_stage_c.py c1 --live --confirm-account 48264806 --symbols BTC`.

Update 2026-10-12: Phili hat W-8BEN um 16:38 UTC unterzeichnet; die Wirkung ist verzögert/unklar. `--exclude-us` ist nur eine Option (Standard aus). Standardauswahl für c1/c5 ohne `--symbols` bleibt Krypto bzw. Nicht-US-Aktie.
(a) jetzt: `run_stage_c.py c5 --live --confirm-account 48264806` (Krypto).
(b) später, Sperre-Probe auf US-Aktie: `run_stage_c.py c5 --live --confirm-account 48264806 --symbols GOOG` (50 USD; bei 768 nur Ablehnung verbucht, kein Schaden; bei Annahme wird halbiert und mit `closeall` beendet).


## Umstellung auf das Demo-Konto (2026-10-12)
Standard ist jetzt `--env demo` (Demo-Konto, ~17.388 USD, Key mit Demo-Scope, `--confirm-account demo`, State `results/stage_c/state_demo.json`). `--env real` bleibt nur für das alte Testkonto 48264806 (Key nicht mehr gültig) und nie für das andere reale Konto, das der neue Key über `/real/` erreicht. W-8BEN: im Demo-Konto frei (Probekauf TSLA 10 USD), im alten Testkonto gesperrt (768). Beispiele: `run_stage_c.py c5 --live --confirm-account demo --symbols BTC`, `... c1 ... --symbols BTC`, `... c4 ... --n 10`, `... closeall --live --confirm-account demo`.
Mindestgebühr: bei 10 USD wurden 1,00 USD Gebühr verbucht (Vermutung: Mindestgebühr je Order) - bei der Auswertung nicht mit bps auf kleine Beträge verwechseln.
