#!/usr/bin/env python3
"""
automation/api_backfiller.py
============================
Standalone API-Backfiller für eToro Nautilus — kein Import aus adapters/.

Ersetzt das alte Gap-Fetch-Skript (ehemals inline in daily_orchestrator.py).

Funktionsweise:
  1. Liest Instrument-IDs und Symbole aus data/universe/momentum_ls.json
  2. Holt price_precision UND size_precision DYNAMISCH via eToro API
     (GET /api/v1/market-data/instruments?instrumentIds=...).
     Fallback: Symbol-basierte Heuristik (kein lokales JSON-Map).
  3. Fragt eToro Candle-History für die letzten N Tage ab.
  4. Konvertiert Candle-Daten DIREKT in PyArrow-Table mit FixedSizeBinary(16)
     — KEIN pandas-Roundtrip, KEIN pandas-Intermediat.
  5. Injiziert Byte-Keys (b"price_precision", b"size_precision", b"instrument_id")
     direkt in den Arrow-Header.
  6. Merged atomar in den bestehenden Katalog (data.parquet).

Verwendung (Standalone-CLI):
  python3 automation/api_backfiller.py [--days 7] [--dry-run]
  python3 automation/api_backfiller.py --symbols BTC.ETORO TSLA.ETORO

Verwendung (als Modul im Orchestrator):
  from automation.api_backfiller import run_backfill
  asyncio.run(run_backfill(api_key, user_key, etoro_id_to_symbol, days=7))

Umgebungsvariablen (via .env):
  ETORO_API_KEY   — API-Key
  ETORO_USER_KEY  — User-Key
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import struct
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv

# ─── Pfade (Standalone, kein sys.path-Hack nötig wenn aus PROJECT_ROOT) ────────
_THIS_DIR    = Path(__file__).resolve().parent
PROJECT_ROOT = _THIS_DIR.parent
CATALOG_PATH     = PROJECT_ROOT / "data" / "nautilus"
QUOTE_TICK_PATH  = CATALOG_PATH / "data" / "quote_tick"
UNIVERSE_PATH    = PROJECT_ROOT / "data" / "universe" / "momentum_ls.json"
ENV_FILE         = PROJECT_ROOT / ".env"

# ─── eToro API ────────────────────────────────────────────────────────────────
_BASE_URL_MARKET = "https://public-api.etoro.com/api/v1/market-data"
_INSTRUMENTS_URL = f"{_BASE_URL_MARKET}/instruments"
_SEARCH_URL      = f"{_BASE_URL_MARKET}/search"
_CANDLES_URL     = (
    f"{_BASE_URL_MARKET}/instruments/{{etoro_id}}/history/candles"
    f"/desc/{{interval}}/{{count}}"
)

# ─── Logging ─────────────────────────────────────────────────────────────────
log = logging.getLogger("api_backfiller")

# ─── Precision-Heuristik (aus automation.utils — kein doppelter Code) ────────
try:
    from automation.utils import _fallback_precisions
except ImportError:
    # Direkter Import wenn automation/ nicht im sys.path ist
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from automation.utils import _fallback_precisions


# ─── FixedSizeBinary(16) Encoding ────────────────────────────────────────────
# Nautilus speichert Price/Quantity als:
#   raw_int64 = round(value * 10^precision)
#   Serialisiert als: 8 Byte Little-Endian int64 + 8 Null-Bytes = 16 Bytes gesamt.
#
# Wichtig: Dies ist das EXAKT gleiche Format das Nautilus' Rust-Backend
# beim Lesen aus dem Parquet-Katalog erwartet.

def _encode_fsb16(value: float, precision: int) -> bytes:
    """Kodiert einen Dezimalwert als Nautilus FixedSizeBinary(16).

    Args:
        value:     Dezimalwert (z.B. Preis oder Menge).
        precision: Anzahl der Nachkommastellen.

    Returns:
        16 Bytes: 8-Byte LE int64 (skalierter Wert) + 8 Null-Bytes.
    """
    raw = round(value * (10 ** precision))
    # int64-Bereich sicherstellen
    raw = max(-(2 ** 63), min(2 ** 63 - 1, raw))
    return struct.pack("<q", raw) + b"\x00" * 8


def _encode_qty_fsb16(qty: float, precision: int) -> bytes:
    """Kodiert eine Menge als FixedSizeBinary(16). Mengen sind immer ≥ 0."""
    raw = round(qty * (10 ** precision))
    raw = max(0, min(2 ** 63 - 1, raw))
    return struct.pack("<q", raw) + b"\x00" * 8


# ─── Dynamische Precision via eToro API ──────────────────────────────────────

async def fetch_precisions_from_api(
    session: aiohttp.ClientSession,
    etoro_ids: list[str],
    api_key: str,
    user_key: str,
) -> dict[str, tuple[int, int]]:
    """Holt price_precision und size_precision DYNAMISCH via eToro API.

    Strategie:
      1. GET /api/v1/market-data/instruments?instrumentIds=...
         Sucht nach Feldern: decimalPlaces, pricePrecision, digits, precision
      2. Falls die API keine Precision-Felder liefert: Fallback auf Heuristik.

    Args:
        session:   aiohttp ClientSession
        etoro_ids: Liste der eToro Instrument-IDs
        api_key:   eToro API-Key
        user_key:  eToro User-Key

    Returns:
        Dict: {etoro_id: (price_precision, size_precision)}
    """
    result: dict[str, tuple[int, int]] = {}
    if not etoro_ids:
        return result

    headers = {
        "x-api-key":    api_key,
        "x-user-key":   user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

    # Batch in Gruppen à 50 aufteilen (API-Limit)
    batch_size = 50
    for i in range(0, len(etoro_ids), batch_size):
        batch = etoro_ids[i : i + batch_size]
        ids_param = ",".join(batch)
        params = {"instrumentIds": ids_param}

        try:
            async with session.get(
                _INSTRUMENTS_URL, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    log.warning(
                        f"[api_backfiller] Instruments-Endpoint HTTP {resp.status} "
                        f"für IDs {ids_param[:80]}… — nutze Fallback."
                    )
                    continue

                raw = await resp.json(content_type=None)
                instruments = raw if isinstance(raw, list) else raw.get("instruments", raw.get("items", []))
                if not isinstance(instruments, list):
                    continue

                for item in instruments:
                    if not isinstance(item, dict):
                        continue
                    eid = str(item.get("instrumentId", item.get("id", "")))
                    if not eid:
                        continue

                    # Preis-Precision: verschiedene mögliche Feldnamen ausprobieren
                    price_prec: int | None = None
                    for field in ("decimalPlaces", "pricePrecision", "priceDecimals", "digits", "precision"):
                        val = item.get(field)
                        if val is not None:
                            try:
                                price_prec = int(val)
                                break
                            except (ValueError, TypeError):
                                pass

                    # Size-Precision: aus Asset-Typ oder dediziertem Feld
                    size_prec: int | None = None
                    for field in ("sizePrecision", "sizeDecimals", "quantityPrecision", "unitPrecision"):
                        val = item.get(field)
                        if val is not None:
                            try:
                                size_prec = int(val)
                                break
                            except (ValueError, TypeError):
                                pass

                    # Instrument-Symbol für Fallback ermitteln
                    sym_raw = item.get("internalSymbolFull", item.get("symbolFull", item.get("symbol", "")))

                    # Fallback wenn API keine Precision-Felder hat
                    fb_price, fb_size = _fallback_precisions(sym_raw)
                    if price_prec is None:
                        price_prec = fb_price
                        log.debug(f"[api_backfiller] ID {eid}: price_precision via Fallback={price_prec}")
                    if size_prec is None:
                        size_prec = fb_size
                        log.debug(f"[api_backfiller] ID {eid}: size_precision via Fallback={size_prec}")

                    result[eid] = (price_prec, size_prec)
                    log.debug(
                        f"[api_backfiller] ID {eid} ({sym_raw}): "
                        f"price_prec={price_prec}, size_prec={size_prec}"
                    )

        except asyncio.TimeoutError:
            log.warning(f"[api_backfiller] Timeout beim Abrufen von IDs {batch} — Fallback.")
        except Exception as e:
            log.warning(f"[api_backfiller] Fehler beim Abrufen von Precisions: {e}")

        await asyncio.sleep(0.5)  # Rate-Limit respektieren

    return result


# ─── Candle-Fetch ─────────────────────────────────────────────────────────────

async def _fetch_candles(
    session: aiohttp.ClientSession,
    etoro_id: str,
    end_time: datetime,
    api_key: str,
    user_key: str,
    interval: str = "OneHour",
    count: int = 168,  # 7 Tage × 24h
) -> list[dict]:
    """Holt historische Candle-Daten für ein Instrument."""
    url = _CANDLES_URL.format(etoro_id=etoro_id, interval=interval, count=count)
    headers = {
        "x-api-key":    api_key,
        "x-user-key":   user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    params = {"endTime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ")}

    for attempt in range(3):
        try:
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    raw = await resp.json(content_type=None)
                    # Verschiedene Response-Strukturen abfangen
                    if isinstance(raw, dict):
                        raw = raw.get("candles") or raw.get("data") or raw
                    if isinstance(raw, list):
                        if raw and isinstance(raw[0], dict):
                            inner = raw[0].get("candles") or raw[0].get("Candles")
                            if inner:
                                return inner
                        return raw
                    return []
                elif resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 30))
                    log.warning(f"[api_backfiller] Rate-Limit für ID {etoro_id} — warte {retry_after}s.")
                    await asyncio.sleep(retry_after)
                else:
                    log.debug(f"[api_backfiller] HTTP {resp.status} für ID {etoro_id}.")
                    return []
        except asyncio.TimeoutError:
            log.warning(f"[api_backfiller] Timeout für ID {etoro_id} (Versuch {attempt + 1}/3).")
            await asyncio.sleep(5 * (attempt + 1))
        except Exception as e:
            log.warning(f"[api_backfiller] Fehler für ID {etoro_id}: {e}")
            await asyncio.sleep(5 * (attempt + 1))

    return []


# ─── Candle → Arrow (FixedSizeBinary(16)) ───────────────────────────────────

def _candles_to_arrow_table(
    candles: list[dict],
    symbol: str,
    price_prec: int,
    size_prec: int,
    start_dt: datetime,
) -> pa.Table | None:
    """Konvertiert Candle-Daten DIREKT in eine PyArrow-Table mit FixedSizeBinary(16).

    KRITISCH: Kein pandas-Roundtrip. Preise und Größen werden sofort als
    FixedSizeBinary(16) enkodiert — das Nautilus Rust-Backend-Format.

    Args:
        candles:    Liste der Candle-Dicts (low/high/date Felder)
        symbol:     Nautilus-Symbol-String (z.B. "TSLA.ETORO")
        price_prec: Anzahl Nachkommastellen für Preise
        size_prec:  Anzahl Nachkommastellen für Mengen
        start_dt:   Frühestes Datum (älter werden ignoriert)

    Returns:
        PyArrow-Table mit FSB(16)-Spalten oder None bei leeren Daten.
    """
    _FSB16 = pa.binary(16)

    bid_prices: list[bytes] = []
    ask_prices: list[bytes] = []
    bid_sizes:  list[bytes] = []
    ask_sizes:  list[bytes] = []
    ts_events:  list[int]   = []
    ts_inits:   list[int]   = []

    # Menge = 1 Lot (Candle-Daten haben keine separaten Volumen-Spalten)
    size_bytes = _encode_qty_fsb16(1.0, size_prec)

    for c in candles:
        try:
            # Feld-Namen normalisieren (eToro gibt manchmal camelCase, manchmal lowercase)
            c_low = {k.lower(): v for k, v in c.items()}
            date_val = (
                c_low.get("fromdate")
                or c_low.get("startdate")
                or c_low.get("date")
                or c_low.get("timestamp")
            )
            low_val  = c_low.get("low")  or c_low.get("l")
            high_val = c_low.get("high") or c_low.get("h")

            if not date_val or low_val is None or high_val is None:
                continue

            low  = float(low_val)
            high = float(high_val)

            if low <= 0 or high <= 0:
                continue

            # Timestamp parsen
            if isinstance(date_val, (int, float)):
                ts_ns = int(date_val * 1e9) if date_val < 1e13 else int(date_val)
            else:
                ts_str = str(date_val).replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts_ns = int(dt.timestamp() * 1e9)

            # Zu alten Eintrag überspringen
            min_ts_ns = int(start_dt.timestamp() * 1e9)
            if ts_ns < min_ts_ns:
                continue

            # Direkt als FSB(16) enkodieren — KEIN Umweg über strings/bytes
            bid_prices.append(_encode_fsb16(low,  price_prec))
            ask_prices.append(_encode_fsb16(high, price_prec))
            bid_sizes.append(size_bytes)
            ask_sizes.append(size_bytes)
            ts_events.append(ts_ns)
            ts_inits.append(ts_ns)

        except Exception as e:
            log.debug(f"[api_backfiller] Candle-Parse-Fehler ({symbol}): {e}")
            continue

    if not ts_events:
        return None

    # PyArrow-Table mit korrektem Schema erstellen
    schema = pa.schema([
        pa.field("bid_price", _FSB16),
        pa.field("ask_price", _FSB16),
        pa.field("bid_size",  _FSB16),
        pa.field("ask_size",  _FSB16),
        pa.field("ts_event",  pa.uint64()),
        pa.field("ts_init",   pa.uint64()),
    ])

    table = pa.table(
        {
            "bid_price": pa.array(bid_prices, type=_FSB16),
            "ask_price": pa.array(ask_prices, type=_FSB16),
            "bid_size":  pa.array(bid_sizes,  type=_FSB16),
            "ask_size":  pa.array(ask_sizes,  type=_FSB16),
            "ts_event":  pa.array(ts_events,  type=pa.uint64()),
            "ts_init":   pa.array(ts_inits,   type=pa.uint64()),
        },
        schema=schema,
    )
    return table


# ─── Metadaten-Builder ────────────────────────────────────────────────────────

def _build_arrow_meta(symbol: str, price_prec: int, size_prec: int) -> dict[bytes, bytes]:
    """Erstellt Nautilus-konforme Arrow-Schema-Metadaten.

    Byte-Keys sind PFLICHT für Nautilus Rust-Backend:
      b"price_precision" → z.B. b"5"
      b"size_precision"  → z.B. b"8"
      b"instrument_id"   → z.B. b"BTC.ETORO"
    """
    if size_prec is None or size_prec <= 0:
        size_prec = 8

    return {
        b"price_precision": str(price_prec).encode(),
        b"size_precision":  str(size_prec).encode(),
        b"instrument_id":   symbol.encode(),
    }


# ─── Parquet Merge ────────────────────────────────────────────────────────────

def _get_latest_ts(parquet_file: Path) -> int | None:
    """Gibt den neuesten ts_event-Wert einer Parquet-Datei zurück."""
    try:
        t = pq.read_table(str(parquet_file), columns=["ts_event"])
        if len(t) == 0:
            return None
        return int(t.column("ts_event").to_pylist()[-1])
    except Exception:
        return None


def _merge_and_save(
    log_ctx: logging.Logger,
    new_table: pa.Table,
    symbol: str,
    price_prec: int,
    size_prec: int,
) -> bool:
    """Merged neue Daten mit bestehendem Parquet-Katalog und speichert atomar.

    Merge-Strategie:
      1. Bestehende data.parquet lesen (falls vorhanden)
      2. Neue Tabelle per pa.concat_tables anhängen
      3. Nach ts_event deduplizieren und sortieren
      4. Metadaten injizieren
      5. Atomar als data.parquet speichern

    Args:
        log_ctx:    Logger
        new_table:  Neue PyArrow-Table (bereits FSB(16))
        symbol:     Nautilus-Symbol
        price_prec: Preis-Precision
        size_prec:  Größen-Precision

    Returns:
        True bei Erfolg.
    """
    dest_dir  = QUOTE_TICK_PATH / symbol
    dest_file = dest_dir / "data.parquet"
    dest_dir.mkdir(parents=True, exist_ok=True)

    tables: list[pa.Table] = []

    # 1. Bestehende Datei einlesen
    if dest_file.exists():
        try:
            existing = pq.read_table(str(dest_file))
            if len(existing) > 0:
                tables.append(existing)
        except Exception as e:
            log_ctx.warning(f"[api_backfiller] Bestehende Datei {symbol} konnte nicht gelesen werden: {e}")

    tables.append(new_table)

    # 2. Concatenieren
    try:
        merged = pa.concat_tables(tables, promote_options="default")
    except Exception as e:
        log_ctx.error(f"[api_backfiller] concat_tables Fehler ({symbol}): {e}")
        return False

    # 3. Deduplizieren und sortieren nach ts_event
    rows_before = len(merged)
    ts_list = merged.column("ts_event").to_pylist()

    seen: set[int] = set()
    keep_indices: list[int] = []
    for i, ts in enumerate(ts_list):
        if ts not in seen:
            seen.add(ts)
            keep_indices.append(i)

    # Sortieren nach ts_event
    keep_indices.sort(key=lambda i: ts_list[i])
    merged = merged.take(pa.array(keep_indices))
    rows_after = len(merged)

    log_ctx.debug(
        f"[api_backfiller] {symbol}: {rows_before}→{rows_after} Zeilen "
        f"(-{rows_before - rows_after} Duplikate)"
    )

    # 4. Metadaten injizieren
    meta = _build_arrow_meta(symbol, price_prec, size_prec)
    merged = merged.replace_schema_metadata(meta)

    # 5. Atomar speichern
    tmp = dest_file.with_suffix(".tmp.parquet")
    try:
        pq.write_table(merged, str(tmp), compression="snappy")
        tmp.rename(dest_file)
        log_ctx.info(
            f"[api_backfiller] {symbol}: {rows_after} Zeilen gespeichert "
            f"(price_prec={price_prec}, size_prec={size_prec}) → {dest_file.name}"
        )
        return True
    except Exception as e:
        log_ctx.error(f"[api_backfiller] Schreib-Fehler {symbol}: {e}")
        tmp.unlink(missing_ok=True)
        return False


# ─── Hauptlogik ───────────────────────────────────────────────────────────────

async def run_backfill(
    api_key: str,
    user_key: str,
    etoro_id_to_symbol: dict[str, str],
    days: int = 7,
    dry_run: bool = False,
    specific_symbols: set[str] | None = None,
) -> list[str]:
    """Backfill-Hauptlogik. Kann als Modul aus dem Orchestrator aufgerufen werden.

    Args:
        api_key:              eToro API-Key
        user_key:             eToro User-Key
        etoro_id_to_symbol:   Dict {etoro_id: nautilus_symbol}
        days:                 Anzahl Tage zurück (Standard: 7)
        dry_run:              Wenn True, kein Schreiben
        specific_symbols:     Wenn gesetzt, nur diese Symbole befüllen

    Returns:
        Liste der erfolgreich backgefüllten Symbole.
    """
    if not api_key or not user_key:
        log.warning("[api_backfiller] API-Keys fehlen — Backfill übersprungen.")
        return []

    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    etoro_ids = list(etoro_id_to_symbol.keys())

    log.info(
        f"[api_backfiller] Starte Backfill: {start_dt.date()} → {end_dt.date()} "
        f"| {len(etoro_ids)} Instrumente"
    )

    timeout = aiohttp.ClientTimeout(total=60)
    filled: list[str] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # ── Schritt 1: Precisions dynamisch via API holen ────────────────────
        log.info("[api_backfiller] Lade Instrument-Precisions via eToro API …")
        api_precisions = await fetch_precisions_from_api(
            session, etoro_ids, api_key, user_key
        )
        log.info(
            f"[api_backfiller] Precisions via API: {len(api_precisions)}/{len(etoro_ids)} Instrumente."
        )

        # ── Schritt 2: Pro Instrument Candles fetchen und speichern ─────────
        for etoro_id, symbol in sorted(etoro_id_to_symbol.items(), key=lambda x: x[1]):
            # Filter auf spezifische Symbole
            if specific_symbols and symbol not in specific_symbols:
                continue

            # Prüfen ob Daten aktuell genug sind (Lücke < 1h → überspringen)
            dest_file = QUOTE_TICK_PATH / symbol / "data.parquet"
            if dest_file.exists():
                latest_ts = _get_latest_ts(dest_file)
                if latest_ts is not None:
                    gap_h = (end_dt.timestamp() - latest_ts / 1e9) / 3600
                    if gap_h < 1.0:
                        log.debug(f"[api_backfiller] {symbol}: Daten aktuell (Lücke {gap_h:.1f}h) — überspringe.")
                        continue

            # Precisions ermitteln (API → Fallback)
            if etoro_id in api_precisions:
                price_prec, size_prec = api_precisions[etoro_id]
            else:
                price_prec, size_prec = _fallback_precisions(symbol)
                log.debug(
                    f"[api_backfiller] {symbol}: Precision-Fallback "
                    f"price_prec={price_prec}, size_prec={size_prec}"
                )

            try:
                # Candles abrufen
                candles = await _fetch_candles(session, etoro_id, end_dt, api_key, user_key)
                if not candles:
                    log.debug(f"[api_backfiller] {symbol}: Keine Candles — überspringe.")
                    await asyncio.sleep(0.5)
                    continue

                # Direkt in Arrow-Table mit FSB(16) konvertieren
                table = _candles_to_arrow_table(
                    candles, symbol, price_prec, size_prec, start_dt
                )
                if table is None or len(table) == 0:
                    log.debug(f"[api_backfiller] {symbol}: Leere Table nach Konvertierung.")
                    await asyncio.sleep(0.5)
                    continue

                log.info(
                    f"[api_backfiller] {symbol}: {len(table)} Candles konvertiert "
                    f"(price_prec={price_prec}, size_prec={size_prec})."
                )

                if dry_run:
                    log.info(f"[api_backfiller] DRY-RUN: {symbol} würde gespeichert werden.")
                    filled.append(symbol)
                else:
                    if _merge_and_save(log, table, symbol, price_prec, size_prec):
                        filled.append(symbol)

                await asyncio.sleep(1.1)  # Rate-Limit respektieren

            except Exception as e:
                log.warning(
                    f"[api_backfiller] Fehler für {symbol} (ID {etoro_id}): {e}\n"
                    f"{traceback.format_exc()}"
                )
                await asyncio.sleep(2)

    log.info(f"[api_backfiller] Backfill abgeschlossen: {len(filled)} Symbole befüllt.")
    return filled


# ─── Universe Loader (Standalone, kein adapters-Import) ──────────────────────

def _load_etoro_id_map(universe_path: Path) -> dict[str, str]:
    """Lädt die eToro-ID → Nautilus-Symbol-Map aus der Universe-Datei.

    Returns:
        Dict {etoro_id: nautilus_symbol}, z.B. {"1111": "TSLA.ETORO"}
    """
    if not universe_path.exists():
        log.warning(f"[api_backfiller] Universe-Datei nicht gefunden: {universe_path}")
        return {}

    try:
        with open(universe_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"[api_backfiller] Fehler beim Lesen der Universe-Datei: {e}")
        return {}

    result: dict[str, str] = {}
    for item in data.get("universe", []):
        eid    = str(item.get("etoro_id", "")).strip()
        symbol = str(item.get("symbol", "")).strip()
        if eid and symbol and symbol != "None":
            result[eid] = symbol

    log.info(f"[api_backfiller] Universe geladen: {len(result)} Instrumente.")
    return result


# ─── CLI Entry-Point ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="eToro Nautilus API Backfiller (Standalone)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--days",         type=int, default=7,      help="Anzahl Tage zurück (Standard: 7)")
    parser.add_argument("--dry-run",      action="store_true",       help="Kein Schreiben, nur Ausgabe")
    parser.add_argument(
        "--symbols", nargs="*", default=None,
        help="Nur diese Symbole backfüllen (z.B. BTC.ETORO TSLA.ETORO)"
    )
    parser.add_argument(
        "--universe", type=Path, default=UNIVERSE_PATH,
        help=f"Pfad zur Universe-JSON-Datei (Standard: {UNIVERSE_PATH})"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    # ── Pflicht-Verzeichnisse vorab anlegen (I/O-Laufzeitfehler verhindern) ──
    QUOTE_TICK_PATH.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data" / "state").mkdir(parents=True, exist_ok=True)

    load_dotenv(str(ENV_FILE))
    api_key  = os.getenv("ETORO_API_KEY",  "")
    user_key = os.getenv("ETORO_USER_KEY", "")

    if not api_key or not user_key:
        if args.dry_run:
            # Im Dry-Run-Modus sind API-Keys optional — Universe-Validierung genügt
            log.warning(
                "[api_backfiller] ETORO_API_KEY oder ETORO_USER_KEY fehlen — "
                "Dry-Run ohne API-Aufruf."
            )
        else:
            log.error("[api_backfiller] ETORO_API_KEY oder ETORO_USER_KEY fehlen in .env — Abbruch.")
            return 1

    etoro_id_map = _load_etoro_id_map(Path(args.universe))
    if not etoro_id_map:
        log.error("[api_backfiller] Keine Instrumente im Universe — Abbruch.")
        return 1

    specific_symbols = set(args.symbols) if args.symbols else None

    # Dry-Run ohne API-Keys: Liste der Symbole ausgeben und exit
    if args.dry_run and (not api_key or not user_key):
        log.info(
            f"[api_backfiller] DRY-RUN (no API keys): "
            f"{len(etoro_id_map)} Symbole im Universe würden backgefüllt."
        )
        for eid, sym in sorted(etoro_id_map.items(), key=lambda x: x[1]):
            pp, sp = _fallback_precisions(sym)
            log.info(f"  {sym} (ID={eid}): price_prec={pp}, size_prec={sp}")
        return 0

    filled = asyncio.run(
        run_backfill(
            api_key=api_key,
            user_key=user_key,
            etoro_id_to_symbol=etoro_id_map,
            days=args.days,
            dry_run=args.dry_run,
            specific_symbols=specific_symbols,
        )
    )

    log.info(f"[api_backfiller] Fertig. {len(filled)} Symbole befüllt: {filled}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
