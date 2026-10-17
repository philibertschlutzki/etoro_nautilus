# Hypothesen-Regelkreis

`automation/hypothesis_loop.py`, wöchentlich per systemd-User-Timer (`deploy/systemd/install_hypotheses.sh`, Sa 07:25 UTC). Ausgabe `results/hypotheses/<Datum>.md`.

1. **Ingestion**: neuester Report aus `results/weekly` und `results/daily`; auffällige Drawdowns, Renditen und Spread-Abweichungen werden als Hinweise gelistet (Auslöser, keine Entscheidung).
2. **Kandidaten**: JSON in `data/hypotheses/candidates/` (Schema: `load_candidate`). Pflicht: kausaler Marktgrund (`mechanism_class` aus Trendstärke, Liquidität, Session-Überlapp, Kostenstruktur, Volatilitätsregime, Financing, Ereigniskalender) und ein Begründungstext. Varianten sind Brutto-Renditen je Trade in bps, gleiche Länge. Die Datei kann von Hand oder vom LLM-Loop (`automation/ai_loop`) kommen.
3. **Falsifikation, fail-closed** (alle müssen bestehen): Netto nach Kosten > 0 (Spread + Gebühr je Klasse aus `backtest.json`, Aktien 7 bps Spread, Krypto 1 % je Seite), DSR >= 0.95 mit allen bisherigen Registry-Trials in der Deflation, PBO (CSCV) <= 0.20, Profit Factor >= 1.25, mindestens 100 Trades und 8 Varianten. Kandidaten dürfen die Schwellen nur verschärfen.
4. **Dokumentation**: Bericht; Gewinner als Vorschlag in `results/hypotheses/proposals/`; Verworfene in `data/hypotheses/rejected/`. `config/optimizer.json` und der Bot bleiben unberührt, Phili entscheidet, danach Forward-Shadow.

Startbestand (`automation/config/hypothesis_seed.json`): H1 bis H9, Kalibrierung, Drift, Sizing, Regimefilter, Krypto, E1/E2. Bereits registrierte Ids oder Mechanismus-Fingerprints werden nicht erneut geprüft; jede getestete Variante erhöht die Deflationshürde.
