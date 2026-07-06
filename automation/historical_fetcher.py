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


# ─── Cache Helpers ────────────────────────────────────────────────────────────

def _load_inception_bounds() -> dict[str, int]:
    """Liest die JSON-Datei mit Inception-Bounds."""
    if not INCEPTION_CACHE_PATH.exists():
        return {}
    try:
        with open(INCEPTION_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Fehler beim Laden von {INCEPTION_CACHE_PATH}: {e}")
        return {}

def _save_inception_bound(symbol: str, ts_ns: int) -> None:
    """Speichert den Inception-Zeitstempel atomar ab."""
    bounds = _load_inception_bounds()
    bounds[symbol] = ts_ns
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

# ─── Sufficiency Check ────────────────────────────────────────────────────────

def is_backtest_range_covered(
    symbol: str,
    start_ns: int,
    catalog_path: Path = CATALOG_PATH,
) -> bool:
    """Returns True if symbol's data.parquet covers the required backtest range."""
    parquet_file = catalog_path / "data" / "quote_tick" / symbol / "data.parquet"
    if not parquet_file.exists():
        return False
    try:
        import pyarrow.compute as pc
        t = pq.read_table(str(parquet_file), columns=["ts_event"])
        if len(t) == 0:
            return False
        oldest_ts = int(pc.min(t.column("ts_event")).as_py())

        # NEU: Inception-Bounds prüfen
        bounds = _load_inception_bounds()
        if symbol in bounds:
            if oldest_ts <= bounds[symbol]:
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
    start_ns: int = 0,
) -> bool:
    """Fetches and saves historical candle data for one symbol. Returns True on success."""
    dest_file = QUOTE_TICK_PATH / symbol / "data.parquet"
    if start_ns > 0:
        target_start = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc)
    else:
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

    # Wenn die Schleifen beendet wurden, wir aber das target_start nicht erreicht haben,
    # ist das Instrument jünger als das angeforderte Backtest-Warmup-Fenster.
    if current_end_time > target_start:
        final_oldest_ns = _get_oldest_ts_ns(dest_file)
        if final_oldest_ns is not None:
            _save_inception_bound(symbol, final_oldest_ns)
            log.info(f"[{symbol}] Maximale historische Tiefe aufgezeichnet. Inception-Bound im Cache registriert: {datetime.fromtimestamp(final_oldest_ns/1e9, tz=timezone.utc).isoformat()}")

    if not all_candles:
        log.warning(f"[{symbol}] Keine Candles gefunden — überspringe.")
        return False

    table = _candles_to_arrow_table(all_candles, symbol, price_prec, size_prec, target_start)
    if table is None or len(table) == 0:
        log.warning(f"[{symbol}] Leere Arrow-Table nach Konvertierung.")
        return False

    log.info(f"[{symbol}] {len(table)} Candles → speichere (price_prec={price_prec}).")
    res = _merge_and_save(log, table, symbol, price_prec, size_prec)

    # Nach dem erfolgreichen Speichern nochmal Inception-Bound prüfen
    if res and current_end_time > target_start:
        final_oldest_ns = _get_oldest_ts_ns(dest_file)
        if final_oldest_ns is not None:
            _save_inception_bound(symbol, final_oldest_ns)
            log.info(f"[{symbol}] Maximale historische Tiefe aufgezeichnet. Inception-Bound im Cache registriert: {datetime.fromtimestamp(final_oldest_ns/1e9, tz=timezone.utc).isoformat()}")

    return res


# ─── Main Async Function ──────────────────────────────────────────────────────

async def run_historical_fetch(
    api_key: str,
    user_key: str,
    etoro_id_to_symbol: dict[str, str],
    months: int = 12,
    start_ns: int = 0,
    force: bool = False,
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

            try:
                ok = await _fetch_symbol(
                    session, etoro_id, symbol, months,
                    api_key, user_key, price_prec, size_prec,
                    start_ns=real_start_ns,
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
) -> dict:
    """Issue #531 — Pre-Sweep-Hook: erzwingt die volle Walk-Forward-Historie VOR dem Sweep.

    Liegt die REAL vorhandene Bar-Spanne eines Symbols (``span_days_by_symbol[sym]``, vom Aufrufer
    aus den Parquet-Statistiken injiziert) unter ``required_span_days + gate1_buffer_days`` (z. B.
    405 + 30 = 435 Tage), wird ein **synchroner** Backfill-Request an den ``historical_fetcher``
    abgesetzt, um das fehlende Delta (z. B. TSLA.ETORO-1h) nachzuladen, bevor der Sweep iteriert.

    Rein orchestrierend und vollständig injizierbar (HI-7): ``span_days_by_symbol`` und ``fetch_fn``
    kommen von außen, es findet KEIN eigenständiges Parquet-I/O statt. Gibt einen Report zurück
    (``required_days``/``threshold_days``/``deficient``/``backfilled``); wirft NIE — schlägt der
    Backfill fehl (keine Keys, Netzfehler), entscheidet das nachgelagerte Gate-1 fail-loud."""
    from automation.optimizer.gate import required_span_days

    log = logger or logging.getLogger("historical_fetcher")
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
    }
    if not deficient:
        return report

    log.warning(
        "[#531] %d Symbol(e) unter der Walk-Forward-Schwelle (%d Tage = %d + Puffer %d) — "
        "synchroner Pre-Sweep-Backfill: %s",
        len(deficient), threshold, required, int(gate1_buffer_days), deficient,
    )
    fetch = fetch_fn or _default_backfill_fetch
    try:
        fetched = fetch(
            deficient, required_days=required, buffer_days=int(gate1_buffer_days),
            universe_path=universe_path, api_key=api_key, user_key=user_key, logger=log,
        )
        report["backfilled"] = list(fetched or [])
        log.info("[#531] Pre-Sweep-Backfill abgeschlossen: %d/%d Symbol(e) nachgeladen.",
                 len(report["backfilled"]), len(deficient))
    except Exception as e:  # pragma: no cover - defensiv: Backfill darf den Sweep nie crashen
        log.warning("[#531] Pre-Sweep-Backfill fehlgeschlagen (%s) — Gate-1 entscheidet fail-loud.", e)
    return report


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
    parser.add_argument("--start-ns", type=int, default=0, help="Mindest-Start-Timestamp in ns")
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
            start_ns=args.start_ns,
            force=args.force,
        )
    )

    log.info(f"[historical_fetcher] Abgeschlossen: {len(fetched)} Symbole befüllt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())