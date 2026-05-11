# Neue Aktien / Tickers hinzufügen

Diese Anleitung erklärt Schritt für Schritt, wie du neben Tesla (TSLA) weitere Aktien oder Instrumente in den Bot einbinden kannst. Sie richtet sich an Einsteiger ohne tiefe Python-Kenntnisse.

---

## Überblick: Wie funktioniert das System?

Bevor wir loslegen, ein kurzer Überblick über die Architektur:

```
get_instruments_id.py       ← Hilfsskript: Findet die numerische eToro-ID für ein Symbol
        │
        ▼
adapters/instrument_map.py  ← Zentrale Tabelle: Verknüpft eToro-ID ↔ Nautilus-Symbol
        │
        ▼
config/setups.py            ← Definiert welche Aktie mit welcher Strategie läuft
        │
        ▼
run_bot.py                  ← Startet den Bot (liest alles automatisch aus setups.py)
```

Für jede neue Aktie musst du **drei Dateien** anpassen:

1. `adapters/instrument_map.py` – ID-Mapping eintragen
2. `config/setups.py` – Bot-Instanz konfigurieren
3. *(optional)* `etoro_tesla_tracker.py` – Nur wenn du den Debug-Tracker nutzt

---

## Schritt 1: eToro Instrument-ID herausfinden

eToro verwendet intern **numerische IDs** (z. B. `1111` für Tesla) statt Ticker-Symbolen. Du brauchst diese ID, bevor du eine Aktie einbinden kannst.

### 1a. Das Hilfsskript anpassen

Öffne die Datei `get_instruments_id.py`. Ganz am Ende findest du diese Zeile:

```python
if __name__ == "__main__":
    get_etoro_instrument_id("TSLA")
```

Ersetze `"TSLA"` durch das Symbol der gewünschten Aktie, z. B. `"NVDA"` für Nvidia:

```python
if __name__ == "__main__":
    get_etoro_instrument_id("NVDA")
```

> **Hinweis:** Das Symbol muss exakt dem `internalSymbolFull`-Feld der eToro API entsprechen (meistens der bekannte Börsenticker, z. B. `AAPL`, `MSFT`, `AMZN`).

### 1b. Skript ausführen

Stelle sicher, dass deine `.env`-Datei mit `ETORO_API_KEY` und `ETORO_USER_KEY` gefüllt ist, dann:

```bash
python get_instruments_id.py
```

**Erwartete Ausgabe (Beispiel für NVDA):**

```
Suche nach Instrumenten-ID für: NVDA ...
✅ Erfolg! Die eToro Instrument ID für NVDA ist: 2254
-> Trage diese ID in deine adapters/etoro_data.py und Tracker ein.
```

**Notiere diese Zahl!** Im Beispiel ist es `2254`. Deine Zahl wird eine andere sein.

---

## Schritt 2: ID in die Instrument-Map eintragen

Öffne die Datei `adapters/instrument_map.py`. Sie sieht aktuell so aus:

```python
ETORO_INSTRUMENTS = {
    "1111": "TSLA.ETORO",
    "1":    "EURUSD.ETORO",
    "1001": "AAPL.ETORO",   # Beispiel (Bitte echte ID prüfen)
    "1002": "AMZN.ETORO",   # Beispiel (Bitte echte ID prüfen)
}
```

Füge eine neue Zeile für dein Instrument hinzu. Das Format ist immer:

```
"<eToro-ID>": "<TICKER>.ETORO",
```

**Beispiel – Nvidia mit ID `2254` hinzufügen:**

```python
ETORO_INSTRUMENTS = {
    "1111": "TSLA.ETORO",
    "1":    "EURUSD.ETORO",
    "1001": "AAPL.ETORO",
    "1002": "AMZN.ETORO",
    "2254": "NVDA.ETORO",   # ← NEU: Nvidia
}
```

> ⚠️ **Wichtig:** Der Teil rechts vom Doppelpunkt (`"NVDA.ETORO"`) muss immer mit `.ETORO` enden. Dieser String wird als eindeutiger Bezeichner in Nautilus Trader verwendet.

---

## Schritt 3: Strategie in setups.py konfigurieren

Öffne die Datei `config/setups.py`. Hier legst du fest, welche Aktie mit welcher Strategie gehandelt werden soll.

Die Datei enthält die Liste `ACTIVE_BOTS`. Aktuell ist dort Tesla eingetragen (und Apple als auskommentiertes Beispiel):

```python
ACTIVE_BOTS = [
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1111",
        "symbol": "TSLA.ETORO",
        "bar_type": "TSLA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5
        }
    },
    # --- BEISPIEL FÜR EIN ZWEITES ASSET ---
    # { ... }
]
```

### Neuen Eintrag hinzufügen

Füge nach dem Tesla-Block (vor der schliessenden `]`-Klammer) ein neues Dictionary ein:

```python
ACTIVE_BOTS = [
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1111",
        "symbol": "TSLA.ETORO",
        "bar_type": "TSLA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5
        }
    },
    # ← NEU: Nvidia
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "2254",                          # Die ID aus Schritt 1
        "symbol": "NVDA.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "NVDA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 10                         # Eigene Strategie-Parameter
        }
    },
]
```

### Felder im Detail

| Feld | Bedeutung | Beispielwert |
|---|---|---|
| `strategy_class` | Welche Strategie-Klasse verwendet wird | `"SmaCrossoverStrategy"` |
| `etoro_id` | Die numerische ID aus Schritt 1 (als String) | `"2254"` |
| `symbol` | Nautilus-Symbol – muss mit `instrument_map.py` übereinstimmen | `"NVDA.ETORO"` |
| `bar_type` | Zeitrahmen und Preistyp für die Bars | `"NVDA.ETORO-1-MINUTE-MID-INTERNAL"` |
| `params.sma_period` | Perioden für den gleitenden Durchschnitt | `10` |

> **Tipp für `bar_type`:** Das Format ist immer `<SYMBOL>-<ZEITRAHMEN>-MID-INTERNAL`. Mögliche Zeitrahmen: `1-MINUTE`, `5-MINUTE`, `15-MINUTE`, `1-HOUR`. Passe den Zeitrahmen deiner Handelsstrategie an.

---

## Schritt 4: Bot starten und überprüfen

Starte den Bot wie gewohnt:

```bash
python run_bot.py
```

In der Konsolenausgabe siehst du jetzt beide Instrumente:

```
✅ Strategie registriert: SMA_TSLA.ETORO_0
✅ Strategie registriert: SMA_NVDA.ETORO_1

🚀 Starte Nautilus eToro-Orchestrator mit 2 Instrumenten...
Drücke Ctrl+C zum Beenden
```

Wenn ein Abonnement für das neue Instrument erfolgreich ist, erscheint ausserdem:

```
Abonniert: ['instrument:1111', 'instrument:2254']
```

---

## Häufige Fehler und Lösungen

### ❌ `eToro ID 2254 nicht in ETORO_INSTRUMENTS gefunden!`

**Ursache:** Die `etoro_id` in `setups.py` wurde nicht in `instrument_map.py` eingetragen oder die IDs stimmen nicht überein.

**Lösung:** Prüfe, ob der Eintrag in `adapters/instrument_map.py` vorhanden ist und beide IDs exakt gleich sind (Tipp: Keine führenden Nullen, kein Leerzeichen).

---

### ❌ `Instrument 'NVDA' wurde in den Suchergebnissen nicht gefunden.`

**Ursache:** Das Symbol in `get_instruments_id.py` entspricht nicht dem internen eToro-Symbol.

**Lösung:** Versuche alternative Schreibweisen. Krypto-Symbole haben oft ein `/` (z. B. `BTC/USD`), manche Aktien haben länderspezifische Suffixe.

---

### ❌ Strategie startet, aber es kommen keine Ticks

**Ursache:** Der Markt ist geschlossen (eToro sendet nur während der Handelszeiten Daten) oder die Instrument-ID ist falsch.

**Lösung:** Prüfe zunächst die ID via `get_instruments_id.py` erneut. Teste ausserhalb der Handelszeiten mit `etoro_tesla_tracker.py` (nach Anpassung der `INSTRUMENT_ID`).

---

## Zusammenfassung: Checkliste für ein neues Instrument

- [ ] Symbol in `get_instruments_id.py` eingetragen und Skript ausgeführt
- [ ] Numerische eToro-ID notiert
- [ ] Eintrag in `adapters/instrument_map.py` hinzugefügt (`"<ID>": "<TICKER>.ETORO"`)
- [ ] Neuen Bot-Block in `config/setups.py` unter `ACTIVE_BOTS` eingefügt
- [ ] `etoro_id`, `symbol` und `bar_type` in `setups.py` sind konsistent mit `instrument_map.py`
- [ ] Bot gestartet und in der Ausgabe beide Instrumente bestätigt
