# Neue Instrumente hinzufügen

Diese Anleitung erklärt Schritt für Schritt, wie du neue Aktien, Kryptowährungen oder andere Instrumente in das System einbindest.

---

## Überblick: Wie das System Instrumente verarbeitet

Bevor wir loslegen, hier ein Überblick der aktuellen Architektur:

```text
automation/universe_fetcher.py          ← Lädt Portfolio-Symbole + Auto-Discovery
        │
        ▼
automation/config/instrument_map.json   ← Zentrale Tabelle: eToro-ID ↔ Nautilus-Symbol
        │
        ▼
automation/backtest_runner.py           ← Matrix-Backtest aller Strategien
        │
        ▼
automation/config/strategies.json       ← Welche Strategien aktiv sind
        │
        ▼
automation/momentum_ls_run.py           ← Startet den Live-Bot mit Tournament-Ergebnissen
```

> **Hinweis:** Die frühere `adapters/instrument_map.py` und `config/setups.py` sind Legacy. Das gesamte `automation/`-Paket ist eigenständig — alle Konfiguration liegt ausschließlich unter `automation/config/`.

---

## Schritt 1: eToro Instrument-ID herausfinden

Jedes Instrument auf eToro hat eine interne numerische ID. Das System benötigt diese ID, um das richtige WebSocket-Feed zu abonnieren.

### Option A: Automatisch via Universe-Fetcher (empfohlen)

Wenn das neue Instrument bereits Teil des eToro Smart Portfolios ist (das du über `MOMENTUM_LS_USERNAME` verfolgst), wird es automatisch erkannt:

```bash
python3 automation/universe_fetcher.py
```

Das Skript fragt die eToro-API ab, findet neue IDs und fügt sie automatisch zur `automation/config/instrument_map.json` hinzu. Danach ist das Instrument sofort im System bekannt.

### Option B: Manuell herausfinden

Wenn das Instrument **nicht** im verfolgten Smart Portfolio ist:

1. Öffne `dev_scripts/get_instruments_id.py`
2. Suche am Ende der Datei den Testaufruf und ändere den Ticker:
   ```python
   if __name__ == "__main__":
       get_etoro_instrument_id("NVDA")  # ← Ticker anpassen
   ```
3. Führe das Skript aus:
   ```bash
   python3 dev_scripts/get_instruments_id.py
   ```
4. Notiere dir die ausgegebene ID (z. B. `2254`).

---

## Schritt 2: Instrument in der Map registrieren

Öffne `automation/config/instrument_map.json`. Füge die neue ID und das dazugehörige Symbol hinzu.

**Beispiel für Nvidia (ID: 2254):**

```json
{
  "instruments": {
    "1111": "TSLA.ETORO",
    "1":    "EURUSD.ETORO",
    "2254": "NVDA.ETORO"
  }
}
```

> **Pflichtformat:** Der Wert (das Nautilus-Symbol) muss immer mit `.ETORO` enden (z. B. `NVDA.ETORO`). Dies ist für die interne Routing-Logik der Nautilus-Engine zwingend erforderlich.

> **Kryptowährungen:** Für Krypto-Symbole muss das Symbol zusätzlich in der internen `_CRYPTO_SYMBOLS`-Liste des eToro-Adapters eingetragen sein (Details in `automation/AGENTS.md`).

---

## Schritt 2.5: Price Precision verstehen

Die Preisgenauigkeit (Dezimalstellen) wird automatisch gesetzt — du musst in der Regel nichts tun:

| Instrument-Kategorie | price_precision | size_precision |
|---------------------|----------------|----------------|
| SHIB / PEPE | 8 | 8 |
| Krypto (BTC, ETH, …) | 2 | 8 |
| Forex / Rohstoffe (NATGAS, PALL, …) | 5 | 5 |
| **Aktien (Default)** | **2** | **2** |

> **Hinweis (Pitfall #14 — GELÖST):** In früheren Versionen war `size_precision` für Aktien auf `0` gesetzt. Seit v2.0 ist `size_precision=2` der Standard für Aktien, was Fractional Shares korrekt unterstützt.

Wenn ein Symbol nicht ins Standardraster passt, kann die Precision manuell in `automation/config/instrument_map.json` überschrieben werden.

---

## Schritt 3: Historische Daten laden

Für das Tournament werden historische Tick-Daten benötigt. Nach dem Eintragen in die Map:

```bash
# Letzte 7 Tage via API laden:
python3 automation/api_backfiller.py --days 7

# Vollständiger historischer Backfill (12 Monate, für neue Symbole empfohlen):
python3 automation/historical_fetcher.py --months 12
```

Die Daten werden gespeichert unter:
```
data/nautilus/data/quote_tick/{SYMBOL}/data.parquet
```

---

## Schritt 4: Strategie konfigurieren

Die aktiven Strategien und ihre Parameter werden in `automation/config/strategies.json` verwaltet:

```json
{
  "strategies": [
    {
      "name": "MeanReversionStrategy",
      "active": true,
      "params": {
        "keltner_period": 20,
        "keltner_multiplier": 2.0
      }
    }
  ]
}
```

Standard-Parameter für alle Strategien: `automation/config/strategy_defaults.json`

> **Hinweis:** Du musst keine neuen Strategie-Einträge für neue Instrumente anlegen. Das Tournament testet **alle** aktiven Strategien automatisch gegen **alle** Symbole im Universe.

---

## Schritt 5: Überprüfung

Führe einen Dry-Run aus, um sicherzustellen, dass das neue Instrument korrekt erkannt wird:

```bash
# Universe neu laden (enthält das neue Instrument):
python3 automation/universe_fetcher.py

# Dry-Run: Backtest + Tournament (kein Bot-Start, kein Risiko):
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

Prüfe im Orchestrator-Log, ob das neue Symbol im Tournament erscheint:
```bash
tail -f logs/orchestrator_$(date +%Y%m%d).log | grep "NVDA"
```

---

## Sonderfall: Momentum-LS Integration (vollautomatisch)

Wenn das neue Instrument im verfolgten eToro Smart Portfolio ist, läuft der gesamte Prozess **automatisch** im nächsten täglichen Cron-Run ab:

1. `universe_fetcher.py` erkennt das neue Asset
2. `instrument_map.json` wird aktualisiert
3. `api_backfiller.py` lädt fehlende Daten
4. Das Tournament nimmt das Symbol automatisch auf
5. Der Live-Bot handelt das neue Instrument (wenn eine Strategie die Kriterien erfüllt)

Kein manueller Eingriff nötig.

---

## Fehlerbehebung

| Problem | Lösung |
|---------|--------|
| `KeyError: 'NVDA.ETORO' not in instrument_map` | eToro-ID in `automation/config/instrument_map.json` eintragen |
| `No parquet data for NVDA.ETORO` | `python3 automation/historical_fetcher.py --months 12` ausführen |
| `No tournament winner for NVDA.ETORO` | Keine Strategie hat die Eligibilitätskriterien erfüllt — mehr Daten laden oder Schwellenwerte in `tournament.json` prüfen |
| `ID nicht gefunden` | Prüfe, ob die eToro-ID als **String** (nicht Integer) in der JSON-Datei steht (z. B. `"2254"`, nicht `2254`) |

---

## Weiterführende Dokumente
- [`manuals/momentum_ls.md`](./momentum_ls.md) — Momentum-LS Pipeline
- [`manuals/deployment.md`](./deployment.md) — VM-Setup und Systemkonfiguration
- [`automation/AGENTS.md`](../automation/AGENTS.md) — Autoritative Architektur-Dokumentation

---
*Zuletzt aktualisiert: 2026-06-07 — Überprüft gegen automation/AGENTS.md*
