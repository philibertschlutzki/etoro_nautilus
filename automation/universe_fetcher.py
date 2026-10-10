import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
from automation.utils import _fallback_precisions


import aiohttp
from dotenv import load_dotenv

# Search .env in automation/ first, then in PROJECT_ROOT (fallback)
_THIS_DIR = Path(__file__).resolve().parent
ENV_FILE = _THIS_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = _THIS_DIR.parent / ".env"
load_dotenv(str(ENV_FILE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# Constants for Metadata
ETORO_METADATA_URL = "https://api.etorostatic.com/sapi/instrumentsmetadata/V1.1/instruments"
CACHE_FILE = Path("data/universe/etoro_metadata_cache.json")
CACHE_TTL_HOURS = 24

# Issue #1249 (Katalog #1352, P0) — Normalisierung von eToros ``AssetClass``-Vokabular auf die
# kanonischen Buckets aus ``backtest.json['spread_bps_by_asset_class']``
# (``equity``/``crypto``/``commodity``/``forex``). Vorher wurde der eToro-Rohwert (bzw. der
# Fallback-String ``"Unknown"``) ungeprüft in ``instrument_map.json`` persistiert — ein Wert, für
# den weder die Kostenkonfiguration noch `invariants.check_instrument_metadata_coherence()` einen
# Eintrag kennt, was jeden nachfolgenden Sweep-Start mit `INSTRUMENT_METADATA_INCOHERENT`
# blockierte. Ein eToro-Wert, der hier fehlt, bleibt bewusst unklassifiziert (siehe
# `_normalize_asset_class`) statt geraten zu werden.
_ETORO_ASSET_CLASS_MAP: dict[str, str] = {
    "stocks": "equity",
    "equities": "equity",
    "equity": "equity",
    "etf": "equity",
    "etfs": "equity",
    "crypto": "crypto",
    "cryptocurrency": "crypto",
    "cryptocurrencies": "crypto",
    "currencies": "forex",
    "currency": "forex",
    "forex": "forex",
    "fx": "forex",
    "commodities": "commodity",
    "commodity": "commodity",
}


_CANONICAL_ASSET_CLASSES = frozenset(_ETORO_ASSET_CLASS_MAP.values())

# Katalog #1352 (GH #1270) — eToros Instrument-Metadaten tragen die Asset-Klasse als numerische
# ``InstrumentTypeID`` (Vokabular des Endpunkts ``/market-data/instrument-types``): 1 Currencies,
# 2 Commodities, 4 Indices, 5 Stocks, 6 ETF, 10 Crypto. Der erste #1352-Fix las ausschliesslich das
# Feld ``AssetClass`` — im Produktionslauf vom 2026-10-04 blieben damit 24 von 24 neu aufgelösten IDs
# (MRK, INTC, ORCL, PDD, …) ``asset_class: null`` und wurden im Backtest per
# ``unknown_asset_class_policy='reject'`` abgewiesen. Indizes (4) haben keinen Kosten-Bucket in
# ``backtest.json['spread_bps_by_asset_class']`` und bleiben bewusst unklassifiziert.
_ETORO_INSTRUMENT_TYPE_MAP: dict[int, str] = {
    1: "forex",
    2: "commodity",
    5: "equity",
    6: "equity",
    10: "crypto",
}

# Katalog #1352 (GH #1270) — Precision-Default je Bucket für Symbole, die die symbolbasierte Tabelle
# (``_fallback_precisions``) nicht kennt und deshalb als Equity (2, 2) einstuft; dieselben Werte wie
# die bestehenden Einträge der Klasse (BTC/ETH: 2/8, USDZAR/NATGAS: 5/5). Ohne die Angleichung
# verletzte ein neu als 'forex' klassifiziertes Paar mit price_precision=2 die blockierende Regel
# ``forex ⇒ price_precision >= 4`` und hielte jeden Sweep-Start an.
_EQUITY_DEFAULT_PRECISIONS = (2, 2)
_CLASS_DEFAULT_PRECISIONS: dict[str, tuple[int, int]] = {
    "crypto": (2, 8),
    "forex": (5, 5),
    "commodity": (5, 5),
}


def _normalize_asset_class(raw_asset_class: str | None) -> str | None:
    """Bildet eToros ``AssetClass``-Rohwert auf einen kanonischen Bucket ab.

    Gibt ``None`` zurück, wenn der Wert fehlt oder nicht in ``_ETORO_ASSET_CLASS_MAP`` bekannt
    ist — von `invariants.check_instrument_metadata_coherence()` bereits korrekt als FEHLEND
    (nicht FALSCH) behandelt, siehe dortige Regel 3 (``if spread_bps_by_asset_class is not None
    and asset_class:``)."""
    if not raw_asset_class:
        return None
    return _ETORO_ASSET_CLASS_MAP.get(str(raw_asset_class).strip().lower())


def _is_canonical_asset_class(asset_class: Any) -> bool:
    return isinstance(asset_class, str) and asset_class in _CANONICAL_ASSET_CLASSES


def _meta_field(item: dict, *names: str) -> Any:
    """Erstes vorhandenes Feld aus ``names`` — der statische Metadaten-Endpunkt liefert PascalCase
    (``InstrumentTypeID``), die Public API camelCase (``instrumentTypeID``)."""
    for name in names:
        value = item.get(name)
        if value is not None:
            return value
    return None


def _classify_instrument_metadata(item: dict) -> str | None:
    """Katalog #1352 (GH #1270) — kanonischer Bucket aus einem ``InstrumentDisplayDatas``-Eintrag:
    zuerst ein ``AssetClass``-Rohwert, dann die ``InstrumentTypeID``. ``None``, wenn keines der
    beiden auswertbar ist (nie geraten, nie das Literal ``"Unknown"``)."""
    asset_class = _normalize_asset_class(_meta_field(item, "AssetClass", "assetClass"))
    if asset_class:
        return asset_class
    type_id = _meta_field(item, "InstrumentTypeID", "instrumentTypeID", "instrumentTypeId")
    try:
        return _ETORO_INSTRUMENT_TYPE_MAP.get(int(type_id))
    except (TypeError, ValueError):
        return None


def _describe_classification_input(item: dict) -> str:
    return (f"AssetClass={_meta_field(item, 'AssetClass', 'assetClass')!r}, "
            f"InstrumentTypeID={_meta_field(item, 'InstrumentTypeID', 'instrumentTypeID', 'instrumentTypeId')!r}")


def _precisions_for(symbol: str, asset_class: str | None) -> tuple[int, int]:
    precisions = _fallback_precisions(symbol)
    if precisions == _EQUITY_DEFAULT_PRECISIONS and asset_class in _CLASS_DEFAULT_PRECISIONS:
        return _CLASS_DEFAULT_PRECISIONS[asset_class]
    return precisions


def _reclassify_existing_entries(existing_map: dict, meta_lookup: dict[str, dict]) -> int:
    """Katalog #1352 (GH #1270) — Bestandseinträge ohne kanonische ``asset_class`` (``null``, das
    Alt-Literal ``"Unknown"``, ein roher eToro-Wert wie ``"Stocks"``) werden an der Schreibstelle
    nachklassifiziert. Neue IDs laufen nur einmal durch ``run_fetch()``; ein einmal falsch
    geschriebener Eintrag blieb deshalb dauerhaft stehen und blockierte jeden Sweep-Start
    (Pitfall #481). Eine manuell gesetzte kanonische Klasse wird nie angefasst.

    Reihenfolge: gespeicherter Rohwert, dann eToro-Metadaten. Bleibt der Eintrag unklassifizierbar,
    wird ein nicht-kanonischer Wert auf ``null`` gesetzt (FEHLEND statt FALSCH: das Symbol wird im
    Backtest einzeln abgewiesen, statt die Kohärenzprüfung für alle zu blockieren). Gibt die Zahl
    geänderter Einträge zurück."""
    changed = 0
    for uid, entry in existing_map.items():
        old = entry.get("asset_class")
        if _is_canonical_asset_class(old):
            continue
        new = _normalize_asset_class(old)
        if new is None and uid in meta_lookup:
            new = _classify_instrument_metadata(meta_lookup[uid])
        if new is None and old is None:
            continue
        entry["asset_class"] = new
        stored_precisions = (entry.get("price_precision"), entry.get("size_precision"))
        if new is not None and stored_precisions == _EQUITY_DEFAULT_PRECISIONS:
            entry["price_precision"], entry["size_precision"] = _precisions_for(entry.get("symbol") or "", new)
        changed += 1
        logger.warning(
            f"[Katalog #1352] Reklassifiziert {uid} ({entry.get('symbol')}): asset_class "
            f"{old!r} -> {new!r}"
        )
    return changed

EXTRA_SYMBOLS_PATH = _THIS_DIR / "config" / "volatile_universe.json"
_EXTRA_ASSET_CLASSES = ("equity", "crypto")


def load_extra_symbols(path: Path | None = None) -> list[str]:
    """Zusätzliche volatile Symbole (``config/volatile_universe.json``); fehlt die Datei ⇒ leer."""
    try:
        raw = json.loads(Path(path or EXTRA_SYMBOLS_PATH).read_text("utf-8")) or {}
    except (OSError, ValueError):
        return []
    return [str(s).strip().upper() for s in (raw.get("symbols") or []) if str(s).strip()]


def resolve_extra_symbols(existing_map: dict, meta_lookup: dict[str, dict], symbols: list[str]) -> dict[str, dict]:
    """Neue ``instrument_map``-Einträge ``{uid: {...}}`` für die ``symbols``, die noch nicht im Map stehen: Auflösung
    NUR über ``SymbolFull`` der eToro-Metadaten (exakt, ohne Gross/Klein), nur ``equity``/``crypto``. Nicht
    auflösbare Symbole bleiben draussen (geloggt) — eine Instrument-ID wird nie geraten."""
    known = {str((e or {}).get("symbol") or "").upper() for e in existing_map.values()}
    by_symbol: dict[str, tuple[str, dict]] = {}
    for uid, item in meta_lookup.items():
        sym = str(_meta_field(item, "SymbolFull", "symbolFull") or "").strip().upper()
        if sym and sym not in by_symbol:
            by_symbol[sym] = (uid, item)
    out: dict[str, dict] = {}
    for sym in symbols:
        if f"{sym}.ETORO" in known:
            continue
        hit = by_symbol.get(sym)
        if hit is None:
            logger.warning(f"[VOLATIL] {sym}: nicht in den eToro-Metadaten gefunden - übersprungen.")
            continue
        uid, item = hit
        if uid in existing_map:
            continue
        asset_class = _classify_instrument_metadata(item)
        if asset_class not in _EXTRA_ASSET_CLASSES:
            logger.warning(f"[VOLATIL] {sym} ({uid}): asset_class={asset_class!r} - übersprungen.")
            continue
        symbol = f"{_meta_field(item, 'SymbolFull', 'symbolFull')}.ETORO"
        pp, sp = _precisions_for(symbol, asset_class)
        out[uid] = {"symbol": symbol, "asset_class": asset_class, "price_precision": pp, "size_precision": sp}
    return out


ROUND_THE_CLOCK_PATH = _THIS_DIR / "config" / "round_the_clock_universe.json"
ROUND_THE_CLOCK_STAMP = "round_the_clock_synced_utc"
_ROUND_THE_CLOCK_CLASSES = ("crypto", "forex", "commodity")


def load_round_the_clock_classes(path: Path | None = None) -> tuple[str, ...]:
    """Anlageklassen, deren Instrumente rund um die Uhr handelbar ins Universum kommen
    (``config/round_the_clock_universe.json``); fehlt die Datei oder ist ``enabled`` falsch ⇒ leer."""
    try:
        raw = json.loads(Path(path or ROUND_THE_CLOCK_PATH).read_text("utf-8")) or {}
    except (OSError, ValueError):
        return ()
    if not raw.get("enabled"):
        return ()
    return tuple(c for c in (raw.get("asset_classes") or []) if c in _ROUND_THE_CLOCK_CLASSES)


def _is_delisted(item: dict) -> bool:
    return bool(_meta_field(item, "IsDelisted", "isDelisted"))


def resolve_round_the_clock_symbols(existing_map: dict, meta_lookup: dict[str, dict],
                                    asset_classes: tuple[str, ...]) -> dict[str, dict]:
    """Neue ``instrument_map``-Einträge ``{uid: {...}}`` für jedes Metadaten-Instrument der ``asset_classes``,
    das noch nicht im Map steht (weder per ID noch per Symbol). Ohne ``SymbolFull`` oder als delisted markiert
    ⇒ übersprungen."""
    wanted = set(asset_classes)
    known = {str((e or {}).get("symbol") or "").upper() for e in existing_map.values()}
    out: dict[str, dict] = {}
    for uid, item in sorted(meta_lookup.items()):
        if uid in existing_map or _is_delisted(item):
            continue
        asset_class = _classify_instrument_metadata(item)
        if asset_class not in wanted:
            continue
        symbol_full = str(_meta_field(item, "SymbolFull", "symbolFull") or "").strip()
        symbol = f"{symbol_full}.ETORO"
        if not symbol_full or symbol.upper() in known:
            continue
        known.add(symbol.upper())
        pp, sp = _precisions_for(symbol, asset_class)
        out[uid] = {"symbol": symbol, "asset_class": asset_class, "price_precision": pp, "size_precision": sp}
    return out


def get_etoro_metadata():
    """Fetch eToro metadata with caching and retries."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if CACHE_FILE.exists():
        mtime = os.path.getmtime(CACHE_FILE)
        age_hours = (time.time() - mtime) / 3600
        if age_hours < CACHE_TTL_HOURS:
            logger.info(f"Using cached metadata (age: {age_hours:.1f} hours).")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

    logger.info("Fetching fresh metadata from eToro API...")
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[ 500, 502, 503, 504 ])
    session.mount('https://', HTTPAdapter(max_retries=retries))

    try:
        response = session.get(ETORO_METADATA_URL, timeout=15)
        response.raise_for_status()
        data = response.json()

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        return data
    except Exception as e:
        logger.error(f"Failed to fetch metadata: {e}")
        if CACHE_FILE.exists():
            logger.info("Falling back to stale cache.")
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

def _make_headers(api_key: str, user_key: str) -> dict[str, str]:
    import uuid
    return {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

def load_instrument_map(path: Path) -> dict[str, str]:
    """Lädt {etoro_id: symbol} aus instrument_map.json."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for etoro_id, info in data.get("instruments", {}).items():
        result[etoro_id] = info.get("symbol")
    return result

def is_universe_stale(universe_path: Path, max_age_hours: float = 24.0) -> bool:
    """Prüft ob die Universe-Datei älter als max_age_hours ist."""
    if not universe_path.exists():
        return True
    try:
        with open(universe_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        fetched_at_str = data.get("fetched_at")
        if not fetched_at_str:
            return True
        fetched_at = datetime.fromisoformat(fetched_at_str)
        now = datetime.now(timezone.utc)
        age = (now - fetched_at).total_seconds() / 3600.0
        return age > max_age_hours
    except Exception as e:
        logger.warning(f"Error checking universe file: {e}")
        return True

async def run_fetch(
    api_key: str,
    user_key: str,
    output_path: Path,
    instrument_map_path: Path,
) -> bool:
    username = os.getenv("MOMENTUM_LS_USERNAME")
    if not username:
        logger.error("Missing required environment variable: MOMENTUM_LS_USERNAME")
        raise NameError("Missing required environment variable: MOMENTUM_LS_USERNAME")
    """Fetcht das Universe und speichert es. Gibt True bei Erfolg zurück."""
    url = f"https://public-api.etoro.com/api/v1/user-info/people/{username}/portfolio/live"
    headers = _make_headers(api_key, user_key)
    timeout = aiohttp.ClientTimeout(total=10.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status in (401, 403):
                    logger.error(f"Authentication failed: HTTP {resp.status}. Check ETORO_API_KEY and ETORO_USER_KEY.")
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=resp.status, message="Authentication failed"
                    )
                elif resp.status != 200:
                    body = await resp.text()
                    logger.error(f"HTTP {resp.status} - {body[:300]}")
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=resp.status, message="Non-200 response"
                    )

                data = await resp.json()
    except asyncio.TimeoutError as e:
        logger.error("Request to eToro API timed out.")
        raise e
    except aiohttp.ClientError as e:
        logger.error(f"Network error: {e}")
        raise e

    instrument_map = load_instrument_map(instrument_map_path)
    positions = data.get("clientPortfolio", data).get("positions", [])

    universe = []
    known_count = 0
    unknown_count = 0
    unknown_instruments = {}

    for pos in positions:
        pos_lower = {k.lower(): v for k, v in pos.items()}
        instrument_id = str(pos_lower.get("instrumentid", ""))
        raw_name = pos_lower.get("instrumentname", "Unknown")

        symbol = instrument_map.get(instrument_id)

        if symbol is None:
            if instrument_id not in unknown_instruments:
                unknown_instruments[instrument_id] = {"name": raw_name, "count": 0}
            unknown_instruments[instrument_id]["count"] += 1
            unknown_count += 1
        else:
            known_count += 1

        universe.append({
            "etoro_id": instrument_id,
            "symbol": symbol,
            "raw_name": raw_name
        })


    # Task: Append missing symbols from instrument_map.json
    for etoro_id, symbol in instrument_map.items():
        if symbol is None:
            continue
        exists = any(str(u.get("etoro_id", "")) == etoro_id for u in universe)
        if not exists:
            universe.append({
                "etoro_id": etoro_id,
                "symbol": symbol,
                "raw_name": symbol.split(".")[0]
            })

    # Katalog #1352 (GH #1270) — auch Bestandseinträge ohne kanonische asset_class brauchen die
    # Metadaten (siehe _reclassify_existing_entries), nicht nur neu entdeckte IDs.
    with open(instrument_map_path, "r", encoding="utf-8") as f:
        instrument_map_data = json.load(f)
    existing_map = instrument_map_data.setdefault("instruments", {})
    needs_reclassification = any(
        not _is_canonical_asset_class(entry.get("asset_class")) for entry in existing_map.values())

    extra_symbols = load_extra_symbols()
    known_symbols = {str((e or {}).get("symbol") or "").upper() for e in existing_map.values()}
    extras_missing = [s for s in extra_symbols if f"{s}.ETORO" not in known_symbols]

    round_the_clock = load_round_the_clock_classes()

    meta_lookup: dict[str, dict] | None = None
    if unknown_instruments or needs_reclassification or extras_missing or round_the_clock:
        if unknown_instruments:
            logger.info(f"Found {len(unknown_instruments)} unknown instrument IDs. Attempting to resolve...")
        metadata = get_etoro_metadata()
        if metadata:
            instruments_list = _meta_field(metadata, "InstrumentDisplayDatas", "instrumentDisplayDatas") or []
            meta_lookup = {
                str(_meta_field(item, "InstrumentID", "instrumentID", "instrumentId")): item
                for item in instruments_list if isinstance(item, dict)
            }

    newly_mapped = 0
    if unknown_instruments and meta_lookup is not None:
        for uid, info in unknown_instruments.items():
            if uid in meta_lookup:
                item = meta_lookup[uid]
                symbol_full = _meta_field(item, "SymbolFull", "symbolFull")
                symbol = f"{symbol_full}.ETORO" if symbol_full else None
                asset_class = _classify_instrument_metadata(item)
                precisions = _precisions_for(symbol or "", asset_class)

                existing_map[uid] = {
                    "symbol": symbol,
                    "asset_class": asset_class,
                    "price_precision": precisions[0],
                    "size_precision": precisions[1]
                }
                newly_mapped += 1
                if asset_class is None:
                    # Issue #1249 (Katalog #1352) — unklassifizierbarer Wert wird als
                    # asset_class=null persistiert statt als Literal "Unknown", und laut
                    # Fix-Vorgabe fail-loud statt still auf INFO protokolliert.
                    logger.error(
                        f"Resolved {uid} -> {symbol}: {_describe_classification_input(item)} konnte "
                        "keinem kanonischen Bucket (equity/crypto/commodity/forex) zugeordnet "
                        "werden - schreibe asset_class=null."
                    )
                else:
                    logger.info(f"Resolved {uid} -> {symbol} ({asset_class})")

                # Update universe entry on-the-fly
                for u in universe:
                    if u["etoro_id"] == uid:
                        u["symbol"] = symbol
            else:
                logger.warning(f"Unknown instrument ID: {uid} ({info['name']}) - could not resolve in metadata")
    elif unknown_instruments:
        logger.error("Could not fetch eToro metadata to resolve unknown instruments.")
        for uid, info in unknown_instruments.items():
            logger.warning(f"Unknown instrument ID: {uid} ({info['name']}) - occurred {info['count']} times")

    if extras_missing and meta_lookup is not None:
        added = resolve_extra_symbols(existing_map, meta_lookup, extras_missing)
        for uid, entry in added.items():
            existing_map[uid] = entry
            universe.append({"etoro_id": uid, "symbol": entry["symbol"], "raw_name": entry["symbol"].split(".")[0]})
            logger.info(f"[VOLATIL] {entry['symbol']} ({uid}, {entry['asset_class']}) ins Universum aufgenommen.")
        newly_mapped += len(added)

    rtc_synced = False
    if round_the_clock and meta_lookup is not None:
        added = resolve_round_the_clock_symbols(existing_map, meta_lookup, round_the_clock)
        for uid, entry in added.items():
            existing_map[uid] = entry
            universe.append({"etoro_id": uid, "symbol": entry["symbol"], "raw_name": entry["symbol"].split(".")[0]})
        if added:
            by_class: dict[str, int] = {}
            for entry in added.values():
                by_class[entry["asset_class"]] = by_class.get(entry["asset_class"], 0) + 1
            logger.info(f"[24H] {len(added)} rund um die Uhr handelbare Instrumente aufgenommen: {by_class}")
        newly_mapped += len(added)
        if not instrument_map_data.get(ROUND_THE_CLOCK_STAMP):
            instrument_map_data[ROUND_THE_CLOCK_STAMP] = datetime.now(timezone.utc).isoformat()
            rtc_synced = True

    reclassified = (_reclassify_existing_entries(existing_map, meta_lookup or {})
                    if needs_reclassification else 0)

    if newly_mapped or reclassified or rtc_synced:
        with open(instrument_map_path, "w", encoding="utf-8") as f:
            json.dump(instrument_map_data, f, indent=2, ensure_ascii=False)
        if newly_mapped:
            logger.info(f"Saved {newly_mapped} new mappings to {instrument_map_path}")
            known_count += newly_mapped
        if reclassified:
            logger.info(f"Saved {reclassified} reclassified asset_class value(s) to {instrument_map_path}")

        # Issue #1249 (Katalog #1352) — Kohärenz sofort nach dem Schreiben prüfen, statt
        # erst Stunden/Tage später beim naechsten manuellen Sweep-Start
        # (assert_instrument_metadata_coherence() in sweep.py). Der Defekt wird hier am
        # Ort und zur Zeit seiner Entstehung (Daily-Orchestrator-Lauf) sichtbar.
        try:
            from automation.optimizer import invariants as _inv
            backtest_path = instrument_map_path.parent / "backtest.json"
            spread_by_asset_class = None
            if backtest_path.exists():
                spread_by_asset_class = (
                    json.loads(backtest_path.read_text(encoding="utf-8")) or {}
                ).get("spread_bps_by_asset_class")
            coherence_result = _inv.check_instrument_metadata_coherence(
                existing_map, spread_bps_by_asset_class=spread_by_asset_class)
            if not coherence_result.passed:
                logger.error(
                    "[Issue-Katalog #920] INSTRUMENT_METADATA_INCOHERENT nach run_fetch(): "
                    f"{coherence_result.detail}"
                )
        except (OSError, ValueError) as e:
            logger.warning(f"Could not run post-write instrument metadata coherence check: {e}")

    # Katalog #1352 (GH #1270) — die Kohärenzprüfung überspringt eine fehlende asset_class bewusst
    # (FEHLEND ist nicht FALSCH); der Backtest weist das Symbol dann einzeln ab. Damit das nicht
    # erst dort auffällt, meldet die Schreibstelle jeden noch unklassifizierten Eintrag selbst.
    unclassified = sorted(
        str(entry.get("symbol") or uid) for uid, entry in existing_map.items()
        if entry.get("asset_class") is None)
    if unclassified:
        logger.error(
            f"[Katalog #1352] {len(unclassified)} Instrument(e) in {instrument_map_path.name} ohne "
            f"asset_class: {unclassified} — unter unknown_asset_class_policy='reject' vor jedem "
            "Backtest abgewiesen (REJECT_INSTRUMENT_METADATA_INCOMPLETE); manuell klassifizieren "
            "(equity/crypto/commodity/forex)."
        )

    output_data = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "username": username,
        "universe": universe
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = output_path.with_suffix('.tmp')

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    os.replace(tmp_file, output_path)

    logger.info(f"Total positions: {len(universe)}")
    logger.info(f"Known symbols: {known_count}")
    logger.info(f"Unknown symbols: {len(unknown_instruments)} unique, {unknown_count} total")
    logger.info(f"Saved to {output_path}")

    return True

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/universe/momentum_ls.json")
    args = parser.parse_args()

    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")

    missing = []
    if not api_key: missing.append("ETORO_API_KEY")
    if not user_key: missing.append("ETORO_USER_KEY")
    if not os.getenv("MOMENTUM_LS_USERNAME"): missing.append("MOMENTUM_LS_USERNAME")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    instrument_map_path = _THIS_DIR / "config" / "instrument_map.json"

    asyncio.run(run_fetch(
        api_key=api_key,
        user_key=user_key,
        output_path=Path(args.output),
        instrument_map_path=instrument_map_path
    ))

if __name__ == "__main__":
    main()
