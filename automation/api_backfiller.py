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

# ─── Bar-Achse: Auflösung → Nanosekunden, Katalog-Schema-Version (Issue #1330-#1333,
# GH #1224-#1227) ────────────────────────────────────────────────────────────
# Issue #1331 (GH #1225): jede Auflösung braucht einen expliziten Nanosekunden-Wert, der
# als `bar_interval_ns`-Spalte je Zeile mitgeschrieben wird — sonst ist die Auflösung eines
# Ticks ab dem Moment des Schreibens nicht mehr rekonstruierbar (ausser heuristisch über Δt).
INTERVAL_TO_NS: dict[str, int] = {
    "OneHour": 3_600_000_000_000,
    "OneDay": 86_400_000_000_000,
}
DEFAULT_INTERVAL = "OneHour"

# Issue #1333 (GH #1227): Version 1 = Legacy (1 Tick/Kerze, gemischte Auflösung, Preis auf
# Kerzenbeginn gestempelt). Version 2 = nach #1330/#1331/#1332 (O/L/H/C-Tick-Expansion,
# Auflösungs-Trennung, korrekte Close-Zeitstempel-Semantik, bar_interval_ns-Spalte).
CATALOG_SCHEMA_VERSION = 2

# Issue #1330 (GH #1224): der aus O/H/L/C synthetisierte Tick-Pfad ist eine Modellannahme,
# keine Beobachtung. Jede spätere Aussage über Stop-Mechanik zitiert dieses Feld (#1350/GH #1244).
INTRABAR_PATH_SYNTHETIC = "synthetic_ohlc_adverse_first"
INTRABAR_PATH_OBSERVED = "observed"

# Issue #1330 (GH #1224) Fix Punkt 2: deterministische, monoton steigende, kollisionsfreie
# Sub-Intervall-Offsets als Konstante im Modul, kein Literal in der Schleife. Die
# Trigger-Reihenfolge ist FEST und UNBEDINGT (Sperrvermerk #7 in Issue #1246): das adverse
# Extrem (`low`, für eine Long-Betrachtung) kommt vor dem günstigen (`high`) — unabhängig von
# `close > open`, das würde die Stop-Statistik systematisch beschönigen.
_INTRABAR_OFFSET_OPEN_FRAC = 0.0
_INTRABAR_OFFSET_LOW_FRAC = 0.25
_INTRABAR_OFFSET_HIGH_FRAC = 0.50
# Der Close-Tick wird an den letzten darstellbaren Zeitpunkt des Intervalls gestempelt
# (candle_end - 1ns), nicht an eine Δ-Fraktion — Issue #1332/GH #1226: ein Preis gehört an
# den Zeitpunkt, an dem er bekannt wird, nicht an den Beginn seines Intervalls.


class CatalogSchemaVersionMismatch(RuntimeError):
    """Issue #1333 (GH #1227): _merge_and_save bricht LAUT ab, wenn die Zielversion von der
    Version der bestehenden Datei abweicht — kein stiller Merge über eine Schemagrenze hinweg."""

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
def _encode_fsb16(value: float, precision: int) -> bytes:
    """Kodiert Preis/Menge als Nautilus FixedSizeBinary(16) (High-Precision i128)."""
    raw = int(value * 10**16)
    return raw.to_bytes(16, "little", signed=True)

def _encode_qty_fsb16(qty: float, precision: int) -> bytes:
    """Kodiert eine Menge als FixedSizeBinary(16) (High-Precision i128)."""
    raw = int(qty * 10**16)
    return raw.to_bytes(16, "little", signed=True)


# ─── Dynamische Precision via eToro API ──────────────────────────────────────

async def fetch_precisions_from_api(
    session: aiohttp.ClientSession,
    etoro_ids: list[str],
    api_key: str,
    user_key: str,
) -> dict[str, tuple[int, int]]:
    """Holt price_precision und size_precision DYNAMISCH via eToro API."""
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
    api_hits = 0
    missing_count = 0

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
                log.debug(f"[api_backfiller] Raw API response (first 500 chars): {str(raw)[:500]}")
                instruments = raw if isinstance(raw, list) else raw.get("instrumentDisplayDatas", raw.get("instruments", raw.get("items", [])))
                if not isinstance(instruments, list):
                    continue

                for item in instruments:
                    if not isinstance(item, dict):
                        continue
                    eid = str(item.get("instrumentID", item.get("instrumentId", item.get("id", ""))))
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

                    # Plausibilitätsprüfung (Sanity Check) für Aktien
                    if size_prec == 2:
                        # Falls size_prec=2 ist, sollte es sich laut Fallback-Regeln um ein reines Equity handeln.
                        # Wenn wir es als Crypto oder Fractional identifizieren, ist das vermutlich falsch (Precision Mismatch).
                        fb_p_test, fb_s_test = _fallback_precisions(str(sym_raw))
                        if fb_s_test != 2:
                            error_msg = (
                                f"[api_backfiller] Plausibilitäts-Fehler: Instrument {eid} ({sym_raw}) hat "
                                f"size_prec=2 (Equity-Wert), wird systemseitig aber als Nicht-Equity mit "
                                f"size_prec={fb_s_test} erwartet."
                            )
                            log.error(error_msg)
                            if os.getenv("STRICT_PRECISION_FAIL") == "1":
                                raise RuntimeError(error_msg)
                            continue # Überspringe dieses Instrument bei Mismatch

                    fb_price, fb_size = _fallback_precisions(str(sym_raw))

                    # Issue #171: Fehlende API-Precision wird für das vorvalidierte,
                    # vertrauenswürdige Universe (momentum_ls.json) über die Symbol-Heuristik
                    # aufgefüllt — KEIN Hard-Reject mehr. Der frühere (2,2)-Drop (ERROR +
                    # continue) warf gültige Standard-Equities (TSLA, GOOG, NVDA) aus dem
                    # Backfill und flutete die Phase-2-Logs des Orchestrators.
                    if price_prec is None or size_prec is None:
                        if fb_size == 2 and fb_price == 2:
                            # (2,2) ist die korrekte Precision für Equities.
                            # API liefert derzeit keine Precision-Felder; Fallback in run_backfill() greift.
                            log.debug(
                                f"[api_backfiller] Keine API-Precision für ID {eid} ({sym_raw}). "
                                f"Equity-Fallback (2,2) wird von run_backfill() angewendet."
                            )
                            # Hinweis: Struktur nach Feldern wie leverageList[0].maxLeverage oder tradingData.priceStep untersuchen
                            if missing_count < 3:
                                log.debug(f"Vollständiger Item-Dump: {json.dumps(item, indent=2)}")
                                missing_count += 1
                            continue

                        if price_prec is None:
                            price_prec = fb_price
                            log.debug(f"[api_backfiller] ID {eid}: price_precision via historischem Fallback={price_prec}")
                        if size_prec is None:
                            size_prec = fb_size
                            log.debug(f"[api_backfiller] ID {eid}: size_precision via historischem Fallback={size_prec}")

                    # Wenn wir hier ankommen, haben wir entweder die API-Werte oder
                    # erfolgreiche Fallbacks für das Trusted Universe. Es gilt als "Hit".
                    api_hits += 1

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

    fallback_count = len(result) - api_hits
    equity_fallback_count = len(etoro_ids) - len(result)

    if api_hits == 0 and len(etoro_ids) > 0:
        if equity_fallback_count == len(etoro_ids):
            log.debug(
                f"[api_backfiller] Precision-API lieferte keine Felder "
                f"(0 von {len(etoro_ids)} Instrumenten), aber alle wurden als Equities abgefangen. "
                f"Dies ist das erwartete Verhalten."
            )
        else:
            log.warning(
                f"[api_backfiller] Precision-API lieferte keine Felder "
                f"(0 von {len(etoro_ids)} Instrumenten). API-Endpunkt oder Response-Format "
                f"möglicherweise geändert."
            )

    if len(etoro_ids) > 0 and api_hits < len(etoro_ids):
        if os.getenv("STRICT_PRECISION_FAIL") == "1":
            raise RuntimeError(f"[api_backfiller] HARD FAIL: Partielle oder keine Precisions geliefert ({api_hits}/{len(etoro_ids)}).")

    log.info(
        f"[api_backfiller] Precision-Auflösung abgeschlossen: "
        f"{api_hits} direkt via API, "
        f"{fallback_count} via Symbol-Fallback (_fallback_precisions), "
        f"{equity_fallback_count} Equities erhalten (2,2) in run_backfill()."
    )

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
    interval: str = DEFAULT_INTERVAL,
) -> pa.Table | None:
    """Konvertiert Candle-Daten DIREKT in eine PyArrow-Table mit FixedSizeBinary(16).

    Issue #1330 (GH #1224): schreibt je Kerze eine geordnete O/L/H/C-Tick-Sequenz statt eines
    Einzeltickers auf dem Close — sonst trägt jede resamplete Bar keine Intrabar-Information
    (`high == low == close`, `ticks_per_bar_median == 1`), und die Risikoschicht ist unbeurteilbar.
    Issue #1331 (GH #1225): jede Zeile trägt die deklarierte Auflösung in `bar_interval_ns`.
    Issue #1332 (GH #1226): der Close-Tick wird an `candle_end - 1ns` gestempelt (dem Zeitpunkt,
    an dem der Schlusskurs bekannt wird), nicht am Kerzenbeginn — sonst entsteht Look-Ahead.
    Issue #1335 (GH #1229): Volumen wird, falls vorhanden, real aus der Payload gelesen und
    gleichmässig auf die Ticks einer Kerze verteilt, statt eines konstanten Platzhalters 1.0.
    """
    _FSB16 = pa.binary(16)
    interval_ns = INTERVAL_TO_NS.get(interval)
    if interval_ns is None:
        raise ValueError(
            f"[api_backfiller] Unbekanntes Intervall '{interval}' — INTERVAL_TO_NS erweitern."
        )

    bid_prices: list[bytes] = []
    ask_prices: list[bytes] = []
    bid_sizes:  list[bytes] = []
    ask_sizes:  list[bytes] = []
    ts_events:  list[int]   = []
    ts_inits:   list[int]   = []
    bar_interval_col: list[int] = []

    _ZERO_SIZE = _encode_qty_fsb16(0.0, size_prec)
    min_ts_ns = int(start_dt.timestamp() * 1e9)

    # ── Pass 1: parsen, plausibilisieren, chronologisch sortieren ─────────────
    # Die eToro-API liefert Kerzen `desc` (jüngste zuerst); der open-Fallback ("Close der
    # Vorgängerkerze", Fix Punkt 1) braucht chronologisch aufsteigende Reihenfolge.
    parsed: list[tuple[int, float | None, float, float, float, float | None]] = []
    for c in candles:
        try:
            c_low = {k.lower(): v for k, v in c.items()}
            date_val = (
                c_low.get("fromdate")
                or c_low.get("startdate")
                or c_low.get("date")
                or c_low.get("timestamp")
            )
            open_val   = c_low.get("open")   or c_low.get("o")
            low_val    = c_low.get("low")    or c_low.get("l")
            high_val   = c_low.get("high")   or c_low.get("h")
            close_val  = c_low.get("close")  or c_low.get("c")
            volume_val = c_low.get("volume") or c_low.get("v")

            if not date_val or low_val is None or high_val is None or close_val is None:
                continue

            low   = float(low_val)
            high  = float(high_val)
            close = float(close_val)
            if low <= 0 or high <= 0 or close <= 0:
                continue
            open_ = float(open_val) if open_val is not None and float(open_val) > 0 else None
            volume = float(volume_val) if volume_val is not None else None

            # Timestamp parsen (Kerzen-BEGINN)
            if isinstance(date_val, (int, float)):
                ts_ns = int(date_val * 1e9) if date_val < 1e13 else int(date_val)
            else:
                ts_str = str(date_val).replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts_ns = int(dt.timestamp() * 1e9)

            if ts_ns < min_ts_ns:
                continue

            parsed.append((ts_ns, open_, low, high, close, volume))
        except Exception as e:
            log.debug(f"[api_backfiller] Candle-Parse-Fehler ({symbol}): {e}")
            continue

    if not parsed:
        return None

    parsed.sort(key=lambda row: row[0])

    volume_seen_any = False
    volume_missing_any = False
    prev_close: float | None = None

    for candle_start_ns, open_val, low, high, close, volume in parsed:
        if open_val is None:
            open_val = prev_close
        candle_end_ns = candle_start_ns + interval_ns

        # Geordnete Tick-Sequenz: O (falls verfügbar) → adverses Extrem (low) → günstiges
        # Extrem (high) → close zuletzt. Reihenfolge ist FEST, nicht richtungsabhängig.
        roles: list[tuple[float, int]] = []
        if open_val is not None:
            roles.append((open_val, candle_start_ns + int(interval_ns * _INTRABAR_OFFSET_OPEN_FRAC)))
        roles.append((low,  candle_start_ns + int(interval_ns * _INTRABAR_OFFSET_LOW_FRAC)))
        roles.append((high, candle_start_ns + int(interval_ns * _INTRABAR_OFFSET_HIGH_FRAC)))
        roles.append((close, candle_end_ns - 1))

        if volume is not None:
            volume_seen_any = True
            size_bytes_for_row = _encode_qty_fsb16(volume / len(roles), size_prec)
        else:
            volume_missing_any = True
            size_bytes_for_row = _ZERO_SIZE

        for price, ts_ns in roles:
            bid_prices.append(_encode_fsb16(price, price_prec))
            ask_prices.append(_encode_fsb16(price, price_prec))
            bid_sizes.append(size_bytes_for_row)
            ask_sizes.append(size_bytes_for_row)
            ts_events.append(ts_ns)
            ts_inits.append(ts_ns)
            bar_interval_col.append(interval_ns)

        prev_close = close

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
        pa.field("bar_interval_ns", pa.uint64()),
    ])

    table = pa.table(
        {
            "bid_price": pa.array(bid_prices, type=_FSB16),
            "ask_price": pa.array(ask_prices, type=_FSB16),
            "bid_size":  pa.array(bid_sizes,  type=_FSB16),
            "ask_size":  pa.array(ask_sizes,  type=_FSB16),
            "ts_event":  pa.array(ts_events,  type=pa.uint64()),
            "ts_init":   pa.array(ts_inits,   type=pa.uint64()),
            "bar_interval_ns": pa.array(bar_interval_col, type=pa.uint64()),
        },
        schema=schema,
    )

    # Issue #1330 Fix Punkt 3 / #1335 Fix Punkt 3: Modellannahme- und Volumen-Herkunft als
    # vorläufige Schema-Metadaten mitgeben — `_merge_and_save`/`_build_arrow_meta` übernehmen
    # sie in die endgültigen Katalog-Metadaten (dort werden `replace_schema_metadata`-Aufrufe
    # sonst diese Felder überschreiben).
    volume_available = volume_seen_any and not volume_missing_any
    table = table.replace_schema_metadata({
        b"intrabar_path": INTRABAR_PATH_SYNTHETIC.encode(),
        b"volume_available": (b"true" if volume_available else b"false"),
        b"catalog_interval": interval.encode(),
    })
    return table


# ─── Metadaten-Builder ────────────────────────────────────────────────────────

def _build_arrow_meta(
    symbol: str,
    price_prec: int,
    size_prec: int,
    *,
    catalog_schema_version: int = CATALOG_SCHEMA_VERSION,
    interval: str = DEFAULT_INTERVAL,
    extra: dict[bytes, bytes] | None = None,
) -> dict[bytes, bytes]:
    """Erstellt Nautilus-konforme Arrow-Schema-Metadaten.

    Issue #1333 (GH #1227): trägt `catalog_schema_version`, damit `_merge_and_save` einen
    Merge über eine Schemagrenze hinweg laut ablehnt statt still zu vermischen.
    Issue #1331 (GH #1225): trägt die deklarierte Auflösung (`catalog_interval`) als
    Katalog-Metadatum, ergänzend zur `bar_interval_ns`-Spalte je Zeile.
    """
    if size_prec is None or size_prec <= 0:
        size_prec = 2

    meta: dict[bytes, bytes] = {
        b"price_precision": str(price_prec).encode(),
        b"size_precision":  str(size_prec).encode(),
        b"instrument_id":   symbol.encode(),
        b"catalog_schema_version": str(catalog_schema_version).encode(),
        b"catalog_interval": interval.encode(),
    }
    if extra:
        meta.update(extra)
    return meta


def _read_catalog_schema_version(parquet_file: Path) -> int | None:
    """Liest `catalog_schema_version` aus den Arrow-Schema-Metadaten einer Katalogdatei.

    `None`, wenn die Datei fehlt, nicht lesbar ist, oder das Feld fehlt (Alt-Katalog vor
    Issue #1333/GH #1227 — wird vom Aufrufer als Version 1 / Legacy behandelt)."""
    try:
        schema = pq.read_schema(str(parquet_file))
    except Exception:
        return None
    meta = schema.metadata or {}
    raw = meta.get(b"catalog_schema_version")
    if raw is None:
        return None
    try:
        return int(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return None


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
    interval: str = DEFAULT_INTERVAL,
) -> bool:
    """Merged neue Daten mit bestehendem Parquet-Katalog und speichert atomar.

    Issue #1331 (GH #1225): Zielpfad ist je Auflösung getrennt
    (`.../quote_tick/<symbol>/<interval>/data.parquet`).
    Issue #1333 (GH #1227): bricht LAUT ab (`CatalogSchemaVersionMismatch`), wenn eine
    bestehende Datei eine andere `catalog_schema_version` trägt als der aktuelle Schreiber —
    kein stiller Merge über eine Schemagrenze hinweg. Die Dedup-Regel ist **letzte-Zeile-
    gewinnt** (Schlüssel `(ts_event, bar_interval_ns)`), nicht mehr erste-Zeile-gewinnt: eine
    Korrektur muss einen Altbestand überschreiben können.
    """
    dest_dir  = QUOTE_TICK_PATH / symbol / interval
    dest_file = dest_dir / "data.parquet"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Vorab-Metadaten aus dem frisch konvertierten new_table übernehmen (Issue #1330 Fix
    # Punkt 3 / #1335 Fix Punkt 3): intrabar_path und volume_available überleben den
    # replace_schema_metadata-Aufruf am Ende nur, wenn sie hier explizit weitergereicht werden.
    new_meta = new_table.schema.metadata or {}
    extra_meta = {
        k: v for k, v in new_meta.items()
        if k in (b"intrabar_path", b"volume_available")
    }

    tables: list[pa.Table] = []

    # 1. Bestehende Datei einlesen — Schema-Version-Gate zuerst (Issue #1333)
    if dest_file.exists():
        existing_version = _read_catalog_schema_version(dest_file)
        if existing_version != CATALOG_SCHEMA_VERSION:
            raise CatalogSchemaVersionMismatch(
                f"[api_backfiller] {symbol}/{interval}: bestehender Katalog hat "
                f"catalog_schema_version={existing_version!r}, Schreiber erwartet "
                f"{CATALOG_SCHEMA_VERSION}. Kein stiller Merge über eine Schemagrenze hinweg — "
                f"Katalog neu aufbauen: "
                f"`python3 automation/historical_fetcher.py --rebuild-catalog {symbol}`."
            )
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

    # 3. Deduplizieren (letzte Zeile je (ts_event, bar_interval_ns) gewinnt) und sortieren
    rows_before = len(merged)
    ts_list = merged.column("ts_event").to_pylist()
    interval_list = merged.column("bar_interval_ns").to_pylist()

    last_index_for_key: dict[tuple[int, int], int] = {}
    for i, key in enumerate(zip(ts_list, interval_list)):
        last_index_for_key[key] = i  # spätere Zeile überschreibt frühere (Issue #1333 Fix Punkt 4)

    keep_indices = sorted(last_index_for_key.values(), key=lambda i: ts_list[i])
    merged = merged.take(pa.array(keep_indices))
    rows_after = len(merged)

    log_ctx.debug(
        f"[api_backfiller] {symbol}: {rows_before}→{rows_after} Zeilen "
        f"(-{rows_before - rows_after} Duplikate)"
    )

    # 4. Metadaten injizieren
    meta = _build_arrow_meta(symbol, price_prec, size_prec, interval=interval, extra=extra_meta)
    merged = merged.replace_schema_metadata(meta)

    # 5. Atomar speichern
    tmp = dest_file.with_suffix(".tmp.parquet")
    try:
        pq.write_table(merged, str(tmp), compression="snappy")
        tmp.rename(dest_file)
        log_ctx.info(
            f"[api_backfiller] {symbol}: {rows_after} Zeilen gespeichert "
            f"(price_prec={price_prec}, size_prec={size_prec}) → {dest_file}"
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
    """Backfill-Hauptlogik."""
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
        log.info("[api_backfiller] Lade Instrument-Precisions via eToro API …")
        api_precisions = await fetch_precisions_from_api(
            session, etoro_ids, api_key, user_key
        )

        for etoro_id, symbol in sorted(etoro_id_to_symbol.items(), key=lambda x: x[1]):
            if specific_symbols and symbol not in specific_symbols:
                continue

            dest_file = QUOTE_TICK_PATH / symbol / DEFAULT_INTERVAL / "data.parquet"
            if dest_file.exists():
                latest_ts = _get_latest_ts(dest_file)
                if latest_ts is not None:
                    gap_h = (end_dt.timestamp() - latest_ts / 1e9) / 3600
                    if gap_h < 1.0:
                        log.debug(f"[api_backfiller] {symbol}: Daten aktuell (Lücke {gap_h:.1f}h) — überspringe.")
                        continue

            if etoro_id in api_precisions:
                price_prec, size_prec = api_precisions[etoro_id]
            else:
                price_prec, size_prec = _fallback_precisions(symbol or "")
                log.debug(
                    f"[api_backfiller] {symbol}: Precision-Fallback "
                    f"price_prec={price_prec}, size_prec={size_prec}"
                )

            try:
                candles = await _fetch_candles(session, etoro_id, end_dt, api_key, user_key)
                if not candles:
                    log.debug(f"[api_backfiller] {symbol}: Keine Candles — überspringe.")
                    await asyncio.sleep(0.5)
                    continue

                table = _candles_to_arrow_table(
                    candles, symbol, price_prec, size_prec, start_dt, interval=DEFAULT_INTERVAL
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

                await asyncio.sleep(1.1)

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
    """Lädt die eToro-ID → Nautilus-Symbol-Map aus der Universe-Datei."""
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

    QUOTE_TICK_PATH.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data" / "state").mkdir(parents=True, exist_ok=True)

    load_dotenv(str(ENV_FILE))
    api_key  = os.getenv("ETORO_API_KEY",  "")
    user_key = os.getenv("ETORO_USER_KEY", "")

    if not api_key or not user_key:
        if args.dry_run:
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