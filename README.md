# 🚀 eToro Nautilus Multi-Bot Plattform

Willkommen beim **eToro Nautilus** Projekt! Dies ist ein anfängerfreundliches, aber hochskalierbares Grundgerüst für einen Algorithmic Trading Bot in Python. Das Projekt nutzt das professionelle [Nautilus Trader](https://nautilustrader.io/) Framework, um eine Echtzeit-WebSocket-Verbindung zur eToro-API herzustellen.

Der Bot ist als **Multi-Asset & Multi-Strategie Orchestrator** aufgebaut. Das bedeutet: Du kannst völlig problemlos mehrere Strategien auf unterschiedlichen Aktien (z.B. Tesla, Apple, Bitcoin) **gleichzeitig** laufen lassen, ohne den eigentlichen Code der Verbindung verändern zu müssen.

---

## 📋 1. Projekt-Übersicht

Dieses Projekt verbindet das professionelle Trading-Framework **Nautilus Trader** mit der **eToro-API** via WebSockets. Der Fokus liegt auf Modularität und einem schnellen Einstieg:
- **Live-Marktdaten:** Empfängt Echtzeit-Updates von eToro für beliebig viele Aktien gleichzeitig.
- **Konfigurationsbasiert:** Füge neue Aktien oder Strategien einfach über eine Textdatei (`setups.py`) hinzu.
- **Strategie-Grundgerüst:** Enthält eine klassische "SMA Crossover" Beispielstrategie, die Kauf- und Verkaufssignale basierend auf gleitenden Durchschnitten (Moving Averages) generiert.

---

## 🛠️ 2. Voraussetzungen (Prerequisites)

Bevor wir starten, stelle bitte sicher, dass du folgende Dinge bereit hast:

- **Python-Version:** Python 3.10 oder neuer wird dringend empfohlen.
- **Benötigte Bibliotheken** (diese werden später automatisch installiert):
  - `nautilus_trader>=1.226.0` (Das Core-Framework)
  - `websockets` (Für die Echtzeit-Verbindung zu eToro)
  - `python-dotenv` (Um sichere Umgebungsvariablen wie API-Keys zu laden)
- **eToro API-Zugangsdaten:** Du benötigst API-Zugang von eToro (einen API-Key und einen User-Key).

---

## 🚀 3. Installation & Setup

Folge dieser Schritt-für-Schritt-Anleitung, um den Bot auf deinem System zum Laufen zu bringen.

### Schritt 1: Repository klonen
Klone das Projekt auf deinen lokalen Rechner:
```bash
git clone [https://github.com/philibertschlutzki/etoro_nautilus.git](https://github.com/philibertschlutzki/etoro_nautilus.git)
cd etoro_nautilus/

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

> ⚠️ **WICHTIG:** Die Datei `.env` darf **niemals** in Git versioniert oder veröffentlicht werden! Sie enthält deine geheimen Schlüssel. In diesem Projekt ist sie bereits in der `.gitignore`-Datei geschützt.

---

## 📁 4. Wie funktioniert der Bot? (Die Architektur)

Der Bot ist in drei logische Bereiche unterteilt, damit du nicht aus Versehen den Verbindungs-Code kaputt machst, wenn du eine neue Strategie baust:

1. **Das Gehirn (`config/setups.py`):** Hier sagst du dem Bot, was er tun soll. Welche Strategie soll auf welcher Aktie mit welchen Parametern laufen?
2. **Die Wörterbücher (`adapters/instrument_map.py` & `get_instruments_id.py`):**
eToro nutzt intern kryptische Zahlen (z.B. ist Tesla die ID `1111`). Hier übersetzen wir diese Zahlen in lesbare Namen wie `TSLA.ETORO`.
3. **Die Logik (`strategies/...`):**
Hier liegen deine Handelsstrategien (z.B. `sma_crossover.py`). Sie sagen dem Bot, *wann* er kaufen oder verkaufen soll.
4. **Der Motor (`run_bot.py`):**
Dieses Skript liest deine Konfiguration aus, verbindet sich mit eToro und startet alle definierten Strategien automatisch.

---

## 🎯 5. So fügst du neue Aktien und Strategien hinzu (Für Anfänger)

Das Hinzufügen einer neuen Aktie (z.B. Apple) oder das Starten einer zweiten Strategie ist super einfach und erfordert keine tiefen Programmierkenntnisse.

### Schritt A: Die eToro-ID der neuen Aktie finden

eToro braucht eine spezifische ID für jede Aktie. Nutze das beiliegende Hilfs-Skript, um sie zu finden:

1. Öffne die Datei `get_instruments_id.py` und ändere ganz unten den Suchbegriff (z.B. auf `"AAPL"` für Apple).
2. Führe das Skript aus: `python get_instruments_id.py`
3. Das Skript gibt dir die ID zurück (z.B. `1001`).

### Schritt B: Die ID in die Map eintragen

Damit Nautilus den Namen versteht, tragen wir die ID in unser "Wörterbuch" ein.

1. Öffne `adapters/instrument_map.py`.
2. Füge deine neue ID hinzu:

```python
ETORO_INSTRUMENTS = {
    "1111": "TSLA.ETORO",
    "1001": "AAPL.ETORO",  # <-- Hier ist deine neue Aktie!
}

```

### Schritt C: Den Bot für die neue Aktie aktivieren

Jetzt sagen wir dem Hauptprogramm, dass es für diese Aktie eine Strategie starten soll.

1. Öffne `config/setups.py`.
2. Kopiere einen bestehenden Bot-Block im `ACTIVE_BOTS`-Array oder füge einen neuen hinzu:

```python
ACTIVE_BOTS = [
    # Dein erster Bot (Tesla)
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1111",
        "symbol": "TSLA.ETORO",
        "bar_type": "TSLA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": { "sma_period": 5 }
    },
    # Dein neuer Bot (Apple)
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1001",
        "symbol": "AAPL.ETORO",
        "bar_type": "AAPL.ETORO-1-MINUTE-MID-INTERNAL",
        "params": { "sma_period": 10 } # Vielleicht willst du hier einen anderen SMA nutzen?
    }
]

```

### Schritt D: Bot starten!

Starte das Hauptprogramm. Der Bot wird sich automatisch mit eToro verbinden und **beide** Streams (Tesla und Apple) parallel überwachen und verarbeiten!

```bash
python run_bot.py

```

---

## 🔧 6. Eigene Strategien entwickeln

Wenn du eine eigene Logik entwickeln willst (z.B. einen Breakout-Bot):

1. Erstelle eine neue Datei im Ordner `strategies/` (z.B. `breakout.py`).
2. Programmiere dort deine Nautilus-Strategie.
3. Importiere diese neue Strategie oben in der Datei `run_bot.py`.
4. Trage sie in der `config/setups.py` ein (ändere den Namen unter `"strategy_class"` auf deine neue Strategie).

Viel Erfolg und Happy Algorithmic Trading! 📈🤖
