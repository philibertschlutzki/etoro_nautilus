#!/bin/bash
# Periodischer Report für das Demo-Papertrading (nur lesend, keine Orders).
#
# Aufruf:  automation/papertrading_report.sh
# Ausgabe: stdout und Anhang an logs/papertrading_report.log
# Exit:    0 = Bericht ok und Bot lebt | 1 = Bot läuft nicht oder Konto-Abfrage fehlgeschlagen | 2 = Konfigurationsfehler
#
# ───────────────────────────── Cronjob auf dem Ubuntu-Server einrichten ─────────────────────────────
# Annahme: das Repo liegt unter /home/minisforum/etoro_nautilus, das venv unter ./venv, die .env mit den Keys im
# Repo-Root (nicht in Cron-Zeilen schreiben!). Der Server-Benutzer ist derselbe, unter dem der Bot läuft.
#
#  1. Skript ausführbar machen und einmal von Hand testen:
#         cd /home/minisforum/etoro_nautilus
#         chmod +x automation/papertrading_report.sh
#         automation/papertrading_report.sh ; echo "Exit=$?"
#     Erwartet: ein Bericht mit "Bot: PID ... läuft", Guthaben und offenen Positionen.
#
#  2. Crontab des Benutzers öffnen:
#         crontab -e
#     (beim ersten Mal einen Editor wählen, z. B. nano)
#
#  3. Diese Zeilen ans Ende setzen (stündlich um Minute 7; Zeitzone des Servers prüfen mit `timedatectl`):
#         SHELL=/bin/bash
#         MAILTO=""
#         7 * * * * /home/minisforum/etoro_nautilus/automation/papertrading_report.sh >> /home/minisforum/etoro_nautilus/logs/papertrading_report_cron.log 2>&1
#     Alternativ nur werktags zu Handelszeiten (09-22 Uhr Servertime):
#         7 9-22 * * 1-5 /home/minisforum/etoro_nautilus/automation/papertrading_report.sh >> /home/minisforum/etoro_nautilus/logs/papertrading_report_cron.log 2>&1
#     Speichern und Editor schliessen. Cron nimmt die Zeile sofort auf, ein Neustart ist nicht nötig.
#
#  4. Prüfen:
#         crontab -l                                   # Zeile ist eingetragen
#         systemctl status cron                        # Dienst läuft (active (running))
#         tail -f logs/papertrading_report_cron.log    # nach der nächsten vollen Stunde :07 erscheint der Bericht
#         tail logs/papertrading_report.log            # Verlauf aller Berichte
#     Wenn nichts erscheint: `grep CRON /var/log/syslog | tail` zeigt, ob der Job gestartet wurde. Typische
#     Ursachen: falscher Pfad, Skript nicht ausführbar (chmod +x), venv fehlt.
#
#  5. Benachrichtigung bei Problemen (optional): Exit-Code != 0 heisst "Bot tot" oder "Konto nicht erreichbar".
#     Z. B. Mail über den lokalen MTA (Paket `mailutils`), dafür die Cron-Zeile ändern zu:
#         7 * * * * /home/minisforum/etoro_nautilus/automation/papertrading_report.sh >> /home/minisforum/etoro_nautilus/logs/papertrading_report_cron.log 2>&1 || echo "Papertrading-Report: Exit $?" | mail -s "Papertrading Alarm" deine@mail.de
#
#  6. Entfernen: `crontab -e` und die Zeilen löschen.
#
# Cron läuft mit minimaler Umgebung (PATH nur /usr/bin:/bin). Deshalb wechselt dieses Skript selbst ins Repo und
# aktiviert das venv, statt sich auf die Umgebung des Aufrufers zu verlassen.
# ─────────────────────────────────────────────────────────────────────────────────────────────────────

cd "$(dirname "$0")/.." || exit 2
source venv/bin/activate || { echo "venv fehlt: $(pwd)/venv" >&2; exit 2; }
exec python -m automation.papertrading_report "$@"
