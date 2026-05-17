# 🎯 Neue Instrumente hinzufügen

Diese Anleitung erklärt Schritt für Schritt, wie du neben den bereits konfigurierten Märkten (z. B. Tesla) weitere Aktien oder Instrumente in den Bot einbinden kannst.









## Überblick: Die Architektur der Marktdaten

Bevor wir loslegen, hier ein kurzer Überblick, wie der Bot Instrumente verarbeitet:

```text
dev_scripts/auto_map_insturments.py  ← Automatisiertes Auto-Discovery (Bevorzugt)
get_instruments_id.py                ← Manuelles Hilfsskript
        │
        ▼
adapters/instrument_map.py           ← Zentrale Tabelle: Verknüpft eToro-ID ↔ Nautilus-Symbol
        │
        ▼
dev_scripts/momentum_ls_universe.py  ← Momentum-LS Smart Portfolio Integration
dev_scripts/momentum_ls_allocator.py ← Dynamische Kapitalscheiben Zuweisung
        │
        ▼
config/setups.py                     ← Definiert, welche Aktie mit welcher Strategie (ohne Momentum-LS) läuft
        │
        ▼
run_bot.py                           ← Startet den Bot (liest die Konfiguration automatisch)
```

Für jedes neue Instrument müssen **zwei zentrale Dateien** angepasst werden:
1.  `adapters/instrument_map.py` (Zentrale Registrierung)
2.  `config/setups.py` (Strategie-Zuweisung)




## Schritt 1: eToro Instrument-ID herausfinden (Auto-Discovery)

Der empfohlene Weg, neue IDs hinzuzufügen, ist die Nutzung des Auto-Discovery Skripts für Momentum-LS:

```bash
python3 dev_scripts/auto_map_insturments.py
```
Dieses Skript gleicht die `momentum_ls.json` mit der eToro-API ab und fügt unbekannte Symbole automatisch der `instrument_map.py` hinzu.

**Alternativ (Manuell):**
1.  Öffne das Hilfsskript `dev_scripts/get_instruments_id.py`.
2.  Ganz am Ende der Datei findest du den Aufruf (ändere den String zum gewünschten Ticker):
    ```python
    if __name__ == "__main__":
        get_etoro_instrument_id("NVDA")
    ```
3.  Führe das Skript aus:
    ```bash
    python3 dev_scripts/get_instruments_id.py
    ```
4.  Notiere dir die ausgegebene ID (z.B. `2254`).


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


> ⚠️ **Wichtig:** Für Kryptowährungen muss das Symbol zusätzlich in `_CRYPTO_SYMBOLS` in `adapters/etoro_data.py` eingetragen werden (als frozenset).

> ⚠️ **Wichtig:** Der Value (das Nautilus-Symbol) muss immer mit dem Suffix `.ETORO` enden (z. B. `NVDA.ETORO`), da dies für die interne Verkaufsplatz-Zuweisung der Nautilus-Engine (`Venue("ETORO")`) zwingend erforderlich ist.

---


### Schritt 2.5: Price Precision

Die Preisgenauigkeit (Dezimalstellen) wird automatisch auf Basis von Regeln in der `Equity` Initialisierung gesetzt.

| Symbol enthält | `price_precision` | Beispiele |
|----------------|-------------------|-----------|
| `SHIB` oder `PEPE` | 8 | SHIBxM, PEPExM |
| `BTC` oder `ETH` | 2 | BTC, ETH |
| Alle anderen | 5 | TSLA, ADA, SOL |

*Hinweis: Wenn ein Symbol nicht in dieses Standardraster passt, musst du die Logik in `_register_instruments()` im eToro Adapter anpassen.*


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
            "fast_sma": 5,
            "slow_sma": 10
        }
    },
    # ← Neuer Block für NVDA
    {
        "strategy_class": "SmaCrossoverStrategy", # oder eine andere Strategie
        "etoro_id": "2254",                       # Die eToro ID aus Schritt 1
        "symbol": "NVDA.ETORO",                   # Muss mit instrument_map.py übereinstimmen
        "bar_type": "NVDA.ETORO-1-MINUTE-MID-INTERNAL", # Nautilus Bar-Typ (z.B. 1-MINUTE, 1-HOUR)
        "params": {
            "fast_sma": 10,
            "slow_sma": 20                      # Deine optimierten Parameter
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

---

## Sonderfall: Momentum-LS Integration (neu)

Wenn ein neues Instrument auch in das Momentum-LS-Turnier aufgenommen werden soll, ist der Prozess noch einfacher:

```bash
# 1. Universe neu laden (enthält automatisch neue Portfolio-Assets)
python3 dev_scripts/momentum_ls_universe.py --output data/universe/momentum_ls.json

# 2. Historische Daten holen (ID und Symbol entsprechend anpassen)
python3 dev_scripts/momentum_ls_fetch_candles.py --etoro-id NEW_ID --symbol NEWSYM.ETORO --months 6

# 3. Turnier neu ausführen
python3 dev_scripts/momentum_ls_tournament.py --universe data/universe/momentum_ls.json --output logs/tournament_today.json
```


---
## Weiterführende Dokumente
- `manuals/momentum_ls.md`
- `manuals/deployment.md`

---
*Zuletzt aktualisiert: 2026-05-17 — Überprüft gegen Repository-Stand vom 2026-05-14*
