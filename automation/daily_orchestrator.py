#!/usr/bin/env python3
"""
automation/daily_orchestrator.py
==================================
eToro Nautilus — Täglicher End-to-End-Orchestrator (v2.0)

Führt 5 Phasen sequentiell und fehlerresistent aus:
  Phase 1  — Universe & Mapping (dynamische Instrumentenliste)
  Phase 2  — Datenbeschaffung  (Multi-ZIP-Import, Merge, API-Backfill)
  Phase 3  — Backtesting        (Matrix-Backtest mit dynamischem Fenster laut Config)
  Phase 4  — Tournament         (Sortino/PF-Turnier, Pitfall-#14-Fix)
  Phase 5  — Live Deployment   (Safety-Interlocks, Detached Bot, Log-Rotation)

**Standalone-Modus:** Kein Import aus adapters/. Komplett eigenständig.
  - Instrument-Map wird direkt aus data/universe/momentum_ls.json geladen.
  - API-Backfill erfolgt via automation/api_backfiller.py (Modul-Import).
  - Precisions als Fallback-Heuristik inline (ohne adapters/instrument_utils.py).

**Shift-Left Data Quality:**
  Phase 2 empfängt bereits 100% Nautilus-kompatible Parquet-Dateien aus:
    1. catalog_service.py  → stündliche ZIPs mit FixedSizeBinary(16)
    2. api_backfiller.py   → Direktes Arrow-Format, keine Typ-Migration nötig
  migrate_catalog_to_fixed_binary() entfällt vollständig.

**Multi-ZIP Handling:**
  Da catalog_service.py stündlich zippt, liegen bei einem täglichen Run
  bis zu 24 ZIPs in data/import/. Alle werden eingelesen, gemergt und
  nach erfolgreichem Merge gelöscht.

Verwendung:
  python3 automation/daily_orchestrator.py [--skip-api-fetch]
  python3 automation/daily_orchestrator.py --help

Wichtig:
  - Muss aus dem Projekt-Root ausgeführt werden.
  - Echte Demo-Keys müssen in .env stehen (ETORO_API_KEY, ETORO_USER_KEY).
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
from pathlib import Path
_THIS_DIR = Path(__file__).resolve().parent
import time
import traceback
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv, set_key

# ─── Projekt-Root in den Python-Path aufnehmen ───────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─── automation.api_backfiller importieren (Standalone-Modul) ────────────────
try:
    from automation.api_backfiller import run_backfill, _load_etoro_id_map
except ImportError:
    # Direkt aus dem Ordner importieren (wenn aus automation/ heraus gestartet)
    _THIS_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(_THIS_DIR.parent))
    from automation.api_backfiller import run_backfill, _load_etoro_id_map

# ─── Pfade ────────────────────────────────────────────────────────────────────
CATALOG_PATH     = PROJECT_ROOT / "data" / "nautilus"
QUOTE_TICK_PATH  = CATALOG_PATH / "data" / "quote_tick"
IMPORT_PATH      = PROJECT_ROOT / "data" / "import"
UNIVERSE_PATH    = PROJECT_ROOT / "data" / "universe" / "momentum_ls.json"
def logs_dir() -> Path:
    return Path(os.environ.get("ETORO_LOGS_DIR", str(PROJECT_ROOT / "logs")))

def latest_tournament_path(directory: Path | None = None) -> Path | None:
    """Jüngste ``tournament_YYYY-MM-DD.json`` im Logs-Verzeichnis (Datum im Namen sortiert lexikografisch)."""
    candidates = sorted((directory or logs_dir()).glob("tournament_????-??-??.json"))
    return candidates[-1] if candidates else None


REPORTS_DIR      = PROJECT_ROOT / "reports"
TOURNAMENT_PATH  = logs_dir() / f"tournament_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
ENV_FILE         = PROJECT_ROOT / ".env"

# ─── Automation-Config-Pfade (neues Config-Root ab Task 1) ───────────────────
def config_dir() -> Path:
    return Path(os.environ.get("ETORO_CONFIG_DIR", str(PROJECT_ROOT / "automation" / "config")))

STRATEGIES_CFG        = config_dir() / "strategies.json"
STRATEGY_DEFAULTS_CFG = config_dir() / "strategy_defaults.json"
TOURNAMENT_CFG        = config_dir() / "tournament.json"
BACKTEST_CFG          = config_dir() / "backtest.json"
INSTRUMENT_MAP_PATH   = config_dir() / "instrument_map.json"

# ─── Live-Parameter (Issue #1360 / GH #1256) ──────────────────────────────────
from automation.live_params import live_params_sha256, load_live_param_sources, resolve_live_params

# ─── Live-Bot-Sperre (Issue #1358 / GH #1254) ─────────────────────────────────
from automation.live_bot_lock import (
    DEFAULT_STOP_TIMEOUT_S as _LIVE_BOT_STOP_TIMEOUT_DEFAULT_S,
    LOCK_PATH as LIVE_BOT_LOCK_PATH,
    compute_whitelist_sha256,
    reconcile_live_bot,
)

# ─── Logging-Konfiguration ────────────────────────────────────────────────────
LOG_MAX_BYTES   = 1 * 1024 * 1024   # 1 MB max pro Log-Datei
LOG_BACKUP_CNT  = 5
LOG_RETENTION_D = 7

# ─── Pflicht-Spalten für QuoteTick-Schema-Validierung ────────────────────────
REQUIRED_COLUMNS = frozenset({"bid_price", "ask_price", "bid_size", "ask_size", "ts_event", "ts_init"})

# ─── Precision-Heuristik (aus automation.utils — kein doppelter Code) ────────
from automation.utils import _fallback_precisions


def _get_size_precision(symbol: str) -> int:
    """Delegiert an automation.utils._fallback_precisions."""
    return _fallback_precisions(symbol)[1]


def _get_price_precision(symbol: str) -> int:
    """Delegiert an automation.utils._fallback_precisions."""
    return _fallback_precisions(symbol)[0]


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def _setup_orchestrator_logging() -> logging.Logger:
    """Richtet strukturiertes, LLM-freundliches Logging für den Orchestrator ein."""
    logs_dir().mkdir(parents=True, exist_ok=True)
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_path  = logs_dir() / f"orchestrator_{today_str}.log"

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
    """Löscht alle Log-Dateien älter als max_age_days."""
    if not log_dir.exists():
        return
    cutoff_ts = time.time() - max_age_days * 86400
    deleted   = 0
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
        "event_type":     event_type,
        "timestamp_utc":  datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    log.info(f"[JSON_EVENT] {json.dumps(event, ensure_ascii=False, default=str)}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Universe & Mapping
# ═══════════════════════════════════════════════════════════════════════════════

def _extras_fetch_due(universe_path: Path, min_age_h: float = 6.0) -> bool:
    """Nicht auflösbare Zusatz-Symbole sollen nicht bei jedem Lauf einen Fetch auslösen: höchstens alle
    ``min_age_h`` Stunden (gemessen am Datei-Zeitstempel des Universums)."""
    try:
        age_h = (time.time() - os.path.getmtime(universe_path)) / 3600
    except OSError:
        return True
    return age_h >= min_age_h


def phase1_universe_and_mapping(log: logging.Logger, api_key: str = "", user_key: str = "") -> dict:
    """Phase 1: Lädt das Anlage-Universum aus der JSON-Datei.
    Wenn alt oder fehlend, wird es automatisch über universe_fetcher aktualisiert.

    Standalone: Kein Import aus adapters/instrument_map.py.
    Die Universe-Datei (data/universe/momentum_ls.json) ist die einzige Quelle.
    """
    import asyncio
    import aiohttp
    from automation.universe_fetcher import run_fetch

    log.info("═" * 60)
    log.info("PHASE 1: Universe & Mapping")
    log.info("═" * 60)

    universe_data  = _load_universe_file(log)
    universe_items = universe_data.get("universe", [])

    needs_fetch = False

    if not universe_items:
        log.warning("[Phase 1] Universe leer — erfordert auto-fetch.")
        needs_fetch = True
    else:
        # Stale-Check
        fetched_at_str = universe_data.get("fetched_at", "")
        if fetched_at_str:
            try:
                fetched_at = datetime.fromisoformat(fetched_at_str)
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
                if age_h > 24:
                    log.warning(f"[Phase 1] Universe-Daten sind {age_h:.1f}h alt (> 24h). Erfordert auto-fetch.")
                    needs_fetch = True
                else:
                    log.info(f"[Phase 1] Universe-Alter: {age_h:.1f}h — frisch genug.")
            except ValueError:
                log.warning("[Phase 1] Konnte Universe-Zeitstempel nicht parsen. Erfordert auto-fetch.")
                needs_fetch = True
        else:
            needs_fetch = True

    if not needs_fetch:
        # Neue volatile Zusatz-Symbole (config/volatile_universe.json) brauchen den Fetch, der sie über die
        # eToro-Metadaten auflöst — auch wenn das Universum selbst noch frisch ist.
        from automation.universe_fetcher import load_extra_symbols, load_instrument_map as _lim
        _known = {str(s).upper() for s in _lim(INSTRUMENT_MAP_PATH).values() if s}
        _missing = [s for s in load_extra_symbols() if f"{s}.ETORO" not in _known]
        if _missing and _extras_fetch_due(UNIVERSE_PATH):
            log.info(f"[Phase 1] {len(_missing)} volatile Zusatz-Symbole noch nicht im Instrument-Map — Fetch.")
            needs_fetch = True

    if needs_fetch:
        if not api_key or not user_key:
            log.error("[Phase 1] API Keys fehlen. Kann Universe nicht automatisch fetchen.")
        else:
            log.info("[Phase 1] Starte automatischen Universe-Fetch...")
            try:
                success = asyncio.run(run_fetch(
                    api_key=api_key,
                    user_key=user_key,
                    output_path=UNIVERSE_PATH,
                    instrument_map_path=INSTRUMENT_MAP_PATH
                ))
                if success:
                    log.info("[Phase 1] Auto-Fetch erfolgreich abgeschlossen.")
                    universe_data = _load_universe_file(log)
                    universe_items = universe_data.get("universe", [])
                else:
                    log.error("[Phase 1] Auto-Fetch fehlgeschlagen.")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.error(f"[Phase 1] Fehler beim automatischen Universe-Fetch (Netzwerk/API): {e}")
                log.warning("[Phase 1] Fallback: Nutze bisheriges (stales) Universum für diesen Lauf.")

    # Validieren und deduplizieren
    # Ensure all symbols from instrument_map are included in the universe
    from automation.universe_fetcher import load_instrument_map
    instrument_map = load_instrument_map(Path('automation/config/instrument_map.json'))

    for etoro_id, symbol in instrument_map.items():
        if symbol is None:
            continue
        exists = any(str(u.get("etoro_id")) == etoro_id for u in universe_items)
        if not exists:
            universe_items.append({
                "etoro_id": etoro_id,
                "symbol": symbol,
                "raw_name": symbol.split(".")[0]
            })

    valid_items:  list[dict] = []
    unmapped:     list[str]  = []
    seen_symbols: set[str]   = set()

    for item in universe_items:
        sym      = item.get("symbol")
        etoro_id = str(item.get("etoro_id", "")).strip()

        if not sym or sym == "None":
            if etoro_id:
                unmapped.append(etoro_id)
                log.warning(f"[Phase 1] Unbekannte eToro-ID {etoro_id} ohne Symbol — überspringe.")
            continue

        if sym not in seen_symbols:
            seen_symbols.add(sym)
            valid_items.append(item)

    log.info(f"[Phase 1] {len(valid_items)} eindeutige Instrumente im Universum.")
    if unmapped:
        log.warning(f"[Phase 1] {len(unmapped)} IDs ohne Symbol: {unmapped}")

    emit_json_event(log, "PHASE1_COMPLETE", {
        "universe_size": len(valid_items),
        "unmapped_count": len(unmapped),
    })
    return {"universe": valid_items, "unmapped_ids": unmapped}


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

def _phase2d_depth_fetch(log: logging.Logger, universe_result: dict, api_key: str, user_key: str,
                         result: dict) -> None:
    """Phase 2d — Tiefen-Abruf (``historical_fetcher.run_historical_fetch``) für Symbole ohne ausreichende
    Historie; schreibt ``result['hist_filled']``. Fehler werden geloggt, nie geworfen."""
    try:
        from automation.historical_fetcher import run_historical_fetch, is_backtest_range_covered
        from automation.api_backfiller import _load_etoro_id_map as _load_id_map_2d

        # Dynamically calculate the minimum bars needed based on backtest configuration
        bt_cfg = {}
        if BACKTEST_CFG.exists():
            try:
                with open(str(BACKTEST_CFG), "r", encoding="utf-8") as f:
                    bt_cfg = json.load(f)
            except Exception:
                pass
        wf_cfg = bt_cfg.get("walk_forward", {})
        total_days = wf_cfg.get("is_window_days", 120) + (wf_cfg.get("splits", 1) * wf_cfg.get("oos_window_days", 30))
        warmup_days = wf_cfg.get("warmup_days", 60) # adding a buffer for indicators
        start_ns = int((datetime.now(timezone.utc) - timedelta(days=total_days + warmup_days)).timestamp() * 1e9)

        insufficient = [
            item["symbol"]
            for item in universe_result.get("universe", [])
            if item.get("symbol") and not is_backtest_range_covered(item["symbol"], start_ns)
        ]
        if insufficient:
            log.info(
                f"[Phase 2d] {len(insufficient)} Symbole unzureichend (benötigt Daten bis {start_ns} ns) — starte Historical Fetcher ..."
            )
            etoro_id_map_2d = _load_id_map_2d(UNIVERSE_PATH)
            insufficient_ids = {
                k: v for k, v in etoro_id_map_2d.items() if v in set(insufficient)
            }
            try:
                hist_filled = asyncio.run(
                    run_historical_fetch(
                        api_key=api_key,
                        user_key=user_key,
                        etoro_id_to_symbol=insufficient_ids,
                        months=12,
                        start_ns=start_ns,
                    )
                )
                result["hist_filled"] = hist_filled
                log.info(f"[Phase 2d] Historical Fetcher: {len(hist_filled)} Symbole befüllt.")
            except Exception as e:
                log.error(f"[Phase 2d] Historical Fetcher Fehler: {e}\n{traceback.format_exc()}")
                result["hist_filled"] = []
        else:
            log.info("[Phase 2d] Alle Symbole haben ausreichend Daten — Historical Fetcher übersprungen.")
            result["hist_filled"] = []

        emit_json_event(log, "PHASE2D_COMPLETE", {
            "insufficient_count": len(insufficient) if insufficient else 0,
            "hist_filled_count": len(result.get("hist_filled", [])),
        })
    except Exception as e:
        log.error(f"[Phase 2d] Historical Fetcher Modul-Fehler: {e}\n{traceback.format_exc()}")
        result["hist_filled"] = []


def phase2_data_acquisition(
    log: logging.Logger,
    universe_result: dict,
    api_key: str,
    user_key: str,
    skip_api_fetch: bool = False,
    offline: bool = False,
    with_oneday: bool = False,
) -> dict:
    """
    Phase 2: Datenbeschaffung (Multi-ZIP-Import, Merge, API-Backfill).

    Issue #1363 (GH #1259): der Vorwärts-Schritt (2c, neue Kerzen seit dem jüngsten lokalen Tick) läuft
    UNABHÄNGIG von ``--skip-api-fetch`` — das Flag überspringt nur den Tiefen-Abruf (2d). Vorher übersprang
    der dokumentierte Tagesbetrieb (``--skip-api-fetch``) genau 2c: der ``OneHour``-Katalog erhielt keine
    neuen Kerzen, die Spanne wuchs nicht, der jüngste Stundenwert alterte. ``--offline`` verzichtet auf
    JEDEN Netzabruf.

    v2.0-Änderungen gegenüber v1.x:
      - Multi-ZIP: Verarbeitet ALLE *.zip in data/import/ (nicht nur eine).
      - Einfacher Merge: pa.concat_tables + ts_event-Dedup (kein _cast_to_schema).
      - Kein migrate_catalog_to_fixed_binary: Quellen liefern bereits FSB(16).
      - API-Backfill via automation.api_backfiller (Modul-Import, Standalone).
    """
    log.info("═" * 60)
    log.info("PHASE 2: Datenbeschaffung (Multi-ZIP, Merge, API-Backfill)")
    log.info("═" * 60)

    result = {
        "imported_instruments": [],
        "merged_count":         0,
        "api_filled":           [],
        "zips_deleted":         0,
    }

    # 2a. Alle ZIPs einlesen
    zip_files = _find_all_zip_files(log)
    if zip_files:
        log.info(f"[Phase 2a] {len(zip_files)} ZIP-Datei(en) gefunden: {[z.name for z in zip_files]}")
        merge_result = _import_and_merge_all_zips(log, zip_files)
        result["imported_instruments"] = merge_result["instruments"]
        result["merged_count"]         = merge_result["merged"]

        # 2b. ZIPs nach erfolgreichem Merge löschen
        if merge_result["success"]:
            deleted = 0
            for zf in zip_files:
                try:
                    os.remove(str(zf))
                    deleted += 1
                    log.info(f"[Phase 2b] ZIP gelöscht: {zf.name}")
                except OSError as e:
                    log.error(f"[Phase 2b] Konnte ZIP nicht löschen {zf.name}: {e}")
            result["zips_deleted"] = deleted
            emit_json_event(log, "ZIPS_DELETED", {"count": deleted, "paths": [str(z) for z in zip_files]})
        else:
            log.warning("[Phase 2b] ZIPs NICHT gelöscht — Import hat Fehler gemeldet.")
    else:
        log.info("[Phase 2a] Keine ZIP-Dateien in data/import/ gefunden.")

    # 2c. API-Backfill (Vorwärts-Schritt) via api_backfiller.py — Issue #1363: nur --offline überspringt ihn.
    if not offline:
        etoro_id_map = _load_etoro_id_map(UNIVERSE_PATH)
        if etoro_id_map:
            specific = {
                item["symbol"]
                for item in universe_result.get("universe", [])
                if item.get("symbol")
            } or None

            log.info(
                f"[Phase 2c] API-Backfill für {len(specific) if specific else len(etoro_id_map)} Symbole …"
            )
            try:
                api_filled = asyncio.run(
                    run_backfill(
                        api_key=api_key,
                        user_key=user_key,
                        etoro_id_to_symbol=etoro_id_map,
                        days=7,
                        specific_symbols=specific,
                        with_oneday=with_oneday,
                    )
                )
                result["api_filled"] = api_filled
                log.info(f"[Phase 2c] API-Backfill: {len(api_filled)} Symbole befüllt.")
            except Exception as e:
                log.error(f"[Phase 2c] API-Backfill Fehler: {e}\n{traceback.format_exc()}")
        else:
            log.warning("[Phase 2c] Keine Instrumente im Universe — API-Backfill übersprungen.")
    else:
        log.info("[Phase 2c] API-Backfill übersprungen (--offline).")

    # 2d. Historical Fetcher (Tiefen-Abruf) für Symbole ohne ausreichende Daten — Issue #1363: übersprungen
    # mit --skip-api-fetch ODER --offline (der Vorwärts-Schritt 2c ist davon unabhängig).
    if skip_api_fetch or offline:
        log.info(f"[Phase 2d] Tiefen-Abruf übersprungen ({'--offline' if offline else '--skip-api-fetch'}).")
        result["hist_filled"] = []
    else:
        _phase2d_depth_fetch(log, universe_result, api_key, user_key, result)

    emit_json_event(log, "PHASE2_COMPLETE", {
        "merged_instruments": result["merged_count"],
        "zips_deleted":       result["zips_deleted"],
        "api_filled_count":   len(result["api_filled"]),
        "hist_filled_count":  len(result.get("hist_filled", [])),
    })
    return result


def _find_all_zip_files(log: logging.Logger) -> list[Path]:
    """Findet alle *.zip-Dateien in data/import/ (sortiert nach Änderungszeit)."""
    IMPORT_PATH.mkdir(parents=True, exist_ok=True)
    zips = sorted(IMPORT_PATH.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        return []
    log.debug(f"[Phase 2a] ZIPs: {[z.name for z in zips]}")
    return zips


def _import_and_merge_all_zips(log: logging.Logger, zip_files: list[Path]) -> dict:
    """Liest alle ZIPs ein, gruppiert nach Symbol und merged in den Katalog.

    Da catalog_service.py und api_backfiller.py bereits 100% Nautilus-kompatible
    Parquet-Dateien mit FixedSizeBinary(16) liefern, ist KEIN Typ-Casting nötig.
    Einfacher Merge: pa.concat_tables + ts_event-Dedup + Speichern.
    """
    log.info(f"[Phase 2a] Verarbeite {len(zip_files)} ZIP-Datei(en) …")
    result = {"instruments": [], "merged": 0, "success": False}

    # Alle Parquet-Daten aus allen ZIPs sammeln: {symbol: [pa.Table, ...]}
    tables_by_symbol: dict[str, list[pa.Table]] = {}
    meta_by_symbol:   dict[str, dict]           = {}

    for zip_path in zip_files:
        try:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                namelist = zf.namelist()
                qt_files = [n for n in namelist if "quote_tick/" in n and n.endswith(".parquet")]
                log.info(f"[Phase 2a] {zip_path.name}: {len(qt_files)} Parquet-Dateien.")

                for fname in qt_files:
                    # Symbol aus Pfad extrahieren: Komponente direkt nach 'quote_tick'
                    parts = fname.split("/")
                    try:
                        qt_idx = parts.index("quote_tick")
                        if qt_idx + 1 >= len(parts) - 1:
                            continue
                        symbol = parts[qt_idx + 1]
                    except ValueError:
                        continue

                    if not symbol:
                        continue

                    try:
                        with zf.open(fname) as f:
                            raw_bytes = f.read()
                        table = pq.read_table(io.BytesIO(raw_bytes))

                        missing = REQUIRED_COLUMNS - set(table.column_names)
                        if missing:
                            log.warning(f"[Phase 2a] Schema-Fehler {fname}: fehlende Spalten {missing}")
                            continue
                        if len(table) == 0:
                            continue

                        tables_by_symbol.setdefault(symbol, []).append(table)

                        # Metadaten merken (mit price_precision bevorzugen)
                        meta = table.schema.metadata or {}
                        if b"price_precision" in meta and symbol not in meta_by_symbol:
                            meta_by_symbol[symbol] = meta

                        log.debug(f"[Phase 2a] {fname}: {len(table)} Zeilen für {symbol}.")

                    except Exception as e:
                        log.warning(f"[Phase 2a] Fehler beim Lesen von {fname}: {e}")

        except zipfile.BadZipFile as e:
            log.error(f"[Phase 2a] Ungültige ZIP-Datei {zip_path.name}: {e}")
        except Exception as e:
            log.error(f"[Phase 2a] ZIP-Import-Fehler {zip_path.name}: {e}\n{traceback.format_exc()}")

    if not tables_by_symbol:
        log.warning("[Phase 2a] Keine validen Daten aus ZIPs.")
        return result

    # Pro Symbol: Merge mit bestehendem Katalog
    for symbol, new_tables in tables_by_symbol.items():
        try:
            if _merge_symbol(log, symbol, new_tables, meta_by_symbol.get(symbol, {})):
                result["instruments"].append(symbol)
                result["merged"] += 1
        except Exception as e:
            log.error(
                f"[Phase 2b] Merge-Fehler für {symbol}: {e}\n{traceback.format_exc()}"
            )

    result["success"] = result["merged"] > 0
    log.info(f"[Phase 2b] Merge abgeschlossen: {result['merged']} Instrumente.")
    return result


def _merge_symbol(
    log: logging.Logger,
    symbol: str,
    new_tables: list[pa.Table],
    zip_meta: dict,
) -> bool:
    """Merged neue Tabellen mit bestehendem Katalog für ein Instrument.

    Voraussetzung (Shift-Left Data Quality):
      - new_tables kommen von catalog_service.py oder api_backfiller.py
      - Beide Quellen liefern bereits FixedSizeBinary(16) mit korrekten Metadaten
      - Daher: KEIN _cast_to_schema, KEIN Typ-Cast nötig

    Merge-Schritte:
      1. Bestehende data.parquet einlesen (falls vorhanden)
      2. pa.concat_tables aller Tabellen
      3. Deduplizieren + sortieren nach ts_event
      4. Metadaten sicherstellen
      5. Atomar als data.parquet speichern
    """
    # Issue #1354 (GH #1251) Fix Punkt 3 / Issue #1366 (GH #1263) — die Echt-Ticks des 24/7-Collectors
    # gehören NICHT mehr ins flache ``<symbol>/data.parquet``: das war exakt die eine Datei, die die
    # NautilusTrader-Engine sah (der Elternordner identifiziert das Instrument) — die Echt-Ticks
    # verfälschten den Backtest oder waren (neben den OneHour-Kerzen) unsichtbar; ausserdem lasen
    # alle Optimizer-Preflights einen anderen Datenstrom (Pitfall #483). Ziel ist jetzt
    # ``<symbol>/RealTick/data.parquet`` (``bar_interval_ns = 0``, ``catalog_interval = "RealTick"``).
    from automation.catalog_paths import REALTICK_INTERVAL
    from automation.api_backfiller import _with_bar_interval_column
    inst_dir  = QUOTE_TICK_PATH / symbol
    dest_dir  = inst_dir / REALTICK_INTERVAL
    dest_file = dest_dir / "data.parquet"
    legacy_flat = inst_dir / "data.parquet"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Migration: ein vorhandenes flaches data.parquet wird beim ersten Lauf nach RealTick/ VERSCHOBEN
    # (os.replace, nie gelöscht). Existiert dort bereits eine Datei, fliessen die Zeilen des flachen
    # Files in den Merge ein und es wird danach unter einem Nicht-*.parquet-Namen aufbewahrt.
    legacy_tables: list[pa.Table] = []
    legacy_to_preserve: Path | None = None
    if legacy_flat.exists():
        if not dest_file.exists():
            os.replace(legacy_flat, dest_file)
            log.info(f"[Phase 2b] {symbol}: flaches data.parquet nach {REALTICK_INTERVAL}/ verschoben (#1354).")
        else:
            try:
                legacy = pq.read_table(str(legacy_flat))
                if len(legacy) > 0:
                    legacy_tables.append(legacy)
            except Exception as e:
                log.warning(f"[Phase 2b] {symbol}: flaches data.parquet nicht lesbar: {e}")
            legacy_to_preserve = legacy_flat

    all_tables: list[pa.Table] = []
    best_meta: dict = zip_meta or {}

    # 1. Bestehende RealTick/data.parquet einlesen
    if dest_file.exists():
        try:
            existing = pq.read_table(str(dest_file))
            if len(existing) > 0:
                all_tables.append(existing)
                ex_meta = existing.schema.metadata or {}
                if b"price_precision" in ex_meta and b"price_precision" not in best_meta:
                    best_meta = ex_meta
        except Exception as e:
            log.warning(f"[Phase 2b] {symbol}: Bestehende Datei nicht lesbar: {e}")

    all_tables.extend(legacy_tables)
    all_tables.extend(new_tables)
    all_tables = [_with_bar_interval_column(t, 0) for t in all_tables]
    total_new = sum(len(t) for t in new_tables)

    if not all_tables:
        log.warning(f"[Phase 2b] {symbol}: Keine validen Tabellen zum Mergen.")
        return False

    # 2. Concatenieren (alle Quellen sind bereits FSB(16) — kein Cast nötig)
    try:
        merged = pa.concat_tables(all_tables, promote_options="default")
    except Exception as e:
        log.error(f"[Phase 2b] concat_tables Fehler ({symbol}): {e}")
        return False

    rows_before = len(merged)

    # 3. Deduplizieren nach ts_event (PyArrow-nativ, kein pandas-Roundtrip der Binärspalten)
    ts_list = merged.column("ts_event").to_pylist()
    seen_ts: set[int] = set()
    keep_indices: list[int] = []
    for i, ts in enumerate(ts_list):
        if ts not in seen_ts:
            seen_ts.add(ts)
            keep_indices.append(i)

    # Sortieren nach ts_event
    keep_indices.sort(key=lambda i: ts_list[i])
    merged = merged.take(pa.array(keep_indices, type=pa.int64()))
    rows_after = len(merged)

    log.info(
        f"[Phase 2b] {symbol}: {rows_before - len(all_tables[0]) if len(all_tables) > 1 else 0} "
        f"bestehend + {total_new} neu → {rows_after} Zeilen "
        f"(-{rows_before - rows_after} Duplikate)"
    )

    # 4. Metadaten sicherstellen (+ Auflösungs-Deklaration der Echt-Ticks, Issue #1354/#1366)
    final_meta = _ensure_metadata(best_meta, symbol)
    final_meta[b"catalog_interval"] = REALTICK_INTERVAL.encode()
    final_meta.pop(b"catalog_schema_version", None)   # RealTick ist nicht Kerzen-versioniert
    merged = merged.replace_schema_metadata(final_meta)

    # 5. Atomar speichern
    tmp = dest_file.with_suffix(".tmp.parquet")
    try:
        pq.write_table(merged, str(tmp), compression="snappy")
        tmp.rename(dest_file)
        log.info(f"[Phase 2b] {symbol}: {rows_after} Zeilen → {dest_file.name}")
    except Exception as e:
        log.error(f"[Phase 2b] Schreib-Fehler {symbol}: {e}")
        tmp.unlink(missing_ok=True)
        return False

    # Die Zeilen eines (zusätzlich zu RealTick/data.parquet) vorgefundenen flachen data.parquet sind
    # nun im Merge enthalten — die Datei wird AUFBEWAHRT (nie gelöscht), unter einem Namen, den kein
    # ``*.parquet``-Glob trifft.
    if legacy_to_preserve is not None and legacy_to_preserve.exists():
        kept = dest_dir / f"legacy_flat_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.parquet.migrated"
        os.replace(legacy_to_preserve, kept)
        log.info(f"[Phase 2b] {symbol}: flaches data.parquet aufbewahrt als {kept.name}.")

    # Alte Timestamp-Dateien löschen (Single-File-Katalog)
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

    return True


def _ensure_metadata(existing_meta: dict, symbol: str) -> dict:
    """Stellt sicher, dass alle Pflicht-Metadaten vorhanden sind.

    Pflicht-Byte-Keys für Nautilus Rust-Backend:
      b"price_precision", b"size_precision", b"instrument_id"
    """
    meta: dict = {k: v for k, v in existing_meta.items()}

    if b"instrument_id" not in meta:
        meta[b"instrument_id"] = symbol.encode()

    if b"price_precision" not in meta:
        pp = _get_price_precision(symbol)
        meta[b"price_precision"] = str(pp).encode()

    if b"size_precision" not in meta:
        sp = _get_size_precision(symbol)
        if sp <= 0:
            sp = 2
        meta[b"size_precision"] = str(sp).encode()

    # pandas-Metadaten entfernen
    meta.pop(b"pandas", None)
    meta.pop("pandas", None)

    return meta


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3 & 4: Backtesting & Tournament
# ═══════════════════════════════════════════════════════════════════════════════

def phase3_4_backtest_and_tournament(
    log: logging.Logger,
) -> dict:
    """Phase 3 & 4: Matrix-Backtesting + Tournament (mit dynamischem Fenster)."""
    log.info("═" * 60)
    log.info("PHASE 3+4: Matrix-Backtesting & Tournament")
    log.info("═" * 60)

    today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # Rollback to Friday end-of-day (Saturday 00:00:00 UTC) if today is Saturday (5) or Sunday (6)
    # If today is Saturday (5), we are already at Saturday 00:00:00 UTC, which is Friday EOD.
    # If today is Sunday (6), we need to roll back to Saturday 00:00:00 UTC, so subtract 1 day.
    if today_midnight.weekday() == 6:
        today_midnight -= timedelta(days=1)

    thirty_days_ago = today_midnight - timedelta(days=30)

    # Start time is calculated inside _build_backtest_config based on WF settings
    pass

    dynamic_config   = _build_backtest_config(thirty_days_ago, today_midnight, start_capital=None)
    dynamic_cfg_path = logs_dir() / "backtest_dynamic_config.json"
    logs_dir().mkdir(parents=True, exist_ok=True)
    with open(str(dynamic_cfg_path), "w", encoding="utf-8") as f:
        json.dump(dynamic_config, f, indent=2, ensure_ascii=False)
    log.info(f"[Phase 3] Dynamische Config: {dynamic_cfg_path}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    bt_log_path = logs_dir() / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"

    cmd = [
        sys.executable,
        str(_THIS_DIR / "backtest_runner.py"),
        "--momentum",
        "--catalog-path", str(CATALOG_PATH),
        "--config",       str(dynamic_cfg_path),
        "--output",       str(TOURNAMENT_PATH),
    ]

    log.info(f"[Phase 3] Backtest-Kommando: {' '.join(cmd)}")

    wf_active = bool(dynamic_config.get("global_settings", {}).get("walk_forward"))

    emit_json_event(log, "BACKTEST_START", {
        "start": dynamic_config.get("global_settings", {}).get("start_time", thirty_days_ago.isoformat()),
        "end":   dynamic_config.get("global_settings", {}).get("end_time", today_midnight.isoformat()),
        "walk_forward_active": wf_active,
    })

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
                    f"({agg['win_count']} Wins, "
                    f"Portfolio-Trades: {agg.get('oos_metrics', {}).get('total_trades', 0)}, "
                    f"Trade-Weighted OOS-Return: {agg.get('oos_metrics', {}).get('total_return', 0.0):.2%}, "
                    f"In-Sample Median Sortino: {agg.get('median_is_sortino')})"
                )
            emit_json_event(log, "TOURNAMENT_COMPLETE", {
                "winner_count":     len(winners),
                "aggregate_winner": agg,
                "median_is_sortino": agg.get("median_is_sortino") if agg else None,
                "path":             str(TOURNAMENT_PATH),
            })
        except Exception as e:
            log.error(f"[Phase 4] Fehler beim Lesen des Tournament-Ergebnisses: {e}")

    return {"tournament_path": str(TOURNAMENT_PATH)}


def _build_backtest_config(start: datetime, end: datetime, start_capital: float | None = None,
                           walk_forward_override: dict | None = None) -> dict:
    from datetime import timedelta

    """Baut die dynamische Backtest-Config aus automation/config/*.json.

    Liest Strategien aus automation/config/strategies.json (nur active=true).
    Liest Start-Kapital aus automation/config/backtest.json (einzige Quelle).
    Generierte Laufzeit-JSONs werden weiterhin in logs/ geschrieben.
    """
    # ── Global Settings aus backtest.json lesen (einzige Quelle für start_capital) ──
    bt_cfg = {}
    if BACKTEST_CFG.exists():
        try:
            with open(str(BACKTEST_CFG), "r", encoding="utf-8") as f:
                bt_cfg = json.load(f)
            start_capital = bt_cfg.get("start_capital", 10000.0)
        except Exception:
            start_capital = 10000.0
    else:
        start_capital = 10000.0

    wf_cfg = bt_cfg.get("walk_forward")
    if walk_forward_override:
        # Issue #1368 (GH #1265) — Inkubations-Selektion: kürzere Geometrie (tournament.json["incubation"]
        # ["walk_forward"], IS + Embargo + Folds × OOS ≤ heutige Stundentiefe), sonst dieselbe Config.
        wf_cfg = {**(wf_cfg or {}), **walk_forward_override}
        total_days = (wf_cfg.get("is_window_days", 120) + wf_cfg.get("embargo_period_days", 0)
                      + wf_cfg.get("splits", 1) * wf_cfg.get("oos_window_days", 30))
        start = end - timedelta(days=total_days)
    elif wf_cfg:
        total_days = wf_cfg.get("is_window_days", 120) + wf_cfg.get("splits", 1) * wf_cfg.get("oos_window_days", 30)
        start = end - timedelta(days=total_days)

    # ── Strategien aus automation/config/strategies.json (nur active=true) ──
    strategies: list[dict] = []
    if STRATEGIES_CFG.exists():
        try:
            with open(str(STRATEGIES_CFG), "r", encoding="utf-8") as f:
                strat_data = json.load(f)
            all_strats = strat_data.get("strategies", [])
            for s in all_strats:
                if s.get("active", True):
                    # _note und active-Felder nicht in die generierte Config übernehmen
                    clean = {k: v for k, v in s.items() if k not in ("active", "_note")}
                    strategies.append(clean)
        except Exception:
            pass

    if not strategies:
        strategies = [{
            "strategy_module": "strategies.sma_crossover",
            "strategy_class":  "SmaCrossoverStrategy",
            "config_class":    "SmaCrossoverConfig",
            "params":          {},
        }]

    return {
        "global_settings": {
            "catalog_path":  str(CATALOG_PATH),
            "start_time":    start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time":      end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "start_capital": start_capital,
            "walk_forward":  wf_cfg if walk_forward_override else bt_cfg.get("walk_forward"),
            "_note": (
                f"Dynamisch generiert — Fenster: {start.date()} bis {end.date()} "
                "(Midnight UTC). Config-Root: automation/config/. "
                "Quellen: catalog_service.py (stündliche ZIPs) + "
                "api_backfiller.py + historical_fetcher.py. Alle Daten als FSB(16)."
            ),
        },
        "strategies": strategies,
    }


def _create_dummy_tournament(log: logging.Logger) -> None:
    dummy = {
        "generated_at":                datetime.now(timezone.utc).isoformat(),
        "universe_snapshot":           "dummy",
        "total_symbol_strategy_pairs": 0,
        "eligible_pairs":              0,
        "per_symbol_winners":          {},
        "aggregate_winner":            None,
        "full_results":                [],
        "_note": "Dummy-Tournament (kein Backtest-Datenmaterial).",
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

def _load_live_param_sources() -> tuple[dict, list]:
    """``(strategy_defaults, strategies_raw)`` für ``resolve_live_params`` (Issue #1360): zuerst das
    aktive ``config_dir()``, sonst das Repo-Config-Verzeichnis neben diesem Modul (robust gegen ein
    in Tests umgebogenes ``PROJECT_ROOT``)."""
    try:
        return load_live_param_sources(config_dir())
    except OSError:
        return load_live_param_sources(Path(__file__).resolve().parent / "config")


def _live_bot_stop_timeout_s() -> float:
    """``backtest.json["live_risk"]["live_bot_stop_timeout_s"]`` (Default 120)."""
    try:
        with open(BACKTEST_CFG, "r", encoding="utf-8") as f:
            return float(((json.load(f) or {}).get("live_risk") or {}).get(
                "live_bot_stop_timeout_s", _LIVE_BOT_STOP_TIMEOUT_DEFAULT_S))
    except (OSError, ValueError, TypeError):
        return float(_LIVE_BOT_STOP_TIMEOUT_DEFAULT_S)


def _stop_running_bot_on_demotion(log: logging.Logger, reason: str, *, no_deploy: bool = False) -> str:
    """Issue #1358 (GH #1254) Fix Punkt 4 — JEDER Phase-5-Pfad, der keinen neuen Bot startet, stoppt
    einen laufenden Bot (``LIVE_BOT_STOPPED_ON_DEMOTION``): was heute nicht zugelassen ist, handelt
    heute nicht. Vor dem Fix beendeten 0 zulässige Paare / OOS nicht auswertbar / OOS-Gate verfehlt /
    leere Whitelist nur den Orchestrator und liessen den Bot von gestern weiterhandeln.

    ``--no-deploy`` fasst Phase 5 nicht an (der Schalter unterbindet ausschliesslich Phase 5). Wirft
    nie. Rückgabe: die ``ReconcileResult.action`` (oder ``"skipped_no_deploy"``/``"error"``)."""
    if no_deploy:
        return "skipped_no_deploy"
    try:
        result = reconcile_live_bot(
            LIVE_BOT_LOCK_PATH, None, stop_timeout_s=_live_bot_stop_timeout_s(), reason=reason)
        for event_type, payload in result.events:
            emit_json_event(log, event_type, payload)
        if result.action == "stopped_on_demotion":
            log.warning(f"[Phase 5] Laufender Bot (PID {result.holder_pid}) wegen '{reason}' gestoppt.")
        elif result.action == "stop_timeout":
            log.error(
                f"[Phase 5] Laufender Bot (PID {result.holder_pid}) hat die Sperre nach SIGTERM nicht "
                f"innerhalb des Timeouts freigegeben (Grund des Stopps: '{reason}')."
            )
        return result.action
    except Exception as exc:  # defensiv: ein Stopp-Fehler darf Phase 5 nie crashen
        log.error(f"[Phase 5] Stopp des laufenden Bots (Grund '{reason}') fehlgeschlagen: {exc}")
        return "error"


def phase5_live_deployment(
    log: logging.Logger,
    universe_result: dict,
    tournament_result: dict,
    no_deploy: bool = False,
) -> int:
    """Phase 5: Safety-Interlocks, Bot als Detached Subprocess starten."""
    log.info("═" * 60)
    log.info("PHASE 5: Live Deployment & Bot-Start")
    log.info("═" * 60)


    tournament_path = tournament_result.get("tournament_path", str(TOURNAMENT_PATH))
    if not Path(tournament_path).exists():
        if no_deploy:
            # Ohne Deployment gibt es nichts abzusichern: kein Tournament ist hier eine Warnung, kein Fehler.
            log.warning(f"[Phase 5] Tournament-Datei nicht gefunden: {tournament_path} — --no-deploy, "
                        f"Phase 5 entfällt (Exit 0).")
            return 0
        log.error(f"[Phase 5] Tournament-Datei nicht gefunden: {tournament_path}")
        return 1

    try:
        with open(tournament_path, "r", encoding="utf-8") as tf:
            t_data = json.load(tf)

        fully_eligible_pairs = t_data.get("fully_eligible_pairs", 0)
        winners = t_data.get("per_symbol_winners", {})
        winner_count = len(winners)

        oos_not_evaluable_pairs = t_data.get("oos_not_evaluable_pairs", 0)
        oos_failed_pairs = t_data.get("oos_failed_pairs", 0)

        log.info(f"[Phase 5] Per-Pair-Eligible Check: {fully_eligible_pairs} fully eligible pairs found (Winners: {winner_count}).")
        log.info(f"[Phase 5] OOS-GATE Statistics: {oos_not_evaluable_pairs} Pairs rejected due to trade shortage, {oos_failed_pairs} Pairs failed performance.")

        if fully_eligible_pairs == 0 or winner_count == 0:
            log.error("[Phase 5] 0 fully eligible per-pair assets gefunden. Live-Deploy strikt verboten (Per-Pair Fail-Closed).")
            emit_json_event(log, "LIVE_DEPLOY_ABORTED", {
                "reason": "zero_fully_eligible_pairs",
                "fully_eligible_pairs": fully_eligible_pairs,
                "winner_count": winner_count,
                "oos_not_evaluable_pairs": oos_not_evaluable_pairs,
                "oos_failed_pairs": oos_failed_pairs
            })
            _stop_running_bot_on_demotion(log, "zero_fully_eligible_pairs", no_deploy=no_deploy)
            return 1

        agg = t_data.get("aggregate_winner")
        if not agg:
            log.error("[Phase 5] Kein Aggregat-Sieger im Tournament. Abbruch.")
            _stop_running_bot_on_demotion(log, "no_aggregate_winner", no_deploy=no_deploy)
            return 1
        oos_evaluated = bool(agg.get("oos_evaluated", False))
        oos_eligible  = bool(agg.get("oos_eligible", False))
        oos_metrics   = agg.get("oos_metrics")
        reasons       = agg.get("oos_rejection_reasons") or ["unbekannt (Runner hat keine Begründung geliefert)"]

        aggregate_oos_sortino = oos_metrics.get("sortino_ratio") if oos_metrics else None
        agg_oos_dd = oos_metrics.get("max_drawdown") if oos_metrics else None

        if not oos_evaluated:
            log.warning(
                f"[Phase 5] OOS-GATE NICHT AUSWERTBAR: Aggregat-Sieger {agg.get('strategy')} — "
                f"kein/zu wenig OOS-Datenmaterial (Aggregate Out-of-Sample Sortino: {aggregate_oos_sortino}, Portfolio DD: {agg_oos_dd}). "
                f"Fail-Closed: kontrollierter Abbruch, kein Live-Deploy."
            )
            emit_json_event(log, "OOS_GATE_NOT_EVALUABLE", {
                "strategy": agg.get("strategy"), "oos_metrics": oos_metrics, "reasons": reasons,
                "aggregate_oos_sortino": aggregate_oos_sortino,
                "aggregate_oos_max_drawdown": agg_oos_dd,
                "fully_eligible_pairs": fully_eligible_pairs, "winner_count": winner_count
            })
            _stop_running_bot_on_demotion(log, "oos_gate_not_evaluable", no_deploy=no_deploy)
            return 0

        if not oos_eligible:
            from automation.optimizer.deployment_gate import load_promotion_records, evaluate_deployment_eligibility
            tournament_cfg_temp: dict = {}
            if TOURNAMENT_CFG.exists():
                with open(TOURNAMENT_CFG, "r", encoding="utf-8") as cf:
                    tournament_cfg_temp = json.load(cf) or {}
            pairs_temp = [(winner.get("strategy"), symbol) for symbol, winner in winners.items()]
            recs_temp = load_promotion_records(pairs_temp, work_dir=PROJECT_ROOT / "data" / "optimizer")
            has_admitted = any(
                evaluate_deployment_eligibility(p, recs_temp, tournament_cfg_temp).admitted
                for p in pairs_temp
            )
            if not has_admitted:
                log.warning(
                    f"[Phase 5] OOS-GATE FEHLGESCHLAGEN: Aggregat-Sieger {agg.get('strategy')} — "
                    f"verletzte Kriterien: {reasons}; Aggregate Out-of-Sample Sortino: {aggregate_oos_sortino}, Portfolio DD: {agg_oos_dd}. "
                    f"Fail-Closed: kontrollierter Abbruch, kein Live-Deploy."
                )
                emit_json_event(log, "OOS_GATE_FAILED", {
                    "strategy": agg.get("strategy"), "reasons": reasons, "oos_metrics": oos_metrics,
                    "aggregate_oos_sortino": aggregate_oos_sortino,
                    "aggregate_oos_max_drawdown": agg_oos_dd,
                    "fully_eligible_pairs": fully_eligible_pairs, "winner_count": winner_count
                })
                _stop_running_bot_on_demotion(log, "oos_gate_failed", no_deploy=no_deploy)
                return 0
            else:
                log.info(
                    f"[Phase 5] Aggregat-Sieger OOS unvollständig, aber valide READY_FOR_PR Promotions bestehen die Deployment-Grenze (#993)."
                )

        log.info(f"[Phase 5] OOS-GATE BESTANDEN: Aggregat-Sieger {agg.get('strategy')} (Aggregate Out-of-Sample Sortino: {aggregate_oos_sortino}, Portfolio DD: {agg_oos_dd}).")

        # --- WHITELIST GENERATION (Issue #993 — Deployment-Grenze) ---
        # Vor #993 entschied EINE Bedingung (oos_eligible ∧ oos_evaluated, Phase-4-Turnier, kein
        # Multiplizitaets-/Deflations-/PBO-/Bootstrap-CI-/Boundary-/R-Edge-/Snapshot-Check) ueber die
        # Aufnahme in die Whitelist — das schwaechere der zwei parallelen Selektionssysteme im Repo
        # (siehe AGENTS.md §"Die Deployment-Grenze") entschied ueber echten Kapitaleinsatz.
        # deployment_gate.evaluate_deployment_eligibility ersetzt diese Bedingung ERSATZLOS durch die
        # vollstaendige, acht-klausige Pruefung gegen die tatsaechlich promoteten Kandidaten
        # (data/optimizer/proposal_{strategy}_{symbol}.json) — nicht mehr gegen das Phase-4-Ergebnis.
        from automation.optimizer.deployment_gate import (
            evaluate_deployment_eligibility, load_promotion_records,
        )
        from automation.optimizer.invariants import check_deployment_gate_completeness

        tournament_cfg: dict = {}
        if TOURNAMENT_CFG.exists():
            with open(TOURNAMENT_CFG, "r", encoding="utf-8") as cf:
                tournament_cfg = json.load(cf) or {}

        pairs = [(winner.get("strategy"), symbol) for symbol, winner in winners.items()]
        promotion_records = load_promotion_records(pairs, work_dir=PROJECT_ROOT / "data" / "optimizer")

        # Issue #1360 (GH #1256) — die Live-Parameter-Quellen EINMAL laden: dieselben Dateien, aus
        # denen der Bot seine Parameter baut (resolve_live_params, einzige Quelle für Bot und Gate).
        live_param_sources = _load_live_param_sources()

        # Issue #1368 (GH #1265) — mit aktivierter Inkubation ist die Deployment-Grenze notwendig, aber
        # nicht hinreichend: Kapital nur für Paare in LIVE_SMALL/LIVE_FULL (Forward-Evidenz erreicht), mit
        # unveränderten Parametern (Fingerabdruck der Stufe == Live-Fingerabdruck), LIVE_SMALL skaliert.
        from automation import incubation as _inc
        _inc_cfg = _inc.incubation_config(tournament_cfg)
        _stages = _inc.DeploymentStages(_incubation_paths()["stages"]) if _inc_cfg.get("enabled") else None
        _fractions = _inc.stage_capital_fractions(_stages, tournament_cfg) if _stages is not None else {}

        whitelisted_winners: dict = {}
        rejected_by_clause: dict[str, int] = {}
        for symbol, winner in winners.items():
            strategy = winner.get("strategy")
            decision = evaluate_deployment_eligibility(
                (strategy, symbol), promotion_records, tournament_cfg,
                live_param_sources=live_param_sources)
            if decision.admitted:
                entry = dict(winner)
                entry["deployment_gate"] = decision.to_dict()
                # Issue #1360 Fix Punkt 4 — der Whitelist-Eintrag trägt den Fingerabdruck der
                # tatsächlich aufgelösten Live-Parameter UND das promovierte Override; der Bot prüft
                # beides je Paar vor add_strategy (LIVE_PARAMS_MISMATCH ⇒ Paar übersprungen).
                entry["live_params_sha256"] = live_params_sha256(
                    resolve_live_params(strategy, symbol, *live_param_sources))
                if _stages is not None:
                    _stage = _stages.stage(strategy, symbol)
                    _stage_reason = (
                        "stage_not_live" if _stage not in _inc.LIVE_STAGES else
                        "stage_params_changed"
                        if _stages.entry(strategy, symbol).get("params_sha256") != entry["live_params_sha256"]
                        else None)
                    if _stage_reason is not None:
                        rejected_by_clause[_stage_reason] = rejected_by_clause.get(_stage_reason, 0) + 1
                        log.info(f"[Phase 5] STAGE-REJECT: {symbol} ({strategy}) — Stufe {_stage}: {_stage_reason}.")
                        continue
                    entry["stage"] = _stage
                    entry["capital_fraction"] = _fractions.get(symbol, 1.0)
                _record = promotion_records.get((strategy, symbol)) or {}
                entry["proposed_instrument_override"] = _record.get("proposed_instrument_override")
                # Issue #1362 (GH #1258) Fix Punkt 3 — Holdout-Round-Trip-Statistik des promovierten
                # Trials für den Live-Verteilungs-Auslöser B (live_risk); fehlt sie, bleibt das Feld
                # None und der Bot nennt den Grund im Start-Event (kein stiller toter Pfad).
                for _field in ("holdout_trade_return_bps_mean", "holdout_trade_return_bps_std",
                               "holdout_trade_return_bps_n"):
                    entry[_field] = _record.get(_field)
                whitelisted_winners[symbol] = entry
            else:
                rejected_by_clause[decision.blocking_clause] = rejected_by_clause.get(decision.blocking_clause, 0) + 1
                log.info(
                    f"[Phase 5] OOS-DEPLOY-REJECT: Symbol {symbol} (Strategy {strategy}) — "
                    f"Deployment-Grenze (#993) verletzt: blocking_clause={decision.blocking_clause} "
                    f"(clause_results={decision.clause_results})."
                )

        whitelist_payload = dict(t_data)
        whitelist_payload["per_symbol_winners"] = whitelisted_winners

        whitelist_path = PROJECT_ROOT / "data" / "state" / "whitelist_tournament.json"
        with open(whitelist_path, "w", encoding="utf-8") as wf:
            json.dump(whitelist_payload, wf, indent=4)

        # Issue #993 Fix Punkt 4 — blockierende Vollstaendigkeits-Invariante VOR dem Bot-Start: jeder
        # Whitelist-Eintrag muss ein vollstaendiges, achtklausiges clause_results-Dict tragen.
        completeness_check = check_deployment_gate_completeness(whitelisted_winners)
        # Issue #1015/#1167 (Katalog #1170) — symmetrisches INVARIANT_STREAM_RESULT (PASS UND FAIL),
        # source="orchestrator": vorher nur bei FAIL ein INVARIANT_CHECK_FAILED-Event (unten), ein
        # bestandener Check spurlos. Bewertet die Whitelist EINES Phase-5-Laufs (whitelist_
        # tournament.json), nicht die Study-Population eines einzelnen Sweep-run_id — report.
        # _DELIBERATELY_UNWIRED_INVARIANT_CHECKS dokumentiert, warum dieses Ereignis strukturell
        # NICHT in ein bestimmtes run.json['invariant_checks'] einsortiert werden kann.
        emit_json_event(log, "INVARIANT_STREAM_RESULT", {
            "name": completeness_check.name, "check": completeness_check.name,
            "passed": completeness_check.passed, "source": "orchestrator",
            "scope": "phase5_deployment_gate",
            "expected": completeness_check.expected,
            "actual": completeness_check.actual if not completeness_check.passed else None,
            "detail": completeness_check.detail, "severity": completeness_check.severity,
        })
        if not completeness_check.passed:
            log.error(f"[Phase 5] check_deployment_gate_completeness FEHLGESCHLAGEN: {completeness_check.detail}")
            emit_json_event(log, "INVARIANT_CHECK_FAILED", {
                "scope": "phase5_deployment_gate", "check": completeness_check.name,
                "expected": completeness_check.expected, "actual": completeness_check.actual,
                "detail": completeness_check.detail,
            })
            _stop_running_bot_on_demotion(log, "deployment_gate_completeness_failed", no_deploy=no_deploy)
            return 1

        emit_json_event(log, "DEPLOYMENT_WHITELIST_GENERATED", {
            "whitelisted_pairs_count": len(whitelisted_winners),
            "rejected_pairs_count": len(winners) - len(whitelisted_winners),
            "rejected_by_clause": rejected_by_clause,
            "whitelist_path": str(whitelist_path)
        })

        if len(whitelisted_winners) == 0:
            log.warning("[Phase 5] Whitelist ist leer (kein Paar besteht die vollstaendige Deployment-Grenze aus acht Klauseln, Issue #993). Live-Deploy abgebrochen.")
            _stop_running_bot_on_demotion(log, "whitelist_empty", no_deploy=no_deploy)
            return 0

        tournament_path = str(whitelist_path)
        # --- END WHITELIST GENERATION ---
    except Exception as e:
        log.error(f"[Phase 5] Fehler beim Lesen des OOS-Gates: {e}")
        return 1

    today_str  = datetime.now(timezone.utc).strftime("%Y%m%d")
    bot_log    = logs_dir() / f"live_bot_{today_str}.log"
    logs_dir().mkdir(parents=True, exist_ok=True)

    bot_script = PROJECT_ROOT / "automation" / "momentum_ls_run.py"
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
        "cmd":        cmd,
        "log_file":   str(bot_log),
        "tournament": tournament_path,
        "universe":   str(UNIVERSE_PATH),
        "no_deploy":  no_deploy,
        "aggregate_oos_sortino": aggregate_oos_sortino,
        "fully_eligible_pairs": fully_eligible_pairs,
        "winner_count": winner_count,
    })

    if no_deploy:
        log.info("[Phase 5] --no-deploy: Live-Deploy unterbunden (Phase 1–4 vollständig ausgeführt).")
        emit_json_event(log, "LIVE_DEPLOY_SKIPPED_NO_DEPLOY", {
            "strategy": agg.get("strategy"),
            "fully_eligible_pairs": fully_eligible_pairs,
            "winner_count": winner_count,
        })
        return 0

    # Issue #1358 (GH #1254) Fix Punkt 3 — vor JEDEM Start die Sperrdatei lesen: gleicher
    # whitelist_sha256 ⇒ kein Neustart; abweichend ⇒ SIGTERM + Warten auf die Freigabe der Sperre,
    # dann Start; Timeout ⇒ Abbruch OHNE zweiten Start. (Vorher: jeder tägliche Lauf = ein weiterer
    # Bot gegen dasselbe Konto, n · 0,6 kumulierte Ziel-Exposure.)
    whitelist_sha256 = compute_whitelist_sha256(whitelisted_winners)
    reconcile = reconcile_live_bot(
        LIVE_BOT_LOCK_PATH, whitelist_sha256, stop_timeout_s=_live_bot_stop_timeout_s(),
        reason="whitelist_changed")
    for event_type, payload in reconcile.events:
        emit_json_event(log, event_type, payload)
    if not reconcile.start_new:
        if reconcile.action == "unchanged":
            log.info(
                f"[Phase 5] Whitelist unverändert (sha256={whitelist_sha256[:12]}…) — Bot "
                f"PID {reconcile.holder_pid} läuft weiter, kein Neustart (LIVE_BOT_UNCHANGED)."
            )
            return 0
        log.error(
            f"[Phase 5] Laufender Bot (PID {reconcile.holder_pid}) gab die Sperre nach SIGTERM nicht "
            f"innerhalb von {_live_bot_stop_timeout_s():.0f}s frei — Abbruch ohne zweiten Start."
        )
        return 1

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
        emit_json_event(log, "BOT_STARTED", {
            "pid": proc.pid, "log_file": str(bot_log), "lock_file": str(LIVE_BOT_LOCK_PATH),
            "whitelist_sha256": whitelist_sha256,
        })
        # Die PID-Datei (logs/live_bot.pid) wurde nie gelesen und ist entfallen: die Wahrheit über
        # den laufenden Bot ist die flock-gehaltene Sperrdatei data/state/live_bot.lock, die der Bot
        # selbst schreibt (pid, started_utc, environment, whitelist_sha256).

    except Exception as e:
        log.error(f"[Phase 5] Fehler beim Starten des Bot-Subprozesses: {e}\n{traceback.format_exc()}")
        return 1

    log.info("═" * 60)
    log.info("ORCHESTRATOR ERFOLGREICH ABGESCHLOSSEN")
    log.info(f"  Bot läuft als PID {proc.pid}")
    log.info(f"  Bot-Log: {bot_log}")
    log.info("═" * 60)
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5b: Demo-Inkubation (Issue #1368 / GH #1265)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_tournament_cfg() -> dict:
    try:
        with open(TOURNAMENT_CFG, "r", encoding="utf-8") as cf:
            return json.load(cf) or {}
    except (OSError, ValueError):
        return {}


def _incubation_paths() -> dict[str, Path]:
    """Alle Zustandspfade relativ zu ``PROJECT_ROOT`` (in Tests umbiegbar)."""
    state = PROJECT_ROOT / "data" / "state"
    return {"stages": state / "deployment_stages.json", "dir": state / "incubation",
            "live_dir": state / "incubation" / "live", "whitelist": state / "incubation_whitelist.json",
            "lock": state / "incubation_bot.lock", "tournament": state / "incubation_tournament.json",
            "trips": state / "incubation" / "distribution_trips.json"}


def _run_incubation_selection(log: logging.Logger, inc_cfg: dict, output_path: Path) -> Path | None:
    """Turnierlauf mit der Inkubations-Geometrie (dieselben Eligibility-Gates wie Phase 4, ohne Holdout-/
    DSR-Promotion). Rückgabe: der Ergebnis-Pfad oder ``None``."""
    today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if today_midnight.weekday() == 6:
        today_midnight -= timedelta(days=1)
    cfg = _build_backtest_config(today_midnight - timedelta(days=30), today_midnight,
                                 walk_forward_override=dict(inc_cfg["walk_forward"]))
    cfg_path = logs_dir() / "backtest_incubation_config.json"
    logs_dir().mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(_THIS_DIR / "backtest_runner.py"), "--momentum",
           "--catalog-path", str(CATALOG_PATH), "--config", str(cfg_path), "--output", str(output_path)]
    bt_log_path = logs_dir() / f"backtest_incubation_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
    env = None
    if inc_cfg.get("selection_config_dir"):     # Paper-Trading: Auswahl auf der Tagesachse (eigenes Overlay)
        env = {**os.environ, "ETORO_CONFIG_DIR": str(inc_cfg["selection_config_dir"])}
        log.info(f"[Phase 5b] Selektions-Config: {inc_cfg['selection_config_dir']}")
    log.info(f"[Phase 5b] Inkubations-Selektion: {' '.join(cmd)}")
    with open(bt_log_path, "w", encoding="utf-8") as bt_log_f:
        proc = subprocess.run(cmd, stdout=bt_log_f, stderr=subprocess.STDOUT, cwd=str(PROJECT_ROOT),
                              timeout=3600, check=False, env=env)
    log.info(f"[Phase 5b] Inkubations-Selektion beendet (Exit-Code: {proc.returncode}).")
    return output_path if output_path.exists() else None


def phase5b_incubation(
    log: logging.Logger,
    *,
    no_deploy: bool = False,
    skip_selection: bool = False,
    inc_cfg_override: dict | None = None,
    now: datetime | None = None,
    selection_fn=None,
    popen=subprocess.Popen,
) -> dict:
    """Phase 5b: Zustandsmaschine ``CANDIDATE → INCUBATING (Demo) → LIVE_SMALL → LIVE_FULL`` (``RETIRED``).

    Nur mit ``tournament.json["incubation"]["enabled"] == true`` (Default aus). Ablauf: (1) Inkubations-
    Selektion (eigener Turnierlauf, kürzere Geometrie), (2) ``incubation.run_incubation_cycle`` — Promotion
    ``INCUBATING → LIVE_SMALL`` und ``LIVE_SMALL → LIVE_FULL`` NUR mit ``evaluate_deployment_eligibility(...).
    admitted is True`` (alle Klauseln), (3) Demo-Bot (``momentum_ls_run --incubation``) mit eigener Sperre und
    ``ETORO_ENV=demo`` — INCUBATING startet nie im ``real``-Environment. Wirft nie (Phase 5 läuft danach)."""
    from automation import incubation as inc

    log.info("═" * 60)
    log.info("PHASE 5b: Demo-Inkubation (Forward-Evidenz, Issue #1368)")
    log.info("═" * 60)
    tournament_cfg = _load_tournament_cfg()
    inc_cfg = inc_cfg_override if inc_cfg_override is not None else inc.incubation_config(tournament_cfg)
    if not inc_cfg.get("enabled"):
        log.info("[Phase 5b] Inkubation deaktiviert (tournament.json incubation.enabled = false).")
        emit_json_event(log, "INCUBATION_SKIPPED", {"reason": "disabled"})
        return {"status": "disabled"}
    paths = _incubation_paths()
    try:
        if skip_selection:
            selection_path = paths["tournament"] if paths["tournament"].exists() else None
        else:
            selection_path = (selection_fn or _run_incubation_selection)(log, inc_cfg, paths["tournament"])
        winners: dict = {}
        if selection_path is not None:
            with open(selection_path, "r", encoding="utf-8") as f:
                winners = (json.load(f) or {}).get("per_symbol_winners", {}) or {}

        from automation.optimizer.deployment_gate import (
            evaluate_deployment_eligibility, load_promotion_records,
        )
        live_param_sources = _load_live_param_sources()

        def _gate(strategy: str, symbol: str) -> dict:
            records = load_promotion_records([(strategy, symbol)], work_dir=PROJECT_ROOT / "data" / "optimizer")
            return evaluate_deployment_eligibility(
                (strategy, symbol), records, tournament_cfg, live_param_sources=live_param_sources).to_dict()

        stages = inc.DeploymentStages(paths["stages"])
        cycle = inc.run_incubation_cycle(
            stages, winners=winners, tournament_cfg=tournament_cfg,
            resolve_params=lambda strategy, symbol: resolve_live_params(strategy, symbol, *live_param_sources),
            deployment_decision_fn=_gate, now=now, ledger_dir=paths["dir"], live_ledger_dir=paths["live_dir"],
            distribution_trips=inc.read_distribution_trips(paths["trips"]), inc_cfg=inc_cfg)
        emit_json_event(log, "INCUBATION_CYCLE", {
            **cycle.to_dict(), "threshold": inc.bonferroni_threshold_for(inc_cfg),
            "stages": {k: v.get("stage") for k, v in stages.data.items()}})
        payload = inc.write_incubation_whitelist(stages, directory=paths["dir"], path=paths["whitelist"], now=now)
    except Exception as exc:  # defensiv: die Inkubation darf Phase 5 nie verhindern
        log.error(f"[Phase 5b] Inkubations-Zyklus fehlgeschlagen: {exc}\n{traceback.format_exc()}")
        emit_json_event(log, "INCUBATION_ERROR", {"error": str(exc)})
        return {"status": "error", "error": str(exc)}

    pairs = payload.get("per_symbol_winners", {})
    if no_deploy:
        log.info("[Phase 5b] --no-deploy: Demo-Bot wird nicht angefasst.")
        return {"status": "no_deploy", "cycle": cycle.to_dict(), "incubating": sorted(pairs)}
    desired_sha = compute_whitelist_sha256(pairs) if pairs else None
    reconcile = reconcile_live_bot(paths["lock"], desired_sha, stop_timeout_s=_live_bot_stop_timeout_s(),
                                   reason="incubation_whitelist_changed" if pairs else "no_incubating_pairs")
    for event_type, payload_ev in reconcile.events:
        emit_json_event(log, event_type, {**payload_ev, "instance": "incubation"})
    if not reconcile.start_new:
        return {"status": reconcile.action, "cycle": cycle.to_dict(), "incubating": sorted(pairs)}
    cmd = [sys.executable, str(PROJECT_ROOT / "automation" / "momentum_ls_run.py"), "--incubation",
           "--universe", str(UNIVERSE_PATH), "--tournament", str(paths["whitelist"])]
    # INCUBATING läuft ausschliesslich im Demo-Konto: das Environment wird hier gesetzt UND vom Bot geprüft.
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    # Demo ist fest verdrahtet; zeigt ETORO_ENV auf etwas anderes, startet kein Bot (Stolperdraht).
    try:
        inc.assert_stage_environment(inc.INCUBATING, os.environ.get("ETORO_ENV") or "demo")
    except inc.IncubationEnvironmentError as exc:
        log.critical(f"[Phase 5b] {exc}")
        return {"status": "error", "error": str(exc)}
    bot_log = logs_dir() / f"incubation_bot_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    try:
        logs_dir().mkdir(parents=True, exist_ok=True)
        with open(bot_log, "a", encoding="utf-8") as handle:
            proc = popen(cmd, stdout=handle, stderr=subprocess.STDOUT, cwd=str(PROJECT_ROOT),
                         start_new_session=True, env=env)
    except Exception as exc:
        log.error(f"[Phase 5b] Demo-Bot-Start fehlgeschlagen: {exc}")
        return {"status": "start_failed", "cycle": cycle.to_dict(), "incubating": sorted(pairs)}
    emit_json_event(log, "INCUBATION_BOT_STARTED", {
        "pid": getattr(proc, "pid", None), "environment": "demo", "pairs": sorted(pairs),
        "whitelist_sha256": desired_sha, "lock_file": str(paths["lock"])})
    return {"status": "started", "cycle": cycle.to_dict(), "incubating": sorted(pairs)}


# ═══════════════════════════════════════════════════════════════════════════════
# HAUPT-EINSTIEGSPUNKT
# ═══════════════════════════════════════════════════════════════════════════════

def build_arg_parser() -> argparse.ArgumentParser:
    """Erstellt und gibt den ArgumentParser für den Orchestrator zurück."""
    parser = argparse.ArgumentParser(
        description="eToro Nautilus — Täglicher End-to-End-Orchestrator v2.0"
    )
    parser.add_argument("--no-deploy",      action="store_true", help="Führt Phase 1–4 vollständig aus (echter Backtest), unterbindet ausschließlich Phase 5 (Live-Deploy).")
    parser.add_argument("--skip-api-fetch", action="store_true",
        help="Tiefen-Abruf (Phase 2d) überspringen; der Vorwärts-Schritt (Phase 2c, neue Kerzen) läuft "
             "weiter (Issue #1363).")
    parser.add_argument("--offline", action="store_true",
        help="Kein Netzabruf in Phase 2 (weder Vorwärts-Schritt 2c noch Tiefen-Abruf 2d; Issue #1363).")
    parser.add_argument("--with-oneday", action="store_true",
        help="Phase 2c schreibt zusätzlich die OneDay-Datei fort (Issue #1276); sie entsteht per "
             "`historical_fetcher --interval OneDay --full-window`.")
    parser.add_argument("--skip-backtest",  action="store_true", help="Phase 3+4 Matrix-Backtesting überspringen.")
    parser.add_argument("--reset-catalog", action="store_true",
        help="Archiviert data/nautilus/data/quote_tick/ nach data/nautilus/archive/<UTC-ts>/ vor Phase 2 "
             "(einmalig; Issue #1364 — es wird nichts mehr gelöscht).")
    parser.add_argument("--papertrading", action="store_true",
        help="Paper-Trading gegen das Demo-Konto mit den vorhandenen Daten: handelt fest im Demo-Konto (bricht bei "
             "einem echten Konto ab), wählt mit der aus der Datentiefe abgeleiteten Geometrie aus und handelt "
             "über die Demo-Inkubation. Kein Phase-3+4-Produktionsturnier, kein Phase 5 (Live).")
    return parser


def _enter_papertrading(log: logging.Logger) -> None:
    """Erzwingt das Demo-Environment VOR jedem Netz-/Bot-Zugriff; wirft ``PaperTradingError`` bei ``real``."""
    from automation.papertrading import assert_demo_environment
    load_dotenv(str(ENV_FILE))
    assert_demo_environment(os.environ.get("ETORO_ENV"))
    from automation.account_guard import PaperAccountError, verify_paper_account
    from automation.papertrading import PaperTradingError
    try:
        endpoints = verify_paper_account(os.getenv("ETORO_API_KEY", ""), os.getenv("ETORO_USER_KEY", ""))
    except PaperAccountError as exc:
        raise PaperTradingError(str(exc)) from exc
    log.info(f"[PAPERTRADING] Trades laufen im Paper-Konto (Endpunkte: {endpoints}; ETORO_PAPER_ACCOUNT_CID "
             f"{'gesetzt und geprüft' if endpoints == 'real' else 'nicht gesetzt'}).")


def _papertrading_inc_cfg(log: logging.Logger) -> dict:
    """Inkubations-Config mit enabled=True. Die Kandidaten-Auswahl läuft auf der Tagesachse (OneDay, ~4 Jahre
    Historie, eigenes Overlay), weil 60 Tage Stundendaten keinen Kandidaten durch die OOS-Gates bringen. Der Bot
    handelt danach unverändert stündlich; die Forward-Evidenz (Bonferroni-Schwelle) bleibt unverändert."""
    from automation import incubation as inc
    from automation.papertrading import PAPER_MAX_CONCURRENT, materialize_daily_selection_overlay
    symbols = None
    try:
        with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
            symbols = [u["symbol"] for u in (json.load(f) or {}).get("universe", []) if u.get("symbol")] or None
    except (OSError, ValueError):
        pass
    overlay, plan = materialize_daily_selection_overlay(CATALOG_PATH, symbols)
    log.info(f"[PAPERTRADING] Auswahl auf Tagesachse: Tiefe {plan['depth']:.1f} d ({plan['n_symbols']} Symbole, "
             f"75-%-Quantil) -> Geometrie {plan['walk_forward']}")
    cfg = inc.incubation_config(_load_tournament_cfg())
    cfg["enabled"] = True
    cfg["max_concurrent"] = max(int(cfg["max_concurrent"]), PAPER_MAX_CONCURRENT)
    cfg["walk_forward"] = {**cfg["walk_forward"], **plan["walk_forward"]}
    cfg["selection_config_dir"] = str(overlay)
    return cfg


def main() -> int:
    """Haupt-Pipeline: 5 Phasen sequentiell ausführen."""
    parser = build_arg_parser()
    args = parser.parse_args()

    load_dotenv(str(ENV_FILE))
    api_key  = os.getenv("ETORO_API_KEY",  "")
    user_key = os.getenv("ETORO_USER_KEY", "")

    # ── Pflicht-Verzeichnisse vorab anlegen (I/O-Laufzeitfehler verhindern) ──
    IMPORT_PATH.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data" / "state").mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    log = _setup_orchestrator_logging()

    if args.papertrading:
        from automation.papertrading import PaperTradingError
        try:
            _enter_papertrading(log)
        except PaperTradingError as exc:
            log.critical(str(exc))
            return 2
        api_key  = os.getenv("ETORO_API_KEY",  "")
        user_key = os.getenv("ETORO_USER_KEY", "")

    # ── Catalog-Reset (einmalig) ────────────────────────────────────────────
    if args.reset_catalog:
        # Issue #1364 (GH #1260) / Pitfall #490: kein rmtree — Historie, die die API nicht
        # zurückliefert (akkumulierte Vorwärts-Kerzen, Echt-Ticks), wäre unwiederbringlich verloren.
        # Der Katalog wird je Instrument nach data/nautilus/archive/<UTC-ts>/<symbol>/ VERSCHOBEN.
        from automation.catalog_paths import catalog_archive_root
        from automation.historical_fetcher import archive_all_instrument_catalogs, _utc_ts_label
        if QUOTE_TICK_PATH.exists():
            archived = archive_all_instrument_catalogs(
                QUOTE_TICK_PATH, catalog_archive_root(QUOTE_TICK_PATH.parent.parent) / _utc_ts_label())
            log.info(f"[RESET] Catalog archiviert (nicht gelöscht): {len(archived)} Instrument(e) "
                     f"aus {QUOTE_TICK_PATH}")
        QUOTE_TICK_PATH.mkdir(parents=True, exist_ok=True)

    log.info("╔" + "═" * 60 + "╗")
    log.info("║  eToro Nautilus — Daily Orchestrator v2.0                  ║")
    log.info(f"║  Start: {datetime.now(timezone.utc).isoformat():<51} ║")
    log.info(f"║  NO-DEPLOY: {'JA' if args.no_deploy else 'NEIN':<50} ║")
    log.info("║  Standalone: Kein adapters/-Import                        ║")
    log.info("╚" + "═" * 60 + "╝")

    emit_json_event(log, "ORCHESTRATOR_START", {
        "no_deploy":      args.no_deploy,
        "skip_api_fetch": args.skip_api_fetch,
        "offline":        args.offline,
        "version":        "2.0",
        "python":         sys.version,
    })

    cleanup_old_logs(logs_dir())

    if not api_key or not user_key:
        log.warning("ETORO_API_KEY oder ETORO_USER_KEY fehlen — API-Backfill wird übersprungen.")

    exit_code = 0
    try:
        universe_result   = phase1_universe_and_mapping(log, api_key=api_key, user_key=user_key)
        data_result       = phase2_data_acquisition(
            log, universe_result, api_key, user_key,
            skip_api_fetch=args.skip_api_fetch,
            offline=args.offline,
            with_oneday=args.with_oneday or args.papertrading,   # Paper-Auswahl läuft auf OneDay
        )
        if args.papertrading:
            log.info("[PAPERTRADING] Phase 3+4 (Produktionsturnier) und Phase 5 (Live) entfallen; Demo-Inkubation "
                     "mit abgeleiteter Geometrie.")
            from automation.papertrading import PaperTradingError
            try:
                inc_cfg = _papertrading_inc_cfg(log)
            except PaperTradingError as exc:
                # Zu wenig Daten ist kein Fehler: sauberer No-Trade-Lauf (Exit 0), der Bot wird nicht angefasst.
                log.warning(f"[PAPERTRADING] Kein Handel in diesem Lauf: {exc}")
                emit_json_event(log, "PAPERTRADING_NO_TRADE", {"reason": "insufficient_history", "detail": str(exc)})
                emit_json_event(log, "ORCHESTRATOR_EXIT", {"exit_code": 0})
                return 0
            res = phase5b_incubation(log, skip_selection=args.skip_backtest, inc_cfg_override=inc_cfg)
            exit_code = 1 if res.get("status") in ("error", "start_failed") else 0
            emit_json_event(log, "ORCHESTRATOR_EXIT", {"exit_code": exit_code})
            return exit_code
        if args.skip_backtest:
            log.info("[Phase 3+4] --skip-backtest: Matrix-Backtesting übersprungen — lade bestehendes Tournament.")
            _tp = TOURNAMENT_PATH if TOURNAMENT_PATH.exists() else (latest_tournament_path() or TOURNAMENT_PATH)
            if _tp != TOURNAMENT_PATH:
                log.warning(f"[Phase 3+4] Tournament von heute fehlt — verwende das jüngste vorhandene: {_tp.name}")
            tournament_result = {"tournament_path": str(_tp), "exit_code": 0}
        else:
            tournament_result = phase3_4_backtest_and_tournament(log)
        # Issue #1368 — Phase 5b VOR Phase 5: eine heutige Promotion (LIVE_SMALL) ist im selben Lauf
        # whitelist-wirksam. Default aus (tournament.json incubation.enabled = false).
        phase5b_incubation(log, no_deploy=args.no_deploy, skip_selection=args.skip_backtest)
        exit_code         = phase5_live_deployment(
            log, universe_result, tournament_result, no_deploy=args.no_deploy
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
