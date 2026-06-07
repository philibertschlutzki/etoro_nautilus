# End-to-End Workflow & Pipeline

Dieses Handbuch dokumentiert den vollständigen Lifecycle einer algorithmischen Trading-Strategie innerhalb der **eToro Nautilus Plattform** – von der Datenbeschaffung über den Backtest bis zum Live-Trading mit echtem Kapital.

Das System folgt einer strikten **5-Phasen-Pipeline**, die vom Master-Orchestrator `automation/daily_orchestrator.py` vollautomatisch ausgeführt wird:

| Phase | Was passiert | Skript/Modul |
|-------|-------------|-------------|
| 1 | Universe & Mapping | `automation/universe_fetcher.py` |
| 2 | Multi-ZIP-Import + Merge + API-Backfill | `automation/api_backfiller.py` |
| 3 | Matrix-Backtest | `automation/backtest_runner.py` |
| 4 | Tournament (beste Strategie pro Symbol) | `automation/backtest_runner.py` |
| 5 | Live Deployment | `automation/momentum_ls_run.py` |

> **Eigenständiges Paket:** Das `automation/`-Verzeichnis ist vollständig in sich geschlossen. Es gibt keine Imports aus `adapters/`, `config/` (Root) oder `strategies/` (Root) — diese sind Legacy und archiviert. Alle Konfiguration liegt unter `automation/config/`.

---

## Architektur-Überblick

```text
automation/universe_fetcher.py
        │
        ▼  data/universe/momentum_ls.json
automation/catalog_service.py (24/7) ─► data/import/*.zip
        │
        ▼
automation/daily_orchestrator.py  ← DER MASTER-ORCHESTRATOR
  Phase 1: Universe laden + Mapping aktualisieren
  Phase 2: ZIPs mergen + fehlende Daten via api_backfiller.py nachholen
  Phase 3: Matrix-Backtest via backtest_runner.py starten
  Phase 4: Tournament: beste Strategie pro Symbol ermitteln
  Phase 5: Live-Bot via momentum_ls_run.py starten
        │
        ▼
  logs/tournament_YYYY-MM-DD.json  ←  Turnier-Ergebnis
  momentum_ls_run.py (Live-Bot)    ←  nutzt Tournament-Ergebnis
```

---

## Phase 1: Universe & Mapping

**Was passiert hier?**
Das System lädt die aktuellen Bestandteile des eToro Smart Portfolios (`MOMENTUM_LS_USERNAME` in `.env`) und ordnet jedem eToro-internen Instrument eine Nautilus-kompatible Symbol-Bezeichnung zu (z. B. `TSLA.ETORO`). Das Ergebnis wird in `data/universe/momentum_ls.json` gespeichert.

**Warum ist das wichtig?**
Ohne ein aktuelles Universe weiß der Bot nicht, welche Instrumente er handeln soll. Ist die Datei älter als 24 Stunden, warnt das System (`Universe data is stale`).

**Manuell ausführen:**
```bash
python3 automation/universe_fetcher.py
# Ergebnis: data/universe/momentum_ls.json (aktualisiert)
```

**Konfiguration:** Die Mapping-Tabelle `eToro-ID → Nautilus-Symbol` liegt in `automation/config/instrument_map.json`. Neue Instrumente werden hier eingetragen (Details: `manuals/new_tickers.md`).

---

## Phase 2: Daten beschaffen (Multi-ZIP-Import + API-Backfill)

**Was passiert hier?**
Der `catalog_service.py` läuft 24/7 und sammelt Tick-Daten als ZIP-Dateien im Verzeichnis `data/import/`. In Phase 2 werden diese ZIPs importiert und mit den bestehenden Parquet-Daten zusammengeführt (Merge). Fehlen Daten für bestimmte Zeiträume, füllt der `api_backfiller.py` diese Lücken automatisch auf.

**Parquet-Format:** Die Daten werden als `FixedSizeBinary(16)` QuoteTicks im Format `data/nautilus/data/quote_tick/{symbol}/data.parquet` gespeichert.

**Wichtige Schalter für den Orchestrator:**

```bash
# Standard-Run (ZIPs aus data/import/ verwenden):
python3 automation/daily_orchestrator.py --skip-api-fetch

# Mit API-Backfill (wenn data/import/ leer ist):
python3 automation/daily_orchestrator.py
```

**Manueller API-Backfill:**
```bash
# Letzte 7 Tage via API nachholen:
python3 automation/api_backfiller.py --days 7

# Vollständiger historischer Backfill (12 Monate):
python3 automation/historical_fetcher.py --months 12
```

---

## Phase 3+4: Matrix-Backtest & Tournament

**Was passiert hier?**
Das System testet **alle** aktiven Strategien gegen **alle** Symbole im Universe. Dies ergibt eine Matrix aus `N Symbolen × M Strategien` Backtest-Jobs, die parallel ausgeführt werden (bis zu 6 CPU-Kerne).

**Konfiguration:** `automation/config/backtest.json` (Zeitfenster, Spread-Modellierung).

Das **Tournament** bestimmt danach für jedes Symbol die beste Strategie anhand dieser Regeln:

### Eligibilitätsprüfung (muss ALLES erfüllt sein):
- `min_trades ≥ 20` — statistisch ausreichend viele Trades
- `total_return > 0%` — profitabel im Test-Zeitraum

### Plus mindestens EINE dieser Bedingungen:
- `min_sortino ≥ 0.3` — gutes Risiko-Rendite-Verhältnis
- `min_profit_factor ≥ 1.1` — Gewinne überwiegen Verluste

### Score-Berechnung (Gewinner hat höchsten Score):
```
Score = sortino × 0.4 + profit_factor × 0.3 + win_rate × 0.2 − max_drawdown × 0.1
```

**Tournament-Konfiguration:** `automation/config/tournament.json`

**Ergebnis:** `logs/tournament_YYYY-MM-DD.json` mit den Gewinner-Strategien pro Symbol.

**Manuell ausführen (Dry-Run, kein Bot-Start):**
```bash
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

---

## Phase 5: Live Deployment

**Was passiert hier?**
Der Orchestrator startet `automation/momentum_ls_run.py` als getrennten Subprocess. Dieser liest das Tournament-Ergebnis, konfiguriert eine Strategie-Instanz pro Gewinner-Symbol und verbindet sich via WebSocket mit der eToro-API.

### Safety Interlock (Echtgeld-Schutzmechanismen)

Um versehentliches Echtgeld-Trading zu verhindern, gibt es eine harte Sicherheitssperre. **Alle drei** der folgenden Bedingungen müssen erfüllt sein — sonst bricht der Bot mit `sys.exit(1)` ab:

1. `environment == 'real'` (in den Bot-Einstellungen)
2. `dry_run == False`
3. Umgebungsvariable `ETORO_CONFIRM_LIVE=1` in der `.env`-Datei gesetzt

> **Merke:** Fehlt `ETORO_CONFIRM_LIVE=1` in der `.env`, startet der Bot nur im Dry-Run-Modus — er empfängt Daten und berechnet Signale, schickt aber **keine** echten Orders.

### Live-Bot manuell starten

```bash
# Voraussetzung: ETORO_CONFIRM_LIVE=1 in .env gesetzt
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json
```

---

## Precision-Tabelle (aktuell, v2.0)

Die Preisgenauigkeit (Dezimalstellen bei Kauf/Verkauf) wird automatisch gesetzt:

| Instrument-Kategorie | price_precision | size_precision |
|---------------------|----------------|----------------|
| SHIB / PEPE | 8 | 8 |
| Krypto (BTC, ETH, …) | 2 | 8 |
| Forex / Rohstoffe (NATGAS, PALL, …) | 5 | 5 |
| **Aktien (Default)** | **2** | **2** |

> **Hinweis (Pitfall #14 — GELÖST):** In früheren Versionen war `size_precision` für Aktien auf `0` gesetzt, was zu `ValueError`-Crashes führte, wenn der Trade-Betrag kleiner als der Preis einer vollen Aktie war. Seit v2.0 ist `size_precision=2` für Aktien der Standard — Fractional Shares werden korrekt unterstützt.

---

## Komplette Pipeline auf einen Blick

```bash
# === TÄGLICHER STANDARD-LAUF (vollautomatisch) ===
python3 automation/daily_orchestrator.py --skip-api-fetch

# === EINZELNE PHASEN MANUELL ===

# Phase 1: Universe aktualisieren
python3 automation/universe_fetcher.py

# Phase 2a: ZIP-Daten aus data/import/ mergen (passiert automatisch via Orchestrator)
# Phase 2b: API-Backfill wenn ZIPs fehlen
python3 automation/api_backfiller.py --days 7

# Phase 2c: Historische Daten (Erstbefüllung)
python3 automation/historical_fetcher.py --months 12

# Phase 3+4: Backtest + Tournament (Dry-Run, kein Bot-Start)
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch

# Phase 5: Live-Bot manuell starten
python3 automation/momentum_ls_run.py \
  --universe data/universe/momentum_ls.json \
  --tournament logs/tournament_$(date +%Y-%m-%d).json

# === CATALOG SERVICE (24/7, separat via systemd) ===
python3 automation/catalog_service.py
```

---

## Warnung: Overfitting & Slippage

- **Overfitting:** Wenn Parameter extrem lange auf historischen Daten optimiert werden, passen sie zwar gut zur Vergangenheit, scheitern aber live. Verwende "Out-of-Sample" Tests: Optimiere auf Januar–Oktober, teste auf November–Dezember.
- **Slippage:** In der Realität schwanken Preise zwischen Ordererteilung und Ausführung. Backtests gehen von perfekter Ausführung aus — rechne mit schlechteren Live-Ergebnissen.

---

## Weiterführende Dokumente
- [`manuals/deployment.md`](./deployment.md) — Einrichtung der VM, systemd-Service und Cron
- [`manuals/backtesting_manual.md`](./backtesting_manual.md) — Backtest-Konfiguration und Auswertung
- [`manuals/momentum_ls.md`](./momentum_ls.md) — Momentum-LS Pipeline im Detail
- [`manuals/new_tickers.md`](./new_tickers.md) — Neue Instrumente hinzufügen
- [`manuals/run_bot_manual.md`](./run_bot_manual.md) — Bot-Betrieb, Log-Diagnose und Notfallmaßnahmen

---
*Zuletzt aktualisiert: 2026-06-07 — Überprüft gegen automation/AGENTS.md*
