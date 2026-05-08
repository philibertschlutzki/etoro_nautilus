# 💻 Useful Commands: Operations & Maintenance

Dieses Handbuch enthält die wichtigsten Befehle zur Verwaltung der eToro Nautilus Plattform auf deiner Cloud-VM.

## 1. System & Deployment (Updates)

Wenn du Code lokal in VS Code geändert und zu GitHub gepusht hast, verwende diese Befehle auf der VM:

* **Repository aktualisieren:**
```bash
cd /opt/etoro_nautilus
sudo -u tradingbot git pull origin main

```


* **Dienste nach Code-Änderung neu starten:**
```bash
sudo systemctl restart nautilus-catalog.service
sudo systemctl restart nautilus-bot.service

```


* **Requirements aktualisieren (falls neue Libs hinzugefügt wurden):**
```bash
sudo -u tradingbot ./venv/bin/pip install -r requirements.txt

```



## 2. Service Management (Systemd)

Befehle zur Kontrolle der Hintergrunddienste:

| Aktion | Befehl (Catalog) | Befehl (Bot) |
| --- | --- | --- |
| **Status prüfen** | `sudo systemctl status nautilus-catalog` | `sudo systemctl status nautilus-bot` |
| **Starten** | `sudo systemctl start nautilus-catalog` | `sudo systemctl start nautilus-bot` |
| **Stoppen** | `sudo systemctl stop nautilus-catalog` | `sudo systemctl stop nautilus-bot` |
| **Neu starten** | `sudo systemctl restart nautilus-catalog` | `sudo systemctl restart nautilus-bot` |

## 3. Logfile-Analyse (Journalctl)

Zur Überwachung der Handelsaktivität und Fehlersuche in Echtzeit:

* **Trading-Bot Logs (Echtzeit):**
```bash
sudo journalctl -u nautilus-bot.service -f

```


* **Data-Catalog Logs (Echtzeit):**
```bash
sudo journalctl -u nautilus-catalog.service -f

```


* **Kombinierte Ansicht (beide Dienste):**
```bash
sudo journalctl -u nautilus-bot.service -u nautilus-catalog.service -f

```


* **Fehler der letzten 24 Stunden anzeigen:**
```bash
sudo journalctl -u nautilus-bot.service --since "24h" | grep -i "error"

```



## 4. Daten-Management (Parquet & Storage)

Überwachung der aufgezeichneten Marktdaten im Katalog:

* **Größe des Datenverzeichnisses prüfen:**
```bash
du -sh /data/nautilus

```


* **Anzahl der gespeicherten Parquet-Dateien zählen:**
```bash
find /data/nautilus -name "*.parquet" | wc -l

```


* **Details der letzten aufgezeichneten Datei:**
```bash
ls -lhR /data/nautilus | tail -n 20

```



## 5. Hilfsskripte (eToro & Nautilus)

Manuelle Ausführung der im Projekt enthaltenen Tools:

* **Instrumenten-ID suchen (z.B. Tesla):**
```bash
sudo -u tradingbot ./venv/bin/python get_instruments_id.py

```


* **Nautilus Installation lokal testen:**
```bash
sudo -u tradingbot ./venv/bin/python test_nautilus.py

```



---

*Tipp: Nutze `alias` in deiner `.bashrc`, um häufig genutzte Befehle wie den Log-Check abzukürzen (z.B. `alias botlog='sudo journalctl -u nautilus-bot.service -f'`).*
