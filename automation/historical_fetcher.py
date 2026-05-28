#!/usr/bin/env python3
"""
automation/historical_fetcher.py
=================================
Standalone historical candle fetcher for eToro Nautilus.
Fetches up to 12 months (default) of hourly + daily candles per instrument.
Writes raw PyArrow FSB(16) to data/nautilus/data/quote_tick/SYMBOL/data.parquet
— same format as api_backfiller.py.

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

_BASE_URL = "https://public-api.etoro.com/api/v1/market-data"
_CANDLES_URL = f"{_BASE_URL}/instruments/{{etoro_id}}/history/candles/desc/{{interval}}/{{count}}"

log = logging.getLogger("historical_fetcher")

# Reuse Arrow encoding, merge logic, precision heuristic, and id map loader from api_backfiller
try:
    from automation.api_backfiller import (
        _candles_to_arrow_table,
        _merge_and_save,
        _fallback_precisions,
        _load_etoro_id_map,
        fetch_precisions_from_api,
    )
except ImportError:
    sys.path.insert(0, str(PROJECT_ROOT))
    from automation.api_backfiller import (
        _candles_to_arrow_table,
        _merge_and_save,
        _fallback_precisions,
        _load_etoro_id_map,
        fetch_precisions_from_api,
    )


# ─── Sufficiency Check ────────────────────────────────────────────────────────

def is_symbol_data_sufficient(
    symbol: str,
    min_bars: int = 200,
    catalog_path: Path = CATALOG_PATH,
) -> bool:
    """Returns True if symbol has >= min_bars rows in its data.parquet."""
    parquet_file = catalog_path / "data" / "quote_tick" / symbol / "data.parquet"
    if not parquet_file.exists():
        return False
    try:
        meta = pq.read_metadata(str(parquet_file))
        return meta.num_rows >= min_bars
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
    end_time: datetime,
    api_key: str,
    user_key: str,
    interval: str,
    count: int = 1000,
) -> list[dict]:
    """Fetches up to `count` candles before `end_time` for the given interval."""
    url = _CANDLES_URL.format(etoro_id=etoro_id, interval=interval, count=count)
    headers = {
        "x-api-key": api_key,
        "x-user-key": user_key,
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
) -> bool:
    """Fetches and saves historical candle data for one symbol. Returns True on success."""
    dest_file = QUOTE_TICK_PATH / symbol / "data.parquet"
    target_start = datetime.now(timezone.utc) - timedelta(days=30 * months)

    # Delta-update: iterate backwards from the oldest locally stored timestamp
    current_end_time = datetime.now(timezone.utc)
    if dest_file.exists():
        oldest_ns = _get_oldest_ts_ns(dest_file)
        if oldest_ns is not None:
            oldest_dt = datetime.fromtimestamp(oldest_ns / 1e9, tz=timezone.utc)
            current_end_time = oldest_dt - timedelta(seconds=1)
            log.info(f"[{symbol}] Delta-Update: Fetch ab {current_end_time.isoformat()} rückwärts bis {target_start.isoformat()}")
    all_candles: list[dict] = []

    # Cascade: OneHour first, then OneDay to reach deeper history
    for interval in ["OneHour", "OneDay"]:
        last_oldest_ts_ns: int | None = None

        while current_end_time > target_start:
            chunk = await _fetch_candle_chunk(
                session, etoro_id, current_end_time, api_key, user_key, interval
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

            all_candles.extend(chunk)
            last_oldest_ts_ns = oldest_ns

            oldest_dt = datetime.fromtimestamp(oldest_ns / 1e9, tz=timezone.utc)
            current_end_time = oldest_dt - timedelta(seconds=1)

            log.debug(
                f"[{symbol}] {interval}: {len(chunk)} Candles, älteste: {oldest_dt.isoformat()}"
            )
            await asyncio.sleep(1.1)

        if current_end_time <= target_start:
            log.info(f"[{symbol}] Ziel-Startdatum mit {interval} erreicht.")
            break

    if not all_candles:
        log.warning(f"[{symbol}] Keine Candles gefunden — überspringe.")
        return False

    table = _candles_to_arrow_table(all_candles, symbol, price_prec, size_prec, target_start)
    if table is None or len(table) == 0:
        log.warning(f"[{symbol}] Leere Arrow-Table nach Konvertierung.")
        return False

    log.info(f"[{symbol}] {len(table)} Candles → speichere (price_prec={price_prec}).")
    return _merge_and_save(log, table, symbol, price_prec, size_prec)


# ─── Main Async Function ──────────────────────────────────────────────────────

async def run_historical_fetch(
    api_key: str,
    user_key: str,
    etoro_id_to_symbol: dict[str, str],
    months: int = 12,
    min_bars: int = 200,
    force: bool = False,
) -> list[str]:
    """
    Fetches historical data for symbols that are insufficient.
    Skips symbols where is_symbol_data_sufficient() returns True (unless force=True).
    Returns list of symbols that were fetched/updated.
    """
    if not api_key or not user_key:
        log.warning("[historical_fetcher] API-Keys fehlen — Fetch übersprungen.")
        return []

    QUOTE_TICK_PATH.mkdir(parents=True, exist_ok=True)

    to_fetch = {
        eid: sym
        for eid, sym in etoro_id_to_symbol.items()
        if force or not is_symbol_data_sufficient(sym, min_bars, CATALOG_PATH)
    }

    if not to_fetch:
        log.info("[historical_fetcher] Alle Symbole haben ausreichend Daten.")
        return []

    log.info(
        f"[historical_fetcher] {len(to_fetch)}/{len(etoro_id_to_symbol)} Symbole "
        f"werden gefetcht (months={months}, min_bars={min_bars})."
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
            f"[historical_fetcher] Precisions via API: "
            f"{len(api_precisions)}/{len(etoro_ids)} Instrumente."
        )

        for etoro_id, symbol in sorted(to_fetch.items(), key=lambda x: x[1]):
            if etoro_id in api_precisions:
                price_prec, size_prec = api_precisions[etoro_id]
            else:
                price_prec, size_prec = _fallback_precisions(symbol)

            try:
                ok = await _fetch_symbol(
                    session, etoro_id, symbol, months,
                    api_key, user_key, price_prec, size_prec,
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


# ─── CLI Entry-Point ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="eToro Nautilus Historical Fetcher (Standalone)",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--months", type=int, default=12, help="Anzahl Monate Historie (Standard: 12)")
    parser.add_argument("--symbol", type=str, default=None, help="Nur dieses Symbol fetchen (z.B. TSLA.ETORO)")
    parser.add_argument("--force", action="store_true", help="Auch Symbole mit ausreichend Daten neu fetchen")
    parser.add_argument("--min-bars", type=int, default=200, help="Mindest-Bars-Schwelle (Standard: 200)")
    parser.add_argument("--universe", type=Path, default=UNIVERSE_PATH, help="Pfad zur Universe-JSON")
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
            min_bars=args.min_bars,
            force=args.force,
        )
    )

    log.info(f"[historical_fetcher] Abgeschlossen: {len(fetched)} Symbole befüllt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
