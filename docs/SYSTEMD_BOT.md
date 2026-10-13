# Paper-Bot als systemd-User-Dienst

Ziel: Fällt der Bot aus (zuletzt mehrfach ohne Log-Zeile, per SIGKILL), startet systemd ihn nach 30 Sekunden neu. Der
Wächter (`ops_guard`) meldet den Ausfall trotzdem im Fehler-Log. Alles läuft als Benutzer-Dienst, ohne Root-Rechte.

## Was sich ändert

| | bisher | mit systemd |
|---|---|---|
| Bot-Start | `daily_orchestrator.py` startet `momentum_ls_run.py` per `Popen` | systemd startet den Dienst `etoro-paper-bot.service` |
| Ausfall | Bot bleibt weg | `Restart=always`, 30 s Pause, kein Startlimit; kein Neustart nach Exit 3 (Circuit-Breaker), 4 (Sperre gehalten), 6 (Umgebung nicht demo) und 7 (Key passt nicht zum Paper-Konto) |
| Tägliche Auswahl | Orchestrator | unverändert: Orchestrator wählt, schreibt die Whitelist und startet/stoppt den Dienst |
| Log | `logs/incubation_bot_<Datum>.log` | unverändert (derselbe Dateiname) |

Der Orchestrator wechselt in den systemd-Modus, sobald `PAPER_BOT_SYSTEMD_UNIT=etoro-paper-bot.service` in seiner Umgebung
steht (`automation/systemd_bot.py`). Ohne die Variable bleibt alles wie vorher. Die Sperrdatei (`incubation_bot.lock`)
verhindert in jedem Fall einen zweiten Bot.

## Einrichtung (einmalig)

1. **Units installieren** (kein sudo, nichts wird gelöscht; aktiviert nur den Wächter-Timer):
   ```bash
   cd ~/etoro_nautilus && git pull origin main
   deploy/systemd/install.sh
   systemd-analyze --user verify ~/.config/systemd/user/etoro-paper-bot.service
   ```
2. **Linger einschalten**, damit die Dienste auch nach einem Neustart ohne Anmeldung laufen (braucht Ihr Passwort, einmalig):
   ```bash
   sudo loginctl enable-linger "$USER"
   loginctl show-user "$USER" -p Linger      # muss Linger=yes zeigen
   ```
   Ohne Linger stoppt systemd die Dienste beim Abmelden.
3. **Wechsel auf systemd** (kurzer Wechsel, der Bot ist ca. 1 Minute weg; eToro-Positionen und Stops bleiben):
   ```bash
   # a) laufenden Bot und Orchestrator geordnet beenden
   pkill -TERM -f 'automation/momentum_ls_run.py' ; sleep 20
   # b) Dienst aktivieren und starten (die Whitelist data/state/incubation_whitelist.json muss existieren)
   systemctl --user enable --now etoro-paper-bot.service
   # c) Umgebungsvariable für den Orchestrator dauerhaft setzen
   mkdir -p ~/.config/environment.d
   echo 'PAPER_BOT_SYSTEMD_UNIT=etoro-paper-bot.service' > ~/.config/environment.d/etoro.conf
   ```
   Der Orchestrator wird als Benutzer-Prozess gestartet (Terminal, `setsid`, ...). Läuft er aus Cron, braucht die
   Cron-Zeile zusätzlich `XDG_RUNTIME_DIR=/run/user/$(id -u)` und `PAPER_BOT_SYSTEMD_UNIT=etoro-paper-bot.service`.
   Danach den Orchestrator wie gewohnt starten, z. B. `PAPER_BOT_SYSTEMD_UNIT=etoro-paper-bot.service venv/bin/python3
   automation/daily_orchestrator.py --papertrading --skip-backtest`. Er stoppt/startet den Dienst nur, wenn sich die
   Whitelist ändert; bei gleicher Whitelist meldet er `LIVE_BOT_UNCHANGED`.

## Prüfen

```bash
systemctl --user status etoro-paper-bot.service           # active (running)
journalctl --user -u etoro-paper-bot.service -n 50         # Start/Stopp/Neustarts
tail -f ~/etoro_nautilus/logs/incubation_bot_$(date -u +%Y%m%d).log
systemctl --user list-timers etoro-ops-guard.timer         # Wächter alle 5 min
cat ~/etoro_nautilus/logs/errors/ops_guard_errors.log      # Ausfälle, Log-Budget
```

Ausfalltest (nur wenn keine kritische Position offen ist, oder bewusst): `kill -9 $(systemctl --user show -p MainPID --value etoro-paper-bot.service)`
-> nach ca. 30 s läuft der Bot wieder, die Reconciliation übernimmt offene eToro-Positionen (`Offene eToro-Position übernommen`),
der Wächter hat den Ausfall im Fehler-Log vermerkt.

## Zurück zum alten Verfahren

```bash
systemctl --user disable --now etoro-paper-bot.service
rm ~/.config/environment.d/etoro.conf          # Variable entfernen (neu anmelden)
```
Danach startet der Orchestrator den Bot wieder selbst per `Popen`. Der Wächter-Timer bleibt aktiv.

## Hinweise

- `systemctl --user stop` sendet SIGTERM, wartet bis zu 90 s (`TimeoutStopSec`) auf das geordnete Herunterfahren und
  startet den Dienst danach nicht von allein neu. Nur ein Absturz oder SIGKILL löst `Restart=always` aus.
- Die Unit lädt keine Schlüssel; der Bot liest `.env` selbst. Es werden keine Zugangsdaten in Unit-Dateien geschrieben.
- Der Dienst startet nur, wenn `data/state/incubation_whitelist.json` existiert (`ConditionPathExists`).
- Nach einem Circuit-Breaker-Stopp (Exit 3) oder `Bot läuft bereits` (Exit 4), nicht-demo-Umgebung (Exit 6) oder falschem Konto-Key (Exit 7, `ETORO_PAPER_ACCOUNT_CID` passt nicht zu `/me`) bleibt der Dienst `failed`: Ursache im Journal und im
  Fehler-Log des Wächters prüfen, dann `systemctl --user reset-failed etoro-paper-bot.service` und bewusst neu starten.

## Wöchentlicher Paper-Bericht

`etoro-paper-weekly.timer` (montags 06:00 UTC, `Persistent=true` holt verpasste Läufe nach) ruft `python -m automation.paper_weekly_report`
auf: abgeschlossene Trades der letzten 7 Tage direkt aus der eToro-Historie, Krypto-Gebühr nachgerechnet, Auswertung je
Anlageklasse, Strategie und Instrument, mit Hinweis, ob die Stichprobe (mindestens 30 Round-Trips) für eine Aussage reicht. Ergebnis:
`data/paper_reports/paper_weekly_<JJJJ-Www>.md` und `paper_weekly_latest.md`; der stündliche `paper_report_publish` legt sie unter
`logs/paper_reports/` ins Repo. Alle Trades werden zusätzlich in `data/state/paper_trades.jsonl` gesammelt. Sofort ausführen:
`venv/bin/python -m automation.paper_weekly_report --all` (alles seit dem ersten Trade) oder `systemctl --user start etoro-paper-weekly.service`.

## Tägliche Timer (Stage-C monitor 06:50 UTC, Gesundheitscheck 06:55 UTC)

`deploy/systemd/install_daily.sh` installiert und aktiviert `ebc-stagec-monitor.timer` (nur `monitor --env demo`, Log `~/ebc_stagec/logs/ebc_stagec_monitor.log`) und `etoro-daily-health.timer` (`automation/daily_health.py`, Bericht `results/daily/<Datum>.md`, Log `logs/daily_health.log`). Beide lesen nur, senden keine Orders und ändern weder `.env` noch bestehende Units.
