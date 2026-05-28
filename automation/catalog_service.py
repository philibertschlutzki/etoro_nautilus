#!/usr/bin/env python3
"""
automation/catalog_service.py
==============================
Standalone Continuous Catalog Service für eToro Nautilus — kein Import aus adapters/.

Ersetzt run_catalog.py. Läuft 24/7 (z.B. via systemd).

Funktionsweise:
  1. Verbindet sich mit dem eToro WebSocket (wss://ws.etoro.com/ws).
  2. Holt price_precision und size_precision DYNAMISCH via eToro API beim Start.
  3. Sammelt QuoteTick-Daten in einem In-Memory-Puffer.
  4. Alle 60 Minuten: asynchroner Flush-Task
       - Puffer leeren (nahtlose Weitersammlung)
       - Deduplizieren nach ts_event
       - Pro Instrument: Parquet-Datei mit FixedSizeBinary(16) schreiben
       - Alle Parquet-Dateien in einem ZIP bündeln: data/import/[Timestamp].zip
       - Puffer-Daten verwerfen (RAM freigeben)
  5. Läuft unbegrenzt weiter. Bei WebSocket-Fehler: os._exit(1) für systemd-Restart.

Verwendung (Standalone):
  python3 automation/catalog_service.py [--instrument-ids 1111 1137 ...]
  python3 automation/catalog_service.py --universe data/universe/momentum_ls.json

systemd-Unit (Empfehlung):
  [Service]
  ExecStart=/usr/bin/python3 /path/to/etoro_nautilus/automation/catalog_service.py
  Restart=always
  RestartSec=5

Umgebungsvariablen (via .env):
  ETORO_API_KEY   — API-Key
  ETORO_USER_KEY  — User-Key
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import logging.handlers
import os
import ssl
import struct
import sys
import traceback
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import aiohttp
import pyarrow as pa
import pyarrow.parquet as pq
import websockets
from dotenv import load_dotenv

# ─── Pfade ────────────────────────────────────────────────────────────────────
_THIS_DIR    = Path(__file__).resolve().parent
PROJECT_ROOT = _THIS_DIR.parent
UNIVERSE_PATH    = PROJECT_ROOT / "data" / "universe" / "momentum_ls.json"
IMPORT_PATH      = PROJECT_ROOT / "data" / "import"
LOGS_DIR         = PROJECT_ROOT / "logs"
ENV_FILE         = PROJECT_ROOT / ".env"

# ─── eToro API ────────────────────────────────────────────────────────────────
_WS_URL           = "wss://ws.etoro.com/ws"
_INSTRUMENTS_URL  = "https://public-api.etoro.com/api/v1/market-data/instruments"
_BASE_URL_MARKET  = "https://public-api.etoro.com/api/v1/market-data"
_SEARCH_URL       = f"{_BASE_URL_MARKET}/search"

# ─── Timing ───────────────────────────────────────────────────────────────────
FLUSH_INTERVAL_S  = 3600   # 1 Stunde
WS_MAX_ATTEMPTS   = 5
WS_CONNECT_TIMEOUT = 30

# ─── Logging ─────────────────────────────────────────────────────────────────
log = logging.getLogger("catalog_service")


def _setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_path  = LOGS_DIR / f"catalog_service_{today_str}.log"

    file_handler = logging.handlers.RotatingFileHandler(
        str(log_path), maxBytes=1 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    stream_handler = logging.StreamHandler(sys.stdout)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[file_handler, stream_handler],
        force=True,
    )


# ─── Precision-Heuristik (aus automation.utils — kein doppelter Code) ────────
try:
    from automation.utils import _fallback_precisions
except ImportError:
    import sys as _sys
    _sys.path.insert(0, str(_THIS_DIR.parent))
    from automation.utils import _fallback_precisions


# ─── FixedSizeBinary(16) Encoding ────────────────────────────────────────────
# Nautilus-Format: raw_int64 = round(value * 10^precision), LE + 8 Null-Bytes

def _encode_fsb16(value: float, precision: int) -> bytes:
    """Kodiert Preis/Menge als Nautilus FixedSizeBinary(16)."""
    raw = round(value * (10 ** precision))
    raw = max(-(2 ** 63), min(2 ** 63 - 1, raw))
    return struct.pack("<q", raw) + b"\x00" * 8


def _encode_qty_fsb16(qty: float, precision: int) -> bytes:
    """Kodiert eine Menge als FixedSizeBinary(16). Immer ≥ 0."""
    raw = round(qty * (10 ** precision))
    raw = max(0, min(2 ** 63 - 1, raw))
    return struct.pack("<q", raw) + b"\x00" * 8


# ─── Tick-Puffer ─────────────────────────────────────────────────────────────

class RawTick(NamedTuple):
    """Rohdaten eines einzelnen Ticks (vor FSB(16)-Konvertierung)."""
    bid:      float
    ask:      float
    ts_event: int    # Nanosekunden UTC


class InstrumentState(NamedTuple):
    """Zustand pro Instrument."""
    symbol:      str          # "TSLA.ETORO"
    etoro_id:    str          # "1111"
    price_prec:  int
    size_prec:   int


# ─── Dynamische Precision via API ────────────────────────────────────────────

async def _fetch_precisions(
    api_key: str,
    user_key: str,
    etoro_id_to_state: dict[str, InstrumentState],
) -> dict[str, tuple[int, int]]:
    """Holt Precisions via eToro Instruments-Endpoint.

    Returns:
        {etoro_id: (price_prec, size_prec)}
    """
    result: dict[str, tuple[int, int]] = {}
    etoro_ids = list(etoro_id_to_state.keys())

    headers = {
        "x-api-key":    api_key,
        "x-user-key":   user_key,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

    batch_size = 50
    timeout    = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for i in range(0, len(etoro_ids), batch_size):
            batch  = etoro_ids[i : i + batch_size]
            params = {"instrumentIds": ",".join(batch)}

            try:
                async with session.get(_INSTRUMENTS_URL, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        log.warning(
                            f"[catalog_service] Instruments-Endpoint HTTP {resp.status} "
                            f"— nutze Fallback-Precisions."
                        )
                        continue

                    raw = await resp.json(content_type=None)
                    items = raw if isinstance(raw, list) else raw.get("instruments", raw.get("items", []))
                    if not isinstance(items, list):
                        continue

                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        eid = str(item.get("instrumentId", item.get("id", "")))
                        if not eid:
                            continue

                        price_prec: int | None = None
                        for field in ("decimalPlaces", "pricePrecision", "priceDecimals", "digits", "precision"):
                            val = item.get(field)
                            if val is not None:
                                try:
                                    price_prec = int(val)
                                    break
                                except (ValueError, TypeError):
                                    pass

                        size_prec: int | None = None
                        for field in ("sizePrecision", "sizeDecimals", "quantityPrecision", "unitPrecision"):
                            val = item.get(field)
                            if val is not None:
                                try:
                                    size_prec = int(val)
                                    break
                                except (ValueError, TypeError):
                                    pass

                        # Fallback aus Symbolname
                        sym_raw = item.get("internalSymbolFull", item.get("symbol", ""))
                        fb_p, fb_s = _fallback_precisions(sym_raw)
                        if price_prec is None:
                            price_prec = fb_p
                        if size_prec is None:
                            size_prec = fb_s

                        result[eid] = (price_prec, size_prec)
                        log.debug(
                            f"[catalog_service] Precision via API: ID {eid} ({sym_raw}) "
                            f"price={price_prec}, size={size_prec}"
                        )

            except Exception as e:
                log.warning(f"[catalog_service] Precision-Fetch-Fehler: {e}")

            await asyncio.sleep(0.3)

    return result


# ─── ZIP-Writer ───────────────────────────────────────────────────────────────

def _write_zip(
    ticks_by_symbol: dict[str, list[RawTick]],
    instrument_states: dict[str, InstrumentState],
) -> Path | None:
    """Schreibt alle Ticks als Parquet-Dateien in ein ZIP.

    ZIP-Struktur (kompatibel mit daily_orchestrator.py):
      quote_tick/{symbol}/{timestamp}.parquet

    Args:
        ticks_by_symbol:   {symbol: [RawTick, ...]}
        instrument_states: {etoro_id: InstrumentState}

    Returns:
        Pfad zur geschriebenen ZIP-Datei oder None bei leerem Puffer.
    """
    if not any(ticks_by_symbol.values()):
        return None

    # Timestamp für Dateiname
    now_str  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_path = IMPORT_PATH / f"{now_str}.zip"
    IMPORT_PATH.mkdir(parents=True, exist_ok=True)

    _FSB16 = pa.binary(16)
    schema = pa.schema([
        pa.field("bid_price", _FSB16),
        pa.field("ask_price", _FSB16),
        pa.field("bid_size",  _FSB16),
        pa.field("ask_size",  _FSB16),
        pa.field("ts_event",  pa.uint64()),
        pa.field("ts_init",   pa.uint64()),
    ])

    # Symbol → InstrumentState-Map aufbauen
    sym_to_state: dict[str, InstrumentState] = {s.symbol: s for s in instrument_states.values()}

    written_count = 0
    with zipfile.ZipFile(str(zip_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for symbol, ticks in sorted(ticks_by_symbol.items()):
            if not ticks:
                continue

            state = sym_to_state.get(symbol)
            if state is None:
                continue

            price_prec = state.price_prec
            size_prec  = state.size_prec
            size_bytes = _encode_qty_fsb16(1.0, size_prec)

            # Deduplizieren nach ts_event
            seen_ts: set[int] = set()
            unique_ticks: list[RawTick] = []
            for tick in ticks:
                if tick.ts_event not in seen_ts:
                    seen_ts.add(tick.ts_event)
                    unique_ticks.append(tick)

            # Nach ts_event sortieren
            unique_ticks.sort(key=lambda t: t.ts_event)

            # In FSB(16)-Arrays konvertieren
            bid_prices = [_encode_fsb16(t.bid, price_prec) for t in unique_ticks]
            ask_prices = [_encode_fsb16(t.ask, price_prec) for t in unique_ticks]
            bid_sizes  = [size_bytes] * len(unique_ticks)
            ask_sizes  = [size_bytes] * len(unique_ticks)
            ts_events  = [t.ts_event for t in unique_ticks]
            ts_inits   = [t.ts_event for t in unique_ticks]

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

            # eToro equity-CFDs use fractional by-amount sizing. A size_precision of 0
            # forces whole-share orders and suppresses all fractional trades downstream.
            if size_prec is None or size_prec < 0:
                size_prec = 8

            # Arrow-Metadaten injizieren
            meta = {
                b"price_precision": str(price_prec).encode(),
                b"size_precision":  str(size_prec).encode(),
                b"instrument_id":   symbol.encode(),
            }
            table = table.replace_schema_metadata(meta)

            # Parquet in Speicher schreiben
            buf = io.BytesIO()
            pq.write_table(table, buf, compression="snappy")
            buf.seek(0)

            # In ZIP eintragen: quote_tick/{symbol}/{timestamp}.parquet
            parquet_path_in_zip = f"quote_tick/{symbol}/{now_str}.parquet"
            zf.writestr(parquet_path_in_zip, buf.read())
            written_count += 1
            log.debug(
                f"[catalog_service] ZIP: {symbol} → {len(unique_ticks)} Ticks "
                f"(price_prec={price_prec}, size_prec={size_prec})"
            )

    if written_count == 0:
        zip_path.unlink(missing_ok=True)
        return None

    total_ticks = sum(len(t) for t in ticks_by_symbol.values())
    log.info(
        f"[catalog_service] ZIP geschrieben: {zip_path.name} "
        f"| {written_count} Instrumente | {total_ticks} Ticks gesamt"
    )
    return zip_path


# ─── CatalogService ───────────────────────────────────────────────────────────

class CatalogService:
    """Kontinuierlicher Katalog-Dienst: WebSocket-Tick-Sammlung + stündliches ZIP.

    Architektur:
      - Zwei AsyncIO-Tasks:
        1. _ws_loop():    WebSocket-Empfang + Puffer-Füllen (läuft 24/7)
        2. _flush_loop(): Stündlicher Flush → ZIP → data/import/ (läuft parallel)
      - _tick_lock:       asyncio.Lock schützt _tick_buffer gegen Race Conditions
      - Bei WebSocket-Fehler: os._exit(1) für systemd-Restart
    """

    def __init__(
        self,
        api_key: str,
        user_key: str,
        etoro_id_to_symbol: dict[str, str],
        flush_interval_s: int = FLUSH_INTERVAL_S,
    ) -> None:
        self.api_key   = api_key
        self.user_key  = user_key
        self.flush_interval_s = flush_interval_s

        # Instrument-States initialisieren (Precisions werden in run() gesetzt)
        self._states: dict[str, InstrumentState] = {
            eid: InstrumentState(
                symbol=sym, etoro_id=eid,
                price_prec=_fallback_precisions(sym)[0],
                size_prec=_fallback_precisions(sym)[1],
            )
            for eid, sym in etoro_id_to_symbol.items()
        }

        # Tick-Puffer: {symbol: [RawTick, ...]}
        self._tick_buffer: dict[str, list[RawTick]] = defaultdict(list)
        self._tick_lock = asyncio.Lock()

        # Zuletzt gesehene Bid/Ask pro eToro-ID (für Snapshot-Handling)
        self._last_bid: dict[str, float] = {}
        self._last_ask: dict[str, float] = {}

        self._ws = None
        self._tick_counter = 0

    # ── Precision-Update ──────────────────────────────────────────────────────

    async def _update_precisions(self) -> None:
        """Aktualisiert Instrument-Precisions via eToro API (beim Start)."""
        log.info("[catalog_service] Lade Instrument-Precisions via eToro API …")
        api_precs = await _fetch_precisions(self.api_key, self.user_key, self._states)

        updated = 0
        for eid, (pp, sp) in api_precs.items():
            if eid in self._states:
                old = self._states[eid]
                self._states[eid] = InstrumentState(
                    symbol=old.symbol,
                    etoro_id=eid,
                    price_prec=pp,
                    size_prec=sp,
                )
                updated += 1

        log.info(
            f"[catalog_service] Precisions aktualisiert: {updated}/{len(self._states)} Instrumente."
        )

    # ── WebSocket ─────────────────────────────────────────────────────────────

    async def _ws_connect(self) -> None:
        """Verbindet mit eToro WebSocket, authentifiziert, abonniert Instrumente."""
        ssl_ctx = ssl.create_default_context()
        last_exc: Exception | None = None

        for attempt in range(1, WS_MAX_ATTEMPTS + 1):
            delay = min(10 * attempt, 60)
            try:
                log.info(
                    f"[catalog_service] WS Verbindungsversuch "
                    f"{attempt}/{WS_MAX_ATTEMPTS} → {_WS_URL}"
                )
                self._ws = await asyncio.wait_for(
                    websockets.connect(
                        _WS_URL, ssl=ssl_ctx,
                        ping_interval=20, ping_timeout=20,
                    ),
                    timeout=WS_CONNECT_TIMEOUT,
                )
                log.info("[catalog_service] WebSocket verbunden. Authentifiziere …")
                self._last_bid.clear()
                self._last_ask.clear()
                await self._ws_authenticate()
                return

            except Exception as e:
                last_exc = e
                log.warning(
                    f"[catalog_service] WS-Versuch {attempt}/{WS_MAX_ATTEMPTS} "
                    f"fehlgeschlagen: {e}"
                )
                if attempt < WS_MAX_ATTEMPTS:
                    await asyncio.sleep(delay)

        log.error(
            f"[catalog_service] WebSocket nach {WS_MAX_ATTEMPTS} Versuchen "
            f"nicht erreichbar (Letzter Fehler: {last_exc}). Erzwinge Neustart."
        )
        os._exit(1)

    async def _ws_authenticate(self) -> None:
        """Sendet Auth-Payload und abonniert Instrumente."""
        auth_payload = {
            "id":        str(uuid.uuid4()),
            "operation": "Authenticate",
            "data": {
                "userKey": self.user_key,
                "apiKey":  self.api_key,
            },
        }
        await self._ws.send(json.dumps(auth_payload))

        # Auf Auth-Antwort warten (erste nicht-leere Nachricht)
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
            if raw and raw not in (b"", b"\x00", ""):
                break

        try:
            resp = json.loads(raw)
            log.info(f"[catalog_service] Auth-Antwort: {resp}")
            status = resp.get("status")
            if status not in (None, "OK", "ok", 200):
                raise RuntimeError(f"Auth fehlgeschlagen: {resp}")
        except json.JSONDecodeError:
            log.warning(f"[catalog_service] Auth-Antwort nicht parsebar: {raw!r}")

        # Alle Instrumente abonnieren
        topics = [f"instrument:{eid}" for eid in self._states]
        sub_payload = {
            "id":        str(uuid.uuid4()),
            "operation": "Subscribe",
            "data":      {"topics": topics, "snapshot": True},
        }
        await self._ws.send(json.dumps(sub_payload))
        log.info(f"[catalog_service] Abonniert: {len(topics)} Instrumente.")

    async def _ws_loop(self) -> None:
        """Hauptloop: Empfängt WebSocket-Nachrichten und füllt den Tick-Puffer."""
        await self._ws_connect()

        try:
            async for raw in self._ws:
                if not raw or raw == b"\x00":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if "messages" in data:
                    for msg in data["messages"]:
                        await self._process_message(msg)

            # Server hat Verbindung sauber geschlossen
            log.warning("[catalog_service] WS vom Server geschlossen. Erzwinge Neustart.")
            os._exit(1)

        except websockets.exceptions.ConnectionClosedOK:
            log.warning("[catalog_service] WS sauber getrennt. Erzwinge Neustart.")
            os._exit(1)
        except websockets.exceptions.ConnectionClosedError as e:
            log.error(f"[catalog_service] WS-Verbindungsfehler: {e}. Erzwinge Neustart.")
            os._exit(1)
        except asyncio.CancelledError:
            log.info("[catalog_service] WS-Loop abgebrochen.")
            raise
        except Exception as e:
            log.error(f"[catalog_service] Unerwarteter WS-Fehler: {e}. Erzwinge Neustart.")
            os._exit(1)

    async def _process_message(self, msg: dict) -> None:
        """Verarbeitet eine einzelne WebSocket-Nachricht und fügt Tick in Puffer."""
        msg_type = msg.get("type")
        if msg_type not in ("Trading.Instrument.Rate", "Snapshot"):
            return

        try:
            content_raw = msg.get("content", {})
            content: dict = (
                json.loads(content_raw)
                if isinstance(content_raw, str)
                else content_raw
            )
            if not isinstance(content, dict):
                return

            topic    = msg.get("topic", "")
            etoro_id = (
                topic.split(":")[1]
                if topic.startswith("instrument:")
                else str(content.get("InstrumentID", ""))
            )

            if etoro_id not in self._states:
                return

            bid_changed = ask_changed = False

            if "Bid" in content:
                self._last_bid[etoro_id] = float(content["Bid"])
                bid_changed = True
            if "Ask" in content:
                self._last_ask[etoro_id] = float(content["Ask"])
                ask_changed = True

            if not bid_changed and not ask_changed and msg_type != "Snapshot":
                return

            bid = self._last_bid.get(etoro_id)
            ask = self._last_ask.get(etoro_id)
            if bid is None or ask is None:
                return

            date_str = content.get("Date")
            if date_str:
                try:
                    dt    = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    ts_ns = int(dt.timestamp() * 1e9)
                except ValueError:
                    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
            else:
                ts_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)

            state  = self._states[etoro_id]
            symbol = state.symbol
            tick   = RawTick(bid=bid, ask=ask, ts_event=ts_ns)

            async with self._tick_lock:
                self._tick_buffer[symbol].append(tick)

            self._tick_counter += 1
            if self._tick_counter % 1000 == 0:
                async with self._tick_lock:
                    total_buffered = sum(len(v) for v in self._tick_buffer.values())
                log.info(
                    f"[catalog_service] Heartbeat: {self._tick_counter} Ticks verarbeitet, "
                    f"{total_buffered} im Puffer."
                )

        except Exception as e:
            log.debug(f"[catalog_service] Message-Fehler: {e} | msg={msg}")

    # ── Stündlicher Flush ─────────────────────────────────────────────────────

    async def _flush_loop(self) -> None:
        """Asynchroner Task: Flusht den Tick-Puffer stündlich als ZIP.

        Läuft parallel zum _ws_loop. Wacht FLUSH_INTERVAL_S Sekunden,
        dann wird der aktuelle Puffer atomar geleert und verarbeitet.
        Die Datensammlung läuft während des Flushes nahtlos weiter.
        """
        while True:
            await asyncio.sleep(self.flush_interval_s)
            await self._do_flush()

    async def _do_flush(self) -> None:
        """Führt einen einzelnen Flush aus: Puffer → Dedup → Parquet → ZIP."""
        # Puffer atomar tauschen (Datensammlung läuft weiter)
        async with self._tick_lock:
            snapshot  = dict(self._tick_buffer)
            total_new = sum(len(v) for v in snapshot.values())
            self._tick_buffer.clear()

        if total_new == 0:
            log.info("[catalog_service] Flush: Puffer leer — kein ZIP geschrieben.")
            return

        log.info(
            f"[catalog_service] Flush gestartet: {total_new} Ticks, "
            f"{len(snapshot)} Instrumente."
        )

        try:
            zip_path = _write_zip(snapshot, self._states)
            if zip_path:
                log.info(f"[catalog_service] ZIP bereit für Orchestrator: {zip_path}")
            else:
                log.warning("[catalog_service] Flush: Kein ZIP erstellt (leere Daten).")
        except Exception as e:
            log.error(
                f"[catalog_service] Flush-Fehler: {e}\n{traceback.format_exc()}"
            )

    # ── Haupt-Run ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Startet den Dienst: Precision-Fetch → WS-Loop + Flush-Loop parallel."""
        log.info(
            f"[catalog_service] Dienst startet: {len(self._states)} Instrumente, "
            f"Flush-Intervall: {self.flush_interval_s}s"
        )

        # Precisions beim Start dynamisch laden
        await self._update_precisions()

        # WS-Loop und Flush-Loop parallel starten
        ws_task    = asyncio.create_task(self._ws_loop(),    name="ws_loop")
        flush_task = asyncio.create_task(self._flush_loop(), name="flush_loop")

        log.info("[catalog_service] WebSocket-Loop und Flush-Loop gestartet.")

        try:
            # Warte auf das erste fertiggestellte Task (beide sollten ewig laufen)
            done, pending = await asyncio.wait(
                [ws_task, flush_task],
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                if task.exception():
                    log.error(
                        f"[catalog_service] Task '{task.get_name()}' beendet mit "
                        f"Fehler: {task.exception()}"
                    )
                    os._exit(1)

        except asyncio.CancelledError:
            log.info("[catalog_service] Dienst wird sauber beendet …")
            ws_task.cancel()
            flush_task.cancel()
            # Letzten Flush vor Beenden
            log.info("[catalog_service] Finaler Flush vor Beenden …")
            await self._do_flush()
            raise


# ─── Universe Loader ──────────────────────────────────────────────────────────

def _load_etoro_id_map(universe_path: Path) -> dict[str, str]:
    """Lädt {etoro_id: symbol} aus data/universe/momentum_ls.json."""
    if not universe_path.exists():
        log.warning(f"[catalog_service] Universe-Datei nicht gefunden: {universe_path}")
        return {}

    try:
        with open(universe_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"[catalog_service] Fehler beim Lesen der Universe-Datei: {e}")
        return {}

    result: dict[str, str] = {}
    for item in data.get("universe", []):
        eid    = str(item.get("etoro_id", "")).strip()
        symbol = str(item.get("symbol", "")).strip()
        if eid and symbol and symbol != "None":
            result[eid] = symbol

    log.info(f"[catalog_service] Universe geladen: {len(result)} Instrumente.")
    return result


# ─── Entry-Point ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="eToro Nautilus Continuous Catalog Service (Standalone)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--universe", type=Path, default=UNIVERSE_PATH,
        help=f"Pfad zur Universe-JSON-Datei (Standard: {UNIVERSE_PATH})"
    )
    parser.add_argument(
        "--instrument-ids", nargs="*", default=None,
        help="Explizite eToro-Instrument-IDs (überschreibt Universe-Datei)"
    )
    parser.add_argument(
        "--flush-interval", type=int, default=FLUSH_INTERVAL_S,
        help=f"Flush-Intervall in Sekunden (Standard: {FLUSH_INTERVAL_S})"
    )
    args = parser.parse_args()

    # ── Pflicht-Verzeichnisse vorab anlegen (I/O-Laufzeitfehler verhindern) ──
    IMPORT_PATH.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data" / "state").mkdir(parents=True, exist_ok=True)

    _setup_logging()

    load_dotenv(str(ENV_FILE))
    api_key  = os.getenv("ETORO_API_KEY",  "")
    user_key = os.getenv("ETORO_USER_KEY", "")

    if not api_key or not user_key:
        log.error("[catalog_service] ETORO_API_KEY oder ETORO_USER_KEY fehlen — Abbruch.")
        return 1

    # Instrument-Map laden
    if args.instrument_ids:
        # Explizite IDs ohne Symbol (Fallback-Symbol = ID.ETORO)
        etoro_id_map = {eid: f"ID{eid}.ETORO" for eid in args.instrument_ids}
        log.info(f"[catalog_service] Explizite IDs: {list(etoro_id_map.keys())}")
    else:
        etoro_id_map = _load_etoro_id_map(Path(args.universe))

    if not etoro_id_map:
        log.error("[catalog_service] Keine Instrumente gefunden — Abbruch.")
        return 1

    service = CatalogService(
        api_key=api_key,
        user_key=user_key,
        etoro_id_to_symbol=etoro_id_map,
        flush_interval_s=args.flush_interval,
    )

    log.info(
        f"[catalog_service] Starte mit {len(etoro_id_map)} Instrumenten | "
        f"Flush alle {args.flush_interval}s | "
        f"ZIPs → {IMPORT_PATH}"
    )

    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        log.info("[catalog_service] Manuell beendet (KeyboardInterrupt).")
    except SystemExit:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
