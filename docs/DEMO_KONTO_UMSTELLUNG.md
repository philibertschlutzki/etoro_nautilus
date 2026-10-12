# Umstellung des Paper-Bots auf das eToro-Demo-Konto

Stand 2026-10-12. Der Key in `.env` erreicht zwei Konten: `/demo/` (Spielgeld, ca. 17'388 USD) und `/real/` (echtes Geld).
Der Bot nutzt ausschliesslich `/demo/`, solange `ETORO_PAPER_ACCOUNT_CID` in `.env` fehlt (`automation/account_guard.py`:
`api_environment()` liefert ohne Konto-ID immer `demo`). Eine gesetzte Konto-ID schaltet auf `/real/` um, aber nur, wenn
`GET /api/v1/me` genau diese Kennung liefert. Die Zeile darf deshalb NIE auf die Kennung des echten Kontos zeigen.

## Vorher prüfen: fremde Positionen im Demo-Konto (Stand 2026-10-12 17:33 UTC)

- 1 GOOG-Long über ca. 9'999 USD, heute 17:13 UTC eröffnet, weder vom Bot noch aus Stage C geplant: ca. 57 % der Equity.
  Der Bot zählt sie in Exposure (Deckel 60 %) und Verlustschutz (3 % Tagesverlust) mit. Er könnte dann kaum noch
  einsteigen, und ein Kursrückgang von ca. 5 % auf GOOG löst den Tagesverlust-Stopp aus. Vor dem Start schliessen
  oder bewusst belassen.
- 11 Altpositionen auf Instrument 1053 (alle long, 2021/22, zusammen ca. 1'140 USD, ca. 6,6 % der Equity): unkritisch,
  dürfen bleiben.

## Reihenfolge (alles auf dem Gerät, Phili führt es aus)

1. Code holen (enthält Exit 7 statt Neustartschleife, falls #1346 gemergt ist):
   `cd ~/etoro_nautilus && git pull origin main && deploy/systemd/install.sh && systemctl --user daemon-reload`
2. Bot-Dienst stoppen (die Positionen im Demo-Konto bleiben, nichts wird geschlossen):
   `systemctl --user stop etoro-paper-bot.service`
3. Hochwasserstand und Tagesbasis des alten Kontos zurücksetzen. Sonst rechnet der Tagesverlust-Stopp (3 %) heute mit der
   Tagesbasis des alten Kontos (ca. 9'950 USD) und wäre bis Mitternacht UTC blind:
   `venv/bin/python3 automation/momentum_ls_run.py --reset-hwm`
4. In `.env` die Zeile `ETORO_PAPER_ACCOUNT_CID=…` entfernen oder mit `#` auskommentieren. `ETORO_ENV=demo` bleibt.
   Keine Schlüssel in Chats oder Commits.
5. Bot starten: `systemctl --user start etoro-paper-bot.service`
6. Prüfen:
   - `systemctl --user status etoro-paper-bot.service` zeigt `active (running)`.
   - Im Log (`logs/incubation_bot_<Datum>.log`) steht kein `[PAPER-KONTO]`-Fehler.
   - `venv/bin/python3 -m automation.paper_report --force-summary` zeigt Guthaben und Positionen des Demo-Kontos.
   - Aktien-Käufe liefern kein `error_768` mehr (W-8BEN ist im Demo-Konto frei).

## Was sich ändert

- Equity-Start ca. 17'388 USD. Verlustschutz (3 % Tagesverlust, 10 % Drawdown) liest Equity und Positionen direkt von eToro.
- Positionen im Demo-Konto, die nicht vom Bot stammen (Stage C, manuelle Tests), zählen in Exposure und Verlustschutz mit.
  Hält der Bot das Instrument selbst, übernimmt er die Position und kann sie per Zeit-Exit schliessen; in allen anderen
  Instrumenten blockiert eine vorhandene Position nur neue Einstiege.
- Wochenbericht und Paper-Report beginnen mit dem Wechsel neu; die Trades des alten Kontos bleiben in
  `data/state/paper_trades.jsonl` und werden nicht gelöscht.

## Zurück zum alten Konto

Der Key des alten Testkontos ist mit dem neuen Key nicht erreichbar. Ein Rückwechsel bräuchte den alten Key und die Zeile
`ETORO_PAPER_ACCOUNT_CID=48264806` in `.env`.
