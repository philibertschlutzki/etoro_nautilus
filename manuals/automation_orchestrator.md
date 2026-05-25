# Automation Orchestrator — Handbuch

> **Letzte Aktualisierung:** 2026-05-25  
> **Version:** v2.0 (Shift-Left Data Quality, Standalone `automation/`)  
> **Dateien:** `automation/daily_orchestrator.py`, `automation/catalog_service.py`, `automation/api_backfiller.py`  
> **Zielgruppe:** Jules (AI-Agent) und Operatoren, die den täglichen Pipeline-Prozess verstehen oder debuggen müssen.

---

## Übersicht: Standalone-Architektur

Der `automation/`-Ordner ist ein **vollständig eigenständiges Produkt** — kein einziger Import aus `adapters/`. Alle drei Hauptskripte arbeiten unabhängig vom Rest des Repositories.

### Neuer Datenfluss (Shift-Left Data Quality)

```
catalog_service.py (24/7, via systemd)
    │  jede Stunde (3600s)
    ▼
data/import/[Timestamp].zip           ← stündliches ZIP mit Parquet-Dateien
    │  z.B. data/import/20260525_140000.zip
    │
api_backfiller.py (bei täglichem Run oder manuell)
    │  füllt Datenlücken der letzten 7 Tage
    ▼
data/nautilus/data/quote_tick/{symbol}/data.parquet [DIREKT als FSB(16)]
    │
    ▼
daily_orchestrator.py (täglich, z.B. via cron)
    │  Phase 2: alle *.zip einlesen → pa.concat_tables + dedup → speichern
    │  ZIPs nach Merge löschen
    ▼
data/nautilus/data/quote_tick/{symbol}/data.parquet [merged, dedupliziert]
    │
    ▼ Phase 3+4
backtesting/run_backtest.py
    ▼ Phase 5
dev_scripts/momentum_ls_run.py (Live-Bot)
```

**Kerninnovation gegenüber v1.x:** Die Datenqualität wird an der Quelle (Schritt 1) sichergestellt — nicht mehr korrigierend im Orchestrator. `migrate_catalog_to_fixed_binary()` entfällt vollständig.

---

## Skript-Übersicht

### `automation/catalog_service.py` — Continuous Catalog Service

Ersetzt `run_catalog.py`. Läuft 24/7 als systemd-Dienst.

**Aufgaben:**
1. Verbindet sich mit eToro WebSocket (`wss://ws.etoro.com/ws`)
2. Holt `price_precision` und `size_precision` **dynamisch via eToro API** beim Start  
   (`GET /api/v1/market-data/instruments?instrumentIds=...`)
3. Sammelt QuoteTicks als `RawTick(bid, ask, ts_event)` im Puffer
4. Exakt alle **60 Minuten**: asynchroner Flush-Task:
   - Puffer atomar tauschen (Datensammlung läuft weiter)
   - Deduplizieren nach `ts_event`
   - Pro Instrument: Parquet mit `FixedSizeBinary(16)` + Arrow-Metadaten
   - Alle Parquet-Dateien in `[Timestamp].zip` bündeln → `data/import/`

**ZIP-Struktur:**
```
20260525_140000.zip
└── quote_tick/
    ├── BTC.ETORO/
    │   └── 20260525_140000.parquet    # FSB(16), Metadaten injiziert
    ├── TSLA.ETORO/
    │   └── 20260525_140000.parquet
    └── ...
```

**Verwendung:**
```bash
# Standalone starten:
python3 automation/catalog_service.py

# Mit expliziten Instrument-IDs:
python3 automation/catalog_service.py --instrument-ids 1111 1137 6434

# Mit anderem Flush-Intervall (z.B. alle 30 Minuten):
python3 automation/catalog_service.py --flush-interval 1800
```

**systemd-Unit (Empfehlung):**
```ini
[Unit]
Description=eToro Nautilus Catalog Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 /path/to/etoro_nautilus/automation/catalog_service.py
WorkingDirectory=/path/to/etoro_nautilus
Restart=always
RestartSec=5
EnvironmentFile=/path/to/etoro_nautilus/.env

[Install]
WantedBy=multi-user.target
```

---

### `automation/api_backfiller.py` — Standalone API-Backfiller

Ersetzt das alte Inline-Gap-Fetch-Skript (ehemals in `daily_orchestrator.py`).

**Aufgaben:**
1. Lädt `data/universe/momentum_ls.json` für Instrument-IDs und Symbole
2. Holt `price_precision` und `size_precision` **dynamisch via eToro API**
3. Fragt Candle-History der letzten N Tage pro Instrument ab
4. Konvertiert Candles direkt zu `FixedSizeBinary(16)` (KEIN pandas-Roundtrip)
5. Injiziert Arrow-Metadaten und speichert atomar als `data.parquet`

**Dynamische Precision-Abfrage:**
```python
GET https://public-api.etoro.com/api/v1/market-data/instruments?instrumentIds=1111,1137,...
# Felder gesucht: decimalPlaces, pricePrecision, priceDecimals, digits, precision
# Fallback: Symbol-basierte Heuristik (BTC→price=2,size=8; Equity→price=2,size=0)
```

**FSB(16)-Enkodierung:**
```python
import struct
# Nautilus-Format: int64 LE + 8 Null-Bytes = 16 Bytes
def _encode_fsb16(value: float, precision: int) -> bytes:
    raw = round(value * (10 ** precision))
    return struct.pack("<q", raw) + b"\x00" * 8
```

**Verwendung:**
```bash
# Standalone (letzte 7 Tage):
python3 automation/api_backfiller.py

# Nur bestimmte Symbole:
python3 automation/api_backfiller.py --symbols BTC.ETORO TSLA.ETORO NVDA.ETORO

# Andere Zeitspanne:
python3 automation/api_backfiller.py --days 14

# Dry-Run (kein Schreiben):
python3 automation/api_backfiller.py --dry-run
```

**Als Modul (vom Orchestrator):**
```python
from automation.api_backfiller import run_backfill, _load_etoro_id_map

etoro_id_map = _load_etoro_id_map(UNIVERSE_PATH)  # {etoro_id: symbol}
filled = asyncio.run(run_backfill(
    api_key=api_key,
    user_key=user_key,
    etoro_id_to_symbol=etoro_id_map,
    days=7,
))
```

---

### `automation/daily_orchestrator.py` v2.0 — Master-Orchestrator

Täglicher End-to-End-Orchestrator. Vollständig standalone (kein adapters/-Import).

**Ausführung (aus PROJECT_ROOT):**
```bash
# Vollständiger Tages-Run (catalog_service.py hat ZIPs befüllt):
python3 automation/daily_orchestrator.py --skip-api-fetch

# Mit API-Backfill (benötigt ETORO_API_KEY + ETORO_USER_KEY in .env):
python3 automation/daily_orchestrator.py

# Dry-Run (testet Phase 1+2, überspringt Backtest + Bot-Start):
python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch
```

---

## Phase 1: Universe & Mapping

**Ziel:** Lade das Instrument-Universum — standalone, kein adapters/-Import.

- **Quelle:** `data/universe/momentum_ls.json` (einzige Quelle, KEIN Fallback auf `adapters/instrument_map.py`)
- **Stale-Warnung:** Wenn die Datei >24h alt ist, wird gewarnt (kein Fehler).
- Symbole ohne eToro-ID werden gewarnt und übersprungen.

**JSON-Event:** `PHASE1_COMPLETE`

---

## Phase 2: Datenbeschaffung (v2.0)

### 2a: Multi-ZIP-Import

**NEU gegenüber v1.x:** Verarbeitet **ALLE** `*.zip`-Dateien in `data/import/` (bei 24/7-Betrieb des catalog_service.py ≈ 24 ZIPs pro Tag).

- Sortiert nach Änderungszeit (älteste zuerst).
- Öffnet jedes ZIP in-memory.
- Filtert Dateien nach Pfadmuster: `quote_tick/**/*.parquet`
- Extrahiert Symbol aus Pfad: Komponente direkt nach `quote_tick/`

```python
# Symbol-Extraktion aus ZIP-Pfad:
parts = fname.split("/")
qt_idx = parts.index("quote_tick")
symbol = parts[qt_idx + 1]  # z.B. "BTC.ETORO"
```

### 2b: Einfacher Merge (kein Cast mehr!)

**Shift-Left bedeutet:** Da `catalog_service.py` und `api_backfiller.py` bereits 100% Nautilus-kompatible Parquet-Dateien liefern, entfällt jegliches Typ-Casting.

**Merge-Prozess (vereinfacht gegenüber v1.x):**

1. Bestehende `data.parquet` lesen (falls vorhanden)
2. Alle ZIP-Tabellen hinzufügen
3. `pa.concat_tables(all_tables)` — kein `_cast_to_schema` nötig
4. Deduplizieren nach `ts_event`:
   ```python
   seen_ts: set[int] = set()
   keep_indices = [i for i, ts in enumerate(ts_list) if ts not in seen_ts and not seen_ts.add(ts)]
   keep_indices.sort(key=lambda i: ts_list[i])
   merged = merged.take(pa.array(keep_indices, type=pa.int64()))
   ```
5. Metadaten sicherstellen: `b"price_precision"`, `b"size_precision"`, `b"instrument_id"`
6. Atomar speichern: `.tmp.parquet` → rename zu `data.parquet`

**Was ENTFÄLLT gegenüber v1.x:**
- ❌ `_build_target_schema()` — nicht mehr nötig
- ❌ `_cast_to_schema()` — nicht mehr nötig
- ❌ `migrate_catalog_to_fixed_binary()` — nicht mehr nötig
- ❌ pandas-Fallback bei concat-Fehler — nicht mehr nötig

### 2b-post: ZIP-Löschung

```python
# Alle verarbeiteten ZIPs nach erfolgreichem Merge löschen:
for zf in zip_files:
    os.remove(str(zf))  # unwiderruflich — kein Trash, kein Backup
```

### 2c: API-Backfill

- Via `automation.api_backfiller.run_backfill()` (Modul-Import, kein Subprocess)
- Überspringt Instrumente, bei denen die jüngsten Daten < 1h alt sind
- Bei `--skip-api-fetch`: vollständig übersprungen

**JSON-Event:** `PHASE2_COMPLETE`

---

## Phase 3+4: Backtesting & Tournament (unverändert)

Unverändert gegenüber v1.x. Siehe Details in Abschnitt weiter unten.

### Zeitfenster (deterministisch)
```python
today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
seven_days_ago = today_midnight - timedelta(days=7)
```

### Konfiguration
- **Startkapital:** 10.000 USD
- **Timeout:** 3600s

**JSON-Events:** `BACKTEST_START`, `TOURNAMENT_COMPLETE`

---

## Phase 5: Live Deployment (unverändert)

### Safety-Interlocks

| Bedingung | Quelle | Fehler wenn |
|-----------|--------|-------------|
| `environment == "real"` | `config/setups.py` → `ETORO_EXECUTION` | WARNING |
| `dry_run == False` | `config/setups.py` → `ETORO_EXECUTION` | WARNING |
| `ETORO_CONFIRM_LIVE == "1"` | `.env` Datei | WARNING |

> **Hinweis:** `config/setups.py` ist kein `adapters/`-Import und bleibt in Phase 5 erlaubt. Die Standalone-Regel gilt nur für die Daten-Infrastruktur.

**JSON-Events:** `BOT_START_INITIATED`, `BOT_STARTED`

---

## Logging-System (unverändert)

### RotatingFileHandler
- **Orchestrator:** `logs/orchestrator_YYYYMMDD.log` (1 MB, 5 Backups, 7 Tage)
- **Catalog-Service:** `logs/catalog_service_YYYYMMDD.log` (1 MB, 5 Backups)

### JSON-Events
```
[JSON_EVENT] {"event_type": "PHASE2_COMPLETE", "timestamp_utc": "...", "merged_instruments": 48, "zips_deleted": 24, ...}
```

---

## Technische Hinweise für Agenten

### STANDALONE-REGEL (PFLICHT)

```
KEINE Datei in automation/ darf aus adapters/ importieren!

❌ FALSCH:
from adapters.instrument_map import ETORO_INSTRUMENTS
from adapters.instrument_utils import get_size_precision

✅ KORREKT:
# Precisions via API:
from automation.api_backfiller import fetch_precisions_from_api
# Oder inline Heuristik:
_CRYPTO = frozenset({"BTC", "ETH", ...})
def _get_size_precision(sym): return 8 if sym in _CRYPTO else ...
```

### Nautilus FixedSizeBinary(16) Format

```python
import struct

# Nautilus-Format für Price/Quantity:
#   raw_int64 = round(value * 10^precision)
#   16 Bytes = 8 Byte LE int64 + 8 Null-Bytes

def _encode_fsb16(value: float, precision: int) -> bytes:
    raw = round(value * (10 ** precision))
    raw = max(-(2**63), min(2**63 - 1, raw))
    return struct.pack("<q", raw) + b"\x00" * 8

# Beispiele:
_encode_fsb16(1.23456, 5)  # → b'\x40\xe2\x01\x00\x00\x00\x00\x00\x00...'
_encode_fsb16(1.0, 0)      # → b'\x01\x00\x00\x00\x00\x00\x00\x00\x00...'

# PyArrow-Schema:
_FSB16 = pa.binary(16)  # = FixedSizeBinary(16) — NICHT pa.binary() (variable)!
schema = pa.schema([
    pa.field("bid_price", _FSB16),
    pa.field("ask_price", _FSB16),
    pa.field("bid_size",  _FSB16),
    pa.field("ask_size",  _FSB16),
    pa.field("ts_event",  pa.uint64()),
    pa.field("ts_init",   pa.uint64()),
])
```

### Arrow-Metadaten (Pflicht für Nautilus)

```python
# Byte-Keys — PFLICHT für Nautilus Rust-Backend:
meta = {
    b"price_precision": b"5",        # Anzahl Nachkommastellen für Preise
    b"size_precision":  b"8",        # Crypto=8, Forex=5, Equity=0
    b"instrument_id":   b"BTC.ETORO",  # Nautilus Symbol-String
}
table = table.replace_schema_metadata(meta)
```

Ohne diese Metadaten: Nautilus Rust-Panic `MissingMetadata("price_precision")`.

### Niemals pandas-Roundtrip für Preis/Größen-Daten

```python
# ❌ FALSCH: binary → BinaryView → Rust-Panic
df = table.to_pandas()
merged_df = pd.concat([df1, df2])
pa.Table.from_pandas(merged_df)  # BinaryView statt FSB(16)!

# ✅ KORREKT: pyarrow-nativ
merged = pa.concat_tables(tables)
# Dedup auf Arrow-Ebene:
ts_list = merged.column("ts_event").to_pylist()
seen: set[int] = set()
keep = [i for i, ts in enumerate(ts_list) if ts not in seen and not seen.add(ts)]
keep.sort(key=lambda i: ts_list[i])
merged = merged.take(pa.array(keep, type=pa.int64()))
```

### Dynamische Precisions via eToro API

```python
# Instruments-Endpoint (Batch à 50 IDs):
GET https://public-api.etoro.com/api/v1/market-data/instruments?instrumentIds=1111,1137,...
# Antwort: Liste mit Feldern wie decimalPlaces, pricePrecision, ...

# Fallback-Heuristik (wenn API keine Precision-Felder liefert):
_CRYPTO = frozenset({"BTC", "ETH", "ADA", "DOGE", "SOL", "XRP", "AVAX", ...})
_FRAC   = frozenset({"NATGAS", "USDTRY", "USDZAR", "PALL"})
def _fallback_precisions(symbol: str) -> tuple[int, int]:
    sym = symbol.split(".")[0]
    if "SHIB" in sym or "PEPE" in sym: return 8, 8
    if sym in _CRYPTO: return 2, 8
    if sym in _FRAC:   return 5, 5
    return 2, 0  # Equity-Default
```

---

## Häufige Fehler und Fixes (v2.0)

| Fehler | Ursache | Fix |
|--------|---------|-----|
| `InvalidColumnType("bid_price", 0, FixedSizeBinary(16), BinaryView)` | pandas-Roundtrip oder PyArrow 24+ liest `binary` als `BinaryView` | Daten von `catalog_service.py`/`api_backfiller.py` regenerieren (liefern FSB(16)) |
| `MissingMetadata("price_precision")` | Parquet ohne Arrow-Metadaten | `_ensure_metadata()` in Orchestrator korrigiert das; neue Quellen injizieren immer |
| ZIP bleibt in `data/import/` | Merge fehlgeschlagen | Orchestrator-Log auf Phase-2-Fehler prüfen; manueller Restart möglich |
| Kein ZIP in `data/import/` | `catalog_service.py` nicht gestartet oder < 1h läuft | `catalog_service.py` starten; `api_backfiller.py` für sofortigen Backfill |
| `BrokenProcessPool` | OOM durch zu viele Backtest-Worker | `max_workers = max(1, min(cpu//2, 6))` (in run_backtest.py) |
| Precisions alle gleich (Fallback) | eToro API liefert keine Precision-Felder | Normal; Fallback-Heuristik greift — Werte sind korrekt für die meisten Instrumente |
| Symbol `None` in Universe | eToro-ID ohne Mapping in `momentum_ls.json` | Universe-Datei aktualisieren (`dev_scripts/momentum_ls_universe.py`) |

---

## Cron-Betrieb

**Empfohlenes Setup:** catalog_service.py läuft 24/7 als systemd-Dienst, daily_orchestrator.py läuft täglich via cron.

```cron
# Täglicher Run um 01:00 UTC (catalog_service.py hat ≈ 24 ZIPs befüllt):
0 1 * * * /usr/local/bin/python3 /path/to/etoro_nautilus/automation/daily_orchestrator.py --skip-api-fetch >> /path/to/etoro_nautilus/logs/cron.log 2>&1

# Oder mit API-Backfill (falls catalog_service.py ausgefallen):
0 1 * * * /usr/local/bin/python3 /path/to/etoro_nautilus/automation/daily_orchestrator.py >> /path/to/etoro_nautilus/logs/cron.log 2>&1
```

**Voraussetzungen:**
1. `catalog_service.py` läuft 24/7 als systemd-Dienst (schreibt stündliche ZIPs).
2. `data/import/` muss von catalog_service.py beschreibbar sein.
3. `ETORO_CONFIRM_LIVE=1` in `.env`.

---

## Datei-Struktur nach erfolgreichem Run

```
data/
├── import/                          # Leer (alle ZIPs wurden gelöscht)
├── nautilus/
│   └── data/
│       └── quote_tick/
│           ├── BTC.ETORO/
│           │   └── data.parquet    # FSB(16), Metadaten: price_prec/size_prec/instrument_id
│           ├── TSLA.ETORO/
│           │   └── data.parquet
│           └── ...
└── state/
    └── size_increment_cache.json   # Persistent cache (fractional_trading.py)

logs/
├── orchestrator_20260525.log           # Haupt-Log (max 1MB, 5 Backups)
├── catalog_service_20260525.log        # Service-Log
├── backtest_20260525_HHMMSS.log        # Backtest-Subprocess-Output
├── tournament_2026-05-25.json          # Tournament-Ergebnisse
├── backtest_dynamic_config.json        # Dynamische Backtest-Konfiguration
├── live_bot_20260525.log               # Bot-Output (RotatingFileHandler)
└── live_bot.pid                        # PID des laufenden Bot-Subprozesses
```
