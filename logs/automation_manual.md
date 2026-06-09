Hier ist das konsolidierte, technisch detaillierte und vollständig überarbeitete Handbuch für die Infrastruktur im `automation/`-Ordner. Es vereint die Konzepte des Datenflusses (Shift-Left Data Quality), des Continuous Cataloging, des API-Backfillings, der Tournament-Logik (Matrix-Backtest) sowie des Live-Deployments mit dem integrierten Risikoschutz.

---

# Automation & Portfolio Integration — Technisches Handbuch v2.0

> **Letzte Aktualisierung:** 2026-06-09
> **Version:** v2.0 (Shift-Left Data Quality / Standalone-Architektur)
> **Zielgruppe:** AI-Agenten (Jules) und Systemoperatoren für Betrieb, Wartung und Pipeline-Debugging.

---

## 1. Standalone-Architektur & Datenfluss (Shift-Left Data Quality)

Der `automation/`-Ordner ist als **vollständig eigenständiges System** konzipiert. Es gilt die strikte Restriktion, dass keine Komponente innerhalb dieses Verzeichnisses Module oder Definitionen aus dem übergeordneten `adapters/`-Pfad importieren darf (z. B. `adapters/instrument_map.py` oder `adapters/instrument_utils.py`). Jegliche Precision-Ermittlung erfolgt dynamisch via eToro API oder über eine interne Heuristik.

Das Paradigma der **Shift-Left Data Quality** verlagert die Sicherstellung der Nautilus-Kompatibilität direkt an den Punkt der Datengenerierung (Schritt 1 und 2). Datenkorrekturen oder Typkonvertierungen (wie das alte `migrate_catalog_to_fixed_binary()`) während der Orchestrierung entfallen vollständig.

### Systemweiter Datenfluss

```text
[catalog_service.py] ──(24/7 WebSocket)──► Raw Ticks ──(Stündlich)──► [Timestamp].zip (in data/import/)
                                                                           │
[api_backfiller.py]  ──(REST Candle-Gap)─► FSB(16) Parquet ────────────────►┤ (Multi-ZIP-Merge)
                                                                           ▼
                                                             [daily_orchestrator.py]
                                                                           │
                                                                           ▼
                                                             data.parquet [merged & deduped]
                                                                           │
                                                                           ▼
                                                             [backtest_runner.py] (Tournament)
                                                                           │
                                                                           ▼
                                                             [momentum_ls_run.py] (Live-Bot)

```

---

## 2. Pipeline-Komponenten & Skript-Spezifikationen

### 2.1. `automation/catalog_service.py` (Continuous Catalog Service)

Läuft als permanenter Hintergrunddienst (24/7 via `systemd`).

1. **API-Initialisierung:** Verbindet sich beim Start mit der eToro API (`GET /api/v1/market-data/instruments?instrumentIds=...`), um die aktuellen Werte für `price_precision` und `size_precision` abzufragen, und baut die WebSocket-Verbindung (`wss://ws.etoro.com/ws`) auf.
2. **Buffering:** Speichert einkommende Marktdaten als `RawTick(bid, ask, ts_event)` in einem atomaren Speicher-Puffer.
3. **Asynchroner Flush (Standard: alle 60 Minuten):**
* Tauscht den aktiven Puffer atomar aus (Datensammlung läuft unterbrechungsfrei weiter).
* Dedupliziert die Einträge basierend auf dem exakten Event-Zeitstempel (`ts_event`).
* Schreibt pro Instrument eine Parquet-Datei, kodiert im `FixedSizeBinary(16)`-Format, und injiziert die Arrow-Metadaten.
* Packt alle generierten Parquet-Dateien in ein ZIP-Archiv mit dem Namensschema `[Timestamp].zip` und legt dieses in `data/import/` ab.



**ZIP-Interne Verzeichnisstruktur:**

```text
20260525_140000.zip
└── quote_tick/
    ├── BTC.ETORO/
    │   └── 20260525_140000.parquet    # FSB(16), injizierte Metadaten
    └── TSLA.ETORO/
        └── 20260525_140000.parquet

```

**CLI-Aufruf:**

```bash
python3 automation/catalog_service.py --instrument-ids 1111 1137 6434 --flush-interval 3600

```

---

### 2.2. `automation/universe_fetcher.py` & Universum-Gating

Verantwortlich für die Synchronisation mit dem eToro Smart Portfolio.

1. **Abruf:** Liest die aktuellen Portfoliokomponenten über den öffentlichen eToro-Benutzernamen des Smart Portfolios aus (definiert über `MOMENTUM_LS_USERNAME` in der `.env`).
2. **Mapping:** Übersetzt die eToro-internen numerischen IDs in standardisierte Nautilus-Symbole (z. B. `1111` -> `BTC.ETORO`) unter Nutzung von `automation/config/instrument_map.json`.
3. **Ausgabe:** Generiert die Datei `data/universe/momentum_ls.json`.
4. **Gating-Regel im Orchestrator:** Ist diese Datei älter als 24 Stunden, bricht die Pipeline mit einer `Universe data is stale`-Warnung ab, bis der Fetcher manuell ausgeführt wurde.

---

### 2.3. `automation/api_backfiller.py` & `historical_fetcher.py` (Datenbeschaffung)

Dienen dem Schließen von Datenlücken (Gaps) und der Erstbefüllung.

* **`api_backfiller.py`:** Wird standardmäßig vom Master-Orchestrator aufgerufen, um Lücken der letzten 7 Tage zu schließen. Lädt das Universum, fragt die Candle-Historie über die eToro REST-API ab, konvertiert die Daten unter Umgehung eines speicherintensiven pandas-Zwischenschritts direkt in `FixedSizeBinary(16)` und speichert sie atomar ab. Instrumente, deren lokal verfügbare Daten jünger als 1 Stunde sind, werden automatisch übersprungen.
* **`historical_fetcher.py`:** Wird für historische Deep-Backfills (z. B. 12 Monate für Erstinbetriebnahme) verwendet.

**CLI-Aufruf:**

```bash
# Täglicher inkrementeller Backfill:
python3 automation/api_backfiller.py --days 7

# Manueller Deep-Backfill:
python3 automation/historical_fetcher.py --months 12

```

---

### 2.4. `automation/daily_orchestrator.py` (Master-Orchestrator)

Führt die tägliche End-to-End-Pipeline sequentiell aus.

#### Phase 1: Universum-Validierung

Lädt `data/universe/momentum_ls.json`. Symbole ohne gemappte eToro-ID werfen eine Warnung und werden aus dem aktuellen Durchlauf exkludiert.

#### Phase 2: Multi-ZIP-Merge & Deduplizierung

1. Erkennt **alle** in `data/import/` liegenden `*.zip`-Dateien und sortiert sie aufsteigend nach Modifikationszeitstempel.
2. Extrahiert die Parquet-Dateien direkt in-memory und isoliert das Symbol aus der Pfadkomponente nach dem Token `quote_tick`.
3. Liest die existierende `data.parquet` des Instruments ein (falls vorhanden).
4. Konkatiniert die Tabellen nativ über `pa.concat_tables([existing_table, *zip_tables])`.
5. Führt eine Deduplizierung auf PyArrow-Ebene über ein Zeitstempel-Gating durch:
```python
seen_ts = set()
keep_indices = [i for i, ts in enumerate(ts_list) if ts not in seen_ts and not seen_ts.add(ts)]
keep_indices.sort(key=lambda i: ts_list[i])
merged = merged.take(pa.array(keep_indices, type=pa.int64()))

```


6. Schreibt die Daten atomar über eine temporäre Datei (`.tmp.parquet`) zurück in den Pfad `data/nautilus/data/quote_tick/{Symbol}/data.parquet`.
7. **Unwiderrufliche Löschung:** Nach erfolgreichem Merge-Vorgang werden alle verarbeiteten ZIP-Dateien aus `data/import/` gelöscht.

---

## 3. Datenformate & Metadaten-Spezifikationen

Nautilus erfordert ein striktes Binärlayout für das PyArrow-Schema. Abweichungen führen zu Kernelschnittstellen-Fehlern (Rust-Panics) im Backtester.

### PyArrow-Schema-Definition

Preise und Volumina müssen als `pa.binary(16)` deklariert sein. Variable Binärfelder (`pa.binary()`) oder der standardmäßige PyArrow 24+ `BinaryView` sind inkompatibel.

```python
import pyarrow as pa

_FSB16 = pa.binary(16)
QUANTIZED_TICK_SCHEMA = pa.schema([
    pa.field("bid_price", _FSB16),
    pa.field("ask_price", _FSB16),
    pa.field("bid_size",  _FSB16),
    pa.field("ask_size",  _FSB16),
    pa.field("ts_event",  pa.uint64()),
    pa.field("ts_init",   pa.uint64()),
])

```

### `FixedSizeBinary(16)` Enkodierungslogik

Werte werden als Little-Endian signed 64-bit Integer (`int64 LE`) formatiert, skaliert um $10^{\text{precision}}$, gefolgt von 8 Null-Bytes Padding.

```python
import struct

def _encode_fsb16(value: float, precision: int) -> bytes:
    raw = round(value * (10 ** precision))
    # Bound-Checking gegen int64-Limits
    raw = max(-(2**63), min(2**63 - 1, raw))
    return struct.pack("<q", raw) + b"\x00" * 8

```

### Arrow-Metadaten-Injektion

Jede Parquet-Datei muss zwingend Byte-Keys im Datei-Metadaten-Header aufweisen, da das Rust-Backend von Nautilus sonst mit einer `MissingMetadata`-Exception abbricht.

```python
meta = {
    b"price_precision": b"5",
    b"size_precision":  b"8",
    b"instrument_id":   b"BTC.ETORO",
}
table = table.replace_schema_metadata(meta)

```

### Dynamische Precision-Ermittlung & Fallback-Heuristik

Kann die eToro API für ein Instrument keine Precision-Werte zurückgeben, greift die systeminterne statische Heuristik:

```python
_CRYPTO = frozenset({"BTC", "ETH", "ADA", "DOGE", "SOL", "XRP", "AVAX"})
_FRAC   = frozenset({"NATGAS", "USDTRY", "USDZAR", "PALL"})

def _fallback_precisions(symbol: str) -> tuple[int, int]:
    sym = symbol.split(".")[0]
    if "SHIB" in sym or "PEPE" in sym: 
        return 8, 8
    if sym in _CRYPTO: 
        return 2, 8
    if sym in _FRAC:   
        return 5, 5
    return 2, 0  # Standard-Default für Equities (z.B. TSLA.ETORO)

```

---

## 4. Tournament-Logik & Strategie-Selektion

Skript: `automation/backtest_runner.py`

Das Tournament validiert die Performance aller registrierten Handelsstrategien über eine rollierende Matrix auf den generierten Daten.

### Deterministisches Zeitfenster

```python
today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
seven_days_ago = today_midnight - timedelta(days=7)

```

### Harte Selektionskriterien (Gating)

Eine Strategie wird für ein Symbol nur dann als valider Kandidat zugelassen, wenn **alle** der folgenden statistischen Schwellenwerte erreicht werden:

* **Mindestanzahl ausgeführter Trades:** $\text{min\_trades} \ge 20$
* **Profitabilität:** $\text{total\_return} > 0.0\%$

Zusätzlich muss **mindestens eine** der folgenden Bedingungen erfüllt sein:

* $\text{Sortino-Ratio} \ge 0.3$
* $\text{Profit Factor} \ge 1.1$

### Score-Evaluationsformel

Die Reihung der qualifizierten Strategien erfolgt über eine gewichtete Zielfunktion:

$$\text{Score} = (\text{Sortino} \times 0.4) + (\text{ProfitFactor} \times 0.3) + (\text{WinRate} \times 0.2) - (\text{MaxDrawdown} \times 0.1)$$

*Hinweis:* Ein Profit Factor von `999` (Resultat aus Perioden ohne Verlusttrades) wird algorithmisch abgefangen und innerhalb der Zielfunktion penalisiert, um Verzerrungen zu vermeiden. Der Gewinner wird in `logs/tournament_YYYY-MM-DD.json` persistent gespeichert. Symbole ohne qualifizierte Strategie werden für den Handelstag gesperrt.

---

## 5. Live Deployment & Ausführungsregeln

Skript: `automation/momentum_ls_run.py`

### Safety Interlock (Echtgeldschutz)

Der Start des Live-Handels-Subprozesses unterliegt einer dreistufigen Schutzschaltung. Wenn eine einzige Komponente fehlt oder falsch deklariert ist, bricht das System die Initialisierung via `sys.exit(1)` ab.

| Stufe | Parameter | Herkunft | Erwarteter Wert |
| --- | --- | --- | --- |
| **1** | `environment` | `config/setups.py` -> `ETORO_EXECUTION` | `"real"` |
| **2** | `dry_run` | CLI-Argument / Konfigurations-Injektion | `False` |
| **3** | `ETORO_CONFIRM_LIVE` | System-Umgebungsvariable (`.env`) | `"1"` |

### `MomentumLSAllocator` (Kapitalallokation)

Der Zuteilungsalgorithmus arbeitet nach folgenden Regeln:

1. **No-Interference-Regel:** Befindet sich für ein Symbol bereits eine offene Position im Markt, wird für dieses Instrument für neue Trades temporär ein Kapital von `$0` allokiert. Der Bot greift nicht in laufende Execution-Zyklen ein.
2. **Dynamisches Slicing:** Das momentan ungebundene Gesamtkapital des Kontos wird zu gleichen Teilen auf alle Symbole aufgeteilt, die *keine* aktive Position halten. Die Positionsgrößen passen sich somit dynamisch an die Volatilität der Universumsgröße an.
3. **Execution Floor:** Liegt der errechnete Zuteilungsbetrag für eine Tranche unter `$11.00`, wird die Order blockiert, um eToro-seitige API-Zurückweisungen (Minimum-Margin-Requirement) zu verhindern.

---

## 6. Systemkonfiguration & Administration

### Systemd-Dienst für den Catalog Service (`/etc/systemd/system/catalog.service`)

```ini
[Unit]
Description=eToro Nautilus Continuous Catalog Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/etoro_nautilus/automation/catalog_service.py
WorkingDirectory=/opt/etoro_nautilus
Restart=always
RestartSec=5
EnvironmentFile=/opt/etoro_nautilus/.env
User=botuser
Group=botuser

[Install]
WantedBy=multi-user.target

```

### Cron-Tabellen-Definition für die tägliche Pipeline (`crontab -e`)

```cron
# Täglicher Pipeline-Run um 01:00 UTC mit Multi-ZIP-Merge und Tournament
0 1 * * * /usr/bin/python3 /opt/etoro_nautilus/automation/daily_orchestrator.py --skip-api-fetch >> /opt/etoro_nautilus/logs/cron.log 2>&1

```

### Lokale Verzeichnisstruktur (Soll-Zustand nach Pipeline-Durchlauf)

```text
/opt/etoro_nautilus/
├── automation/
│   ├── catalog_service.py
│   ├── daily_orchestrator.py
│   └── ...
├── data/
│   ├── import/                      # Leer (Archivierte ZIPs nach Merge gelöscht)
│   ├── universe/
│   │   └── momentum_ls.json         # Aktuelle Portfolio-Komponenten
│   └── nautilus/
│       └── data/
│           └── quote_tick/
│               └── BTC.ETORO/
│                   └── data.parquet # FSB(16) Datentabelle inklusive Metadaten
└── logs/
    ├── orchestrator_20260609.log     # Rolling File-Handler (1MB, Max 5 Backups)
    ├── catalog_service_20260609.log  # WebSocket-Verbindungs-Logs
    └── tournament_2026-06-09.json    # Ergebnisse des Matrix-Backtests

```

---

## 7. Pipeline-Diagnose & Fehlerbehebung

| Identifizierter Log-Fehler | Primäre Ursache | Technische Behebungsmaßnahme |
| --- | --- | --- |
| `InvalidColumnType("bid_price", ..., FixedSizeBinary(16), BinaryView)` | Ein pandas-Roundtrip wurde ausgeführt oder PyArrow hat die Daten implizit gecastet. | Quell-Generatoren prüfen; sicherstellen, dass keine Manipulation über DataFrames erfolgt. Daten über `api_backfiller.py` neu aufbauen. |
| `MissingMetadata("price_precision")` | Die Metadaten-Header wurden beim Schreiben der Parquet-Datei verworfen oder nicht injiziert. | Datei wird im Regelfall durch die Routine `_ensure_metadata()` des Orchestrators repariert. Falls permanent: Injektions-Block im Writer prüfen. |
| Verarbeitete ZIP-Dateien verbleiben dauerhaft in `data/import/` | Phase 2 (Merge/Deduplizierung) ist vor Erreichen der Löschroutine mit einem Fehler abgebrochen. | `logs/orchestrator_*.log` auf PyArrow-Fehler oder Dateisperren prüfen. Nach Fehlerbehebung Orchestrator neu starten. |
| `BrokenProcessPool` | Der Backtester hat aufgrund zu vieler paralleler Worker ein Out-of-Memory (OOM) Event ausgelöst. | Die Variable `max_workers` innerhalb von `run_backtest.py` restriktiver konfigurieren: `max_workers = max(1, min(cpu//2, 6))`. |
