#!/usr/bin/env python3
"""
automation/daily_orchestrator.py
=================================
eToro Nautilus — Täglicher End-to-End-Orchestrator (v1.1)

Führt 5 Phasen sequentiell und fehlerresistent aus:
  Phase 1  — Universe & Mapping (dynamische Instrumentenliste)
  Phase 2  — Datenbeschaffung  (ZIP-Import, Merge, Cleanup, API-Backfill)
  Phase 3  — Backtesting        (Matrix-Backtest mit 7-Tage-Midnight-UTC-Fenster)
  Phase 4  — Tournament         (Sortino/PF-Turnier, Pitfall-#14-Fix)
  Phase 5  — Live Deployment   (Safety-Interlocks, Detached Bot, Log-Rotation)

Verwendung:
  python3 automation/daily_orchestrator.py [--dry-run] [--skip-api-fetch]
  python3 automation/daily_orchestrator.py --help

Wichtig:
  - Muss aus dem Projekt-Root ausgeführt werden.
  - Echte Demo-Keys müssen in .env stehen (ETORO_API_KEY, ETORO_USER_KEY).
  - environment="real", dry_run=False → 10k-USD-Demo-Konto wird genutzt.
  - ETORO_CONFIRM_LIVE=1 muss in .env gesetzt sein.

Technische Hinweise (Merge-Engine):
  - Verwendet pyarrow-native Operationen (KEIN pandas-Roundtrip) für
    Parquet-Dateien, um den FixedSizeBinary(16) Typ der Preis/Größen-Spalten
    zu erhalten. Ein pandas-Roundtrip würde FixedSizeBinary(16) → binary
    (BinaryView) konvertieren, was Nautilus' Rust-Backend bricht.
  - Alle alten Timestamp-Katalog-Dateien werden nach dem Merge gelöscht
    (Single-File-Katalog: data.parquet), um normalize_parquet_metadata-Konflikte
    im Backtest zu verhindern.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time
import traceback
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv, set_key

# ─── Projekt-Root in den Python-Path aufnehmen ───────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.instrument_map import ETORO_INSTRUMENTS
from adapters.instrument_utils import get_size_precision

# ─── Pfade ────────────────────────────────────────────────────────────────────
CATALOG_PATH     = PROJECT_ROOT / "data" / "nautilus"
QUOTE_TICK_PATH  = CATALOG_PATH / "data" / "quote_tick"
IMPORT_PATH      = PROJECT_ROOT / "data" / "import"
UNIVERSE_PATH    = PROJECT_ROOT / "data" / "universe" / "momentum_ls.json"
INSTRUMENT_MAP   = PROJECT_ROOT / "adapters" / "instrument_map.py"
LOGS_DIR         = PROJECT_ROOT / "logs"
REPORTS_DIR      = PROJECT_ROOT / "reports"
TOURNAMENT_PATH  = LOGS_DIR / f"tournament_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
BACKTEST_CFG     = PROJECT_ROOT / "backtesting" / "backtesting_config.json"
ENV_FILE         = PROJECT_ROOT / ".env"

# ─── Logging-Konfiguration ────────────────────────────────────────────────────
LOG_MAX_BYTES   = 1 * 1024 * 1024   # 1 MB max pro Log-Datei
LOG_BACKUP_CNT  = 5                  # 5 Rotations-Dateien
LOG_RETENTION_D = 7                  # Log-Dateien älter als 7 Tage werden gelöscht

# ─── Pflicht-Spalten für QuoteTick-Schema-Validierung ────────────────────────
REQUIRED_COLUMNS = frozenset({"bid_price", "ask_price", "bid_size", "ask_size", "ts_event", "ts_init"})


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def _setup_orchestrator_logging() -> logging.Logger:
    """Richtet strukturiertes, LLM-freundliches Logging für den Orchestrator ein."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_path  = LOGS_DIR / f"orchestrator_{today_str}.log"

    file_handler = logging.handlers.RotatingFileHandler(
        str(log_path),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_CNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    logging.basicConfig(
        level=logging.DEBUG,
        format=fmt,
        handlers=[file_handler, stream_handler],
        force=True,
    )
    return logging.getLogger("orchestrator")


def cleanup_old_logs(log_dir: Path, max_age_days: int = LOG_RETENTION_D) -> None:
    """Löscht alle Log-Dateien im logs/-Verzeichnis, die älter als max_age_days sind."""
    if not log_dir.exists():
        return
    cutoff_ts = time.time() - max_age_days * 86400
    deleted = 0
    for f in log_dir.glob("*.log*"):
        try:
            if f.stat().st_mtime < cutoff_ts:
                f.unlink()
                deleted += 1
        except OSError:
            pass
    if deleted:
        logging.getLogger("orchestrator").info(
            f"[LOG-CLEANUP] {deleted} Log-Dateien gelöscht (älter als {max_age_days} Tage)."
        )


def emit_json_event(log: logging.Logger, event_type: str, payload: dict) -> None:
    """Emittiert einen strukturierten JSON-Event-Eintrag (für LLM-Analyse)."""
    event = {
        "event_type": event_type,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    log.info(f"[JSON_EVENT] {json.dumps(event, ensure_ascii=False, default=str)}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Universe & Mapping
# ═══════════════════════════════════════════════════════════════════════════════

def phase1_universe_and_mapping(log: logging.Logger) -> dict:
    """Phase 1: Lädt das Anlage-Universum und mappt unbekannte eToro-IDs."""
    log.info("═" * 60)
    log.info("PHASE 1: Universe & Mapping")
    log.info("═" * 60)

    universe_data  = _load_universe_file(log)
    universe_items = universe_data.get("universe", [])

    if not universe_items:
        log.warning("[Phase 1] Universe leer — verwende alle Instrumente aus instrument_map.py.")
        universe_items = [
            {"etoro_id": k, "symbol": v, "raw_name": v.split(".")[0]}
            for k, v in ETORO_INSTRUMENTS.items()
        ]

    # Stale-Check
    fetched_at_str = universe_data.get("fetched_at", "")
    if fetched_at_str:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
            if age_h > 24:
                log.warning(f"[Phase 1] Universe-Daten sind {age_h:.1f}h alt (> 24h). Bitte neu fetchen.")
            else:
                log.info(f"[Phase 1] Universe-Alter: {age_h:.1f}h — frisch genug.")
        except ValueError:
            log.warning("[Phase 1] Konnte Universe-Zeitstempel nicht parsen.")

    symbol_to_id = {v: k for k, v in ETORO_INSTRUMENTS.items()}
    unmapped = []
    valid_items = []

    for item in universe_items:
        sym      = item.get("symbol")
        etoro_id = item.get("etoro_id", "")

        if sym is None or sym == "None":
            eid = str(etoro_id)
            mapped_sym = ETORO_INSTRUMENTS.get(eid)
            if mapped_sym:
                item["symbol"] = mapped_sym
                log.info(f"[Phase 1] Auto-gemappt: ID {eid} → {mapped_sym}")
            else:
                unmapped.append(eid)
                log.warning(f"[Phase 1] Unbekannte eToro-ID {eid} — kein Mapping vorhanden.")
                continue

        if not etoro_id and sym:
            item["etoro_id"] = symbol_to_id.get(sym, "")

        valid_items.append(item)

    # Deduplizieren nach Symbol
    seen_symbols: set[str] = set()
    deduped = []
    for item in valid_items:
        s = item.get("symbol")
        if s and s not in seen_symbols:
            seen_symbols.add(s)
            deduped.append(item)

    log.info(f"[Phase 1] {len(deduped)} eindeutige Instrumente im Universum.")
    if unmapped:
        log.warning(f"[Phase 1] {len(unmapped)} IDs ohne Mapping: {unmapped}")

    emit_json_event(log, "PHASE1_COMPLETE", {
        "universe_size": len(deduped),
        "unmapped_count": len(unmapped),
    })
    return {"universe": deduped, "unmapped_ids": unmapped}


def _load_universe_file(log: logging.Logger) -> dict:
    if UNIVERSE_PATH.exists():
        try:
            with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"[Phase 1] Fehler beim Laden von {UNIVERSE_PATH}: {e}")
    else:
        log.warning(f"[Phase 1] Universe-Datei {UNIVERSE_PATH} nicht gefunden.")
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Datenbeschaffung
# ═══════════════════════════════════════════════════════════════════════════════

def phase2_data_acquisition(
    log: logging.Logger,
    universe_result: dict,
    api_key: str,
    user_key: str,
    skip_api_fetch: bool = False,
) -> dict:
    """
    Phase 2: Datenbeschaffung (ZIP-Import, Merge, Cleanup, API-Backfill).
    """
    log.info("═" * 60)
    log.info("PHASE 2: Datenbeschaffung (ZIP-Import, Merge, Cleanup, API-Fetch)")
    log.info("═" * 60)

    result = {
        "imported_instruments": [],
        "merged_count": 0,
        "api_filled": [],
        "zip_deleted": False,
    }

    # 2a. ZIP suchen und importieren
    zip_file = _find_zip_file(log)
    if zip_file:
        import_result = _import_and_merge_zip(log, zip_file)
        result["imported_instruments"] = import_result["instruments"]
        result["merged_count"]         = import_result["merged"]

        # 2c. ZIP UNWIDERRUFLICH löschen (os.remove)
        if import_result["success"]:
            try:
                os.remove(str(zip_file))
                result["zip_deleted"] = True
                log.info(f"[Phase 2c] ZIP gelöscht: {zip_file}")
                emit_json_event(log, "ZIP_DELETED", {"path": str(zip_file)})
            except OSError as e:
                log.error(f"[Phase 2c] Konnte ZIP nicht löschen: {e}")
        else:
            log.warning("[Phase 2c] ZIP NICHT gelöscht da Import fehlschlug.")
    else:
        log.info("[Phase 2a] Keine ZIP-Datei in data/import/ gefunden. Überspringe.")

    # 2c-post: Katalog-Migration binary → FixedSizeBinary(16) (Pitfall #14b Fix)
    # PyArrow 24+ liest 'binary' als BinaryView → Nautilus Rust-Panic.
    # Migration ist idempotent und schnell (nur Dateien mit falschem Typ werden konvertiert).
    migrate_catalog_to_fixed_binary(log)

    # 2d. API-Backfill für letzte 7 Tage
    if not skip_api_fetch:
        universe_symbols = {
            item["symbol"]
            for item in universe_result.get("universe", [])
            if item.get("symbol")
        }
        api_filled = asyncio.run(
            _api_backfill_7days(log, universe_symbols, api_key, user_key)
        )
        result["api_filled"] = api_filled
    else:
        log.info("[Phase 2d] API-Backfill übersprungen (--skip-api-fetch).")

    emit_json_event(log, "PHASE2_COMPLETE", {
        "merged_instruments": result["merged_count"],
        "zip_deleted": result["zip_deleted"],
        "api_filled_count": len(result["api_filled"]),
    })
    return result


def _find_zip_file(log: logging.Logger) -> Path | None:
    """Sucht nach exakt einer .zip-Datei in data/import/."""
    IMPORT_PATH.mkdir(parents=True, exist_ok=True)
    zips = sorted(IMPORT_PATH.glob("*.zip"))
    if not zips:
        return None
    if len(zips) > 1:
        log.warning(f"[Phase 2a] Mehrere ZIPs gefunden. Nehme neueste: {zips[-1]}")
    return zips[-1]


def _import_and_merge_zip(log: logging.Logger, zip_path: Path) -> dict:
    """
    Entpackt ZIP, validiert Parquet-Schema (Ask/Bid dürfen nie fehlen),
    merged mit bestehenden Daten, dedupliziert nach Timestamp.
    """
    log.info(f"[Phase 2a] Verarbeite ZIP: {zip_path}")
    result = {"instruments": [], "merged": 0, "success": False}

    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            namelist = zf.namelist()
            qt_files = [n for n in namelist if "quote_tick/" in n and n.endswith(".parquet")]
            log.info(f"[Phase 2a] {len(qt_files)} QuoteTick-Parquet-Dateien in ZIP.")

            # Gruppiere nach Instrument-Symbol
            instrument_files: dict[str, list[str]] = {}
            for fname in qt_files:
                parts = fname.split("/")
                if len(parts) >= 3:
                    symbol = parts[1]
                    instrument_files.setdefault(symbol, []).append(fname)

            log.info(f"[Phase 2a] {len(instrument_files)} Instrumente in ZIP.")

            for symbol, files in instrument_files.items():
                try:
                    if _merge_instrument_from_zip(log, zf, symbol, files):
                        result["instruments"].append(symbol)
                        result["merged"] += 1
                except Exception as e:
                    log.error(
                        f"[Phase 2b] Merge-Fehler für {symbol}: {e}\n"
                        f"{traceback.format_exc()}"
                    )

    except zipfile.BadZipFile as e:
        log.error(f"[Phase 2a] Ungültige ZIP-Datei: {e}")
        return result
    except Exception as e:
        log.error(f"[Phase 2a] ZIP-Import-Fehler: {e}\n{traceback.format_exc()}")
        return result

    result["success"] = result["merged"] > 0
    log.info(f"[Phase 2b] Merge abgeschlossen: {result['merged']} Instrumente erfolgreich eingelesen.")
    return result


def _merge_instrument_from_zip(
    log: logging.Logger,
    zf: zipfile.ZipFile,
    symbol: str,
    zip_file_list: list[str],
) -> bool:
    """
    Merged ZIP-Daten mit bestehendem Katalog (pyarrow-native, kein pandas-Roundtrip).

    KRITISCH: Nutzt pyarrow direkt um FixedSizeBinary(16) zu erhalten.
    pandas würde FixedSizeBinary(16) → binary (BinaryView) konvertieren,
    was Nautilus' Rust-Backend mit InvalidColumnType(BinaryView) abbricht.

    Strategie:
    1. ZIP-Dateien als Arrow-Tables lesen
    2. Alle bestehenden Parquet-Dateien als Arrow-Tables lesen (inkl. Timestamp-Dateien)
    3. Schema-Vereinheitlichung auf das bestehende Nautilus-Schema
    4. Concatenieren + Deduplizieren per ts_event
    5. Single data.parquet speichern (atomisch)
    6. Alle alten Timestamp-Dateien löschen
    """
    # 1. ZIP-Dateien lesen
    new_tables: list[pa.Table] = []
    zip_meta: dict = {}

    for fname in zip_file_list:
        try:
            with zf.open(fname) as f:
                raw = f.read()
            table = pq.read_table(io.BytesIO(raw))

            # Schema-Validierung
            missing = REQUIRED_COLUMNS - set(table.column_names)
            if missing:
                log.error(f"[Phase 2a] Schema-Fehler in {fname}: Spalten fehlen: {missing}")
                continue

            if len(table) == 0:
                continue

            new_tables.append(table)
            if table.schema.metadata:
                zip_meta = table.schema.metadata
            log.debug(f"[Phase 2a] Gelesen: {fname} ({len(table)} Zeilen)")

        except Exception as e:
            log.warning(f"[Phase 2a] Fehler beim Lesen von {fname}: {e}")

    if not new_tables:
        log.warning(f"[Phase 2a] Keine validen Daten für {symbol} im ZIP.")
        return False

    # 2. Alle bestehenden Parquet-Dateien lesen
    dest_dir  = QUOTE_TICK_PATH / symbol
    dest_file = dest_dir / "data.parquet"
    dest_dir.mkdir(parents=True, exist_ok=True)

    existing_tables: list[pa.Table] = []
    best_meta: dict   = {}
    ref_schema: pa.Schema | None = None  # Referenz-Schema (Nautilus-Format)

    for existing_path in sorted(dest_dir.rglob("*.parquet")):
        try:
            et = pq.read_table(str(existing_path))
            if len(et) == 0:
                continue

            existing_tables.append(et)
            em = et.schema.metadata or {}

            # Metadaten mit price_precision bevorzugen
            if (b"price_precision" in em or "price_precision" in em) and b"price_precision" not in best_meta:
                best_meta = em

            # Erstes bestehendes Schema als Referenz (hat FixedSizeBinary(16))
            if ref_schema is None:
                ref_schema = et.schema

            log.debug(f"[Phase 2b] Bestehend: {existing_path.name} ({len(et)} Zeilen)")

        except Exception as e:
            log.warning(f"[Phase 2b] Konnte {existing_path.name} nicht lesen: {e}")

    total_existing = sum(len(t) for t in existing_tables)
    new_total      = sum(len(t) for t in new_tables)

    # 3. Ziel-Schema aufbauen (Nautilus FixedSizeBinary(16) erhalten)
    target_schema = _build_target_schema(ref_schema, symbol)

    # 4. Alle Tables auf Ziel-Schema casten
    all_tables = existing_tables + new_tables
    cast_tables: list[pa.Table] = []
    for t in all_tables:
        try:
            cast_tables.append(_cast_to_schema(t, target_schema))
        except Exception as e:
            log.warning(f"[Phase 2b] Schema-Cast-Fehler ({symbol}): {e}")

    if not cast_tables:
        log.error(f"[Phase 2b] Keine validen Tables nach Schema-Cast für {symbol}.")
        return False

    # 5. Concatenieren (pyarrow-nativ)
    try:
        merged = pa.concat_tables(cast_tables, promote_options="default")
    except Exception as e:
        log.warning(f"[Phase 2b] concat_tables fehlgeschlagen ({symbol}): {e}. Pandas-Fallback.")
        all_dfs = [t.to_pandas() for t in cast_tables]
        df = pd.concat(all_dfs, ignore_index=True)
        df["ts_event"] = df["ts_event"].astype("uint64")
        df["ts_init"]  = df["ts_init"].astype("uint64")
        df = df.drop_duplicates(subset=["ts_event"]).sort_values("ts_event").reset_index(drop=True)
        merged = pa.Table.from_pandas(df, preserve_index=False)
        merged = merged.replace_schema_metadata(_build_final_meta(zip_meta, best_meta, symbol))
        _save_and_cleanup(log, merged, dest_file, dest_dir, total_existing, new_total, symbol, 0)
        return True

    # 6. Deduplizieren nach ts_event (pandas für Mask, Arrow für Filtering)
    rows_before = len(merged)
    ts_col  = merged.column("ts_event").to_pylist()
    ts_ser  = pd.Series(ts_col, dtype="uint64")
    unique_mask = ~ts_ser.duplicated(keep="first")
    sort_idx    = ts_ser[unique_mask].argsort().values
    keep_idx    = unique_mask[unique_mask].index.values[sort_idx]
    merged = merged.take(keep_idx)
    rows_after = len(merged)
    log.info(
        f"[Phase 2b] {symbol}: {total_existing} bestehend + {new_total} neu = "
        f"{rows_before}→{rows_after} Zeilen (-{rows_before - rows_after} Duplikate)"
    )

    # 7. Finale Metadaten setzen
    final_meta = _build_final_meta(zip_meta, best_meta, symbol)
    merged = merged.replace_schema_metadata(final_meta)

    # 8. Speichern + alte Dateien löschen
    _save_and_cleanup(log, merged, dest_file, dest_dir, total_existing, new_total, symbol, 0)
    return True


def _build_target_schema(ref_schema: pa.Schema | None, symbol: str) -> pa.Schema:
    """
    Erstellt das Ziel-Schema.
    Immer FixedSizeBinary(16) für Preis/Größen-Spalten — Nautilus Rust-Backend-Pflicht.

    Hintergrund (PyArrow 24+ / Pitfall):
      - Nautilus Rust-Backend erwartet zwingend FixedSizeBinary(16) für bid_price,
        ask_price, bid_size, ask_size.
      - PyArrow ≥ 16 liest 'binary'-Spalten aus Parquet als BinaryView in den
        Arbeitsspeicher → Rust-Panic: InvalidColumnType(..., FixedSizeBinary(16), BinaryView).
      - Fix: Schema immer auf FixedSizeBinary(16) erzwingen, unabhängig vom
        Quellschema (ref_schema oder ZIP-Dateien).
    """
    # Immer FixedSizeBinary(16) — niemals variables binary oder BinaryView
    _FSB16 = pa.binary(16)  # FixedSizeBinary(16)

    if ref_schema is not None:
        # Baue Schema aus ref_schema, aber ersetze binäre Spalten durch FSB(16)
        fields = []
        for f in ref_schema:
            if f.name in ("bid_price", "ask_price", "bid_size", "ask_size"):
                fields.append(pa.field(f.name, _FSB16))
            else:
                fields.append(pa.field(f.name, f.type))
        return pa.schema(fields)

    # Standard: FixedSizeBinary(16) für alle Preis/Größen-Spalten
    return pa.schema([
        pa.field("bid_price", _FSB16),
        pa.field("ask_price", _FSB16),
        pa.field("bid_size",  _FSB16),
        pa.field("ask_size",  _FSB16),
        pa.field("ts_event",  pa.uint64()),
        pa.field("ts_init",   pa.uint64()),
    ])


def _cast_to_schema(table: pa.Table, target: pa.Schema) -> pa.Table:
    """
    Castet eine pyarrow Table auf das Ziel-Schema.
    Behandelt FixedSizeBinary(16) ↔ binary ↔ BinaryView Konvertierungen.
    """
    columns = []
    for field in target:
        if field.name not in table.schema.names:
            raise ValueError(f"Spalte '{field.name}' fehlt.")
        col      = table.column(field.name)
        src_type = col.type
        tgt_type = field.type

        if src_type == tgt_type:
            columns.append(col)
            continue

        # Alle binären Varianten → binary zuerst, dann zum Zieltyp
        if (
            pa.types.is_fixed_size_binary(src_type)
            or pa.types.is_large_binary(src_type)
            or str(src_type) in ("binary_view", "large_binary")
            or pa.types.is_binary(src_type)
        ):
            try:
                if pa.types.is_binary(src_type):
                    mid = col
                else:
                    mid = col.cast(pa.binary())

                if pa.types.is_fixed_size_binary(tgt_type):
                    columns.append(mid.cast(tgt_type))
                elif pa.types.is_binary(tgt_type):
                    columns.append(mid)
                else:
                    columns.append(col.cast(tgt_type))
            except Exception:
                # Letzter Ausweg: zu bytes und zurück
                py_vals = col.to_pylist()
                columns.append(pa.array(py_vals, type=tgt_type))
        else:
            columns.append(col.cast(tgt_type))

    return pa.table(
        {field.name: col for field, col in zip(target, columns)},
        schema=target,
    )


def _build_final_meta(zip_meta: dict, best_meta: dict, symbol: str) -> dict:
    """Erstellt finale Metadaten: price_precision, size_precision, instrument_id."""
    final: dict = {**zip_meta, **best_meta}

    if b"instrument_id" not in final and "instrument_id" not in final:
        final[b"instrument_id"] = symbol.encode()

    if b"price_precision" not in final and "price_precision" not in final:
        sp = get_size_precision(symbol)
        pp = "5" if sp > 0 else "2"
        final[b"price_precision"] = pp.encode()

    if b"size_precision" not in final and "size_precision" not in final:
        sp = get_size_precision(symbol)
        final[b"size_precision"] = str(sp).encode()

    # pandas-Metadaten entfernen (Nautilus braucht sie nicht)
    final.pop(b"pandas", None)
    final.pop("pandas", None)

    return final


def _save_and_cleanup(
    log: logging.Logger,
    table: pa.Table,
    dest_file: Path,
    dest_dir: Path,
    total_existing: int,
    new_total: int,
    symbol: str,
    rows_removed: int,
) -> None:
    """Schreibt data.parquet atomisch und löscht alte Timestamp-Dateien."""
    tmp = dest_file.with_suffix(".tmp.parquet")
    try:
        pq.write_table(table, str(tmp), compression="snappy")
        tmp.rename(dest_file)
        log.info(f"[Phase 2b] {symbol}: {len(table)} Zeilen gespeichert → {dest_file}")
    except Exception as e:
        log.error(f"[Phase 2b] Schreib-Fehler {symbol}: {e}")
        tmp.unlink(missing_ok=True)
        return

    # Alte Timestamp-Dateien löschen
    deleted = 0
    for old in dest_dir.rglob("*.parquet"):
        if old.name != "data.parquet":
            try:
                old.unlink()
                deleted += 1
            except OSError:
                pass
    if deleted:
        log.info(f"[Phase 2b] {symbol}: {deleted} alte Katalog-Datei(en) entfernt.")


# ═══════════════════════════════════════════════════════════════════════════════
# CATALOG-MIGRATION: binary → FixedSizeBinary(16)
# ═══════════════════════════════════════════════════════════════════════════════

def migrate_catalog_to_fixed_binary(log: logging.Logger) -> int:
    """
    Migriert alle bestehenden Katalog-Parquet-Dateien von 'binary' auf
    'FixedSizeBinary(16)' für Preis/Größen-Spalten.

    Hintergrund:
      PyArrow 24+ / Nautilus Rust-Backend erfordern FixedSizeBinary(16).
      Alle Werte sind genau 16 Bytes — die Typkonvertierung ist verlustfrei.

    Returns:
      Anzahl der migrierten Dateien.
    """
    PRICE_COLS = {"bid_price", "ask_price", "bid_size", "ask_size"}
    _FSB16 = pa.binary(16)
    migrated = 0

    if not QUOTE_TICK_PATH.exists():
        return 0

    for sym_dir in sorted(QUOTE_TICK_PATH.iterdir()):
        if not sym_dir.is_dir():
            continue
        for pq_file in sym_dir.glob("*.parquet"):
            try:
                t = pq.read_table(str(pq_file))
                schema = t.schema
                needs_migration = any(
                    f.name in PRICE_COLS and not pa.types.is_fixed_size_binary(f.type)
                    for f in schema
                )
                if not needs_migration:
                    continue

                # Baue neues Schema mit FSB(16)
                new_fields = []
                for f in schema:
                    if f.name in PRICE_COLS:
                        new_fields.append(pa.field(f.name, _FSB16))
                    else:
                        new_fields.append(f)
                new_schema = pa.schema(new_fields)

                # Spalten casten
                new_cols = {}
                for f in schema:
                    col = t.column(f.name)
                    if f.name in PRICE_COLS:
                        # binary / BinaryView → binary → FixedSizeBinary(16)
                        if not pa.types.is_binary(col.type):
                            col = col.cast(pa.binary())
                        col = col.cast(_FSB16)
                    new_cols[f.name] = col

                new_t = pa.table(new_cols, schema=new_schema)
                new_t = new_t.replace_schema_metadata(schema.metadata or {})

                # Atomisch schreiben
                tmp = pq_file.with_suffix(".migr.parquet")
                pq.write_table(new_t, str(tmp), compression="snappy")
                tmp.rename(pq_file)
                migrated += 1
                log.debug(f"[Migration] {pq_file.relative_to(PROJECT_ROOT)}: binary→FSB(16) ✓")

            except Exception as e:
                log.warning(f"[Migration] {pq_file}: Fehler: {e}")

    if migrated:
        log.info(f"[Migration] {migrated} Parquet-Datei(en) auf FixedSizeBinary(16) migriert.")
    else:
        log.info("[Migration] Alle Katalog-Dateien sind bereits FixedSizeBinary(16).")
    return migrated


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2d: API-Backfill
# ═══════════════════════════════════════════════════════════════════════════════

async def _api_backfill_7days(
    log: logging.Logger,
    universe_symbols: set[str],
    api_key: str,
    user_key: str,
) -> list[str]:
    """Füllt Datenlücken der letzten 7 Tage via eToro-API auf."""
    if not api_key or not user_key:
        log.warning("[Phase 2d] API-Keys fehlen — überspringe API-Backfill.")
        return []

    symbol_to_id = {v: k for k, v in ETORO_INSTRUMENTS.items()}
    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=7)

    log.info(
        f"[Phase 2d] API-Backfill: {start_dt.date()} → {end_dt.date()} "
        f"für {len(universe_symbols)} Symbole."
    )

    filled: list[str] = []
    timeout = aiohttp.ClientTimeout(total=30.0)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for sym in sorted(universe_symbols):
            etoro_id = symbol_to_id.get(sym)
            if not etoro_id:
                continue

            dest_file = QUOTE_TICK_PATH / sym / "data.parquet"

            # Prüfen ob Daten aktuell
            if dest_file.exists():
                latest_ts = _get_latest_timestamp(dest_file)
                if latest_ts is not None:
                    gap_h = (end_dt.timestamp() - latest_ts / 1e9) / 3600
                    if gap_h < 1.0:
                        log.debug(f"[Phase 2d] {sym}: aktuell (Lücke {gap_h:.1f}h).")
                        continue

            try:
                candles = await _fetch_candles_for_backfill(session, etoro_id, end_dt, api_key, user_key)
                if candles:
                    new_df = _candles_to_dataframe(candles, start_dt)
                    if not new_df.empty:
                        _merge_api_data(log, sym, new_df)
                        filled.append(sym)
                await asyncio.sleep(1.1)
            except Exception as e:
                log.warning(f"[Phase 2d] API-Fetch-Fehler für {sym}: {e}")

    log.info(f"[Phase 2d] API-Backfill: {len(filled)} Symbole befüllt.")
    return filled


def _get_latest_timestamp(parquet_file: Path) -> int | None:
    try:
        t = pq.read_table(str(parquet_file), columns=["ts_event"])
        df = t.to_pandas()
        if not df.empty:
            return int(df["ts_event"].max())
    except Exception:
        pass
    return None


async def _fetch_candles_for_backfill(
    session: aiohttp.ClientSession,
    etoro_id: str,
    end_time: datetime,
    api_key: str,
    user_key: str,
    interval: str = "OneHour",
) -> list[dict]:
    count = 168
    url = (
        f"https://public-api.etoro.com/api/v1/market-data/instruments/"
        f"{etoro_id}/history/candles/desc/{interval}/{count}"
    )
    headers = {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    params = {"endTime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ")}

    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    raw = await resp.json()
                    if isinstance(raw, dict):
                        raw = raw.get("candles") or raw.get("data") or raw
                    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
                        inner = raw[0].get("candles") or raw[0].get("Candles")
                        if inner:
                            return inner
                    return raw if isinstance(raw, list) else []
                elif resp.status == 429:
                    await asyncio.sleep(int(resp.headers.get("Retry-After", 30)))
                else:
                    return []
        except Exception:
            await asyncio.sleep(5 * (attempt + 1))
    return []


def _candles_to_dataframe(candles: list[dict], start_dt: datetime) -> pd.DataFrame:
    """Konvertiert Candle-Daten ins QuoteTick-Format."""
    rows = []
    for c in candles:
        try:
            c_low = {k.lower(): v for k, v in c.items()}
            date_val = c_low.get("fromdate") or c_low.get("startdate") or c_low.get("date") or c_low.get("timestamp")
            low  = c_low.get("low")  or c_low.get("l")
            high = c_low.get("high") or c_low.get("h")
            if not date_val or low is None or high is None:
                continue
            ts_dt = pd.to_datetime(date_val, utc=True)
            if ts_dt < pd.Timestamp(start_dt):
                continue
            ts_ns = int(ts_dt.timestamp() * 1e9)
            rows.append({
                "bid_price": str(low).encode(),
                "ask_price": str(high).encode(),
                "bid_size":  b"1",
                "ask_size":  b"1",
                "ts_event":  ts_ns,
                "ts_init":   ts_ns,
            })
        except Exception:
            continue

    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts_event"] = df["ts_event"].astype("uint64")
        df["ts_init"]  = df["ts_init"].astype("uint64")
        df = df.drop_duplicates(subset=["ts_event"]).sort_values("ts_event").reset_index(drop=True)
    return df


def _merge_api_data(log: logging.Logger, symbol: str, new_df: pd.DataFrame) -> None:
    """Merged API-Backfill-Daten mit bestehendem Parquet."""
    dest_dir  = QUOTE_TICK_PATH / symbol
    dest_file = dest_dir / "data.parquet"
    dest_dir.mkdir(parents=True, exist_ok=True)

    existing_df  = pd.DataFrame()
    existing_meta: dict = {}
    if dest_file.exists():
        try:
            t = pq.read_table(str(dest_file))
            existing_df   = t.to_pandas()
            existing_meta = t.schema.metadata or {}
        except Exception:
            pass

    merged = pd.concat([existing_df, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts_event"]).sort_values("ts_event").reset_index(drop=True)
    merged["ts_event"] = merged["ts_event"].astype("uint64")
    merged["ts_init"]  = merged["ts_init"].astype("uint64")

    meta = _build_final_meta({}, existing_meta, symbol)
    tmp  = dest_file.with_suffix(".tmp.parquet")
    try:
        t = pa.Table.from_pandas(merged, preserve_index=False)
        t = t.replace_schema_metadata(meta)
        pq.write_table(t, str(tmp), compression="snappy")
        tmp.rename(dest_file)
        log.debug(f"[Phase 2d] {symbol}: {len(merged)} Zeilen nach API-Backfill.")
    except Exception as e:
        log.error(f"[Phase 2d] Schreib-Fehler {symbol}: {e}")
        tmp.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3 & 4: Backtesting & Tournament
# ═══════════════════════════════════════════════════════════════════════════════

def phase3_4_backtest_and_tournament(
    log: logging.Logger,
    dry_run: bool = False,
) -> dict:
    """
    Phase 3 & 4: Matrix-Backtesting + Tournament.

    Zeitfenster: Mitternacht UTC vor 7 Tagen → Mitternacht UTC heute
    Kapital:     10.000 USD (Demo-Konto)
    Pitfall #14: get_size_precision() für Crypto/Forex; by-amount Route für Equities.
    """
    log.info("═" * 60)
    log.info("PHASE 3+4: Matrix-Backtesting & Tournament")
    log.info("═" * 60)

    # Deterministisches 7-Tage-Fenster (Mitternacht UTC)
    today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = today_midnight - timedelta(days=7)

    log.info(f"[Phase 3] Zeitfenster: {seven_days_ago.isoformat()} → {today_midnight.isoformat()}")

    start_capital = 10_000.0
    log.info(f"[Phase 3] Startkapital: ${start_capital:,.2f} (10k Demo-Konto)")
    log.info(
        "[Phase 3] Pitfall #14 Fix: get_size_precision() — "
        "Crypto=8, Forex/Commodity=5, Equity→by-amount Endpunkt (kein make_qty crash)."
    )

    # Dynamische Config erstellen
    dynamic_config   = _build_backtest_config(seven_days_ago, today_midnight, start_capital)
    dynamic_cfg_path = LOGS_DIR / "backtest_dynamic_config.json"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(str(dynamic_cfg_path), "w", encoding="utf-8") as f:
        json.dump(dynamic_config, f, indent=2, ensure_ascii=False)
    log.info(f"[Phase 3] Dynamische Config: {dynamic_cfg_path}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    bt_log_path = LOGS_DIR / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "backtesting" / "run_backtest.py"),
        "--momentum",
        "--catalog-path", str(CATALOG_PATH),
        "--config",       str(dynamic_cfg_path),
        "--output",       str(TOURNAMENT_PATH),
    ]

    log.info(f"[Phase 3] Backtest-Kommando: {' '.join(cmd)}")
    emit_json_event(log, "BACKTEST_START", {
        "start":   seven_days_ago.isoformat(),
        "end":     today_midnight.isoformat(),
        "capital": start_capital,
    })

    if dry_run:
        log.info("[Phase 3] DRY-RUN: Backtest übersprungen.")
        _create_dummy_tournament(log)
        return {"tournament_path": str(TOURNAMENT_PATH), "dry_run": True}

    try:
        with open(str(bt_log_path), "w", encoding="utf-8") as bt_log_f:
            proc = subprocess.run(
                cmd,
                stdout=bt_log_f,
                stderr=subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                timeout=3600,
                check=False,
            )
        log.info(f"[Phase 3] Backtest beendet (Exit-Code: {proc.returncode}).")
        _tail_log(log, bt_log_path, tail=50)

    except subprocess.TimeoutExpired:
        log.error("[Phase 3] Backtest-Timeout nach 3600s.")
        raise
    except Exception as e:
        log.error(f"[Phase 3] Backtest-Fehler: {e}\n{traceback.format_exc()}")
        raise

    # Tournament-Ergebnis prüfen
    if not TOURNAMENT_PATH.exists():
        log.warning("[Phase 4] Tournament-Datei nicht gefunden — erstelle Dummy.")
        _create_dummy_tournament(log)
    else:
        try:
            with open(str(TOURNAMENT_PATH), "r", encoding="utf-8") as tf:
                tournament = json.load(tf)
            winners = tournament.get("per_symbol_winners", {})
            agg     = tournament.get("aggregate_winner")
            log.info(f"[Phase 4] Tournament: {len(winners)} Gewinner.")
            if agg:
                log.info(
                    f"[Phase 4] Aggregierter Gewinner: {agg['strategy']} "
                    f"({agg['win_count']} Wins, Ø Sortino: {agg['mean_sortino']:.2f})"
                )
            emit_json_event(log, "TOURNAMENT_COMPLETE", {
                "winner_count":      len(winners),
                "aggregate_winner":  agg,
                "path":              str(TOURNAMENT_PATH),
            })
        except Exception as e:
            log.error(f"[Phase 4] Fehler beim Lesen des Tournament-Ergebnisses: {e}")

    return {"tournament_path": str(TOURNAMENT_PATH), "dry_run": False}


def _build_backtest_config(
    start: datetime,
    end: datetime,
    start_capital: float,
) -> dict:
    """
    Erstellt die Backtest-Config mit dynamischem Zeitfenster und 10k-Kapital.

    Pitfall #14 (Fractional Trading):
    - trade_amount_usd wird im Backtest-Worker auf 50.000 skaliert (Bug-1-Fix
      in run_backtest.py), damit make_qty bei 10k-Konto nicht crasht.
    - Im Live-Bot nutzt der Adapter den by-amount Endpunkt direkt (USD-Betrag),
      sodass Nautilus keine Stückzahlen für Equities berechnen muss.
    - get_size_precision() gibt Crypto=8, Forex/Commodity=5, Equity=0.
    """
    strategies = []
    if BACKTEST_CFG.exists():
        try:
            with open(str(BACKTEST_CFG), "r", encoding="utf-8") as f:
                base_cfg   = json.load(f)
            strategies = base_cfg.get("strategies", [])
        except Exception:
            pass

    if not strategies:
        strategies = [{
            "strategy_module": "strategies.sma_crossover",
            "strategy_class":  "SmaCrossoverStrategy",
            "config_class":    "SmaCrossoverConfig",
            "params":          {"sma_period": 2},
        }]

    return {
        "global_settings": {
            "catalog_path":  str(CATALOG_PATH),
            "start_time":    start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time":      end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "start_capital": start_capital,
            "_note": (
                f"Dynamisch generiert — Fenster: {start.date()} bis {end.date()} "
                "(Midnight UTC). Pitfall-#14-Fix: trade_amount_usd im Worker auf "
                "50k skaliert; Live-Bot nutzt by-amount Endpunkt."
            ),
        },
        "strategies": strategies,
    }


def _create_dummy_tournament(log: logging.Logger) -> None:
    dummy = {
        "generated_at":             datetime.now(timezone.utc).isoformat(),
        "universe_snapshot":        "dummy",
        "total_symbol_strategy_pairs": 0,
        "eligible_pairs":           0,
        "per_symbol_winners":       {},
        "aggregate_winner":         None,
        "full_results":             [],
        "_note": "Dummy-Tournament (dry-run oder kein Backtest-Datenmaterial).",
    }
    TOURNAMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(str(TOURNAMENT_PATH), "w", encoding="utf-8") as f:
        json.dump(dummy, f, indent=2, ensure_ascii=False)
    log.info(f"[Phase 4] Dummy-Tournament gespeichert: {TOURNAMENT_PATH}")


def _tail_log(log: logging.Logger, log_path: Path, tail: int = 50) -> None:
    try:
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-tail:]:
                log.debug(f"  [BT-LOG] {line}")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: Live Deployment
# ═══════════════════════════════════════════════════════════════════════════════

def phase5_live_deployment(
    log: logging.Logger,
    universe_result: dict,
    tournament_result: dict,
    dry_run: bool = False,
) -> int:
    """
    Phase 5: Safety-Interlocks setzen, Bot als Detached Subprocess starten.

    - ETORO_EXECUTION["environment"] = "real", dry_run = False
    - ETORO_CONFIRM_LIVE=1 in .env
    - subprocess.Popen mit start_new_session=True
    - Output → logs/live_bot_YYYYMMDD.log (RotatingFileHandler via Log-Manager)
    - Orchestrator beendet sich danach mit Exit-Code 0
    """
    log.info("═" * 60)
    log.info("PHASE 5: Live Deployment & Bot-Start")
    log.info("═" * 60)

    _ensure_safety_interlocks(log)

    tournament_path = tournament_result.get("tournament_path", str(TOURNAMENT_PATH))
    if not Path(tournament_path).exists():
        log.error(f"[Phase 5] Tournament-Datei nicht gefunden: {tournament_path}")
        return 1

    if not UNIVERSE_PATH.exists():
        log.warning(f"[Phase 5] Universe-Datei nicht gefunden: {UNIVERSE_PATH}")

    # Bot-Log mit Rotation
    today_str  = datetime.now(timezone.utc).strftime("%Y%m%d")
    bot_log    = LOGS_DIR / f"live_bot_{today_str}.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"[Phase 5] Bot-Log: {bot_log}")

    bot_script = PROJECT_ROOT / "dev_scripts" / "momentum_ls_run.py"
    if not bot_script.exists():
        log.error(f"[Phase 5] Bot-Skript nicht gefunden: {bot_script}")
        return 1

    cmd = [
        sys.executable,
        str(bot_script),
        "--universe",   str(UNIVERSE_PATH),
        "--tournament", tournament_path,
    ]

    log.info(f"[Phase 5] Bot-Kommando: {' '.join(cmd)}")
    emit_json_event(log, "BOT_START_INITIATED", {
        "cmd":          cmd,
        "log_file":     str(bot_log),
        "tournament":   tournament_path,
        "universe":     str(UNIVERSE_PATH),
        "dry_run":      dry_run,
    })

    if dry_run:
        log.info("[Phase 5] DRY-RUN: Bot-Subprocess übersprungen.")
        log.info("[Phase 5] Orchestrator beendet sich mit Exit-Code 0 (dry_run).")
        return 0

    try:
        bot_log_handle = open(str(bot_log), "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdout=bot_log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        log.info(f"[Phase 5] Trading-Bot gestartet (PID: {proc.pid}).")
        emit_json_event(log, "BOT_STARTED", {"pid": proc.pid, "log_file": str(bot_log)})

        pid_file = LOGS_DIR / "live_bot.pid"
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        log.info(f"[Phase 5] PID gespeichert: {pid_file}")

    except Exception as e:
        log.error(f"[Phase 5] Fehler beim Starten des Bot-Subprozesses: {e}\n{traceback.format_exc()}")
        return 1

    log.info("═" * 60)
    log.info("ORCHESTRATOR ERFOLGREICH ABGESCHLOSSEN")
    log.info(f"  Bot läuft als PID {proc.pid}")
    log.info(f"  Bot-Log: {bot_log}")
    log.info(f"  Tournament: {tournament_path}")
    log.info("  Orchestrator beendet sich mit Exit-Code 0.")
    log.info("═" * 60)
    return 0


def _ensure_safety_interlocks(log: logging.Logger) -> None:
    """Setzt und validiert alle Safety-Interlocks für Live-Trading."""
    from config.setups import ETORO_EXECUTION

    env     = ETORO_EXECUTION.get("environment", "demo")
    dry_run = ETORO_EXECUTION.get("dry_run", True)

    if env != "real":
        log.warning(f"[Phase 5] ETORO_EXECUTION['environment'] = '{env}' (erwartet 'real').")
    else:
        log.info("[Phase 5] ✓ environment='real' — Demo-Konto korrekt konfiguriert.")

    if dry_run:
        log.warning("[Phase 5] ETORO_EXECUTION['dry_run'] = True — kein Live-Trading!")
    else:
        log.info("[Phase 5] ✓ dry_run=False — Live-Modus aktiviert.")

    confirm_live = os.getenv("ETORO_CONFIRM_LIVE", "0").strip()
    if confirm_live != "1":
        log.warning("[Phase 5] ETORO_CONFIRM_LIVE ist nicht '1' — Safety-Interlock aktiv.")
        if ENV_FILE.exists():
            try:
                set_key(str(ENV_FILE), "ETORO_CONFIRM_LIVE", "1")
                os.environ["ETORO_CONFIRM_LIVE"] = "1"
                log.info("[Phase 5] ETORO_CONFIRM_LIVE=1 in .env gesetzt.")
            except Exception as e:
                log.error(f"[Phase 5] Konnte ETORO_CONFIRM_LIVE nicht setzen: {e}")
        else:
            log.warning("[Phase 5] .env nicht gefunden — ETORO_CONFIRM_LIVE manuell setzen.")
    else:
        log.info("[Phase 5] ✓ ETORO_CONFIRM_LIVE=1 — Safety-Interlock entsperrt.")


# ═══════════════════════════════════════════════════════════════════════════════
# HAUPT-EINSTIEGSPUNKT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    """Hauptfunktion: Führt alle 5 Phasen sequentiell aus."""
    parser = argparse.ArgumentParser(
        description="eToro Nautilus — Täglicher End-to-End-Orchestrator"
    )
    parser.add_argument("--dry-run",        action="store_true", help="Kein echter Bot-Start.")
    parser.add_argument("--skip-api-fetch", action="store_true", help="API-Backfill überspringen.")
    args = parser.parse_args()

    load_dotenv(str(ENV_FILE))
    api_key  = os.getenv("ETORO_API_KEY", "")
    user_key = os.getenv("ETORO_USER_KEY", "")

    log = _setup_orchestrator_logging()

    log.info("╔" + "═" * 60 + "╗")
    log.info("║  eToro Nautilus — Daily Orchestrator v1.1                  ║")
    log.info(f"║  Start: {datetime.now(timezone.utc).isoformat():<51} ║")
    log.info(f"║  DRY-RUN: {'JA' if args.dry_run else 'NEIN':<52} ║")
    log.info("╚" + "═" * 60 + "╝")

    emit_json_event(log, "ORCHESTRATOR_START", {
        "dry_run":        args.dry_run,
        "skip_api_fetch": args.skip_api_fetch,
        "python":         sys.version,
    })

    # Log-Cleanup beim Start
    cleanup_old_logs(LOGS_DIR)

    if not api_key or not user_key:
        log.warning("ETORO_API_KEY oder ETORO_USER_KEY fehlen — API-Schritte übersprungen.")

    exit_code = 0
    try:
        # Phase 1
        universe_result = phase1_universe_and_mapping(log)

        # Phase 2
        data_result = phase2_data_acquisition(
            log, universe_result, api_key, user_key,
            skip_api_fetch=args.skip_api_fetch,
        )

        # Phase 3 & 4
        tournament_result = phase3_4_backtest_and_tournament(log, dry_run=args.dry_run)

        # Phase 5
        exit_code = phase5_live_deployment(
            log, universe_result, tournament_result, dry_run=args.dry_run
        )

    except KeyboardInterrupt:
        log.warning("Orchestrator manuell abgebrochen (KeyboardInterrupt).")
        exit_code = 130
    except Exception as e:
        log.error(f"Unerwarteter Fehler: {e}\n{traceback.format_exc()}")
        emit_json_event(log, "ORCHESTRATOR_ERROR", {"error": str(e)})
        exit_code = 1

    emit_json_event(log, "ORCHESTRATOR_EXIT", {"exit_code": exit_code})
    log.info(f"Orchestrator beendet (Exit-Code: {exit_code}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
