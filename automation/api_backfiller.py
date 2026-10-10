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
from typing import Any, Callable

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

# Kerzen ohne Volumen (eToro liefert für Krypto kein ``volume``): eine Quote-Größe 0 ergibt ein
# L1-Buch ohne Liquidität, und der simulierte Handelsplatz lehnt jede Order mit "no market" ab
# (Krypto: 0 Füllungen bei 825 Orders, Paper-Selektion 2026-10-10). Stattdessen eine große
# synthetische Top-of-Book-Größe; ``volume_available=false`` bleibt als Kennzeichnung stehen.
SYNTHETIC_TOB_SIZE = 1_000_000_000.0

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


class CatalogSchemaMigrationUnavailable(RuntimeError):
    """Issue #1364 (GH #1260): zwischen ``from_v`` und ``to_v`` ist keine Migration registriert."""


# Issue #1364 (GH #1260) Fix Punkt 4: Registry REINER Schema-/Metadaten-Migrationen
# ``(from_version, to_version) -> Callable[[pa.Table], pa.Table]``. Eine Migration darf nur
# Zeilen erzeugen, die im Zielschema dieselbe Bedeutung haben wie im Quellschema (Spalten
# ergänzen, Metadaten umschreiben). v1 -> v2 ist bewusst NICHT registriert: eine v1-Kerze ist ein
# Einzeltick auf dem Kerzenbeginn, kein O/L/H/C-Tickpfad (#1330/#1332) — die Semantik lässt sich
# nicht aus den Zeilen rekonstruieren. Solche Zeilen bleiben im Archiv (siehe
# ``historical_fetcher.rebuild_catalog``).
SCHEMA_MIGRATIONS: dict[tuple[int, int], Callable[[pa.Table], pa.Table]] = {}


def schema_migration_path(from_v: int, to_v: int) -> list[tuple[int, int]] | None:
    """Kette registrierter Einzelschritte von ``from_v`` nach ``to_v`` (``[]`` bei Gleichheit,
    ``None`` ohne Pfad). Breitensuche über ``SCHEMA_MIGRATIONS`` — reine Funktion."""
    if from_v == to_v:
        return []
    frontier: list[tuple[int, list[tuple[int, int]]]] = [(from_v, [])]
    seen = {from_v}
    while frontier:
        node, path = frontier.pop(0)
        for (src, dst) in sorted(SCHEMA_MIGRATIONS):
            if src != node or dst in seen:
                continue
            new_path = path + [(src, dst)]
            if dst == to_v:
                return new_path
            seen.add(dst)
            frontier.append((dst, new_path))
    return None


def has_schema_migration(from_v: int | None, to_v: int = 0) -> bool:
    """True, wenn ein Katalog der Version ``from_v`` (``None`` = Legacy = 1) ohne Datenverlust auf
    ``to_v`` (Default: ``CATALOG_SCHEMA_VERSION``) migriert werden kann."""
    src = 1 if from_v is None else int(from_v)
    return schema_migration_path(src, to_v or CATALOG_SCHEMA_VERSION) is not None


def schema_mismatch_message(symbol: str, interval: str, existing_version: int | None) -> str:
    """Fehlermeldung der ``CatalogSchemaVersionMismatch`` (Issue #1364 Fix Punkt 4): nennt ZUERST
    die Migration (falls registriert), den Rebuild nur als letzten Ausweg mit Warnhinweis."""
    head = (
        f"[api_backfiller] {symbol}/{interval}: bestehender Katalog hat "
        f"catalog_schema_version={existing_version!r}, Schreiber erwartet "
        f"{CATALOG_SCHEMA_VERSION}. Kein stiller Merge über eine Schemagrenze hinweg — "
    )
    if has_schema_migration(existing_version):
        return head + (
            f"Migration verfügbar (verlustfrei): "
            f"`python3 automation/historical_fetcher.py --migrate-catalog {symbol}`."
        )
    return head + (
        f"für {existing_version!r} -> {CATALOG_SCHEMA_VERSION} ist KEINE verlustfreie Migration "
        f"registriert (migrate_catalog_schema). Letzter Ausweg: "
        f"`python3 automation/historical_fetcher.py --rebuild-catalog {symbol}` — WARNUNG: der "
        f"Katalog wird nach data/nautilus/archive/ verschoben und aus der API neu aufgebaut; "
        f"Historie jenseits der API-Tiefe, die im neuen Schema nicht darstellbar ist, bleibt nur im "
        f"Archiv (der Rebuild bricht ohne --accept-history-loss ab, wenn er Verlust vorhersagt)."
    )

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
    from automation.utils import _fallback_precisions, apply_price_precision_floor
except ImportError:
    # Direkter Import wenn automation/ nicht im sys.path ist
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from automation.utils import _fallback_precisions, apply_price_precision_floor


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
    end_time: datetime | None,
    api_key: str,
    user_key: str,
    interval: str = "OneHour",
    count: int = 168,  # 7 Tage × 24h
) -> list[dict]:
    """Holt historische Candle-Daten für ein Instrument.

    Issue #1372 (Pitfall #494): ``end_time=None`` sendet keinen ``endTime`` (der Endpunkt ist laut
    API-Referenz zählerbasiert und liefert die jüngsten ``count`` Kerzen)."""
    url = _CANDLES_URL.format(etoro_id=etoro_id, interval=interval, count=count)
    headers = {
        "x-api-key":    api_key,
        "x-user-key":   user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    params = {"endTime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ")} if end_time is not None else {}

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

def oneday_session_window_for(symbol: str):
    """Issue #1382 (GH #1284) — ``SessionWindow`` für die Session-Expansion der ``OneDay``-Ticks (Asset-Class aus
    ``instrument_map.json``, Fenster aus ``backtest.json``; ``None`` ⇒ 24/7-Markt bzw. nicht auflösbar ⇒ Alt-
    Expansion auf UTC-Tagesbruchteilen). Kein Fehlerpfad: eine Config-Lücke darf den Abruf nie stoppen."""
    try:
        from automation.session_windows import load_session_window_for_symbol
        return load_session_window_for_symbol(symbol)
    except Exception:
        return None


def _candles_to_arrow_table(
    candles: list[dict],
    symbol: str,
    price_prec: int,
    size_prec: int,
    start_dt: datetime,
    interval: str = DEFAULT_INTERVAL,
    asof_ns: int | None = None,
    oneday_session_window=None,
) -> pa.Table | None:
    """Konvertiert Candle-Daten DIREKT in eine PyArrow-Table mit FixedSizeBinary(16).

    Issue #1373 (Pitfall #495): mit ``asof_ns`` (Zeitpunkt der API-Antwort) werden unfertige Kerzen
    (``candle_start + interval > asof_ns``) NICHT geschrieben — ihr Teil-Close läge als
    ``candle_end − 1 ns`` nach dem Abrufzeitpunkt. Die Zahl wird als ``n_incomplete_dropped`` geloggt und
    als Event ``INCOMPLETE_CANDLES_DROPPED`` gemeldet. ``asof_ns=None`` ⇒ kein Filter (bit-identisch).

    Issue #1382 (GH #1284) Fix Punkt 2: ``oneday_session_window`` (``SessionWindow``) legt die O/L/H/C-Ticks einer
    ``OneDay``-Kerze INNERHALB der Session ihres Handelstags ab (O bei Session-Open, L/H dazwischen, C bei
    Session-Close − 1 ns; Look-Ahead-Invariante #1332) statt auf UTC-Tagesbruchteilen; Kerzen an Nicht-Handelstagen
    (Wochenende, Feiertag) werden nicht geschrieben (``n_non_trading_dropped`` im Log). Der Handelstag ist das UTC-
    Datum von ``fromDate`` (die #1375-Klasse entscheidet über die Bezugszeit). ``None`` ⇒ bit-identisches Alt-
    Verhalten; die Spalte ``bar_interval_ns`` bleibt die Kerzenlänge der Achse.

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

    _NO_VOLUME_SIZE = _encode_qty_fsb16(SYNTHETIC_TOB_SIZE, size_prec)
    min_ts_ns = int(start_dt.timestamp() * 1e9)

    # ── Pass 1: parsen, plausibilisieren, chronologisch sortieren ─────────────
    # Die eToro-API liefert Kerzen `desc` (jüngste zuerst); der open-Fallback ("Close der
    # Vorgängerkerze", Fix Punkt 1) braucht chronologisch aufsteigende Reihenfolge.
    parsed: list[tuple[int, float | None, float, float, float, float | None]] = []
    n_incomplete_dropped = 0
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
            if asof_ns is not None and ts_ns + interval_ns > asof_ns:
                n_incomplete_dropped += 1
                continue

            parsed.append((ts_ns, open_, low, high, close, volume))
        except Exception as e:
            log.debug(f"[api_backfiller] Candle-Parse-Fehler ({symbol}): {e}")
            continue

    if n_incomplete_dropped:
        log.info(f"[api_backfiller] {symbol}: n_incomplete_dropped={n_incomplete_dropped} ({interval}) — "
                 f"unfertige Kerze(n) nicht geschrieben (Issue #1373).")
        try:
            from automation.log_manager import emit_execution_event
            emit_execution_event(log, "INCOMPLETE_CANDLES_DROPPED", {
                "symbol": symbol, "interval": interval, "n_incomplete_dropped": n_incomplete_dropped})
        except Exception:  # pragma: no cover - Telemetrie darf den Abruf nie stoppen
            pass

    if not parsed:
        return None

    parsed.sort(key=lambda row: row[0])

    volume_seen_any = False
    volume_missing_any = False
    n_non_trading_dropped = 0
    prev_close: float | None = None

    for candle_start_ns, open_val, low, high, close, volume in parsed:
        if open_val is None:
            open_val = prev_close
        candle_end_ns = candle_start_ns + interval_ns
        span_ns = interval_ns
        if oneday_session_window is not None and interval == "OneDay":
            from automation.session_windows import is_trading_day, session_bounds_utc_ns
            _day = datetime.fromtimestamp(candle_start_ns / 1e9, tz=timezone.utc).date()
            if not is_trading_day(_day, oneday_session_window):
                n_non_trading_dropped += 1
                continue
            candle_start_ns, candle_end_ns = session_bounds_utc_ns(_day, oneday_session_window)
            span_ns = candle_end_ns - candle_start_ns

        # Geordnete Tick-Sequenz: O (falls verfügbar) → adverses Extrem (low) → günstiges
        # Extrem (high) → close zuletzt. Reihenfolge ist FEST, nicht richtungsabhängig.
        roles: list[tuple[float, int]] = []
        if open_val is not None:
            roles.append((open_val, candle_start_ns + int(span_ns * _INTRABAR_OFFSET_OPEN_FRAC)))
        roles.append((low,  candle_start_ns + int(span_ns * _INTRABAR_OFFSET_LOW_FRAC)))
        roles.append((high, candle_start_ns + int(span_ns * _INTRABAR_OFFSET_HIGH_FRAC)))
        roles.append((close, candle_end_ns - 1))

        if volume is not None and volume > 0:
            volume_seen_any = True
            size_bytes_for_row = _encode_qty_fsb16(volume / len(roles), size_prec)
        else:
            volume_missing_any = True
            size_bytes_for_row = _NO_VOLUME_SIZE

        for price, ts_ns in roles:
            bid_prices.append(_encode_fsb16(price, price_prec))
            ask_prices.append(_encode_fsb16(price, price_prec))
            bid_sizes.append(size_bytes_for_row)
            ask_sizes.append(size_bytes_for_row)
            ts_events.append(ts_ns)
            ts_inits.append(ts_ns)
            bar_interval_col.append(interval_ns)

        prev_close = close

    if n_non_trading_dropped:
        log.info(f"[api_backfiller] {symbol}: n_non_trading_dropped={n_non_trading_dropped} ({interval}) — "
                 f"Kerze(n) an Nicht-Handelstagen nicht geschrieben (Issue #1382).")

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


def read_intrabar_path(parquet_file: Path) -> str | None:
    """Issue #1350 (GH #1244, P1) Fix-Punkt 5 — liest `intrabar_path`
    (`INTRABAR_PATH_SYNTHETIC`/`INTRABAR_PATH_OBSERVED`, siehe `_build_arrow_meta`) aus den
    Arrow-Schema-Metadaten einer Katalogdatei, analog `_read_catalog_schema_version`. Jede
    Stop-Kennzahl, die auf dieser Datei beruht, muss diesen Vermerk mitfuehren — solange
    `intrabar_path == INTRABAR_PATH_SYNTHETIC` gilt, ist die Trigger-Reihenfolge innerhalb einer
    Bar (adverse-first: low vor high, siehe `_candles_to_arrow_table`) eine KONSERVATIVE ANNAHME,
    keine Beobachtung.

    `None`, wenn die Datei fehlt, nicht lesbar ist, oder das Feld fehlt (Alt-Katalog vor
    Issue #1330/GH #1224 — ein Einzeltick-je-Kerze-Katalog kennt gar keinen Pfad-Begriff)."""
    try:
        schema = pq.read_schema(str(parquet_file))
    except Exception:
        return None
    meta = schema.metadata or {}
    raw = meta.get(b"intrabar_path")
    if raw is None:
        return None
    try:
        return raw.decode()
    except UnicodeDecodeError:
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
                schema_mismatch_message(symbol, interval, existing_version)
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
    merged = _dedupe_sort_last_wins(merged)
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


def _dedupe_sort_last_wins(merged: pa.Table) -> pa.Table:
    """Dedup-Regel des Katalogs (Issue #1333 Fix Punkt 4): je ``(ts_event, bar_interval_ns)`` gewinnt
    die LETZTE Zeile, Ergebnis nach ``ts_event`` sortiert. Einzige Implementierung — ``_merge_and_save``
    und ``restore_archived_rows`` (Issue #1364) teilen sie, damit eine Rückführung aus dem Archiv
    exakt dieselbe Regel anwendet wie ein regulärer Merge."""
    ts_list = merged.column("ts_event").to_pylist()
    interval_list = merged.column("bar_interval_ns").to_pylist()
    last_index_for_key: dict[tuple[int, int], int] = {}
    for i, key in enumerate(zip(ts_list, interval_list)):
        last_index_for_key[key] = i  # spätere Zeile überschreibt frühere
    keep_indices = sorted(last_index_for_key.values(), key=lambda i: ts_list[i])
    return merged.take(pa.array(keep_indices, type=pa.int64()))


def _with_bar_interval_column(table: pa.Table, interval_ns: int) -> pa.Table:
    """Ergänzt die Spalte ``bar_interval_ns`` (konstant ``interval_ns``), falls sie fehlt — z. B. bei
    einer flachen Echt-Tick-Datei des ``catalog_service`` (Issue #1364/#1366: ``RealTick`` trägt
    ``bar_interval_ns = 0``)."""
    if "bar_interval_ns" in table.column_names:
        return table
    return table.append_column(
        "bar_interval_ns", pa.array([interval_ns] * len(table), type=pa.uint64())
    )


def restore_archived_rows(
    log_ctx: logging.Logger,
    archived_table: pa.Table,
    symbol: str,
    interval: str,
    *,
    price_prec: int,
    size_prec: int,
    interval_ns: int | None = None,
    quote_tick_path: Path | None = None,
) -> int:
    """Issue #1364 (GH #1260) Fix Punkt 2 — führt Zeilen aus einem Rebuild-Archiv in den Katalog
    zurück. Die archivierten Zeilen stehen in der Dedup-Reihenfolge UNTER der aktuellen Datei
    (frisch aus der API gebaute Zeilen gewinnen, exakt die Regel von ``_merge_and_save``); es
    kommen nur Zeilen hinzu, die der Neuaufbau nicht liefert. Schreibt atomar, schema-gate wie
    ``_merge_and_save`` (``CatalogSchemaVersionMismatch`` bei fremder Version der aktuellen Datei).

    Rückgabe: Anzahl tatsächlich hinzugekommener Zeilen (0, wenn der Neuaufbau alles abdeckt)."""
    if interval_ns is None:
        interval_ns = INTERVAL_TO_NS.get(interval, 0)
    root = Path(quote_tick_path) if quote_tick_path is not None else QUOTE_TICK_PATH
    dest_dir = root / symbol / interval
    dest_file = dest_dir / "data.parquet"
    dest_dir.mkdir(parents=True, exist_ok=True)

    archived_table = _with_bar_interval_column(archived_table, interval_ns)
    arch_meta = archived_table.schema.metadata or {}
    extra_meta = {
        k: v for k, v in arch_meta.items() if k in (b"intrabar_path", b"volume_available")
    }

    live_rows = 0
    tables: list[pa.Table] = [archived_table.replace_schema_metadata(None)]
    if dest_file.exists():
        live_version = _read_catalog_schema_version(dest_file)
        if interval in INTERVAL_TO_NS and live_version != CATALOG_SCHEMA_VERSION:
            raise CatalogSchemaVersionMismatch(
                schema_mismatch_message(symbol, interval, live_version)
            )
        live = pq.read_table(str(dest_file))
        live_meta = live.schema.metadata or {}
        extra_meta.update({
            k: v for k, v in live_meta.items() if k in (b"intrabar_path", b"volume_available")
        })
        live = _with_bar_interval_column(live, interval_ns)
        live_rows = len(live)
        tables.append(live.replace_schema_metadata(None))

    merged = pa.concat_tables(
        [t.select(tables[0].column_names) for t in tables], promote_options="default"
    )
    merged = _dedupe_sort_last_wins(merged)
    meta = _build_arrow_meta(symbol, price_prec, size_prec, interval=interval, extra=extra_meta)
    merged = merged.replace_schema_metadata(meta)

    tmp = dest_file.with_suffix(".tmp.parquet")
    try:
        pq.write_table(merged, str(tmp), compression="snappy")
        os.replace(tmp, dest_file)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    added = len(merged) - live_rows
    log_ctx.info(
        "[api_backfiller] %s/%s: %d Zeilen aus dem Archiv zurückgeführt (%d -> %d Zeilen).",
        symbol, interval, added, live_rows, len(merged),
    )
    return added


def migrate_catalog_schema(
    from_v: int,
    to_v: int = CATALOG_SCHEMA_VERSION,
    *,
    symbols: list[str] | None = None,
    quote_tick_path: Path | None = None,
) -> list[Path]:
    """Issue #1364 (GH #1260) Fix Punkt 4 — verlustfreie Schema-Migration der Katalogdateien von
    ``from_v`` nach ``to_v`` über die in ``SCHEMA_MIGRATIONS`` registrierten Schritte. Schreibt je
    Datei atomar (tmp + ``os.replace``) und stempelt ``catalog_schema_version``.

    Wirft ``CatalogSchemaMigrationUnavailable``, wenn kein Pfad registriert ist (heute für 1 -> 2:
    die Semantik einer v1-Kerze ist nicht aus den Zeilen rekonstruierbar). Das Archiv wird nie
    angefasst. Rückgabe: die tatsächlich umgeschriebenen Dateien."""
    steps = schema_migration_path(int(from_v), int(to_v))
    if steps is None:
        raise CatalogSchemaMigrationUnavailable(
            f"[api_backfiller] Keine Migration von catalog_schema_version {from_v} auf {to_v} "
            f"registriert (SCHEMA_MIGRATIONS={sorted(SCHEMA_MIGRATIONS)})."
        )
    root = Path(quote_tick_path) if quote_tick_path is not None else QUOTE_TICK_PATH
    migrated: list[Path] = []
    if not root.is_dir():
        return migrated
    wanted = set(symbols) if symbols else None
    for inst_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if wanted is not None and inst_dir.name not in wanted:
            continue
        for interval in INTERVAL_TO_NS:
            f = inst_dir / interval / "data.parquet"
            if not f.exists():
                continue
            version = _read_catalog_schema_version(f)
            if (1 if version is None else version) != int(from_v):
                continue
            table = pq.read_table(str(f))
            meta = dict(table.schema.metadata or {})
            for step in steps:
                table = SCHEMA_MIGRATIONS[step](table)
            meta[b"catalog_schema_version"] = str(int(to_v)).encode()
            table = table.replace_schema_metadata(meta)
            tmp = f.with_suffix(".tmp.parquet")
            try:
                pq.write_table(table, str(tmp), compression="snappy")
                os.replace(tmp, f)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
            migrated.append(f)
    return migrated


# ─── Hauptlogik ───────────────────────────────────────────────────────────────

async def run_backfill(
    api_key: str,
    user_key: str,
    etoro_id_to_symbol: dict[str, str],
    days: int = 7,
    dry_run: bool = False,
    specific_symbols: set[str] | None = None,
    with_oneday: bool = False,
) -> list[str]:
    """Backfill-Hauptlogik.

    Issue #1276 (GH #1149, Katalog #1374): mit ``with_oneday=True`` folgt auf den Stunden-Vorwärts-Schritt ein
    ``OneDay``-Vorwärts-Schritt (``count = min(1000, ceil(gap_d) + 2)``, nur fertige Tageskerzen) in die EIGENE
    Datei ``<symbol>/OneDay/data.parquet`` — ``OneHour`` bleibt unberührt."""
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
            latest_ts = None
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
            price_prec, size_prec = apply_price_precision_floor(symbol or "", (price_prec, size_prec))

            try:
                # Issue #1363 (GH #1259) Fix Punkt 1 — mit lokalem Bestand: Vorwärts-Schritt bis zur
                # Überlappung mit dem jüngsten lokalen Tick (count aus der Lücke, paginiert) statt fix
                # 168 Kerzen — eine längere Lücke blieb sonst als Loch im Katalog.
                filter_start_dt = start_dt
                if latest_ts is not None:
                    from automation.historical_fetcher import fetch_forward_candles
                    candles = await fetch_forward_candles(
                        session, etoro_id, symbol, latest_ts, api_key=api_key, user_key=user_key,
                        interval=DEFAULT_INTERVAL, now=end_dt)
                    filter_start_dt = min(
                        start_dt, datetime.fromtimestamp(latest_ts / 1e9, tz=timezone.utc) - timedelta(days=1))
                else:
                    from automation.historical_fetcher import pagination_mode, PAGINATION_END_TIME
                    candles = await _fetch_candles(
                        session, etoro_id,
                        end_dt if pagination_mode(symbol, DEFAULT_INTERVAL) == PAGINATION_END_TIME else None,
                        api_key, user_key)
                if not candles:
                    log.debug(f"[api_backfiller] {symbol}: Keine Candles — überspringe.")
                    await asyncio.sleep(0.5)
                    continue

                _asof_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)   # Zeitpunkt der API-Antwort
                table = _candles_to_arrow_table(
                    candles, symbol, price_prec, size_prec, filter_start_dt, interval=DEFAULT_INTERVAL,
                    asof_ns=_asof_ns,
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

        if with_oneday:
            await _oneday_forward_steps(session, etoro_id_to_symbol, api_precisions, specific_symbols,
                                        api_key, user_key, end_dt, dry_run)

    log.info(f"[api_backfiller] Backfill abgeschlossen: {len(filled)} Symbole befüllt.")
    return filled


async def _oneday_forward_steps(session, etoro_id_to_symbol, api_precisions, specific_symbols,
                                api_key, user_key, end_dt, dry_run) -> list[str]:
    """Issue #1276 — OneDay-Vorwärts-Schritt je Symbol mit vorhandener ``OneDay``-Datei (die Datei entsteht per
    ``historical_fetcher --interval OneDay --full-window``). Gibt die fortgeschriebenen Symbole zurück."""
    from automation.historical_fetcher import fetch_forward_candles
    done: list[str] = []
    for etoro_id, symbol in sorted(etoro_id_to_symbol.items(), key=lambda x: x[1]):
        if specific_symbols and symbol not in specific_symbols:
            continue
        dest = QUOTE_TICK_PATH / symbol / "OneDay" / "data.parquet"
        if not dest.exists():
            continue
        latest = _get_latest_ts(dest)
        if latest is None:
            continue
        price_prec, size_prec = apply_price_precision_floor(
            symbol or "", api_precisions.get(etoro_id) or _fallback_precisions(symbol or ""))
        try:
            candles = await fetch_forward_candles(
                session, etoro_id, symbol, latest, api_key=api_key, user_key=user_key,
                interval="OneDay", now=end_dt)
            if not candles:
                continue
            table = _candles_to_arrow_table(
                candles, symbol, price_prec, size_prec,
                datetime.fromtimestamp(latest / 1e9, tz=timezone.utc) - timedelta(days=3),
                interval="OneDay", asof_ns=int(end_dt.timestamp() * 1e9),
                oneday_session_window=oneday_session_window_for(symbol))
            if table is None or len(table) == 0:
                continue
            if not dry_run:
                _merge_and_save(log, table, symbol, price_prec, size_prec, interval="OneDay")
            done.append(symbol)
            await asyncio.sleep(1.1)
        except Exception as exc:
            log.warning(f"[api_backfiller] OneDay-Vorwärts-Schritt {symbol}: {exc}")
    return done


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