# 🚀 eToro Nautilus Trading Bot

Willkommen beim **eToro Nautilus** Projekt! Dies ist ein anfängerfreundliches Grundgerüst für einen Algorithmic Trading Bot in Python. Das Projekt nutzt das leistungsstarke [Nautilus Trader](https://nautilustrader.io/) Framework, um eine Echtzeit-WebSocket-Verbindung zur eToro-API herzustellen.

Aktuell ist der Bot darauf ausgelegt, Live-Preisdaten (Quote Ticks) für die **Tesla-Aktie (TSLA)** zu empfangen und den Spread in einer Basis-Strategie zu berechnen. Es ist der perfekte Startpunkt, um in die Welt des automatisierten Handels einzusteigen!

---

## 📋 1. Projekt-Übersicht

Dieses Projekt verbindet das professionelle Trading-Framework **Nautilus Trader** mit der **eToro-API** via WebSockets. Der Fokus liegt auf Einfachheit und einem schnellen Einstieg:
- **Live-Marktdaten:** Empfängt Echtzeit-Updates für Tesla (TSLA).
- **Strategie-Grundgerüst:** Eine einfache Strategie, die ankommende Preisdaten verarbeitet und den Spread (die Differenz zwischen Kauf- und Verkaufspreis) berechnet und in der Konsole ausgibt.

---

## 🛠️ 2. Voraussetzungen (Prerequisites)

Bevor wir starten, stelle bitte sicher, dass du folgende Dinge bereit hast:

- **Python-Version:** Python 3.10 oder neuer wird dringend empfohlen.
- **Benötigte Bibliotheken** (diese werden später automatisch installiert):
  - `nautilus_trader>=1.226.0` (Das Core-Framework)
  - `websockets` (Für die Echtzeit-Verbindung zu eToro)
  - `python-dotenv` (Um sichere Umgebungsvariablen wie API-Keys zu laden)
- **eToro API-Zugangsdaten:** Du benötigst API-Zugang von eToro (einen API-Key und ggf. einen User-Key).

---

## 🚀 3. Installation & Setup

Folge dieser Schritt-für-Schritt-Anleitung, um den Bot auf deinem System zum Laufen zu bringen. Keine Sorge, es ist einfacher als es aussieht!

### Schritt 1: Repository klonen
Klone das Projekt auf deinen lokalen Rechner:
```bash
git clone <URL_ZUM_REPOSITORY>
cd <NAME_DES_VERZEICHNISSES>
```

### Schritt 2: Virtuelles Python-Environment erstellen und aktivieren
Es ist immer eine gute Praxis, Python-Projekte in einer isolierten Umgebung ("Virtual Environment") laufen zu lassen.

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Schritt 3: Abhängigkeiten installieren
Jetzt installieren wir alle benötigten Pakete aus der `requirements.txt`:
```bash
pip install -r requirements.txt
```

### Schritt 4: Konfiguration der `.env`-Datei 🔐
Dein Bot benötigt Zugangsdaten, um sich mit eToro zu verbinden. Diese speichern wir sicher ab.
Erstelle dazu eine neue Datei namens `.env` im **Hauptverzeichnis** des Projekts und füge folgenden Inhalt ein:

```env
ETORO_API_KEY=DEIN_API_KEY_HIER
ETORO_USER_KEY=DEIN_USER_KEY_HIER
```

Ersetze die Platzhalter mit deinen echten eToro-Zugangsdaten.
> ⚠️ **WICHTIG:** Die Datei `.env` darf **niemals** in Git versioniert oder veröffentlicht werden! Sie enthält deine geheimen Schlüssel. In diesem Projekt ist sie bereits in der `.gitignore`-Datei abgedeckt.

---

## 📁 4. Projektstruktur & Nutzung

Hier ist eine kurze Übersicht der wichtigsten Dateien und wie du sie nutzt:

- **`test_nautilus.py`**
  Dieses Skript testet, ob die Nautilus Trader Bibliothek korrekt installiert wurde.
  *Ausführen:* `python test_nautilus.py`

- **`etoro_tesla_tracker.py`**
  Ein einfaches asynchrones Skript, um die reine WebSocket-Verbindung zu eToro und die Authentifizierung zu testen – völlig unabhängig vom Nautilus-Framework. Ideal zur Fehlersuche, falls die Verbindung mal nicht klappt!

- **`run_bot.py`**
  Das ist das **Hauptskript** deines Trading-Bots. Es startet den "TradingNode", registriert den "ETORO_WS_CLIENT" (über die Factory in `adapters/etoro_data.py`) und führt deine "EToroStrategy" (aus `strategies/etoro_strategy.py`) aus.
  *Ausführen:* `python run_bot.py`

---

## 🏗️ 5. Architektur (Wie der Bot funktioniert)

Der Bot basiert auf einer klaren Trennung von Aufgaben, was ihn sehr erweiterbar macht:

1. **Der Adapter (`adapters/etoro_data.py`):**
   Dieses Modul stellt den eToro WebSocket-Client bereit. Er verbindet sich mit eToro, authentifiziert sich mit deinen Schlüsseln und wandelt die einkommenden JSON-Nachrichten von eToro in saubere Nautilus-kompatible Datenstrukturen (wie `QuoteTick`) um.
2. **Die Strategie (`strategies/etoro_strategy.py`):**
   Hier lebt die eigentliche "Intelligenz" deines Bots. Die Strategie empfängt die vom Adapter aufbereiteten Preisdaten (QuoteTicks) und führt deine Logik aus. Aktuell berechnet und protokolliert ("loggt") sie den Spread der Aktie.

Das Hauptskript (`run_bot.py`) verbindet beide Welten, indem es den Adapter und die Strategie im Nautilus Trader `TradingNode` zusammenführt.

---

## 🎯 6. Nächste Schritte / Anpassungsmöglichkeiten

Sobald du den Bot erfolgreich gestartet hast, kannst du anfangen, ihn an deine Wünsche anzupassen:

- **Andere Aktien tracken:**
  Öffne die relevanten Dateien und ändere die Instrument-ID (z. B. von Tesla `TSLA` zu Apple `AAPL` oder Bitcoin `BTC`).
- **Echte Order-Logik implementieren:**
  Schau dir die Methode `on_quote_tick` in der Datei `strategies/etoro_strategy.py` an. Dies ist der perfekte Ort, um Bedingungen hinzuzufügen: *"Wenn der Spread kleiner als X ist, dann kaufe (Buy-Order senden)"*.
- **Daten speichern:**
  Du könntest die empfangenen Tick-Daten in einer Datenbank oder einer CSV-Datei speichern, um Backtests für spätere Strategien durchzuführen.

Viel Erfolg und Happy Trading! 📈🤖
