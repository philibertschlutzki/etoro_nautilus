# Paper-Bot als systemd-User-Dienst

Ziel: Fällt der Bot aus (zuletzt mehrfach ohne Log-Zeile, per SIGKILL), startet systemd ihn nach 30 Sekunden neu. Der
Wächter (`ops_guard`) meldet den Ausfall trotzdem im Fehler-Log. Alles läuft als Benutzer-Dienst, ohne Root-Rechte.

## Was sich ändert

| | bisher | mit systemd |
|---|---|---|
| Bot-Start | `daily_orchestrator.py` startet `momentum_ls_run.py` per `Popen` | systemd startet den Dienst `etoro-paper-bot.service` |
| Ausfall | Bot bleibt weg | `Restart=always`, 30 s Pause, kein Startlimit; kein Neustart nach Exit 3 (Circuit-Breaker) und 4 (Sperre gehalten) |
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
- Nach einem Circuit-Breaker-Stopp (Exit 3) oder `Bot läuft bereits` (Exit 4) bleibt der Dienst `failed`: Ursache im Journal und im
  Fehler-Log des Wächters prüfen, dann `systemctl --user reset-failed etoro-paper-bot.service` und bewusst neu starten.

## Wochenbericht (systemd-Timer, kein cron)

`deploy/systemd/install.sh` installiert und aktiviert zusätzlich `etoro-paper-weekly.timer` (Montag 06:00 UTC,
`Persistent=true` holt einen verpassten Lauf nach). Der Dienst schreibt `data/paper_reports/paper_weekly_<Jahr>-W<Woche>.md`
und veröffentlicht ihn nach `logs/paper_reports/`. Prüfen: `systemctl --user list-timers etoro-paper-weekly.timer`,
Ausgabe in `logs/paper_weekly.log`. Sofort ausführen: `systemctl --user start etoro-paper-weekly.service`.
