import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import pandas as pd
from dotenv import load_dotenv

# Damit wir das Modul aus dem übergeordneten Ordner importieren können
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from archive.adapters.instrument_map import ETORO_INSTRUMENTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# eToro Rate Limit (max 60 Requests pro Minute)
RATE_LIMIT_DELAY = 1.1


def _make_headers(api_key: str, user_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def format_nautilus_timestamp(ts_ns: int) -> str:
    """Konvertiert Nanosekunden-Zeitstempel in das Nautilus Zeitstempel-Format."""
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    base_time = dt.strftime('%Y-%m-%dT%H-%M-%S')
    ns_remainder = int(ts_ns % 1_000_000_000)
    return f"{base_time}-{ns_remainder:09d}Z"


async def fetch_candles_chunk(
    session: aiohttp.ClientSession,
    instrument_id: int,
    end_time: datetime,
    api_key: str,
    user_key: str,
    interval: str
) -> list[dict]:
    count = 1000
    url = f"https://public-api.etoro.com/api/v1/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"

    params = {
        "endTime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    logger.info(f"[{instrument_id}] Fetching max {count} ({interval}) candles before {params['endTime']}...")

    headers = _make_headers(api_key, user_key)

    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    raw_data = await resp.json()

                    if isinstance(raw_data, dict):
                        inner = raw_data.get("candles") or raw_data.get("Candles") or raw_data.get("data")
                        if inner is not None:
                            raw_data = inner

                    if isinstance(raw_data, list) and len(raw_data) > 0:
                        first = raw_data[0]
                        if isinstance(first, dict):
                            inner_candles = first.get("candles") or first.get("Candles")
                            if inner_candles is not None and isinstance(inner_candles, list):
                                return inner_candles

                    return raw_data if isinstance(raw_data, list) else []

                elif resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"[{instrument_id}] Rate limit exceeded (HTTP 429). Waiting for {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    continue

                else:
                    body = await resp.text()
                    logger.warning(f"[{instrument_id}] Failed to fetch candles: HTTP {resp.status} - {body[:200]}")
                    return []

        except Exception as e:
            logger.warning(f"[{instrument_id}] Network error (Attempt {attempt+1}): {e}")
            await asyncio.sleep(5)

    return []


def _get_key_case_insensitive(row: dict, possible_keys: list) -> str:
    row_keys_lower = {k.lower(): k for k in row.keys()}
    for pk in possible_keys:
        if pk.lower() in row_keys_lower:
            return row_keys_lower[pk.lower()]
    return None


def _ensure_instrument_registered(catalog, instrument_id_str: str, price_precision: int = 5) -> None:
    """Registriert ein Mock-Instrument im Katalog, falls noch nicht vorhanden."""
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.instruments import Equity
    from nautilus_trader.model.currencies import USD

    inst_id = InstrumentId.from_str(instrument_id_str)
    increment = round(10 ** (-price_precision), price_precision)
    instrument = Equity(
        instrument_id=inst_id,
        raw_symbol=inst_id.symbol,
        currency=USD,
        price_precision=price_precision,
        price_increment=Price(increment, precision=price_precision),
        lot_size=Quantity(1, precision=0),
        ts_event=0,
        ts_init=0,
    )
    try:
        catalog.write_data([instrument])
    except Exception as e:
        logger.debug(f"Instrument {instrument_id_str} already registered or write skipped: {e}")


def _collect_existing_ts(symbol_dir: str, instrument_id_str: str) -> set:
    """Sammelt ausschliesslich ts_event Zeitstempel, ohne Nautilus-Erweiterungstypen zu berühren."""
    symbol_path = Path(symbol_dir)
    existing_ts: set = set()
    for f in symbol_path.glob("*.parquet"):
        try:
            df = pd.read_parquet(f, columns=['ts_event'])
            if not df.empty and 'ts_event' in df.columns:
                existing_ts.update(df['ts_event'].tolist())
        except Exception as e:
            logger.warning(f"[{instrument_id_str}] Konnte Zeitstempel aus {f.name} nicht lesen: {e}")
    return existing_ts


def write_candles_to_catalog(
    candles: list[dict],
    instrument_id_str: str,
    catalog_path: str,
    price_precision: int = 5,
    max_ts_ns: int = None,
) -> int:
    """Schreibt historische Candle-Daten als QuoteTick-Objekte in den ParquetDataCatalog."""
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    inst_id = InstrumentId.from_str(instrument_id_str)
    catalog = ParquetDataCatalog(catalog_path)

    ticks = []
    for c in candles:
        try:
            date_key = _get_key_case_insensitive(c, ['fromdate', 'startdate', 'date', 'timestamp', 'time', 'start'])
            low_key = _get_key_case_insensitive(c, ['low', 'l'])
            high_key = _get_key_case_insensitive(c, ['high', 'h'])
            if not date_key or not low_key or not high_key:
                continue

            ts_dt = pd.to_datetime(c[date_key])
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.tz_localize('UTC')
            ts_ns = int(ts_dt.timestamp() * 1e9)

            # Schutzschicht: Verhindert das Schreiben von überlappenden Intervallen im Katalog
            if max_ts_ns is not None and ts_ns <= max_ts_ns:
                continue

            bid = float(c[low_key])
            ask = float(c[high_key])

            prec = len(str(bid).rstrip('0').split('.')[-1]) if '.' in str(bid) else 0
            prec = min(max(prec, 2), 8)

            tick = QuoteTick(
                instrument_id=inst_id,
                bid_price=Price(bid, precision=prec),
                ask_price=Price(ask, precision=prec),
                bid_size=Quantity(1, precision=0),
                ask_size=Quantity(1, precision=0),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            ticks.append(tick)
        except Exception as e:
            logger.debug(f"Skipping candle for {instrument_id_str}: {e}")
            continue

    if ticks:
        prec = ticks[0].bid_price.precision if ticks else price_precision

        _base = os.path.join(catalog_path, "data", "quote_tick")
        symbol_dir_plain = os.path.join(_base, instrument_id_str)
        symbol_dir_hive = os.path.join(_base, f"instrument_id={instrument_id_str}")
        existing_ts: set = set()
        if os.path.isdir(symbol_dir_plain):
            existing_ts.update(_collect_existing_ts(symbol_dir_plain, instrument_id_str))
        if os.path.isdir(symbol_dir_hive):
            existing_ts.update(_collect_existing_ts(symbol_dir_hive, instrument_id_str))
        
        if existing_ts:
            ticks = [t for t in ticks if t.ts_event not in existing_ts]

        _ensure_instrument_registered(catalog, instrument_id_str, prec)
        if ticks:
            ticks.sort(key=lambda x: x.ts_init)
            catalog.write_data(ticks)

    return len(ticks)


async def process_instrument(
    session: aiohttp.ClientSession,
    etoro_id: int,
    symbol: str,
    target_start_date: datetime,
    api_key: str,
    user_key: str,
    intervals_cascade: list[str],
    output_dir: str,
    catalog_path: str,
) -> dict:
    """Lade historische Kerzen herunter und retourniere einen Summary-Eintrag."""
    adjusted_start = target_start_date
    has_existing_data = False
    _base_tick_dir = os.path.join(catalog_path, "data", "quote_tick")
    symbol_dir_plain = os.path.join(_base_tick_dir, symbol)
    symbol_dir_hive = os.path.join(_base_tick_dir, f"instrument_id={symbol}")
    
    max_ts_ns = None
    try:
        existing_ts: set = set()
        if os.path.isdir(symbol_dir_plain):
            existing_ts.update(_collect_existing_ts(symbol_dir_plain, symbol))
        if os.path.isdir(symbol_dir_hive):
            existing_ts.update(_collect_existing_ts(symbol_dir_hive, symbol))
        if existing_ts:
            has_existing_data = True
            max_ts_ns = max(existing_ts)
            latest_dt = datetime.fromtimestamp(max_ts_ns / 1e9, tz=timezone.utc)
            if latest_dt > target_start_date:
                adjusted_start = latest_dt
                logger.info(f"[{symbol}] Delta-Update: Lade nur Daten neuer als {adjusted_start}")
    except Exception as e:
        logger.warning(f"[{symbol}] Bestehende Daten konnten nicht geprüft werden: {e}")

    target_start_date = adjusted_start

    logger.info(f"=== Starte API-Download für {symbol} (ID: {etoro_id}) ===")

    current_end_time = datetime.now(timezone.utc)
    all_candles = []
    intervals_used = []

    for current_interval in intervals_cascade:
        logger.info(f"[{symbol}] Versuche Download mit Intervall: {current_interval}")
        last_fetched_oldest_timestamp = None

        while current_end_time > target_start_date:
            chunk = await fetch_candles_chunk(
                session, etoro_id, current_end_time, api_key, user_key, current_interval
            )

            if not chunk:
                logger.info(f"[{symbol}] Keine weiteren Kerzen für {current_interval} verfügbar. Kaskadiere.")
                break

            chunk_df = pd.DataFrame(chunk)

            possible_date_cols = ['fromdate', 'startdate', 'date', 'timestamp', 'time', 'start']
            date_col = next((col for col in chunk_df.columns if col.lower() in possible_date_cols), None)

            if not date_col:
                logger.error(f"[{symbol}] API-Response Format hat sich geändert. Zeitspalte fehlt.")
                break

            chunk_df['parsed_date'] = pd.to_datetime(chunk_df[date_col])
            if chunk_df['parsed_date'].dt.tz is None:
                chunk_df['parsed_date'] = chunk_df['parsed_date'].dt.tz_localize('UTC')

            chunk_df['ts_ns'] = chunk_df['parsed_date'].apply(lambda x: int(x.timestamp() * 1e9))

            # Fehler behoben: Korrekter Variablenname max_ts_ns wird für die Überlappungsprüfung genutzt
            if max_ts_ns is not None:
                if chunk_df['ts_ns'].min() <= max_ts_ns:
                    # Behalte ausschliesslich Kerzen, die strikt neuer sind als das vorhandene Maximum
                    chunk_df = chunk_df[chunk_df['ts_ns'] > max_ts_ns]
                    if not chunk_df.empty:
                        cleaned_chunk = chunk_df.drop(columns=['parsed_date', 'ts_ns']).to_dict(orient='records')
                        all_candles.extend(cleaned_chunk)
                    logger.info(f"[{symbol}] Überlappung mit bestehenden Daten (max_ts) erreicht. Beende Download-Schleife.")
                    break

            oldest_candle_time = chunk_df['parsed_date'].min()

            if last_fetched_oldest_timestamp == oldest_candle_time:
                logger.warning(f"[{symbol}] Historische Tiefe für {current_interval} erreicht. Kaskadiere auf nächstes Intervall.")
                break

            all_candles.extend(chunk)
            if current_interval not in intervals_used:
                intervals_used.append(current_interval)

            last_fetched_oldest_timestamp = oldest_candle_time
            current_end_time = oldest_candle_time - timedelta(seconds=1)

            logger.info(f"[{symbol}] ({current_interval}) Chunk geladen. Älteste Kerze: {oldest_candle_time}. Gesamt: {len(all_candles)}")
            await asyncio.sleep(RATE_LIMIT_DELAY)

        if current_end_time <= target_start_date:
            logger.info(f"[{symbol}] Ziel-Startdatum ({target_start_date}) erfolgreich erreicht.")
            break

    new_count = 0
    if all_candles:
        new_count = write_candles_to_catalog(all_candles, symbol, catalog_path, max_ts_ns=max_ts_ns)
        logger.info(f"[{symbol}] {new_count} neue Ticks in den Katalog geschrieben.")

    if new_count == 0 and not has_existing_data:
        logger.warning(f"[{symbol}] Keine Daten verfügbar.")
        return {"symbol": symbol, "status": "No Data", "intervals": "-", "rows": 0, "range": "N/A"}

    total_rows = 0
    date_range = "N/A"
    all_ts: list = []
    
    for folder in [symbol_dir_plain, symbol_dir_hive]:
        if os.path.isdir(folder):
            try:
                for f in Path(folder).glob("*.parquet"):
                    try:
                        df = pd.read_parquet(f, columns=['ts_event'])
                        if not df.empty and "ts_event" in df.columns:
                            all_ts.extend(df['ts_event'].tolist())
                    except Exception:
                        pass
            except Exception:
                pass

    total_rows = len(all_ts)
    if all_ts:
        min_dt = datetime.fromtimestamp(min(all_ts) / 1e9, tz=timezone.utc)
        max_dt = datetime.fromtimestamp(max(all_ts) / 1e9, tz=timezone.utc)
        date_range = f"{min_dt.strftime('%Y-%m-%d')} bis {max_dt.strftime('%Y-%m-%d')}"

    status = "Updated/New" if new_count > 0 else "Up-to-date"
    return {
        "symbol": symbol,
        "status": status,
        "intervals": ", ".join(intervals_used) if intervals_used else "-",
        "rows": total_rows,
        "range": date_range,
    }


async def main():
    parser = argparse.ArgumentParser(description="Fetch historical data for eToro instruments.")
    parser.add_argument("--symbol", type=str, default=None, help="Optional: Bestimmtes Nautilus-Symbol filtern.")
    parser.add_argument("--months", type=int, default=36, help="Standard-Historie in Monaten (Tiefe)")
    parser.add_argument("--intervals", type=str, default="OneHour,OneDay,OneWeek", help="Intervall-Kaskade")
    parser.add_argument("--output-dir", type=str, default="data/nautilus/data/quote_tick", help="Output Report Verzeichnis")
    parser.add_argument("--catalog-path", type=str, default="data/nautilus", help="Nautilus ParquetDataCatalog Root")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")

    if not api_key or not user_key:
        logger.error("ETORO_API_KEY oder ETORO_USER_KEY fehlen in der .env-Datei.")
        sys.exit(1)

    intervals_list = [i.strip() for i in args.intervals.split(",")]

    universe_json_path = Path("data/universe/momentum_ls.json")
    instruments_to_fetch = {}

    if universe_json_path.exists():
        try:
            with open(universe_json_path, "r", encoding="utf-8") as f:
                uni_data = json.load(f)
                for item in uni_data.get("universe", []):
                    eid = item.get("etoro_id")
                    sym = item.get("symbol")
                    if eid and sym:
                        instruments_to_fetch[str(eid)] = sym
            logger.info(f"Erfolgreich {len(instruments_to_fetch)} eindeutige Assets aus {universe_json_path} geladen.")
        except Exception as e:
            logger.warning(f"Konnte {universe_json_path} nicht parsen ({e}). Nutze Fallback.")

    if not instruments_to_fetch:
        instruments_to_fetch = ETORO_INSTRUMENTS

    if args.symbol:
        instruments_to_fetch = {k: v for k, v in instruments_to_fetch.items() if v == args.symbol}

    logger.info(f"Starte Bulk-Download für {len(instruments_to_fetch)} Instrumente mit Kaskade: {intervals_list} ...")

    target_start_date = datetime.now(timezone.utc) - timedelta(days=30 * args.months)
    summary_results = []

    timeout = aiohttp.ClientTimeout(total=20.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for etoro_id_str, symbol in instruments_to_fetch.items():
            try:
                etoro_id = int(etoro_id_str)
                result = await process_instrument(
                    session=session,
                    etoro_id=etoro_id,
                    symbol=symbol,
                    target_start_date=target_start_date,
                    api_key=api_key,
                    user_key=user_key,
                    intervals_cascade=intervals_list,
                    output_dir=args.output_dir,
                    catalog_path=args.catalog_path,
                )
                if result:
                    summary_results.append(result)
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Verarbeiten von {symbol}: {e}")
                summary_results.append({"symbol": symbol, "status": "Failed", "intervals": "-", "rows": 0, "range": "N/A"})

            await asyncio.sleep(RATE_LIMIT_DELAY)

    logger.info("\n=== Bulk-Update-Prozess abgeschlossen ===")

    if summary_results:
        report_lines = []
        report_lines.append("=== DOWNLOAD SUMMARY REPORT ===")
        report_lines.append(f"{'Symbol':<15} | {'Status':<12} | {'Intervals Used':<25} | {'Total Rows':<10} | {'Date Range'}")
        report_lines.append("-" * 105)

        for res in summary_results:
            report_lines.append(f"{res['symbol']:<15} | {res['status']:<12} | {res['intervals']:<25} | {res['rows']:<10} | {res['range']}")

        report_text = "\n".join(report_lines)
        print(f"\n{report_text}\n")

        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = Path(args.output_dir) / f"download_summary_{timestamp_str}.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)

        logger.info(f"Zusammenfassung gespeichert unter: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())