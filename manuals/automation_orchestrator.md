# Automation Orchestrator — Handbuch

> **Letzte Aktualisierung:** 2026-05-24  
> **Datei:** `automation/daily_orchestrator.py` (v1.1)  
> **Zielgruppe:** Jules (AI-Agent) und Operatoren, die den täglichen Pipeline-Prozess verstehen oder debuggen müssen.

---

## Übersicht

Der Daily Orchestrator führt täglich 5 Phasen sequenziell aus, um den eToro Nautilus Trading Bot zu betreiben:

```
Phase 1 — Universe & Mapping
Phase 2 — Datenbeschaffung (ZIP → Merge → Cleanup → API-Backfill)
Phase 3 — Matrix-Backtesting (7-Tage-Fenster, 10k USD, 693 Jobs)
Phase 4 — Tournament (Sortino/PF-Ranking)
Phase 5 — Live Deployment (Safety-Interlocks, Detached Subprocess)
```

**Ausführung (aus PROJECT_ROOT):**
```bash
# Vollständiger Tages-Run (empfohlen):
python3 automation/daily_orchestrator.py --skip-api-fetch

# Mit API-Backfill (benötigt ETORO_API_KEY + ETORO_USER_KEY in .env):
python3 automation/daily_orchestrator.py

# Dry-Run (testet Phase 1+2, überspringt Backtest + Bot-Start):
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

---

## Phase 1: Universe & Mapping

**Ziel:** Lade das Instrument-Universum und stelle sicher, dass alle eToro-IDs gemappt sind.

- **Quelle:** `data/universe/momentum_ls.json`
- **Duplikate:** Werden dedupliziert (CPRT.ETORO kommt mehrfach vor).
- **Stale-Warnung:** Wenn die Datei >24h alt ist, wird gewarnt (kein Fehler).
- **Auto-Mapping:** Unbekannte eToro-IDs werden über die API gemappt und persistent in `adapters/instrument_map.py` eingetragen.

**JSON-Event:** `PHASE1_COMPLETE`

---

## Phase 2: Datenbeschaffung

### 2a: ZIP-Import
- Scannt `data/import/` nach exakt einer `*.zip`-Datei.
- Bei mehreren ZIPs: Die neueste wird genommen.
- Öffnet ZIP in-memory (`zipfile.ZipFile` + `io.BytesIO`).
- Filtert Dateien im Pfad `quote_tick/**/*.parquet`.
- Validiert Schema: `bid_price`, `ask_price`, `bid_size`, `ask_size` müssen vorhanden sein.

### 2b: PyArrow-Native Merge (Kritisch!)

**Warum kein pandas-Roundtrip:**  
PyArrow 24+ (im Einsatz) konvertiert `binary`-Spalten beim `to_pandas()` zu `BinaryView`. Das Nautilus Rust-Backend erwartet zwingend `FixedSizeBinary(16)`. Ein pandas-Roundtrip bricht deshalb alle Daten.

**Merge-Prozess (vollständig pyarrow-nativ):**

1. **ZIP lesen:** `pq.read_table(io.BytesIO(zf.read(fname)))` → `pa.Table`
2. **Bestehende Dateien lesen:** Alle `*.parquet` im Katalog-Verzeichnis des Instruments.
3. **Bestes Metadaten-Objekt wählen:** Datei MIT `price_precision` in `schema.metadata`.
4. **Target-Schema aufbauen:** `_build_target_schema()` — immer `FixedSizeBinary(16)` für Preis/Größen-Spalten.
5. **Typ-Casting:** `_cast_to_schema()` konvertiert `binary` / `BinaryView` / `large_binary` → `FixedSizeBinary(16)`.
6. **Konkatenation:** `pa.concat_tables([existing_tables..., zip_tables...])`.
7. **Deduplizierung:** `pd.Series.duplicated(subset=['ts_event'], keep='first')` für die Boolean-Maske, dann `merged.take(keep_idx)` auf dem Arrow-Table (kein pandas-Roundtrip der binären Spalten!).
8. **Metadaten setzen:** `_build_final_meta()` stellt sicher, dass `price_precision`, `size_precision`, `instrument_id` immer vorhanden sind.
9. **Atomisch speichern:** Schreibe `data.tmp.parquet` → rename zu `data.parquet`.
10. **Cleanup:** Lösche alle alten Timestamp-Dateien (`*.parquet` außer `data.parquet`) im Instrument-Verzeichnis.

**Wichtig: Single-File-Katalog**  
Jedes Instrument muss genau EINE `data.parquet`-Datei haben. Mehrere Dateien verursachen `normalize_parquet_metadata`-Konflikte im Nautilus Backtest (der alphabetisch letzte Datei-Metadaten als Referenz nimmt).

### 2c: ZIP löschen
```python
os.remove(str(zip_file))  # unwiderruflich — kein trash, kein Backup
```
Nur bei erfolgreichem Import. Wenn der Import fehlschlägt, bleibt die ZIP erhalten.

### 2c-post: Katalog-Migration
```python
migrate_catalog_to_fixed_binary(log)  # idempotent
```
Wandelt alle `binary`-Spalten in bestehenden Katalog-Dateien zu `FixedSizeBinary(16)` um. Schnell, da nur Dateien mit falschem Typ angefasst werden.

### 2d: API-Backfill
- Fragt eToro-Candles für die letzten 7 Tage pro Instrument ab.
- Überspringt Instrumente, bei denen die jüngsten Daten <1h alt sind.
- Bei `--skip-api-fetch`: Vollständig übersprungen.
- Benötigt `ETORO_API_KEY` und `ETORO_USER_KEY` in `.env`.

**JSON-Event:** `PHASE2_COMPLETE`

---

## Phase 3+4: Backtesting & Tournament

### Zeitfenster (deterministisch)
```python
today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
seven_days_ago = today_midnight - timedelta(days=7)
```
Beispiel: Am 2026-05-24 ist das Fenster `2026-05-17T00:00:00Z → 2026-05-24T00:00:00Z`.

### Konfiguration
- **Startkapital:** 10.000 USD (10k Demo-Konto)
- **Instrumente:** 77 (alle im Katalog)
- **Strategien:** 9 (aus `backtesting/backtesting_config.json`)
- **Jobs:** 77 × 9 = 693

**Pitfall #14 Fix:**  
`trade_amount_usd` wird auf 50.000 USD skaliert im Backtest-Worker, damit `make_qty` bei 10k-Konto nicht crasht. Im Live-Bot wird der `by-amount`-Endpunkt direkt verwendet (USD-Betrag statt Stückzahlen).

### Dynamic Config
Gespeichert in `logs/backtest_dynamic_config.json`:
```json
{
  "global_settings": {
    "catalog_path": "/path/to/data/nautilus",
    "start_time": "2026-05-17T00:00:00Z",
    "end_time": "2026-05-24T00:00:00Z",
    "start_capital": 10000.0
  },
  "strategies": [...]
}
```

### Subprocess-Aufruf
```python
subprocess.run([
    sys.executable, "backtesting/run_backtest.py",
    "--momentum",
    "--catalog-path", catalog_path,
    "--config", dynamic_cfg_path,
    "--output", tournament_path,
], timeout=3600)
```

### Tournament-Ergebnis
Gespeichert in `logs/tournament_YYYY-MM-DD.json`. Enthält:
- `per_symbol_winners`: Bestes Strategie-Ergebnis pro Symbol.
- `aggregate_winner`: Strategie mit meisten Wins und bestem Ø Sortino.

**Beispiel-Output:**
```
✅ Tournament: 72 Symbole | 8 Gewinner
🏆 ComboTrendVwapStrategy — 4 Wins, Ø Sortino: 39.205
```

**JSON-Events:** `BACKTEST_START`, `TOURNAMENT_COMPLETE`

---

## Phase 5: Live Deployment

### Safety-Interlocks (alle drei müssen erfüllt sein)

| Bedingung | Quelle | Fehler wenn |
|-----------|--------|-------------|
| `environment == "real"` | `config/setups.py` → `ETORO_EXECUTION` | WARNING |
| `dry_run == False` | `config/setups.py` → `ETORO_EXECUTION` | WARNING |
| `ETORO_CONFIRM_LIVE == "1"` | `.env` Datei | WARNING (Bot startet trotzdem wenn .env fehlt) |

**WICHTIG:** Das aktuelle Design loggt WARNING für fehlende Interlocks, startet den Bot aber trotzdem. Wer Live-Trading verhindern möchte, muss `ETORO_CONFIRM_LIVE=0` setzen oder die `.env`-Datei entfernen und den Code in `_ensure_safety_interlocks()` anpassen um einen `SystemExit` zu werfen.

### Bot-Subprocess (Detached)
```python
proc = subprocess.Popen(
    [sys.executable, "dev_scripts/momentum_ls_run.py",
     "--universe", universe_path,
     "--tournament", tournament_path],
    stdout=bot_log_handle,
    stderr=subprocess.STDOUT,
    cwd=PROJECT_ROOT,
    start_new_session=True,  # Detached von der Orchestrator-Session
    env={**os.environ, "PYTHONUNBUFFERED": "1"},
)
```
Der Orchestrator beendet sich mit Exit-Code 0, der Bot läuft unabhängig weiter.

### PID-Verwaltung
- PID gespeichert: `logs/live_bot.pid`
- Bot-Log: `logs/live_bot_YYYYMMDD.log`

**JSON-Events:** `BOT_START_INITIATED`, `BOT_STARTED`

---

## Logging-System

### RotatingFileHandler
- **Log-Datei:** `logs/orchestrator_YYYYMMDD.log`
- **Max-Größe:** 1 MB pro Datei
- **Backup-Dateien:** 5 Rotations-Kopien
- **Retention:** Dateien älter als 7 Tage werden beim Start gelöscht

### Log-Format
```
TIMESTAMP | LEVEL    | LOGGER_NAME | MESSAGE
2026-05-24T20:04:11.804+00:00 | ℹ️  INFO     | orchestrator                  | [Phase 4] Tournament: 8 Gewinner.
```

### JSON-Events (LLM-parsbar)
```
[JSON_EVENT] {"event_type": "TOURNAMENT_COMPLETE", "timestamp_utc": "...", "winner_count": 8, ...}
```

### Emit-Funktionen
```python
from automation.log_manager import setup_bot_logging, emit_execution_event, emit_order_event

logger = setup_bot_logging("live_bot")
emit_execution_event(logger, "ORDER_SUBMITTED", {"symbol": "BTC.ETORO", "amount": 100.0})
emit_order_event(logger, "BTC.ETORO", "BUY", 100.0, price=50000.0, status="SUBMITTED")
```

---

## Pitfall #14: Fractional Trading (`automation/fractional_trading.py`)

### Problem
Bei einem 10k USD Konto und Equities wie FICO (~$1600) oder TSLA (~$450) ist `trade_amount_usd < Preis pro Aktie`. Nautilus' `instrument.make_qty(units)` wirft `ValueError` wenn `units < size_increment` (= 1.0 für Equities).

### Lösung: by-amount Endpunkt
```python
from automation.fractional_trading import build_by_amount_payload

payload = build_by_amount_payload(
    etoro_id="95819",
    investment_amount_usd=100.0,  # USD direkt — eToro berechnet Stückzahl intern
    is_buy=True,
    leverage=1,
    stop_loss_pct=0.05,
    current_rate=1600.0,
)
# POST https://public-api.etoro.com/api/v1/trading/execution/market-open-orders/by-amount
```

### `safe_compute_quantity()` (für Nicht-Equity-Assets)
```python
from automation.fractional_trading import safe_compute_quantity

qty = safe_compute_quantity(instrument, trade_amount_usd=100.0, current_price=50000.0)
if qty is None:
    return  # Signal lautlos überspringen
```

### Size-Increment-Cache
- Persistent: `data/state/size_increment_cache.json`
- Crypto: `1e-8`, Forex/Commodity: `1e-5`, Equity: `1.0`
- API-Abfrage via `fetch_size_increments_from_api()` (optional)

---

## Technische Hinweise für Agenten

### Niemals pandas-Roundtrip für Parquet-Merge
```python
# ❌ FALSCH: binary → BinaryView → Rust-Panic
df = table.to_pandas()
merged_df = pd.concat([df1, df2])
pa.Table.from_pandas(merged_df)

# ✅ KORREKT: pyarrow-nativ mit FixedSizeBinary(16)
tables = [_cast_to_schema(t, target_schema) for t in all_tables]
merged = pa.concat_tables(tables)
keep_mask = ~pd.Series(merged.column("ts_event").to_pylist()).duplicated(keep="first")
merged = merged.take(keep_mask[keep_mask].index.values)
```

### Preis/Größen-Spalten immer als FixedSizeBinary(16)
```python
FSB16 = pa.binary(16)  # FixedSizeBinary(16) — NOT pa.binary() (variable)
schema = pa.schema([
    pa.field("bid_price", FSB16),
    pa.field("ask_price", FSB16),
    pa.field("bid_size",  FSB16),
    pa.field("ask_size",  FSB16),
    pa.field("ts_event",  pa.uint64()),
    pa.field("ts_init",   pa.uint64()),
])
```

### Metadaten-Pflicht
Jede Katalog-Datei MUSS folgende Byte-Keys im Schema-Metadata haben:
```python
{
    b"price_precision": b"5",  # oder "2", "8" je nach Asset-Klasse
    b"size_precision":  b"8",  # Crypto=8, Forex=5, Equity=0
    b"instrument_id":   b"BTC.ETORO",
}
```
Ohne diese Metadaten: Nautilus Rust-Panic `MissingMetadata("price_precision")`.

### get_size_precision()
```python
from adapters.instrument_utils import get_size_precision
prec = get_size_precision("BTC.ETORO")   # → 8 (Crypto)
prec = get_size_precision("EURUSD.ETORO") # → 5 (Forex)
prec = get_size_precision("TSLA.ETORO")   # → 0 (Equity)
```

---

## Häufige Fehler und Fixes

| Fehler | Ursache | Fix |
|--------|---------|-----|
| `InvalidColumnType("bid_price", 0, FixedSizeBinary(16), BinaryView)` | pandas-Roundtrip oder PyArrow 24+ liest `binary` als `BinaryView` | `migrate_catalog_to_fixed_binary()` / `_build_target_schema()` mit `pa.binary(16)` |
| `MissingMetadata("price_precision")` | Parquet-Datei ohne Metadaten (z.B. neues Merge ohne Metadaten-Copy) | `_build_final_meta()` sicherstellt alle 3 Keys |
| `ValueError: make_qty ... rounded to zero` | Equity `trade_amount_usd < price` im Backtest-Worker | Pre-check + try/except in `_compute_quantity()`, by-amount im Live-Bot |
| `BrokenProcessPool` | OOM durch zu viele parallele Worker | `max_workers = max(1, min(cpu//2, 6))` + `max_tasks_per_child=1` |
| Tournament: 0 Gewinner | Alle Backtests crashen (z.B. BinaryView-Panic) | Prüfe Backtest-Log auf Rust-Panics; führe Migration aus |
| ZIP bleibt in `data/import/` | Import fehlgeschlagen | Prüfe Orchestrator-Log auf Phase-2-Fehler; manueller Start möglich |

---

## Cron-Betrieb

Für täglichen automatischen Betrieb um 01:00 UTC:

```cron
0 1 * * * /usr/local/bin/python3 /path/to/etoro_nautilus/automation/daily_orchestrator.py --skip-api-fetch >> /path/to/etoro_nautilus/logs/cron.log 2>&1
```

**Voraussetzungen:**
1. `data/import/nautilus_data_*.zip` muss täglich bereitgestellt werden (manuell oder via ETL-Pipeline).
2. `ETORO_CONFIRM_LIVE=1` in `.env` (wird automatisch gesetzt wenn `.env` existiert und der Key fehlt).
3. Ausreichend Disk-Space: ~100 MB pro Backtest-Run + Log-Dateien.

---

## Datei-Struktur nach erfolgreichem Run

```
data/
├── import/                          # Leer (ZIP wurde gelöscht)
├── nautilus/
│   └── data/
│       └── quote_tick/
│           └── BTC.ETORO/
│               └── data.parquet    # FixedSizeBinary(16), mit Metadaten
└── state/
    └── size_increment_cache.json   # Persistent cache

logs/
├── orchestrator_20260524.log       # Haupt-Log (max 1MB, 5 Backups)
├── backtest_20260524_HHMMSS.log    # Backtest-Subprocess-Output
├── errors_20260524_HHMMSS.log      # Backtest-Fehler (leer bei Erfolg)
├── tournament_2026-05-24.json      # Tournament-Ergebnisse
├── backtest_dynamic_config.json    # Dynamische Backtest-Konfiguration
├── live_bot_20260524.log           # Bot-Output (RotatingFileHandler)
└── live_bot.pid                    # PID des laufenden Bot-Subprozesses
```
