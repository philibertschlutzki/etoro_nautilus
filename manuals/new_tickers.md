# 🎯 Neue Instrumente hinzufügen

Diese Anleitung erklärt Schritt für Schritt, wie du neben den bereits konfigurierten Märkten (z. B. Tesla) weitere Aktien oder Instrumente in den Bot einbinden kannst.

---

## Überblick: Die Architektur der Marktdaten

Bevor wir loslegen, hier ein kurzer Überblick, wie der Bot Instrumente verarbeitet:

```text
get_instruments_id.py       ← Hilfsskript: Findet die numerische eToro-ID für ein Symbol
        │
        ▼
adapters/instrument_map.py  ← Zentrale Tabelle: Verknüpft eToro-ID ↔ Nautilus-Symbol
        │
        ▼
config/setups.py            ← Definiert, welche Aktie mit welcher Strategie läuft
        │
        ▼
run_bot.py                  ← Startet den Bot (liest die Konfiguration automatisch)
```

Für jedes neue Instrument müssen **zwei zentrale Dateien** angepasst werden:
1.  `adapters/instrument_map.py` (Zentrale Registrierung)
2.  `config/setups.py` (Strategie-Zuweisung)

---

## Schritt 1: eToro Instrument-ID herausfinden

Die eToro API arbeitet intern mit numerischen IDs (z. B. `1111` für Tesla) und nicht mit den bekannten Ticker-Symbolen. Du musst diese ID ermitteln.

1.  Öffne das Hilfsskript `get_instruments_id.py`.
2.  Ganz am Ende der Datei findest du den Aufruf:
    ```python
    if __name__ == "__main__":
        get_etoro_instrument_id("TSLA") # Ändere "TSLA" zum gewünschten Symbol
    ```
3.  Ändere den String zum gewünschten Ticker (z. B. `"NVDA"` für Nvidia).
4.  Führe das Skript aus:
    ```bash
    python get_instruments_id.py
    ```
5.  Notiere dir die ausgegebene ID (z.B. `2254`).

*Hinweis: Das Skript erfordert, dass deine `.env`-Datei mit den eToro API-Keys korrekt konfiguriert ist.*

---

## Schritt 2: Instrument in der Map registrieren

Öffne die Datei `adapters/instrument_map.py`. Füge deine neu ermittelte ID und das dazugehörige Nautilus-Symbol als neues Key-Value-Paar in das Dictionary ein.

**Beispiel für Nvidia (ID: 2254):**

```python
ETORO_INSTRUMENTS = {
    "1111": "TSLA.ETORO",
    "1":    "EURUSD.ETORO",
    "2254": "NVDA.ETORO",   # ← Neuer Eintrag
}
```

> ⚠️ **Wichtig:** Der Value (das Nautilus-Symbol) muss immer mit dem Suffix `.ETORO` enden (z. B. `NVDA.ETORO`), da dies für die interne Verkaufsplatz-Zuweisung der Nautilus-Engine (`Venue("ETORO")`) zwingend erforderlich ist.

---

## Schritt 3: Strategie konfigurieren (`setups.py`)

Öffne die Datei `config/setups.py`. Hier befindet sich das Array `ACTIVE_BOTS`, in dem die laufenden Strategien definiert sind.

Füge einen neuen Dictionary-Block für dein neues Instrument hinzu:

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
    # ← Neuer Block für NVDA
    {
        "strategy_class": "SmaCrossoverStrategy", # oder eine andere Strategie
        "etoro_id": "2254",                       # Die eToro ID aus Schritt 1
        "symbol": "NVDA.ETORO",                   # Muss mit instrument_map.py übereinstimmen
        "bar_type": "NVDA.ETORO-1-MINUTE-MID-INTERNAL", # Nautilus Bar-Typ (z.B. 1-MINUTE, 1-HOUR)
        "params": {
            "sma_period": 14                      # Deine optimierten Parameter
        }
    },
]
```

### Parameter-Erklärung:
*   `etoro_id`: Muss ein String sein.
*   `bar_type`: Format ist immer `<SYMBOL>-<ZEITRAHMEN>-MID-INTERNAL`. Passe den Zeitrahmen (`1-MINUTE`, `1-HOUR` etc.) an die Anforderungen deiner Strategie an.

---

## Schritt 4: Überprüfung und Neustart

Nachdem du die Änderungen gespeichert hast, starte den Bot neu.

```bash
python run_bot.py
```

Beobachte die Konsolenausgabe. Du solltest Meldungen sehen, dass das neue Instrument registriert und erfolgreich abonniert wurde:

```text
✅ Strategie registriert: SMA_NVDA.ETORO_1
...
Abonniert: ['instrument:1111', 'instrument:2254']
```

### Fehlerbehebung
*   **Keine Ticks empfangen?** Prüfe, ob die regulären Handelszeiten des Marktes geöffnet sind.
*   **ID nicht gefunden Fehler?** Stelle sicher, dass die `etoro_id` in `setups.py` exakt als String mit dem Key in `instrument_map.py` übereinstimmt (keine Leerzeichen).
