# W-8BEN-Befund (2026-10-12, Paper-Konto 48264806)

- **Long auf US-Aktie abgelehnt:** `c5`, BUY 50 USD auf Instrument 1002 (GOOG): HTTP 200 beim POST, danach asynchron `statusID 4`, `errorCode 768` — „User is blocked by W-8BEN from opening position." (`rejections.csv`, `fills.csv`).
- **Short auf US-Aktien angenommen:** `c4`, SELL 20 USD auf RBLX (6916) und FVRR (5503): Fill zum Bid (Slippage 0 bps), Gebühr 0,03 USD je 20 USD; sofort geschlossen (`shortability.csv`).
- **Folgerung:** Eröffnungen (Long) auf US-Aktien sind für dieses Konto gesperrt, bis das W-8BEN-Formular hinterlegt ist; das betrifft auch den Bot und jede Long-Strategie auf US-Aktien. Offen: gilt die Sperre für alle US-Aktien (Shorts gehen) oder nur Long? Messung: `run_stage_c.py c4 --side long --live ...` (US-Aktien, <= 20 USD; Ablehnungen 768 landen in `rejections.csv`).
- **Skript:** `c1`/`c5` laufen nie auf US-Aktien (Standard Krypto, sonst Nicht-US-Aktie mit Symbolendung wie `.DE`/`.PA`, Börse offen; oder `--symbols`).
