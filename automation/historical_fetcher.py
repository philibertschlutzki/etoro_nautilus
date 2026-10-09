#!/usr/bin/env python3
"""
automation/historical_fetcher.py
=================================
Standalone historical candle fetcher for eToro Nautilus.
Fetches up to 12 months (default) of hourly + daily candles per instrument.
Writes raw PyArrow FSB(16) to data/nautilus/data/quote_tick/SYMBOL/INTERVAL/data.parquet
(interval subdirectory per resolution since Issue #1331/GH #1225, e.g. .../SYMBOL/OneHour/
data.parquet) — same format as api_backfiller.py.

Usage (standalone):
  python3 automation/historical_fetcher.py [--months 12] [--symbol TSLA.ETORO]
  python3 automation/historical_fetcher.py --force

Usage (as module from orchestrator):
  from automation.historical_fetcher import run_historical_fetch
  asyncio.run(run_historical_fetch(api_key, user_key, etoro_id_map, months=12))
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

import aiohttp
import pyarrow.parquet as pq
from dotenv import load_dotenv

_THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _THIS_DIR.parent
CATALOG_PATH = PROJECT_ROOT / "data" / "nautilus"
QUOTE_TICK_PATH = CATALOG_PATH / "data" / "quote_tick"
UNIVERSE_PATH = PROJECT_ROOT / "data" / "universe" / "momentum_ls.json"
ENV_FILE = PROJECT_ROOT / ".env"
INCEPTION_CACHE_PATH = PROJECT_ROOT / "data" / "state" / "inception_bounds.json"

_BASE_URL = "https://public-api.etoro.com/api/v1/market-data"
_CANDLES_URL = f"{_BASE_URL}/instruments/{{etoro_id}}/history/candles/desc/{{interval}}/{{count}}"

log = logging.getLogger("historical_fetcher")

# Reuse Arrow encoding, merge logic, precision heuristic, and id map loader from api_backfiller
try:
    from automation.api_backfiller import (
        _candles_to_arrow_table,
        _merge_and_save,
        _fallback_precisions,
        apply_price_precision_floor,
        _load_etoro_id_map,
        fetch_precisions_from_api,
        CatalogSchemaVersionMismatch,
        INTERVAL_TO_NS,
    )
except ImportError:
    sys.path.insert(0, str(PROJECT_ROOT))
    from automation.api_backfiller import (
        _candles_to_arrow_table,
        _merge_and_save,
        _fallback_precisions,
        apply_price_precision_floor,
        _load_etoro_id_map,
        fetch_precisions_from_api,
        CatalogSchemaVersionMismatch,
        INTERVAL_TO_NS,
    )

# Issue #1331 (GH #1225) Fix Punkt 4: der Optimizer konsumiert ausschliesslich die
# Stundenauflösung; das Tages-Segment bleibt für Regime-/Benchmark-Zwecke erhalten,
# betritt aber nie den Backtest-Pfad — daher zuerst in der Kaskade.
_FETCH_INTERVALS: tuple[str, ...] = ("OneHour", "OneDay")


# ─── Cache Helpers ────────────────────────────────────────────────────────────

BACKFILL_RETRY_DAYS_DEFAULT = 7


def _load_inception_bounds() -> dict[str, dict]:
    """Issue #1363 (GH #1259) — Inception-Bounds JE INTERVALL: ``{symbol: {"OneHour": ns, "OneDay": ns,
    "observed_utc": iso}}``. Das Altformat ``{symbol: ns}`` (ein Wert, registriert aus der OneHour-Datei,
    wenn die GESAMTE Kaskade das Ziel verfehlte) wird beim Lesen migriert (``OneHour``, ``observed_utc``
    unbekannt ⇒ ``None``)."""
    if not INCEPTION_CACHE_PATH.exists():
        return {}
    try:
        with open(INCEPTION_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
    except Exception as e:
        log.warning(f"Fehler beim Laden von {INCEPTION_CACHE_PATH}: {e}")
        return {}
    out: dict[str, dict] = {}
    for symbol, value in raw.items():
        if isinstance(value, dict):
            out[symbol] = dict(value)
        elif isinstance(value, (int, float)):
            out[symbol] = {"OneHour": int(value), "observed_utc": None}
    return out


def _write_inception_bounds(bounds: dict[str, dict], symbol: str) -> None:
    """Schreibt ``bounds`` atomar nach ``INCEPTION_CACHE_PATH`` (Tempdatei + ``os.replace``)."""
    INCEPTION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = INCEPTION_CACHE_PATH.with_suffix(".tmp.json")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(bounds, f, indent=2)
        os.replace(tmp_path, INCEPTION_CACHE_PATH)
    except Exception as e:
        log.warning(f"Fehler beim Speichern von {INCEPTION_CACHE_PATH} für {symbol}: {e}")
        if tmp_path.exists():
            tmp_path.unlink()


def _save_inception_bound(symbol: str, ts_ns: int, interval: str = "OneHour",
                          *, now: datetime | None = None) -> None:
    """Speichert die erreichte Tiefe ``ts_ns`` für ``symbol``/``interval`` atomar (Issue #1363: je Intervall,
    mit ``observed_utc`` — Grundlage der ``backfill_retry_days``-Sperre)."""
    bounds = _load_inception_bounds()
    entry = bounds.setdefault(symbol, {})
    entry[interval] = int(ts_ns)
    entry["observed_utc"] = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_inception_bounds(bounds, symbol)


# ─── Pagination-Modus und API-Fenster (Issue #1372, Pitfall #494) ─────────────────────

PAGINATION_END_TIME = "end_time"
PAGINATION_COUNT_ONLY = "count_only"
_WINDOW_COUNT = 1000          # candlesCount-Maximum der API (1–1000)


def _utc_str(dt_: datetime) -> str:
    return dt_.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _save_pagination_probe(symbol: str, interval: str, mode: str, *, now: datetime | None = None) -> None:
    """Stempelt das Ergebnis der Pagination-Probe je Symbol und Intervall (``pagination_mode``,
    ``probed_utc``) in ``inception_bounds.json`` und emittiert ``CANDLE_PAGINATION_PROBED``."""
    when = now or datetime.now(timezone.utc)
    bounds = _load_inception_bounds()
    bounds.setdefault(symbol, {}).setdefault("pagination", {})[interval] = {
        "pagination_mode": mode, "probed_utc": _utc_str(when)}
    _write_inception_bounds(bounds, symbol)
    try:
        from automation.log_manager import emit_execution_event
        emit_execution_event(log, "CANDLE_PAGINATION_PROBED", {
            "symbol": symbol, "interval": interval, "pagination_mode": mode})
    except Exception:  # pragma: no cover - Telemetrie darf den Abruf nie stoppen
        pass


def pagination_mode(symbol: str, interval: str = "OneHour") -> str:
    """Gestempelter Pagination-Modus; ohne Probe gilt ``count_only`` (kein ``endTime`` — die API-Referenz
    kennt nur ``candlesCount``; ein undokumentierter Parameter ist eine Hoffnung, kein Vertrag, #494)."""
    stamp = ((_load_inception_bounds().get(symbol) or {}).get("pagination") or {}).get(interval) or {}
    return PAGINATION_END_TIME if stamp.get("pagination_mode") == PAGINATION_END_TIME else PAGINATION_COUNT_ONLY


def _save_api_window(symbol: str, interval: str, chunk: list[dict], *,
                     now: datetime | None = None) -> dict | None:
    """Registriert das API-Fenster (die jüngsten ``len(chunk)`` Kerzen): ``window_oldest_utc`` und
    ``window_span_h`` (Zeitspanne bis zum Abrufzeitpunkt). Kein Archivende — nur ein Fenster."""
    oldest_ns = _oldest_ts_ns_from_chunk(chunk)
    if oldest_ns is None:
        return None
    when = now or datetime.now(timezone.utc)
    oldest = datetime.fromtimestamp(oldest_ns / 1e9, tz=timezone.utc)
    window = {"window_oldest_utc": _utc_str(oldest),
              "window_span_h": round((when - oldest).total_seconds() / 3600.0, 2),
              "n_candles": len(chunk), "observed_utc": _utc_str(when)}
    bounds = _load_inception_bounds()
    bounds.setdefault(symbol, {}).setdefault("window", {})[interval] = window
    _write_inception_bounds(bounds, symbol)
    log.info(f"[{symbol}] API-Fenster {interval}: jüngste {len(chunk)} Kerzen, älteste {window['window_oldest_utc']}")
    return window


def api_window_span_h(symbol: str, interval: str = "OneHour") -> float | None:
    """Zeitspanne (h) des zuletzt registrierten API-Fensters oder ``None``."""
    win = ((_load_inception_bounds().get(symbol) or {}).get("window") or {}).get(interval) or {}
    span = win.get("window_span_h")
    return float(span) if span is not None else None


def check_forward_gap_horizon(symbol: str, interval: str, latest_local_ns: int, *, now: datetime,
                              window_span_h: float | None = None) -> str | None:
    """Horizont-Wächter: die Lücke seit dem jüngsten lokalen Tick gegen die Fensterspanne.

    > 0,5 × ⇒ WARNING ``FORWARD_GAP_NEAR_HORIZON``; > 1,0 × ⇒ ERROR ``FORWARD_GAP_UNRECOVERABLE`` mit dem
    verlorenen Intervall ``[jüngster lokaler Tick, ältester Fensterzeitpunkt]``. Gibt den Eventnamen oder
    ``None`` zurück (ohne bekannte Fensterspanne kein Befund)."""
    span = window_span_h if window_span_h is not None else api_window_span_h(symbol, interval)
    if not span or span <= 0:
        return None
    gap_h = (now.timestamp() - latest_local_ns / 1e9) / 3600.0
    if gap_h <= 0.5 * span:
        return None
    latest = datetime.fromtimestamp(latest_local_ns / 1e9, tz=timezone.utc)
    payload = {"symbol": symbol, "interval": interval, "gap_h": round(gap_h, 2),
               "window_span_h": round(span, 2)}
    if gap_h > span:
        event, level = "FORWARD_GAP_UNRECOVERABLE", logging.ERROR
        payload["lost_interval"] = [_utc_str(latest), _utc_str(now - timedelta(hours=span))]
    else:
        event, level = "FORWARD_GAP_NEAR_HORIZON", logging.WARNING
    log.log(level, f"[{symbol}] {event}: Lücke {gap_h:.1f} h bei API-Fenster {span:.1f} h ({interval}).")
    try:
        from automation.log_manager import emit_execution_event
        emit_execution_event(log, event, payload, level=level)
    except Exception:  # pragma: no cover
        pass
    return event


def inception_bound(symbol: str, interval: str = "OneHour") -> int | None:
    """Registrierte API-Tiefe (ältester erreichbarer Zeitstempel, ns) für ``symbol``/``interval``."""
    value = (_load_inception_bounds().get(symbol) or {}).get(interval)
    return int(value) if value is not None else None


def inception_bound_is_fresh(symbol: str, interval: str = "OneHour", *,
                             retry_days: int = BACKFILL_RETRY_DAYS_DEFAULT,
                             now: datetime | None = None) -> bool:
    """Issue #1363 — ``True``, solange die für ``interval`` registrierte Tiefe jünger als ``retry_days``
    beobachtet wurde: ein erneuter Rückwärts-Abruf ist dann zwecklos (die API liefert nicht tiefer)."""
    entry = _load_inception_bounds().get(symbol) or {}
    if entry.get(interval) is None or not entry.get("observed_utc"):
        return False
    try:
        observed = datetime.fromisoformat(str(entry["observed_utc"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now or datetime.now(timezone.utc)) - observed < timedelta(days=retry_days)

# ─── Sufficiency Check ────────────────────────────────────────────────────────

def is_backtest_range_covered(
    symbol: str,
    start_ns: int,
    catalog_path: Path = CATALOG_PATH,
    interval: str = "OneHour",
) -> bool:
    """Returns True if symbol's data.parquet covers the required backtest range.

    Issue #1331 (GH #1225): sucht zuerst im auflösungs-getrennten Layout
    (``<symbol>/<interval>/data.parquet``), fällt für Alt-Kataloge auf das flache Layout
    zurück (via ``catalog_paths.resolve_quote_tick_files``, Single Source of Truth)."""
    from automation.catalog_paths import resolve_quote_tick_files

    files = resolve_quote_tick_files(catalog_path, symbol, interval=interval)
    if not files:
        return False
    parquet_file = files[0]
    try:
        import pyarrow.compute as pc
        t = pq.read_table(str(parquet_file), columns=["ts_event"])
        if len(t) == 0:
            return False
        oldest_ts = int(pc.min(t.column("ts_event")).as_py())

        # Inception-Bound (Issue #1363: je Intervall) — die lokale Historie reicht bis zur API-Tiefe.
        bound = inception_bound(symbol, interval)
        if bound is not None and oldest_ts <= bound:
            log.info(f"[{symbol}] Inception-Bound-Check erfolgreich: Volle historische Tiefe ({datetime.fromtimestamp(oldest_ts/1e9, tz=timezone.utc).date()}) liegt vor.")
            return True

        return oldest_ts <= start_ns
    except Exception:
        return False


# ─── Latest Timestamp Helper ─────────────────────────────────────────────────

def _get_latest_ts_ns(parquet_file: Path) -> int | None:
    """Returns the latest ts_event in nanoseconds from an existing parquet file."""
    try:
        t = pq.read_table(str(parquet_file), columns=["ts_event"])
        if len(t) == 0:
            return None
        return int(t.column("ts_event").to_pylist()[-1])
    except Exception:
        return None

def _get_oldest_ts_ns(parquet_file: Path) -> int | None:
    """Returns the oldest ts_event in nanoseconds from an existing parquet file."""
    try:
        import pyarrow.compute as pc
        t = pq.read_table(str(parquet_file), columns=["ts_event"])
        if len(t) == 0:
            return None
        return int(pc.min(t.column("ts_event")).as_py())
    except Exception:
        return None


# ─── Candle Timestamp Parser ─────────────────────────────────────────────────

def _oldest_ts_ns_from_chunk(chunk: list[dict]) -> int | None:
    """Returns the minimum (oldest) timestamp in nanoseconds from a candle chunk."""
    oldest_ns: int | None = None
    for c in chunk:
        c_low = {k.lower(): v for k, v in c.items()}
        date_val = (
            c_low.get("fromdate")
            or c_low.get("startdate")
            or c_low.get("date")
            or c_low.get("timestamp")
        )
        if not date_val:
            continue
        try:
            if isinstance(date_val, (int, float)):
                ts_ns = int(date_val * 1e9) if date_val < 1e13 else int(date_val)
            else:
                ts_str = str(date_val).replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts_ns = int(dt.timestamp() * 1e9)
            if oldest_ns is None or ts_ns < oldest_ns:
                oldest_ns = ts_ns
        except Exception:
            continue
    return oldest_ns


# ─── Candle Fetch ─────────────────────────────────────────────────────────────

async def _fetch_candle_chunk(
    session: aiohttp.ClientSession,
    etoro_id: str,
    end_time: datetime | None,
    api_key: str,
    user_key: str,
    interval: str,
    count: int = 1000,
) -> list[dict]:
    """Fetches up to `count` candles for the given interval.

    Issue #1372 (Pitfall #494): ``end_time=None`` sendet KEINEN ``endTime`` — der Endpunkt ist laut
    API-Referenz zählerbasiert (``candlesCount`` 1–1000) und liefert die jüngsten ``count`` Kerzen. Nur ein
    per Probe als ``end_time`` gestempelter Endpunkt (``pagination_mode``) bekommt den Parameter."""
    url = _CANDLES_URL.format(etoro_id=etoro_id, interval=interval, count=count)
    headers = {
        "x-api-key": api_key,
        "x-user-key": user_key,
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
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    log.warning(f"[{etoro_id}] Rate-Limit — warte {retry_after}s.")
                    await asyncio.sleep(retry_after)
                else:
                    log.debug(f"[{etoro_id}] HTTP {resp.status} für {interval}.")
                    return []
        except asyncio.TimeoutError:
            log.warning(f"[{etoro_id}] Timeout für {interval} (Versuch {attempt + 1}/3).")
            await asyncio.sleep(5 * (attempt + 1))
        except Exception as e:
            log.warning(f"[{etoro_id}] Fehler für {interval}: {e}")
            await asyncio.sleep(5 * (attempt + 1))

    return []


# ─── Pagination-Probe (Issue #1372) ──────────────────────────────────────────

async def probe_pagination(session, etoro_id: str, symbol: str, interval: str, *, api_key: str,
                           user_key: str, fetch_chunk=None, now: datetime | None = None) -> str:
    """Zwei Requests: A ohne ``endTime`` (``candlesCount=100``), B mit ``endTime = ältester(A) − 1 s``.

    Enthält B Kerzen, die älter sind als ältester(A) ⇒ ``end_time`` (die API wertet den Parameter aus,
    die „API-Tiefe" wäre dann eine echte Archivgrenze), sonst ``count_only`` (ein 1000-Kerzen-Fenster).
    Das Ergebnis wird je Symbol und Intervall gestempelt."""
    fetch = fetch_chunk or _fetch_candle_chunk
    chunk_a = await fetch(session, etoro_id, None, api_key, user_key, interval, count=100)
    oldest_a = _oldest_ts_ns_from_chunk(chunk_a) if chunk_a else None
    mode = PAGINATION_COUNT_ONLY
    if oldest_a is not None:
        end_time = datetime.fromtimestamp(oldest_a / 1e9, tz=timezone.utc) - timedelta(seconds=1)
        chunk_b = await fetch(session, etoro_id, end_time, api_key, user_key, interval, count=100)
        oldest_b = _oldest_ts_ns_from_chunk(chunk_b) if chunk_b else None
        if oldest_b is not None and oldest_b < oldest_a:
            mode = PAGINATION_END_TIME
    _save_pagination_probe(symbol, interval, mode, now=now)
    return mode


# ─── Vorwärts-Schritt (Issue #1363) ──────────────────────────────────────────

def forward_fill_count(gap_hours: float, interval: str = "OneHour") -> int:
    """``count = min(1000, ceil(gap_h) + 24)`` — die Lücke seit dem jüngsten lokalen Tick plus ein Tag
    Überlappung (Issue #1363; vorher fix 168 Kerzen: eine längere Lücke blieb als Loch).

    Issue #1276 (GH #1149, Katalog #1374): für ``OneDay`` wird intervallgerecht gezählt —
    ``count = min(1000, ceil(gap_d) + 2)`` (die Stundenformel lieferte 24 Kerzen Überlappung = 24 Tage)."""
    import math
    if interval == "OneDay":
        return int(min(1000, math.ceil(max(0.0, gap_hours) / 24.0) + 2))
    return int(min(1000, math.ceil(max(0.0, gap_hours)) + 24))


def _forward_min_gap_h(interval: str) -> float:
    """Kleinste Lücke (h), ab der ein Vorwärts-Schritt lohnt: eine fertige Kerze des Intervalls (≥ 1 h bzw. 24 h)."""
    return 24.0 if interval == "OneDay" else 1.0


async def fetch_forward_candles(
    session, etoro_id: str, symbol: str, latest_local_ns: int, *, api_key: str, user_key: str,
    interval: str = "OneHour", now: datetime | None = None, fetch_chunk=None, max_pages: int = 50,
    mode: str | None = None,
) -> list[dict]:
    """Issue #1363 (GH #1259) Fix Punkt 1 — Vorwärts-Schritt: von ``now`` rückwärts bis zur Überlappung mit
    ``latest_local_ns`` (``count`` aus der Lücke). Vorher kamen neue Kerzen ausschliesslich über
    Phase 2c (fix 168 Kerzen), die ``--skip-api-fetch`` übersprang — im dokumentierten Betrieb wuchs der
    ``OneHour``-Katalog nicht mehr.

    Issue #1372 (Pitfall #494): im Modus ``count_only`` (Default ohne Probe) genau EIN Abruf ohne ``endTime``
    (``count`` ≤ 1000); eine Lücke über das API-Fenster hinaus bleibt dann ein Loch — der Horizont-Wächter
    (``check_forward_gap_horizon``) meldet ``FORWARD_GAP_NEAR_HORIZON``/``FORWARD_GAP_UNRECOVERABLE``.
    Paginiert wird nur, wenn die Probe ``end_time`` gestempelt hat."""
    fetch = fetch_chunk or _fetch_candle_chunk
    now = now or datetime.now(timezone.utc)
    mode = mode or pagination_mode(symbol, interval)
    gap_h = (now.timestamp() - latest_local_ns / 1e9) / 3600.0
    if gap_h < _forward_min_gap_h(interval):
        return []
    if mode == PAGINATION_COUNT_ONLY:
        check_forward_gap_horizon(symbol, interval, latest_local_ns, now=now)
        out = await fetch(session, etoro_id, None, api_key, user_key, interval,
                          count=forward_fill_count(gap_h, interval)) or []
        log.info(f"[{symbol}] Vorwärts-Schritt {interval}: {len(out)} Kerzen (Lücke {gap_h:.1f} h, count_only).")
        return out
    out = []
    end_time, last_oldest = now, None
    count = forward_fill_count(gap_h, interval)
    for _ in range(max_pages):
        chunk = await fetch(session, etoro_id, end_time, api_key, user_key, interval, count=count)
        if not chunk:
            break
        out.extend(chunk)
        oldest = _oldest_ts_ns_from_chunk(chunk)
        if oldest is None or oldest <= latest_local_ns or oldest == last_oldest:
            break
        last_oldest = oldest
        end_time = datetime.fromtimestamp(oldest / 1e9, tz=timezone.utc) - timedelta(seconds=1)
        count = forward_fill_count((end_time.timestamp() - latest_local_ns / 1e9) / 3600.0, interval)
    log.info(f"[{symbol}] Vorwärts-Schritt {interval}: {len(out)} Kerzen (Lücke {gap_h:.1f} h).")
    return out


def _oneday_window(symbol: str):
    from automation.api_backfiller import oneday_session_window_for
    return oneday_session_window_for(symbol)


# ─── Per-Symbol Fetch ─────────────────────────────────────────────────────────

async def _fetch_symbol(
    session: aiohttp.ClientSession,
    etoro_id: str,
    symbol: str,
    months: int,
    api_key: str,
    user_key: str,
    price_prec: int,
    size_prec: int,
    start_ns: int = 0,
    backfill_retry_days: int = BACKFILL_RETRY_DAYS_DEFAULT,
) -> bool:
    """Fetches and saves historical candle data for one symbol. Returns True on success.

    Issue #1363 (GH #1259): ZUERST der Vorwärts-Schritt (``fetch_forward_candles``, von ``now`` bis zum
    jüngsten lokalen Tick), DANN der Rückwärts-Schritt. Die erreichte API-Tiefe wird JE INTERVALL
    registriert, sobald dessen Kaskade endet (vorher nur, wenn die GESAMTE Kaskade das Ziel verfehlte —
    da ``OneDay`` das Ziel erreichte, wurde die ``OneHour``-Tiefe nie registriert und jeder Lauf rief
    erneut ab). Ein Intervall mit frisch registrierter Tiefe (< ``backfill_retry_days``) wird rückwärts
    nicht erneut abgerufen.

    Issue #1331 (GH #1225): die `OneHour`/`OneDay`-Kaskade sammelte beide Auflösungen in
    EINER Liste, konvertierte sie mit EINEM Aufruf und schrieb sie in EINE Datei — die
    Auflösung eines Ticks war ab dem Moment des Schreibens nicht mehr rekonstruierbar.
    `candles_by_interval` hält die Kandidaten je Auflösung getrennt; Konvertierung und
    Speicherung laufen unten je Intervall separat (Fix Punkt 2), in getrennte Zielpfade
    (Fix Punkt 3, via `_merge_and_save(..., interval=...)`)."""
    # Delta-update reference file: die primäre (Stunden-)Auflösung bestimmt den Fortschritt,
    # damit ein Delta-Update nicht durch das grobere Tages-Segment verkürzt wird.
    primary_interval = _FETCH_INTERVALS[0]
    dest_file = QUOTE_TICK_PATH / symbol / primary_interval / "data.parquet"
    if start_ns > 0:
        target_start = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc)
    else:
        target_start = datetime.now(timezone.utc) - timedelta(days=30 * months)

    candles_by_interval: dict[str, list[dict]] = {itv: [] for itv in _FETCH_INTERVALS}

    # Issue #1372 — Intervalle OHNE als ``end_time`` gestempelte Probe sind ``count_only``: genau ein
    # Fenster-Abruf (``candlesCount=1000``, kein ``endTime``, keine Rückwärtsschleife). Er ersetzt für
    # dieses Intervall Vorwärts- und Rückwärts-Schritt (die API liefert nur die jüngsten Kerzen).
    window_intervals = [itv for itv in _FETCH_INTERVALS
                        if pagination_mode(symbol, itv) == PAGINATION_COUNT_ONLY]
    for interval in window_intervals:
        latest_local_ns = _get_latest_ts_ns(QUOTE_TICK_PATH / symbol / interval / "data.parquet") \
            if (QUOTE_TICK_PATH / symbol / interval / "data.parquet").exists() else None
        chunk = await _fetch_candle_chunk(session, etoro_id, None, api_key, user_key, interval,
                                          count=_WINDOW_COUNT)
        if chunk:
            candles_by_interval[interval].extend(chunk)
            window = _save_api_window(symbol, interval, chunk)
            if window is not None and latest_local_ns is not None:
                check_forward_gap_horizon(symbol, interval, latest_local_ns,
                                          now=datetime.now(timezone.utc),
                                          window_span_h=window["window_span_h"])
        else:
            log.info(f"[{symbol}] Keine Candles für {interval}.")
        await asyncio.sleep(1.1)

    # Issue #1363 Fix Punkt 1 — Vorwärts-Schritt VOR dem Rückwärts-Schritt (primäre Auflösung).
    if dest_file.exists() and primary_interval not in window_intervals:
        latest_local_ns = _get_latest_ts_ns(dest_file)
        if latest_local_ns is not None:
            candles_by_interval[primary_interval].extend(await fetch_forward_candles(
                session, etoro_id, symbol, latest_local_ns, api_key=api_key, user_key=user_key,
                interval=primary_interval))

    # Delta-update: iterate backwards from the oldest locally stored timestamp
    current_end_time = datetime.now(timezone.utc)
    if dest_file.exists():
        oldest_ns = _get_oldest_ts_ns(dest_file)
        if oldest_ns is not None:
            oldest_dt = datetime.fromtimestamp(oldest_ns / 1e9, tz=timezone.utc)
            current_end_time = oldest_dt - timedelta(seconds=1)
            log.info(f"[{symbol}] Delta-Update: Fetch ab {current_end_time.isoformat()} rückwärts bis {target_start.isoformat()}")
    cascade_end_time = current_end_time

    # Cascade: OneHour first, then OneDay to reach deeper history — jede Auflösung sammelt
    # in ihren EIGENEN Kandidaten-Puffer (kein all_candles.extend() über die Kaskade hinweg).
    for interval in _FETCH_INTERVALS:
        if interval in window_intervals:
            continue                      # Issue #1372: bereits per Fenster-Abruf bedient
        last_oldest_ts_ns: int | None = None
        if cascade_end_time > target_start and inception_bound_is_fresh(
                symbol, interval, retry_days=backfill_retry_days):
            # Issue #1363 — die API-Tiefe dieses Intervalls ist bekannt und jünger als
            # backfill_retry_days beobachtet: kein erneuter (wirkungsloser) Rückwärts-Abruf; die Kaskade
            # setzt an der registrierten Tiefe mit der nächsten Auflösung fort.
            known = inception_bound(symbol, interval)
            log.info(f"[{symbol}] {interval}: API-Fenster bekannt "
                     f"({datetime.fromtimestamp(known / 1e9, tz=timezone.utc).date()}) — kein erneuter "
                     f"Rückwärts-Abruf (< {backfill_retry_days} Tage).")
            cascade_end_time = min(cascade_end_time,
                                   datetime.fromtimestamp(known / 1e9, tz=timezone.utc) - timedelta(seconds=1))
            continue

        while cascade_end_time > target_start:
            chunk = await _fetch_candle_chunk(
                session, etoro_id, cascade_end_time, api_key, user_key, interval
            )

            if not chunk:
                log.info(f"[{symbol}] Keine Candles für {interval} — kaskadiere.")
                break

            oldest_ns = _oldest_ts_ns_from_chunk(chunk)
            if oldest_ns is None:
                break

            # Historical depth reached: API returns the same oldest candle twice
            if last_oldest_ts_ns is not None and oldest_ns == last_oldest_ts_ns:
                log.info(f"[{symbol}] Historische Tiefe für {interval} erreicht.")
                break

            candles_by_interval[interval].extend(chunk)
            last_oldest_ts_ns = oldest_ns

            oldest_dt = datetime.fromtimestamp(oldest_ns / 1e9, tz=timezone.utc)
            cascade_end_time = oldest_dt - timedelta(seconds=1)

            log.debug(
                f"[{symbol}] {interval}: {len(chunk)} Candles, älteste: {oldest_dt.isoformat()}"
            )
            await asyncio.sleep(1.1)

        if cascade_end_time <= target_start:
            log.info(f"[{symbol}] Ziel-Startdatum mit {interval} erreicht.")
            break

        # Issue #1363 Fix Punkt 2 — die Kaskade DIESES Intervalls endete vor dem Ziel: seine API-Tiefe
        # registrieren (unabhängig davon, ob die nächste Auflösung das Ziel noch erreicht).
        _local_oldest = _get_oldest_ts_ns(QUOTE_TICK_PATH / symbol / interval / "data.parquet")
        _depth = min((x for x in (_local_oldest, last_oldest_ts_ns) if x is not None), default=None)
        if _depth is not None:
            _save_inception_bound(symbol, _depth, interval)
            log.info(f"[{symbol}] {interval}: API-Fenster registriert "
                     f"({datetime.fromtimestamp(_depth / 1e9, tz=timezone.utc).isoformat()}).")

    if not any(candles_by_interval.values()):
        log.warning(f"[{symbol}] Keine Candles gefunden — überspringe.")
        return False

    any_saved = False
    for interval, candles in candles_by_interval.items():
        if not candles:
            continue
        table = _candles_to_arrow_table(
            candles, symbol, price_prec, size_prec, target_start, interval=interval,
            asof_ns=int(datetime.now(timezone.utc).timestamp() * 1e9),   # Issue #1373: unfertige Kerzen nicht schreiben
            oneday_session_window=_oneday_window(symbol) if interval == "OneDay" else None,   # Issue #1382
        )
        if table is None or len(table) == 0:
            log.warning(f"[{symbol}] {interval}: Leere Arrow-Table nach Konvertierung.")
            continue

        log.info(f"[{symbol}] {interval}: {len(table)} Ticks → speichere (price_prec={price_prec}).")
        try:
            if _merge_and_save(log, table, symbol, price_prec, size_prec, interval=interval):
                any_saved = True
        except CatalogSchemaVersionMismatch as e:
            log.error(str(e))

    return any_saved


# ─── Main Async Function ──────────────────────────────────────────────────────

async def run_historical_fetch(
    api_key: str,
    user_key: str,
    etoro_id_to_symbol: dict[str, str],
    months: int = 12,
    start_ns: int = 0,
    force: bool = False,
    backfill_retry_days: int = BACKFILL_RETRY_DAYS_DEFAULT,
) -> list[str]:
    """
    Fetches historical data for symbols that are insufficient.
    Skips symbols where is_backtest_range_covered() returns True (unless force=True).
    Returns list of symbols that were fetched/updated.
    """
    if not api_key or not user_key:
        log.warning("[historical_fetcher] API-Keys fehlen — Fetch übersprungen.")
        return []

    QUOTE_TICK_PATH.mkdir(parents=True, exist_ok=True)

    # REPARATUR: Reales Startdatum im Voraus berechnen, falls start_ns = 0 oder negativ ist
    if start_ns <= 0:
        target_start = datetime.now(timezone.utc) - timedelta(days=30 * months)
        real_start_ns = int(target_start.timestamp() * 1e9)
    else:
        real_start_ns = start_ns

    to_fetch = {
        eid: sym
        for eid, sym in etoro_id_to_symbol.items()
        if force or not is_backtest_range_covered(sym, real_start_ns, CATALOG_PATH)
    }

    if not to_fetch:
        log.info("[historical_fetcher] Alle Symbole haben ausreichend Daten.")
        return []

    log.info(
        f"[historical_fetcher] {len(to_fetch)}/{len(etoro_id_to_symbol)} Symbole "
        f"werden gefetcht (months={months}, start_ns={start_ns})."
    )

    fetched: list[str] = []
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Fetch precisions via API (batch)
        etoro_ids = list(to_fetch.keys())
        log.info("[historical_fetcher] Lade Instrument-Precisions via eToro API …")
        try:
            api_precisions = await fetch_precisions_from_api(session, etoro_ids, api_key, user_key)
        except Exception as e:
            log.warning(f"[historical_fetcher] Precision-Fetch Fehler: {e} — nutze Fallback.")
            api_precisions = {}

        log.info(
            f"[historical_fetcher] Precisions via API/Fallback: "
            f"{len(api_precisions)} geladen (restliche via Standard-Equity-Fallback)."
        )

        for etoro_id, symbol in sorted(to_fetch.items(), key=lambda x: x[1]):
            if etoro_id in api_precisions:
                price_prec, size_prec = api_precisions[etoro_id]
            else:
                price_prec, size_prec = _fallback_precisions(symbol)
            price_prec, size_prec = apply_price_precision_floor(symbol, (price_prec, size_prec))

            try:
                ok = await _fetch_symbol(
                    session, etoro_id, symbol, months,
                    api_key, user_key, price_prec, size_prec,
                    start_ns=real_start_ns, backfill_retry_days=backfill_retry_days,
                )
                if ok:
                    fetched.append(symbol)
            except Exception as e:
                log.warning(
                    f"[historical_fetcher] Fehler für {symbol} (ID {etoro_id}): "
                    f"{e}\n{traceback.format_exc()}"
                )

            await asyncio.sleep(1.1)

    log.info(f"[historical_fetcher] Fertig: {len(fetched)} Symbole befüllt.")
    return fetched


# ─── Pre-Sweep Backfill Hook (Issue #531) ────────────────────────────────────

def _default_backfill_fetch(
    symbols: list[str],
    *,
    required_days: int,
    buffer_days: int,
    universe_path: Path,
    api_key: str | None,
    user_key: str | None,
    logger: logging.Logger,
) -> list[str]:
    """Synchroner Default-Backfill für ``ensure_walkforward_history`` (Issue #531).

    Löst Symbole → eToro-IDs (Universe) auf, liest die API-Keys aus der Umgebung/.env und stößt
    einen **synchronen** ``run_historical_fetch`` an, der bis ``now − (required_days + buffer_days)``
    zurückreicht. Fehlen Keys oder das Universe-Mapping, wird sauber (Fail-Open) mit ``[]`` beendet —
    das Sweep-Gate entscheidet danach ohnehin fail-loud über unzureichende Symbole."""
    if not api_key or not user_key:
        load_dotenv(str(ENV_FILE))
        api_key = api_key or os.getenv("ETORO_API_KEY", "")
        user_key = user_key or os.getenv("ETORO_USER_KEY", "")
    if not api_key or not user_key:
        logger.warning("[#531] Backfill übersprungen: ETORO_API_KEY/ETORO_USER_KEY fehlen.")
        return []

    id_map = _load_etoro_id_map(universe_path)
    wanted = set(symbols)
    to_fetch = {eid: sym for eid, sym in id_map.items() if sym in wanted}
    if not to_fetch:
        logger.warning("[#531] Backfill übersprungen: kein Universe-Mapping für %s.", sorted(wanted))
        return []

    depth_days = int(required_days + buffer_days)
    start_ns = int((datetime.now(timezone.utc) - timedelta(days=depth_days)).timestamp() * 1e9)
    months = max(1, (depth_days + 29) // 30)
    return asyncio.run(run_historical_fetch(
        api_key=api_key, user_key=user_key, etoro_id_to_symbol=to_fetch,
        months=months, start_ns=start_ns,
    ))


def measure_span_days(symbol: str, catalog_path: Path = CATALOG_PATH, interval: str = "OneHour") -> float:
    """Rohe Spanne (Tage, ``newest − oldest``) der ``interval``-Datei; 0.0 ohne Datei/Ticks."""
    from automation.catalog_paths import resolve_quote_tick_files

    files = resolve_quote_tick_files(catalog_path, symbol, interval=interval)
    if not files:
        return 0.0
    oldest, newest = _get_oldest_ts_ns(files[0]), _get_latest_ts_ns(files[0])
    if oldest is None or newest is None:
        return 0.0
    return max(0.0, (newest - oldest) / 86_400_000_000_000)


def ensure_walkforward_history(
    symbols: list[str],
    walk_forward_dict: dict,
    *,
    span_days_by_symbol: dict[str, float],
    gate1_buffer_days: int = 0,
    logger: logging.Logger | None = None,
    fetch_fn=None,
    universe_path: Path = UNIVERSE_PATH,
    api_key: str | None = None,
    user_key: str | None = None,
    span_fn=None,
    backfill_retry_days: int = BACKFILL_RETRY_DAYS_DEFAULT,
    now: datetime | None = None,
) -> dict:
    """Issue #531 — Pre-Sweep-Hook: erzwingt die volle Walk-Forward-Historie VOR dem Sweep.

    Liegt die REAL vorhandene Bar-Spanne eines Symbols (``span_days_by_symbol[sym]``, vom Aufrufer
    aus den Parquet-Statistiken injiziert) unter ``required_span_days + gate1_buffer_days`` (Issue #1376: der
    Puffer ist AUSSCHLIESSLICH dieser Backfill-Auslöser, nicht Teil des Historien-Floors), wird ein
    **synchroner** Backfill-Request an den ``historical_fetcher`` abgesetzt, bevor der Sweep iteriert.

    Issue #1363 (GH #1259) — mit NACHBEDINGUNG: die Spanne wird vorher/nachher gemessen (``span_fn``,
    Default ``measure_span_days``); der Report trägt ``span_before_days``/``span_after_days``/
    ``gain_days`` je Symbol. ``gain_days < 1`` ⇒ WARNING ``BACKFILL_NO_GAIN`` und die erreichte Tiefe wird
    registriert (vorher meldete der Hook "3/3 nachgeladen" bei 0 Tagen Zugewinn — und wiederholte den
    wirkungslosen Abruf in jedem Lauf). Symbole, deren ``OneHour``-Tiefe jünger als
    ``backfill_retry_days`` registriert ist, werden NICHT erneut abgerufen (``skipped_known_depth``).

    Wirft NIE — schlägt der Backfill fehl (keine Keys, Netzfehler), entscheidet das nachgelagerte Gate."""
    from automation.optimizer.gate import required_span_days

    log = logger or logging.getLogger("historical_fetcher")
    measure = span_fn or measure_span_days
    required = required_span_days(walk_forward_dict)
    threshold = required + int(gate1_buffer_days)
    deficient = sorted(
        s for s in symbols if float(span_days_by_symbol.get(s, 0.0)) < threshold
    )
    report = {
        "required_days": required,
        "buffer_days": int(gate1_buffer_days),
        "threshold_days": threshold,
        "deficient": deficient,
        "backfilled": [],
        "skipped_known_depth": [],
        "span_before_days": {},
        "span_after_days": {},
        "gain_days": {},
        "no_gain": [],
    }
    if not deficient:
        return report

    to_fetch = []
    for sym in deficient:
        if inception_bound_is_fresh(sym, "OneHour", retry_days=backfill_retry_days, now=now):
            report["skipped_known_depth"].append(sym)
        else:
            to_fetch.append(sym)
    if report["skipped_known_depth"]:
        log.info("[#1363] %d Symbol(e) mit bekanntem API-Fenster (< %d Tage registriert) — kein erneuter "
                 "Rückwärts-Abruf: %s", len(report["skipped_known_depth"]), backfill_retry_days,
                 report["skipped_known_depth"])
    if not to_fetch:
        return report

    log.warning(
        "[#531] %d Symbol(e) unter der Walk-Forward-Schwelle (%d Tage = %d + Puffer %d) — "
        "synchroner Pre-Sweep-Backfill: %s",
        len(to_fetch), threshold, required, int(gate1_buffer_days), to_fetch,
    )
    for sym in to_fetch:
        # Vorher/nachher mit DERSELBEN Messung (``span_fn``) — ``span_days_by_symbol`` (vom Aufrufer, ggf.
        # aus Bar-Zählern abgeleitet) entscheidet nur über die Unterdeckung.
        try:
            before = float(measure(sym))
        except Exception:
            before = float(span_days_by_symbol.get(sym, 0.0))
        report["span_before_days"][sym] = round(before, 4)
    fetch = fetch_fn or _default_backfill_fetch
    try:
        fetched = fetch(
            to_fetch, required_days=required, buffer_days=int(gate1_buffer_days),
            universe_path=universe_path, api_key=api_key, user_key=user_key, logger=log,
        )
        report["backfilled"] = list(fetched or [])
    except Exception as e:  # pragma: no cover - defensiv: Backfill darf den Sweep nie crashen
        log.warning("[#531] Pre-Sweep-Backfill fehlgeschlagen (%s) — Gate-1 entscheidet fail-loud.", e)

    for sym in to_fetch:
        try:
            after = float(measure(sym))
        except Exception:
            after = report["span_before_days"][sym]
        report["span_after_days"][sym] = round(after, 4)
        gain = after - report["span_before_days"][sym]
        report["gain_days"][sym] = round(gain, 4)
        if gain < 1.0:
            report["no_gain"].append(sym)
    if report["no_gain"]:
        log.warning("[#1363] BACKFILL_NO_GAIN: %d Symbol(e) ohne Spannen-Zugewinn (< 1 Tag): %s — die "
                    "API liefert nicht tiefer; Tiefe registriert, kein erneuter Abruf für %d Tage.",
                    len(report["no_gain"]), {s: report["gain_days"][s] for s in report["no_gain"]},
                    backfill_retry_days)
        try:
            from automation.log_manager import emit_execution_event
            emit_execution_event(log, "BACKFILL_NO_GAIN", {
                "symbols": report["no_gain"],
                "span_before_days": {s: report["span_before_days"][s] for s in report["no_gain"]},
                "span_after_days": {s: report["span_after_days"][s] for s in report["no_gain"]},
                "required_days": required, "backfill_retry_days": backfill_retry_days,
            }, level=logging.WARNING)
        except Exception:
            pass
        for sym in report["no_gain"]:
            files_oldest = None
            try:
                from automation.catalog_paths import resolve_quote_tick_files
                files = resolve_quote_tick_files(CATALOG_PATH, sym, interval="OneHour")
                files_oldest = _get_oldest_ts_ns(files[0]) if files else None
            except Exception:
                files_oldest = None
            # Ohne lokale Daten ist keine Tiefe bekannt (nichts geliefert ≠ API-Tiefe erreicht) — dann keine
            # Retry-Sperre, der nächste Lauf versucht es erneut.
            if files_oldest is not None:
                _save_inception_bound(sym, files_oldest, "OneHour", now=now)
    log.info("[#531] Pre-Sweep-Backfill abgeschlossen: %d/%d Symbol(e) geschrieben, Zugewinn je Symbol "
             "(Tage): %s.", len(report["backfilled"]), len(to_fetch), report["gain_days"])
    return report


# ─── Catalog Rebuild (Issue #1333 / GH #1227, Issue #1364 / GH #1260) ────────

_DAY_NS = 86_400_000_000_000
from automation.catalog_paths import REALTICK_INTERVAL  # noqa: E402  (Issue #1354/#1366)


class HistoryLossRefused(RuntimeError):
    """Issue #1364 (GH #1260) Fix Punkt 3: der Probelauf sagt ``history_lost_days > 0`` voraus und
    ``--accept-history-loss`` fehlt. Wird VOR jeder Verschiebung geworfen — der Katalog ist
    unverändert."""


def _utc_ts_label(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _file_range_ns(parquet_file: Path) -> tuple[int, int] | None:
    """``(erster, letzter) ts_event`` einer Katalogdatei, ``None`` bei leer/nicht lesbar."""
    try:
        import pyarrow.compute as pc
        t = pq.read_table(str(parquet_file), columns=["ts_event"])
        if len(t) == 0:
            return None
        mm = pc.min_max(t.column("ts_event")).as_py()
        return int(mm["min"]), int(mm["max"])
    except Exception:
        return None


def uncovered_days(old: tuple[int, int], new: tuple[int, int] | None) -> float:
    """Tage des alten Bereichs ``old`` = ``[first, last]``, die der neue Bereich ``new`` NICHT
    abdeckt — die Messgrösse hinter ``history_lost_days`` (Issue #1364 Fix Punkt 2). Reine
    Funktion; ``new = None`` (nichts neu geliefert) ⇒ der ganze alte Bereich."""
    old_first, old_last = old
    if new is None:
        return max(0, old_last - old_first) / _DAY_NS
    new_first, new_last = new
    lost = max(0, min(new_first, old_last) - old_first)
    lost += max(0, old_last - max(new_last, old_first))
    return lost / _DAY_NS


def classify_catalog_files(inst_dir: Path) -> list[dict]:
    """Klassifiziert alle Datendateien eines Instrument-Katalogverzeichnisses für den Rebuild.

    Je Eintrag: ``interval`` (``OneHour``/``OneDay``/``RealTick``/…), ``path``, ``range`` (ns oder
    ``None``), ``version`` (``catalog_schema_version`` oder ``None``), ``representable`` (lässt sich
    ins aktuelle Schema zurückführen — gleiche Version oder registrierte Migration, siehe
    ``api_backfiller.has_schema_migration``) und ``reason``.

    * ``<interval>/data.parquet`` mit Kerzen-Auflösung: darstellbar ⇔ gleiche/migrierbare Version.
      Eine v1-Ein-Tick-Kerze (Version ``None``) ist NICHT darstellbar.
    * ``RealTick/data.parquet`` und die flache ``data.parquet`` (Echt-Ticks des ``catalog_service``,
      siehe ``daily_orchestrator._merge_symbol``): darstellbar als ``RealTick`` — es sei denn, die
      flache Datei deklariert per ``catalog_interval`` eine Kerzen-Auflösung.
    * unbekannte Unterverzeichnisse: nicht darstellbar (``unknown_interval``)."""
    from automation.api_backfiller import (
        CATALOG_SCHEMA_VERSION, INTERVAL_TO_NS, _read_catalog_schema_version, has_schema_migration,
    )

    entries: list[dict] = []
    if not inst_dir.is_dir():
        return entries

    def _entry(path: Path, interval: str) -> dict:
        version = _read_catalog_schema_version(path)
        if interval == REALTICK_INTERVAL:
            representable, reason = True, "realtick"
        elif interval in INTERVAL_TO_NS:
            if version == CATALOG_SCHEMA_VERSION:
                representable, reason = True, "same_schema_version"
            elif has_schema_migration(version):
                representable, reason = True, "migratable"
            else:
                representable, reason = False, f"schema_version_{version!r}_not_representable"
        else:
            representable, reason = False, "unknown_interval"
        return {
            "interval": interval, "path": path, "range": _file_range_ns(path),
            "version": version, "representable": representable, "reason": reason,
        }

    flat = inst_dir / "data.parquet"
    if flat.is_file():
        declared = None
        try:
            declared = (pq.read_schema(str(flat)).metadata or {}).get(b"catalog_interval")
        except Exception:
            declared = None
        declared_s = declared.decode() if declared else REALTICK_INTERVAL
        entries.append(_entry(flat, declared_s if declared_s in INTERVAL_TO_NS else REALTICK_INTERVAL))
    for sub in sorted(p for p in inst_dir.iterdir() if p.is_dir()):
        f = sub / "data.parquet"
        if f.is_file():
            entries.append(_entry(f, sub.name))
    return entries


def predict_history_loss(
    symbols: list[str],
    probe_oldest_ns: dict[str, dict[str, int | None]],
    *,
    now_ns: int | None = None,
    quote_tick_path: Path | None = None,
) -> dict[str, dict[str, dict]]:
    """Probelauf (Issue #1364 Fix Punkt 3): sagt je Symbol und Intervall ``history_lost_days``
    VORAUS, ohne etwas zu verschieben. Darstellbare Dateien kommen aus dem Archiv zurück ⇒ 0.
    Nicht darstellbare Kerzen-Dateien gehen verloren, soweit ihr Bereich nicht von dem abgedeckt
    wird, was die API liefert (``probe_oldest_ns[symbol][interval]`` = ältester per API erreichbarer
    Zeitstempel, ``None``/fehlend = unbekannt ⇒ nichts abgedeckt, konservativ)."""
    root = Path(quote_tick_path) if quote_tick_path is not None else QUOTE_TICK_PATH
    now_ns = now_ns if now_ns is not None else int(datetime.now(timezone.utc).timestamp() * 1e9)
    out: dict[str, dict[str, dict]] = {}
    for sym in symbols:
        per_interval: dict[str, dict] = {}
        for e in classify_catalog_files(root / sym):
            if e["range"] is None:
                continue
            if e["representable"]:
                lost = 0.0
            else:
                oldest = (probe_oldest_ns.get(sym) or {}).get(e["interval"])
                lost = uncovered_days(e["range"], None if oldest is None else (int(oldest), now_ns))
            per_interval[e["interval"]] = {
                "predicted_history_lost_days": lost, "representable": e["representable"],
                "reason": e["reason"],
            }
        out[sym] = per_interval
    return out


async def _probe_symbol_depth(
    session: aiohttp.ClientSession, etoro_id: str, symbol: str, intervals: list[str],
    api_key: str, user_key: str, target_start: datetime,
) -> dict[str, int | None]:
    """Ältester per API erreichbarer Zeitstempel je Auflösung (paginiert rückwärts bis zur
    Tiefengrenze — wie ``_fetch_symbol``, aber ohne etwas zu schreiben)."""
    result: dict[str, int | None] = {}
    for interval in intervals:
        if pagination_mode(symbol, interval) == PAGINATION_COUNT_ONLY:
            # Issue #1372 — zählerbasierter Endpunkt: ein Abruf, die „Tiefe" ist das API-Fenster.
            window = await _fetch_candle_chunk(session, etoro_id, None, api_key, user_key, interval,
                                               count=_WINDOW_COUNT)
            result[interval] = _oldest_ts_ns_from_chunk(window) if window else None
            await asyncio.sleep(1.1)
            continue
        end_time = datetime.now(timezone.utc)
        last_oldest: int | None = None
        reached: int | None = None
        while end_time > target_start:
            chunk = await _fetch_candle_chunk(session, etoro_id, end_time, api_key, user_key, interval)
            if not chunk:
                break
            oldest_ns = _oldest_ts_ns_from_chunk(chunk)
            if oldest_ns is None or (last_oldest is not None and oldest_ns == last_oldest):
                break
            reached = oldest_ns if reached is None else min(reached, oldest_ns)
            last_oldest = oldest_ns
            end_time = datetime.fromtimestamp(oldest_ns / 1e9, tz=timezone.utc) - timedelta(seconds=1)
            await asyncio.sleep(1.1)
        result[interval] = reached
    return result


def _default_probe_fn(api_key: str, user_key: str, id_by_symbol: dict[str, str], months: int):
    """Default-Probe für ``rebuild_catalog_with_report``: echte API-Tiefenmessung."""
    def _probe(needed: dict[str, list[str]]) -> dict[str, dict[str, int | None]]:
        async def _run() -> dict[str, dict[str, int | None]]:
            target_start = datetime.now(timezone.utc) - timedelta(days=30 * months)
            out: dict[str, dict[str, int | None]] = {}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                for sym, intervals in sorted(needed.items()):
                    eid = id_by_symbol.get(sym)
                    if eid is None:
                        out[sym] = {}
                        continue
                    out[sym] = await _probe_symbol_depth(
                        session, eid, sym, intervals, api_key, user_key, target_start)
            return out
        return asyncio.run(_run())
    return _probe


def archive_instrument_catalog(
    symbol: str, archive_dir: Path, *, quote_tick_path: Path | None = None,
) -> Path | None:
    """Verschiebt ``quote_tick/<symbol>/`` atomar (``os.replace``) nach ``archive_dir/<symbol>/``.
    Löscht nie (Issue #1364 Fix Punkt 1). ``None``, wenn es nichts zu archivieren gibt."""
    root = Path(quote_tick_path) if quote_tick_path is not None else QUOTE_TICK_PATH
    inst_dir = root / symbol
    if not inst_dir.exists():
        return None
    dest = archive_dir / symbol
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(inst_dir, dest)
    return dest


def archive_all_instrument_catalogs(quote_tick_path: Path, archive_dir: Path) -> list[Path]:
    """Verschiebt JEDES Instrument-Verzeichnis unter ``quote_tick_path`` ins Archiv (Pfad für
    ``daily_orchestrator --reset-catalog``, Issue #1364). Nie löschen; Rückgabe: die Archivpfade."""
    out: list[Path] = []
    if not quote_tick_path.is_dir():
        return out
    for inst in sorted(p for p in quote_tick_path.iterdir() if p.is_dir()):
        dest = archive_instrument_catalog(inst.name, archive_dir, quote_tick_path=quote_tick_path)
        if dest is not None:
            out.append(dest)
    return out


def _restore_from_archive(
    symbol: str, archived_dir: Path, *, quote_tick_path: Path, logger: logging.Logger,
) -> dict[str, dict]:
    """Führt alle darstellbaren Zeilen aus ``archived_dir`` in den frisch gebauten Katalog zurück
    und liefert den Bericht je Intervall (``history_lost_days`` aus dem alten gegen den neuen
    Bereich nach der Rückführung)."""
    from automation.api_backfiller import (
        CATALOG_SCHEMA_VERSION, INTERVAL_TO_NS, SCHEMA_MIGRATIONS, restore_archived_rows,
        schema_migration_path,
    )

    report: dict[str, dict] = {}
    for e in classify_catalog_files(archived_dir):
        interval = e["interval"]
        old_range = e["range"]
        entry = {
            "representable": e["representable"], "reason": e["reason"],
            "restored_rows": 0, "archive_path": str(e["path"]),
        }
        if e["representable"] and old_range is not None:
            table = pq.read_table(str(e["path"]))
            meta = table.schema.metadata or {}
            if interval in INTERVAL_TO_NS and e["version"] != CATALOG_SCHEMA_VERSION:
                for step in schema_migration_path(1 if e["version"] is None else e["version"],
                                                  CATALOG_SCHEMA_VERSION) or []:
                    table = SCHEMA_MIGRATIONS[step](table)
                    table = table.replace_schema_metadata(meta)
            live_file = quote_tick_path / symbol / interval / "data.parquet"
            live_meta = {}
            if live_file.exists():
                try:
                    live_meta = pq.read_schema(str(live_file)).metadata or {}
                except Exception:
                    live_meta = {}

            def _prec(key: bytes, fallback: int) -> int:
                for m in (live_meta, meta):
                    if key in m:
                        try:
                            return int(m[key].decode())
                        except ValueError:
                            pass
                return fallback

            fb_price, fb_size = _fallback_precisions(symbol)
            try:
                entry["restored_rows"] = restore_archived_rows(
                    logger, table, symbol, interval,
                    price_prec=_prec(b"price_precision", fb_price),
                    size_prec=_prec(b"size_precision", fb_size),
                    quote_tick_path=quote_tick_path,
                )
            except CatalogSchemaVersionMismatch as exc:
                logger.error(str(exc))
                entry["error"] = str(exc)
        live_file = quote_tick_path / symbol / interval / "data.parquet"
        new_range = _file_range_ns(live_file) if live_file.exists() else None
        entry["old_first_utc"] = (
            datetime.fromtimestamp(old_range[0] / 1e9, tz=timezone.utc).isoformat() if old_range else None)
        entry["old_last_utc"] = (
            datetime.fromtimestamp(old_range[1] / 1e9, tz=timezone.utc).isoformat() if old_range else None)
        entry["new_first_utc"] = (
            datetime.fromtimestamp(new_range[0] / 1e9, tz=timezone.utc).isoformat() if new_range else None)
        entry["new_last_utc"] = (
            datetime.fromtimestamp(new_range[1] / 1e9, tz=timezone.utc).isoformat() if new_range else None)
        entry["history_lost_days"] = uncovered_days(old_range, new_range) if old_range else 0.0
        report[interval] = entry
    return report


def rebuild_catalog_with_report(
    api_key: str,
    user_key: str,
    etoro_id_to_symbol: dict[str, str],
    target: str,
    months: int = 12,
    *,
    accept_history_loss: bool = False,
    probe_fn=None,
    fetch_fn=None,
    now: datetime | None = None,
    quote_tick_path: Path | None = None,
    archive_root: Path | None = None,
) -> dict:
    """Baut den Katalog für ``target`` (Symbol oder ``"all"``) aus der API neu auf — OHNE Historie zu
    vernichten (Issue #1364 / GH #1260; ersetzt die ``rmtree``-Variante aus #1333/GH #1227):

    1. Probelauf VOR jeder Verschiebung: ``predict_history_loss``. Sagt er ``history_lost_days > 0``
       voraus und fehlt ``accept_history_loss`` ⇒ ``HistoryLossRefused``, nichts verschoben.
    2. Jedes Instrument-Verzeichnis wird atomar nach ``<catalog>/archive/<UTC-ts>/<symbol>/``
       VERSCHOBEN (``os.replace``), nie gelöscht.
    3. Neuaufbau aus der API (``fetch_fn`` oder ``run_historical_fetch(force=True)``).
    4. Darstellbare archivierte Zeilen (gleiche ``catalog_schema_version`` / registrierte Migration /
       Echt-Ticks) werden zurückgeführt (Dedup wie ``_merge_and_save``, frische Zeilen gewinnen);
       nicht darstellbare (z. B. v1-Ein-Tick-Kerzen) bleiben im Archiv.
    5. Bericht ``history_lost_days`` je Symbol und Intervall (auch als ``rebuild_report.json`` im
       Archivordner).

    Rückgabe: ``{"rebuilt": [...], "archive_dir": str|None, "symbols": {sym: {interval: {...}}},
    "predicted": {...}, "history_lost_days_total": float}``."""
    root = Path(quote_tick_path) if quote_tick_path is not None else QUOTE_TICK_PATH
    from automation.catalog_paths import catalog_archive_root

    symbols = sorted(set(etoro_id_to_symbol.values())) if target == "all" else [target]
    wanted = set(symbols)
    to_fetch = {eid: sym for eid, sym in etoro_id_to_symbol.items() if sym in wanted}
    report: dict = {"rebuilt": [], "archive_dir": None, "symbols": {}, "predicted": {},
                    "history_lost_days_total": 0.0}
    if not to_fetch:
        log.error(f"[historical_fetcher] --rebuild-catalog: Symbol(e) {sorted(wanted)} nicht im Universe.")
        return report

    # 1. Probelauf — nur Symbole mit nicht darstellbaren Kerzen-Dateien brauchen eine API-Messung.
    needed: dict[str, list[str]] = {}
    for sym in symbols:
        for e in classify_catalog_files(root / sym):
            if not e["representable"] and e["range"] is not None:
                needed.setdefault(sym, []).append(e["interval"])
    probe = probe_fn
    if probe is None and needed:
        id_by_symbol = {sym: eid for eid, sym in etoro_id_to_symbol.items()}
        probe = _default_probe_fn(api_key, user_key, id_by_symbol, months)
    probed = probe(needed) if (probe is not None and needed) else {}
    now_dt = now or datetime.now(timezone.utc)
    predicted = predict_history_loss(
        symbols, probed, now_ns=int(now_dt.timestamp() * 1e9), quote_tick_path=root)
    report["predicted"] = predicted
    predicted_total = sum(
        v["predicted_history_lost_days"] for per in predicted.values() for v in per.values())
    if predicted_total > 0 and not accept_history_loss:
        detail = {
            sym: {itv: round(v["predicted_history_lost_days"], 2) for itv, v in per.items()
                  if v["predicted_history_lost_days"] > 0}
            for sym, per in predicted.items()
            if any(v["predicted_history_lost_days"] > 0 for v in per.values())
        }
        raise HistoryLossRefused(
            f"[#1364] --rebuild-catalog sagt {predicted_total:.1f} verlorene Historie-Tage voraus "
            f"({detail}). Nichts wurde verschoben. Mit --accept-history-loss bewusst fortfahren "
            f"(die Zeilen bleiben im Archiv unter {catalog_archive_root(root.parent.parent)}/)."
        )

    # 2. Archivieren (atomar verschieben, nie löschen).
    archive_dir = (Path(archive_root) if archive_root is not None
                   else catalog_archive_root(root.parent.parent)) / _utc_ts_label(now_dt)
    archived: dict[str, Path] = {}
    for sym in symbols:
        dest = archive_instrument_catalog(sym, archive_dir, quote_tick_path=root)
        if dest is not None:
            log.warning(f"[{sym}] --rebuild-catalog: Katalog nach {dest} archiviert (nicht gelöscht).")
            archived[sym] = dest
    report["archive_dir"] = str(archive_dir) if archived else None

    # 3. Neuaufbau aus der API.
    if fetch_fn is not None:
        rebuilt = list(fetch_fn(to_fetch) or [])
    else:
        rebuilt = asyncio.run(run_historical_fetch(
            api_key=api_key, user_key=user_key, etoro_id_to_symbol=to_fetch,
            months=months, force=True,
        ))
    report["rebuilt"] = rebuilt

    # 4./5. Rückführung + Bericht (auch für Symbole, die die API nicht mehr geliefert hat).
    for sym, dest in archived.items():
        report["symbols"][sym] = _restore_from_archive(sym, dest, quote_tick_path=root, logger=log)
    report["history_lost_days_total"] = sum(
        v["history_lost_days"] for per in report["symbols"].values() for v in per.values())
    if archived:
        try:
            (archive_dir / "rebuild_report.json").write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - Bericht ist Zusatz, kein Abbruchgrund
            log.warning(f"[historical_fetcher] rebuild_report.json nicht schreibbar: {exc}")
    return report


def rebuild_catalog(
    api_key: str,
    user_key: str,
    etoro_id_to_symbol: dict[str, str],
    target: str,
    months: int = 12,
    *,
    accept_history_loss: bool = False,
    **kwargs,
) -> list[str]:
    """Kompatibilitäts-Wrapper um ``rebuild_catalog_with_report`` — liefert die Liste der neu
    befüllten Symbole. Wirft ``HistoryLossRefused`` bei vorhergesagtem Verlust ohne
    ``accept_history_loss``."""
    return rebuild_catalog_with_report(
        api_key, user_key, etoro_id_to_symbol, target, months,
        accept_history_loss=accept_history_loss, **kwargs,
    )["rebuilt"]


def migrate_catalog(target: str, etoro_id_to_symbol: dict[str, str] | None = None) -> list[Path]:
    """CLI-Pfad ``--migrate-catalog`` (Issue #1364 Fix Punkt 4): verlustfreie Schema-Migration
    ``catalog_schema_version`` → aktuelle Version für ``target`` (Symbol oder ``"all"``).
    Wirft ``CatalogSchemaMigrationUnavailable``, wenn für die gefundene Version keine Migration
    registriert ist."""
    from automation.api_backfiller import (
        CATALOG_SCHEMA_VERSION, _read_catalog_schema_version, INTERVAL_TO_NS, migrate_catalog_schema,
    )
    symbols = None if target == "all" else [target]
    migrated: list[Path] = []
    found: set[int] = set()
    if QUOTE_TICK_PATH.is_dir():
        for inst in QUOTE_TICK_PATH.iterdir():
            if symbols is not None and inst.name not in symbols:
                continue
            for itv in INTERVAL_TO_NS:
                f = inst / itv / "data.parquet"
                if f.exists():
                    v = _read_catalog_schema_version(f)
                    found.add(1 if v is None else v)
    for from_v in sorted(found - {CATALOG_SCHEMA_VERSION}):
        migrated += migrate_catalog_schema(from_v, CATALOG_SCHEMA_VERSION, symbols=symbols)
    return migrated


# ─── OneDay-Vollfenster (Issue #1276) ────────────────────────────────────────

ONEDAY = "OneDay"


def oneday_span_days(symbol: str, quote_tick_path: Path | None = None) -> dict:
    """``{oneday_n_candles, oneday_oldest_utc, oneday_effective_span_days}`` aus ``<symbol>/OneDay/data.parquet``
    (read-only). Je Tageskerze liegen bis zu vier O/L/H/C-Ticks; gezählt wird nach ``ts_event``-Tagen."""
    path = Path(quote_tick_path or QUOTE_TICK_PATH) / symbol / ONEDAY / "data.parquet"
    empty = {"oneday_n_candles": 0, "oneday_oldest_utc": None, "oneday_effective_span_days": None}
    if not path.exists():
        return empty
    try:
        ts = pq.read_table(str(path), columns=["ts_event"]).column("ts_event").to_pylist()
    except Exception:
        return empty
    if not ts:
        return empty
    # O/L/H-Ticks liegen ab candle_start, der Close-Tick bei candle_end − 1 ns: alle Ticks einer Tageskerze
    # (fromDate = UTC-Mitternacht) fallen in denselben UTC-Tag. Welche Bezugszeit eToro tatsächlich liefert,
    # klärt der Messlauf aus #1277 (oneday_definition).
    days = {int(t // 86_400_000_000_000) for t in ts}
    oldest_day = min(days)
    oldest = datetime.fromtimestamp(oldest_day * 86400, tz=timezone.utc)
    newest = datetime.fromtimestamp(max(days) * 86400, tz=timezone.utc)
    return {"oneday_n_candles": len(days), "oneday_oldest_utc": oldest.strftime("%Y-%m-%d"),
            "oneday_effective_span_days": float((newest - oldest).days + 1)}


async def fetch_oneday_full_window(
    session, etoro_id: str, symbol: str, price_prec: int, size_prec: int, *, api_key: str, user_key: str,
    now: datetime | None = None, fetch_chunk=None,
) -> dict | None:
    """Issue #1276 (GH #1149, Katalog #1374) Fix Punkt 1 — EIN Abruf (``candlesCount=1000``, #1372) und
    Speichern OHNE ``target_start``-Schnitt nach ``<symbol>/OneDay/data.parquet``
    (``_merge_and_save(..., interval="OneDay")``). ``OneHour/data.parquet`` wird nie angefasst. Gibt die
    Messgrössen (Anzahl, ältester Tag) zurück, ``None`` bei leerer Antwort/Tabelle."""
    from automation.api_backfiller import _candles_to_arrow_table, _merge_and_save, oneday_session_window_for
    fetch = fetch_chunk or _fetch_candle_chunk
    now = now or datetime.now(timezone.utc)
    chunk = await fetch(session, etoro_id, None, api_key, user_key, ONEDAY, count=_WINDOW_COUNT)
    if not chunk:
        log.info(f"[{symbol}] Keine Candles für {ONEDAY}.")
        return None
    _save_api_window(symbol, ONEDAY, chunk)
    table = _candles_to_arrow_table(
        chunk, symbol, price_prec, size_prec, datetime.fromtimestamp(0, tz=timezone.utc), interval=ONEDAY,
        asof_ns=int(now.timestamp() * 1e9),                              # Issue #1373: nur fertige Tageskerzen
        oneday_session_window=oneday_session_window_for(symbol),         # Issue #1382: Ticks in der Handelstag-Session
    )
    if table is None or len(table) == 0:
        log.warning(f"[{symbol}] {ONEDAY}: Leere Arrow-Table nach Konvertierung.")
        return None
    _merge_and_save(log, table, symbol, price_prec, size_prec, interval=ONEDAY)
    info = oneday_span_days(symbol)
    log.info(f"[{symbol}] ONEDAY_FULL_WINDOW_SAVED n_candles={info['oneday_n_candles']} "
             f"oldest={info['oneday_oldest_utc']} span_d={info['oneday_effective_span_days']}")
    try:
        from automation.log_manager import emit_execution_event
        emit_execution_event(log, "ONEDAY_FULL_WINDOW_SAVED", {"symbol": symbol, **info})
    except Exception:  # pragma: no cover - Telemetrie darf den Abruf nie stoppen
        pass
    return info


async def run_oneday_full_window(
    api_key: str, user_key: str, etoro_id_to_symbol: dict[str, str], symbols: list[str],
) -> dict[str, dict]:
    """``--interval OneDay --full-window --symbols …`` — ``{symbol: {oneday_n_candles, …}}`` je Symbol."""
    wanted = {x.strip().upper().removesuffix(".ETORO") for x in symbols if x.strip()}
    targets = {eid: sym for eid, sym in etoro_id_to_symbol.items()
               if sym.upper().removesuffix(".ETORO") in wanted}
    result: dict[str, dict] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        try:
            precisions = await fetch_precisions_from_api(session, list(targets), api_key, user_key)
        except Exception as exc:
            log.warning(f"[historical_fetcher] Precision-Fetch Fehler: {exc} — nutze Fallback.")
            precisions = {}
        for eid, sym in sorted(targets.items(), key=lambda x: x[1]):
            price_prec, size_prec = apply_price_precision_floor(
                sym, precisions.get(eid) or _fallback_precisions(sym))
            info = await fetch_oneday_full_window(
                session, eid, sym, price_prec, size_prec, api_key=api_key, user_key=user_key)
            if info is not None:
                result[sym] = info
            await asyncio.sleep(1.1)
    return result


# ─── CLI Entry-Point ──────────────────────────────────────────────────────────

async def run_pagination_probe(
    api_key: str, user_key: str, etoro_id_to_symbol: dict[str, str], symbols: list[str],
    intervals: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Issue #1372 — Pagination-Probe für ``symbols`` (mit oder ohne ``.ETORO``-Suffix) je Intervall;
    ergibt ``{symbol: {interval: pagination_mode}}`` und stempelt jedes Ergebnis."""
    wanted = {x.strip().upper().removesuffix(".ETORO") for x in symbols if x.strip()}
    targets = {eid: sym for eid, sym in etoro_id_to_symbol.items()
               if sym.upper().removesuffix(".ETORO") in wanted}
    result: dict[str, dict[str, str]] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        for eid, sym in sorted(targets.items(), key=lambda x: x[1]):
            for interval in intervals or list(_FETCH_INTERVALS):
                mode = await probe_pagination(session, eid, sym, interval, api_key=api_key, user_key=user_key)
                result.setdefault(sym, {})[interval] = mode
                log.info(f"[{sym}] Pagination-Probe {interval}: {mode}")
                await asyncio.sleep(1.1)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="eToro Nautilus Historical Fetcher (Standalone)",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--months", type=int, default=12, help="Anzahl Monate Historie (Standard: 12)")
    parser.add_argument("--symbol", type=str, default=None, help="Nur dieses Symbol fetchen (z.B. TSLA.ETORO)")
    parser.add_argument("--force", action="store_true", help="Auch Symbole mit ausreichend Daten neu fetchen")
    parser.add_argument("--start-ns", type=int, default=0, help="Mindest-Start-Timestamp in ns")
    parser.add_argument("--universe", type=Path, default=UNIVERSE_PATH, help="Pfad zur Universe-JSON")
    parser.add_argument(
        "--rebuild-catalog", type=str, default=None, metavar="SYMBOL|all",
        help="Verwirft den bestehenden Katalog für SYMBOL (oder 'all') und baut ihn vollständig "
             "aus der API neu auf (Issue #1333/GH #1227) — erzeugt catalog_schema_version=2.",
    )
    parser.add_argument(
        "--accept-history-loss", action="store_true",
        help="--rebuild-catalog trotz vorhergesagtem Historie-Verlust (history_lost_days > 0) ausführen "
             "(Issue #1364/GH #1260). Der Katalog wird nie gelöscht, sondern nach "
             "data/nautilus/archive/ verschoben.",
    )
    parser.add_argument(
        "--migrate-catalog", type=str, default=None, metavar="SYMBOL|all",
        help="Verlustfreie catalog_schema_version-Migration (Issue #1364/GH #1260) statt Rebuild.",
    )
    parser.add_argument(
        "--probe-pagination", type=str, default=None, metavar="SYMBOL[,…]",
        help="Issue #1372: prüft je Symbol/Intervall, ob der Candle-Endpunkt `endTime` auswertet "
             "(pagination_mode end_time) oder zählerbasiert ist (count_only), und stempelt das Ergebnis.",
    )
    parser.add_argument("--interval", type=str, default=None, choices=list(_FETCH_INTERVALS),
                        help="Nur mit --probe-pagination: nur dieses Intervall (Default: beide).")
    parser.add_argument("--full-window", action="store_true",
                        help="Issue #1276: mit --interval OneDay --symbol/--symbols: ein Abruf (candlesCount=1000) "
                             "und Speichern OHNE target_start-Schnitt nach <symbol>/OneDay/data.parquet.")
    parser.add_argument("--symbols", type=str, default=None, metavar="SYMBOL[,…]",
                        help="Symbolliste für --full-window (mit oder ohne .ETORO-Suffix).")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    QUOTE_TICK_PATH.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data" / "state").mkdir(parents=True, exist_ok=True)

    load_dotenv(str(ENV_FILE))
    api_key = os.getenv("ETORO_API_KEY", "")
    user_key = os.getenv("ETORO_USER_KEY", "")

    if not api_key or not user_key:
        log.error("[historical_fetcher] ETORO_API_KEY oder ETORO_USER_KEY fehlen in .env — Abbruch.")
        return 1

    etoro_id_map = _load_etoro_id_map(Path(args.universe))
    if not etoro_id_map:
        log.error("[historical_fetcher] Keine Instrumente im Universe — Abbruch.")
        return 1

    if args.full_window:
        if args.interval != ONEDAY or not (args.symbols or args.symbol):
            log.error("[historical_fetcher] --full-window braucht --interval OneDay und --symbols.")
            return 1
        out = asyncio.run(run_oneday_full_window(
            api_key, user_key, etoro_id_map, (args.symbols or args.symbol).split(",")))
        print(json.dumps(out, indent=2))
        return 0 if out else 1

    if args.probe_pagination:
        modes = asyncio.run(run_pagination_probe(
            api_key, user_key, etoro_id_map, args.probe_pagination.split(","),
            [args.interval] if args.interval else None))
        print(json.dumps(modes, indent=2))
        return 0 if modes else 1

    if args.migrate_catalog:
        from automation.api_backfiller import CatalogSchemaMigrationUnavailable
        try:
            migrated = migrate_catalog(args.migrate_catalog, etoro_id_map)
        except CatalogSchemaMigrationUnavailable as exc:
            log.error(str(exc))
            return 3
        log.info(f"[historical_fetcher] Migration abgeschlossen: {len(migrated)} Dateien.")
        return 0

    if args.rebuild_catalog:
        try:
            rep = rebuild_catalog_with_report(
                api_key=api_key, user_key=user_key, etoro_id_to_symbol=etoro_id_map,
                target=args.rebuild_catalog, months=args.months,
                accept_history_loss=args.accept_history_loss,
            )
        except HistoryLossRefused as exc:
            log.error(str(exc))
            return 2
        rebuilt = rep["rebuilt"]
        log.info(
            f"[historical_fetcher] Rebuild abgeschlossen: {len(rebuilt)} Symbole befüllt: {rebuilt}; "
            f"history_lost_days_total={rep['history_lost_days_total']:.1f}; Archiv: {rep['archive_dir']}"
        )
        return 0 if rebuilt else 1

    if args.symbol:
        etoro_id_map = {k: v for k, v in etoro_id_map.items() if v == args.symbol}
        if not etoro_id_map:
            log.error(f"[historical_fetcher] Symbol '{args.symbol}' nicht im Universe.")
            return 1

    fetched = asyncio.run(
        run_historical_fetch(
            api_key=api_key,
            user_key=user_key,
            etoro_id_to_symbol=etoro_id_map,
            months=args.months,
            start_ns=args.start_ns,
            force=args.force,
        )
    )

    log.info(f"[historical_fetcher] Abgeschlossen: {len(fetched)} Symbole befüllt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())