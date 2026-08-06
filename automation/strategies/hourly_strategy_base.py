"""
automation/strategies/hourly_strategy_base.py
=============================================
Base class for all hourly-candle strategies in automation/.
Adds automatic ATR Trailing Stop (1.5× ATR) and Time-based Exit (48 bars).

All strategies that previously extended `Strategy` should now extend
`HourlyStrategyBase`. The on_bar() override calls `_check_exits_and_update()`
automatically at the START of each bar, before strategy signal logic runs.

Usage in a strategy:
    class MyStrategy(HourlyStrategyBase):
        def on_start(self):
            super().on_start()  # REQUIRED
            self.subscribe_bars(self.bar_type)

        def on_bar(self, bar: Bar):
            if self._check_exits_and_update(bar):
                return  # Exit was triggered — do not generate new signals this bar
            # ... strategy signal logic ...
"""
from __future__ import annotations

import enum
import logging
import statistics
import traceback
import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path
from nautilus_trader.model.objects import Price

from nautilus_trader.config import StrategyConfig
import math
from datetime import datetime
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import SimpleMovingAverage
from automation.momentum_ls_allocator import MomentumLSAllocator
from automation.log_manager import emit_execution_event
from automation.optimizer._contracts import MAX_BARS_IN_TRADE_HARD_CAP

log = logging.getLogger(__name__)


class ExitReason(enum.Enum):
    """Issue #838 — Enum statt stringbasierter Exit-Klassifikation (`"Trailing Stop" not in
    exit_reason`), damit eine Umformulierung der Log-Meldung die Semantik nicht mehr ändern kann."""

    TRAILING_STOP = "TRAILING_STOP"
    TIME_BOX = "TIME_BOX"
    SIGNAL_REVERSAL = "SIGNAL_REVERSAL"
    PROFIT_TARGET = "PROFIT_TARGET"


class HourlyStrategyConfig(StrategyConfig, kw_only=True, frozen=True):
    instrument_id: str
    bar_type: str
    trade_amount_usd: float = 100.0
    trade_amount_pct: float | None = None
    max_open_positions: int = 1
    atr_period: int = 14
    atr_trailing_multiplier: float = 1.5
    # Issue #897 — Anker des ATR-Trailing-Stops. "price_extreme" (Chandelier-Formulierung, Default)
    # rastet ausschliesslich auf dem seit Einstieg erreichten Kurs-Extremum; "close_ratchet"
    # reproduziert das Alt-Verhalten (rastet zusaetzlich auf der ATR-Schaetzung, siehe Pitfall #285
    # in AGENTS.md) bit-identisch und bleibt fuer den A/B-Kalibrierlauf erhalten.
    trailing_stop_anchor: str = "price_extreme"
    # Issue #897 Fix 4 — Untergrenze fuer den ATR-Wert in bps des aktuellen Preises. Eine ATR von
    # exakt 0 (Bar mit high==low==prev_close) wuerde sonst den Stop auf den Schlusskurs setzen und
    # die Position beim naechsten Tick beenden.
    atr_floor_bps: float = 2.0
    # Issue #714 (GR-01) — 24h-Zeitbox = 24 Bar-Intervalle (1h-Bars), nicht Kalenderzeit. Der
    # bestehende Bar-Zähler-Exit (siehe _check_exits_and_update) ist der Mechanismus; dieser Default
    # UND alle Optimizer-Suchraum-Obergrenzen (spaces.py) sind auf <= 24 geklemmt.
    max_bars_in_trade: int = 24
    profit_target_pct: float | None = None
    cooldown_bars: int = 12
    trend_filter_period: int = 0
    max_daily_trades: int | None = 5
    min_holding_time: int = 0
    min_signal_strength: float = 0.0
    max_trades_cap: int | None = None
    # Issue #836 — Bars, die der Watchdog wartet, bevor er einen ausgelösten, aber nie bestätigten
    # Exit (Cancel/Fill/Reject-Event blieb aus) mit einem erzwungenen Markt-Close abschliesst.
    exit_close_max_bars: int = 2
    # Issue #859 — Obergrenze vergeblicher Markt-Close-Versuche (rejected/denied), bevor der Trial
    # terminal unrecoverable gilt (statt eine offene Position endlos erneut zu versuchen).
    exit_close_max_retries: int = 3
    # Issue #712 (Req-02+Req-03, opt-in) — vereinheitlichtes dynamisches Take-Profit-Modell,
    # in der Basisklasse für alle 15 Strategien verfügbar. Default False ⇒ bit-identische
    # Regression (der statische profit_target_pct-Pfad bleibt unverändert erhalten).
    dyn_tp_enabled: bool = False
    dyn_tp_lambda: float = 1.0
    dyn_tp_gamma: float = 1.5
    # Issue #715 (GR-02) — Pre-Trade-Spread-Gate (Laufzeit). spread_gate_bps=None ⇒ aus
    # k_spread · spread_bps_model (backtest.json, Single Source of Truth) abgeleitet.
    spread_gate_bps: float | None = None
    k_spread: float = 2.0
    # Issue #716 (GR-03) — node-weiter Aggregat-Exposure-Cap + harter Notional-Deckel, ZUSÄTZLICH
    # zum (unverändert bestehenden) per-Strategie max_open_positions. Konservative Defaults
    # (Schutz gegen Capital Starvation im Paper-Konto, start_capital=10000 USD/backtest.json):
    # max_aggregate_open_positions=5 ⇒ bei typischem trade_amount_pct=15% (strategy_defaults.json)
    # sind maximal ~75% Equity gleichzeitig als Position gebunden, deutlich unter der de-facto
    # ungedeckelten Obergrenze von bis zu 15 Strategien × je max_open_positions. max_order_notional
    # =2000 USD liegt knapp über dem typischen 15%-Trade (1500 USD) und deckelt Fehl-Sizing
    # (Balance-Query-Bug, Allocator-Fehlkonfiguration) hart ab, ohne den Normalbetrieb einzuschränken.
    max_aggregate_open_positions: int = 5
    max_order_notional: float = 2000.0


DEFAULT_ATR_TRAILING_MULTIPLIER = 1.5
# Issue #714 (GR-01) — 24-Bar-Zeitbox (1h-Bars). Auch die HARTE Obergrenze für aus dem Cache
# geladene Alt-Configs/Studies mit Werten > 24 (Konstruktor-Klemmung unten). Issue #858 — Single
# Source of Truth über einen Import statt einer eigenen dritten Kopie des Literals, konsistent mit
# ``spaces._MAX_BARS_IN_TRADE_CAP``/``invariants._MAX_BARS_IN_TRADE_CAP`` (Pitfall #271).
DEFAULT_MAX_BARS_IN_TRADE = 24

# Issue #712 (Req-02+Req-03) — Cancel/Replace nur bei |ΔTarget| > 1 Tick (Order-Sturm-Schutz,
# konsistent mit etoro_rate_limiter.py).
_DYN_TP_MIN_TICK_DELTA = 1


# Issue #715 (GR-02) — gecachte, lazily geladene Resolution der modellierten Normal-Spread (bps)
# aus backtest.json/instrument_map.json. SINGLE SOURCE OF TRUTH mit dem Backtest-Kostenmodell
# (dieselbe Symbol-Override -> Asset-Class -> DEFAULT Auflösung wie backtest_runner.resolve_
# spread_bps, #566) — bewusst als eigenständige, dependency-leichte Kopie gehalten, damit der
# LIVE-Pfad (momentum_ls_run.py) nicht automation.backtest_runner (inkl. pandas/pyarrow/
# BacktestEngine) importieren muss. Nie zwei unabhängig gepflegte Spread-Zahlen (#712-Analogie).
_spread_cfg_cache: dict | None = None
_instrument_asset_class_cache: dict | None = None


def _read_spread_cfg() -> dict:
    global _spread_cfg_cache
    if _spread_cfg_cache is not None:
        return _spread_cfg_cache
    try:
        from automation.optimizer.trial_config import config_dir
        import json as _json_mod
        with open(config_dir() / "backtest.json", "r", encoding="utf-8") as f:
            data = _json_mod.load(f) or {}
        _spread_cfg_cache = {
            "by_asset_class": data.get("spread_bps_by_asset_class") or {},
            "by_symbol": data.get("spread_bps_by_symbol") or {},
        }
    except (OSError, ValueError):
        _spread_cfg_cache = {"by_asset_class": {}, "by_symbol": {}}
    return _spread_cfg_cache


def _read_instrument_asset_class(instrument_id_str: str) -> str:
    global _instrument_asset_class_cache
    if _instrument_asset_class_cache is None:
        _instrument_asset_class_cache = {}
        try:
            from automation.optimizer.trial_config import config_dir
            import json as _json_mod
            with open(config_dir() / "instrument_map.json", "r", encoding="utf-8") as f:
                data = _json_mod.load(f) or {}
            for _, inst_data in (data.get("instruments") or {}).items():
                sym = inst_data.get("symbol")
                if sym:
                    _instrument_asset_class_cache[sym] = (inst_data.get("asset_class") or "DEFAULT").upper()
        except (OSError, ValueError):
            pass
    return _instrument_asset_class_cache.get(instrument_id_str, "DEFAULT")


def resolve_spread_bps_model(instrument_id_str: str) -> float:
    """Issue #715 — modellierte Normal-Spread (bps) für ein Instrument: Symbol-Override ->
    Asset-Class -> DEFAULT (dieselbe Auflösungsreihenfolge wie backtest_runner.resolve_spread_bps,
    #566). 0.0, wenn keine Konfiguration geladen werden kann (fail-safe: das Gate bleibt dann
    inaktiv, kein erfundener Schwellenwert)."""
    cfg = _read_spread_cfg()
    by_symbol = cfg["by_symbol"]
    if instrument_id_str in by_symbol:
        return float(by_symbol[instrument_id_str])
    by_class = cfg["by_asset_class"]
    if not by_class:
        return 0.0
    asset_class_key = _read_instrument_asset_class(instrument_id_str)
    return float(by_class.get(asset_class_key, by_class.get("DEFAULT", 4.0)))


def compute_dyn_tp_target(
    entry_price: float,
    atr_value: float,
    side: str,
    bars_in_pos: int,
    max_bars_in_trade: int,
    dyn_tp_lambda: float,
    dyn_tp_gamma: float,
) -> float:
    """Issue #712 — vereinheitlichtes dynamisches Take-Profit-Modell (Req-02 e^{-λt}-Decay +
    Req-03 μ+γ·ATR-Scaling zu EINER kohärenten, deadline-bewussten Zielfunktion vereint):

        TP_long(t)  = entry + γ · ATR_n · exp(−λ · bars_in_pos / max_bars_in_trade)
        TP_short(t) = entry − γ · ATR_n · exp(−λ · bars_in_pos / max_bars_in_trade)

    ``bars_in_pos=0 ⇒ exp(0)=1 ⇒`` Target ``= entry ± γ·ATR`` (voller Abstand). Je näher die
    24-Bar-Zeitbox (GR-01, #714) rückt, desto kleiner (aber weiterhin positiv) der Abstand — erzwingt
    Liquidation bei progressiv kleineren, aber positiven Bewegungen. Reine, deterministische Funktion
    (kein I/O) — separat unit-testbar von der Cancel/Replace-Order-Mechanik."""
    span = max(1, int(max_bars_in_trade))
    decay = math.exp(-float(dyn_tp_lambda) * (float(bars_in_pos) / float(span)))
    offset = float(dyn_tp_gamma) * float(atr_value) * decay
    if side == "LONG":
        return float(entry_price) + offset
    return float(entry_price) - offset


class HourlyStrategyBase(Strategy):
    """
    Base strategy providing ATR Trailing Stop and Time-based Exit for hourly candles.

    State per instance:
      _trailing_stop_price: float | None  — current trailing stop level
      _trailing_stop_side: str | None     — "LONG" or "SHORT"
      _bars_in_position: int              — bars elapsed since last entry
      _in_position: bool                  — whether a position is currently open
    """

    def __init__(self, config: HourlyStrategyConfig, allocator: MomentumLSAllocator | None = None):
        super().__init__(config)
        self.allocator: MomentumLSAllocator | None = allocator
        self._account_id = None
        # Subclasses set these in their own __init__
        self.instrument_id: InstrumentId
        self.bar_type: BarType

        self._exit_atr = AverageTrueRange(self.config.atr_period)
        self._trailing_stop_price: float | None = None
        self.trend_filter_period = getattr(config, "trend_filter_period", 0)
        if self.trend_filter_period > 0:
            self.trend_filter_sma = SimpleMovingAverage(self.trend_filter_period)
        else:
            self.trend_filter_sma = None
        self._trend_filter_ready = False
        self._take_profit_price: float | None = None
        self._trailing_stop_side: str | None = None
        self._bars_in_position: int = 0
        self._in_position: bool = False
        # Issue #837 — entkoppelt die Trailing-Stop-Initialisierung vom _in_position-Flag:
        # _in_position bleibt jetzt waehrend eines laufenden (asynchronen) Exit-Versuchs True,
        # darf also nie mehr eine Neuverankerung des Trailing-Stops ausloesen koennen.
        self._trailing_initialised: bool = False
        self._pending_cancels: set = set()
        # Issue #836 — Fortsetzungs-Zustand eines ausgeloesten, aber noch nicht bestaetigten Exits.
        # Wird ausschliesslich in on_position_closed() auf None zurueckgesetzt (der einzige Ort, der
        # eine BESTAETIGTE Transaktion beschreibt).
        self._exit_pending: str | None = None
        self._exit_pending_kind: "ExitReason | None" = None
        self._exit_pending_bars: int = 0
        # Issue #859 — ersetzt das vorherige Boolean-Flag ``_exit_market_close_submitted`` (gesetzt
        # beim ABSENDEN, nur bei Erfolg geräumt — ein abgelehnter/verweigerter Close entwaffnete den
        # Watchdog dann DAUERHAFT, Pitfall #273). Der Watchdog gilt jetzt als entwaffnet, solange
        # DIESE Order (per ID) im Cache offen ist, nicht ab dem blossen Absenden.
        self._exit_market_close_order_id = None
        # Issue #859 Fix Punkt 4 — Obergrenze vergeblicher Markt-Close-Versuche (rejected/denied),
        # bevor der Trial als terminal unrecoverable gilt (statt eine offene Position endlos erneut
        # zu versuchen).
        self._exit_close_retries: int = 0
        self._exit_close_unrecoverable: bool = False
        self._exit_close_max_retries = max(1, int(getattr(config, "exit_close_max_retries", None) or 3))
        self._max_bars_in_trade = getattr(config, "max_bars_in_trade", None)
        if self._max_bars_in_trade is None:
            self._max_bars_in_trade = DEFAULT_MAX_BARS_IN_TRADE
        # Issue #714 (GR-01) — harte 24-Bar-Klemmung, damit aus dem Cache geladene Alt-Configs/
        # Studies mit Werten > 24 (vor der Bounds-Klemmung in spaces.py optimiert) die Invariante
        # nicht unterlaufen. Untergrenzen bleiben unverändert; bestehendes Bar-Zähler-Exit-Verhalten
        # bleibt bit-identisch für Positionen <= 24 Bars.
        self._max_bars_in_trade = min(int(self._max_bars_in_trade), MAX_BARS_IN_TRADE_HARD_CAP)

        self._atr_trailing_multiplier = getattr(config, "atr_trailing_multiplier", None)
        if self._atr_trailing_multiplier is None:
            self._atr_trailing_multiplier = DEFAULT_ATR_TRAILING_MULTIPLIER

        # Issue #897 — Trailing-Stop-Anker + ATR-Floor.
        self._trailing_stop_anchor = getattr(config, "trailing_stop_anchor", None) or "price_extreme"
        if self._trailing_stop_anchor not in ("price_extreme", "close_ratchet"):
            self._trailing_stop_anchor = "price_extreme"
        self._atr_floor_bps = float(getattr(config, "atr_floor_bps", None) or 2.0)
        # Issue #897 Fix 1 — seit Einstieg erreichtes Kurs-Extremum (Chandelier-Anker). Wird
        # AUSSCHLIESSLICH in on_position_opened() initialisiert (analog _trailing_initialised, #837).
        self._position_extreme: float | None = None
        # Issue #899 — je-Bar ATR-Ablesungen (effektiv, bps des Preises) waehrend der laufenden
        # Position; Rohmaterial fuer die ATR_median/ATR_min-Exit-Telemetrie, die dem schliessenden
        # Markt-Close-Order als Tag mitgegeben wird (siehe _execute_market_close).
        self._position_atr_bps_readings: list[float] = []

        self._profit_target_pct = getattr(config, "profit_target_pct", None)
        self._daily_trades: int = 0
        self._current_day: int | None = None
        self._executed_trades: int = 0

        # Issue #838 (Pitfall #263) — min_holding_time und max_bars_in_trade sind zwei unabhaengig
        # sampelbare Parameter mit einer impliziten Ordnungsbeziehung. Ohne Klemmung wuerde
        # min_holding_time >= max_bars_in_trade den Zeit-Exit dauerhaft unterdruecken koennen
        # (analog zur bestehenden MAX_BARS_IN_TRADE_HARD_CAP-Klemmung oben).
        min_holding_time_cfg = int(getattr(config, "min_holding_time", 0) or 0)
        if min_holding_time_cfg >= self._max_bars_in_trade:
            clamped = max(0, self._max_bars_in_trade - 1)
            self._log.warning(
                f"[{config.instrument_id}] min_holding_time="
                f"{min_holding_time_cfg} >= max_bars_in_trade={self._max_bars_in_trade} — geklemmt auf "
                f"{clamped} (Issue #838)."
            )
            self._min_holding_time = clamped
        else:
            self._min_holding_time = min_holding_time_cfg
        self._exit_close_max_bars = max(1, int(getattr(config, "exit_close_max_bars", None) or 2))

        # Issue #712 — dynamischer Take-Profit (opt-in, siehe HourlyStrategyConfig.dyn_tp_enabled).
        # _dyn_tp_pending_cancel/_dyn_tp_pending_target verwalten das Cancel/Replace GETRENNT von
        # _pending_cancels (das ausschliesslich dem Exit-Markt-Close-Pfad gehört) — sonst würde ein
        # Dyn-TP-Replace fälschlich _execute_market_close() auslösen, sobald die Order-Canceled-
        # Bestätigung eintrifft.
        self._dyn_tp_order_id = None
        self._dyn_tp_price: float | None = None
        self._dyn_tp_entry_price: float | None = None
        self._dyn_tp_side: str | None = None
        self._dyn_tp_pending_cancel = None
        self._dyn_tp_pending_target: float | None = None

        # Issue #717 (GR-04) — Bar-Zähler-Rehydrierung nach Reconnect/Neustart (#714-Root-Cause:
        # der In-Memory-_bars_in_position-Zähler ist sonst verloren). Vom on_start()-Reconnect-Check
        # gesetzt; vom NÄCHSTEN _check_exits_and_update-Init-Block konsumiert (statt auf 0 zu
        # resetten), sodass der reguläre Bar-Zähler-Exit nahtlos weiterläuft.
        self._bars_in_position_rehydrated: int | None = None
        self._gr04_subscribed = False

    def on_start(self):
        """Subclasses MUST call super().on_start() first."""
        if self.allocator is not None:
            accounts = self.cache.accounts()
            if accounts:
                self._account_id = accounts[0].id
            else:
                self._log.warning("No accounts found in cache on start. Allocation might fail.")

        if getattr(self, "trend_filter_period", 0) > 0 and getattr(self, "trend_filter_sma", None) is not None:
            self._warmup_trend_filter()

        # Issue #717 (GR-04) — Reconnect-Reconciliation: eine bereits offene Position (Reconnect
        # INNERHALB desselben Prozesses, oder ein Cache, der Positionen über einen Neustart hinweg
        # bewahrt) hat einen verlorenen In-Memory-_bars_in_position-Zähler (#714-Root-Cause).
        # Rekonstruiert ihn aus dem Bar-Cache relativ zu Nautilus' nativem pos.ts_opened (übersteht
        # den Reconnect via der Position selbst, keine eigene Persistenz nötig) und liquidiert
        # SOFORT, falls die 24-Bar-Zeitbox bereits (offline) abgelaufen ist.
        self._reconcile_after_reconnect()

        # Issue #717 (GR-04) — Phantom-Positions-Signal vom Execution-Client (Portfolio-Reconcile
        # bei (Re-)Connect, siehe etoro_execution._reconcile_positions_on_connect). Der Execution-
        # Client kann selbst keine neue Order ohne einen von Strategy.order_factory initiierten
        # Kontext erzeugen — das Signal löst stattdessen den bereits bestehenden, geprüften
        # _close_position_base-Pfad hier aus (schliesst [EX-2-followup]).
        if not self._gr04_subscribed and getattr(self, "msgbus", None) is not None:
            self.msgbus.subscribe(
                topic=f"events.gr04_close_request.{self.instrument_id}",
                handler=self._on_gr04_close_request,
            )
            self._gr04_subscribed = True

    def _reconcile_after_reconnect(self) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if not positions:
            return
        pos = positions[0]
        entry_ns = int(pos.ts_opened)
        bars_elapsed = self._count_bars_since(entry_ns)

        if bars_elapsed is not None:
            expired = bars_elapsed >= self._max_bars_in_trade
            rehydrated_bars = min(bars_elapsed, self._max_bars_in_trade)
            basis = f"bars_seit_entry={bars_elapsed}"
        else:
            # Fallback (Bar-Historie nicht rekonstruierbar, z. B. Cache leer nach Kaltstart):
            # konservativ per Wall-Clock — die sichere Richtung im Offline-Recovery-Kontext (eher
            # schliessen als dangeln lassen).
            now_ns = self.clock.timestamp_ns()
            elapsed_hours = (now_ns - entry_ns) / 3_600_000_000_000.0
            expired = elapsed_hours >= 24.0
            rehydrated_bars = self._max_bars_in_trade if expired else 0
            basis = f"wall_clock_elapsed_h={elapsed_hours:.2f}"

        self._bars_in_position_rehydrated = rehydrated_bars

        if expired:
            self._log.warning(
                f"[{self.instrument_id}] GR-04 Offline-Ablauf nach Reconnect ({basis}, "
                f"max_bars_in_trade={self._max_bars_in_trade}) — liquidiere sofort."
            )
            self._close_position_base(pos)

    def _count_bars_since(self, since_ns: int) -> int | None:
        """Issue #717 — Anzahl Bars in der Cache-Historie STRIKT NACH ``since_ns``. ``None``, wenn
        der Bar-Cache (noch) keine Historie für dieses Instrument trägt (Kaltstart) — der Aufrufer
        fällt dann auf den Wall-Clock-Fallback zurück."""
        try:
            bars = self.cache.bars(self.bar_type)
        except Exception:
            return None
        if not bars:
            return None
        return sum(1 for b in bars if int(b.ts_event) > since_ns)

    def _on_gr04_close_request(self, msg) -> None:
        """Issue #717 (GR-04) — Reaktion auf das Phantom-Positions-Signal des Execution-Clients
        (eine bei eToro bereits geschlossene, in Nautilus noch offene Position). Nutzt den
        bestehenden, geprüften _close_position_base-Pfad (kein neuer, riskanter Order-Fabrikations-
        Code in der ExecutionClient nötig)."""
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if not positions:
            return
        reason = msg.get("reason", "unknown") if isinstance(msg, dict) else "unknown"
        self._log.warning(
            f"[{self.instrument_id}] GR-04 Close-Request empfangen (reason={reason}) — schliesse."
        )
        self._close_position_base(positions[0])

    def _warmup_trend_filter(self):

        # Calculate how many hours we need
        needed_hours = self.trend_filter_period + 48  # Buffer of 48 hours

        parquet_dir = Path("data/nautilus/quote_tick") / str(self.instrument_id)
        if not parquet_dir.exists():
            self._log.error(f"[{self.instrument_id}] Keine Parquet-Daten für SMA Warmup gefunden.")
            return

        try:
            # We want to use pandas to read and resample the data.
            # We only need the last `needed_hours` of data ideally, but we can just read the whole parquet and take tail.
            dataset = pq.ParquetDataset(str(parquet_dir))
            table = dataset.read(columns=['ts_event', 'bid_price', 'ask_price'])
            df = table.to_pandas()
            if df.empty:
                self._log.error(f"[{self.instrument_id}] Parquet-Daten sind leer.")
                return

            df['ts_event'] = pd.to_datetime(df['ts_event'], unit='ns')
            df.set_index('ts_event', inplace=True)
            df.sort_index(inplace=True)

            # Mid price for creating synthetic bars to feed the SMA
            df['mid_price'] = (df['bid_price'] + df['ask_price']) / 2.0

            # Resample to 1h bars
            bars_1h = df['mid_price'].resample('h').last().dropna()

            if len(bars_1h) < self.trend_filter_period:
                self._log.error(f"[TrendFilter Warmup] Failed for {self.instrument_id}. Required: {self.trend_filter_period} bars. Found: {len(bars_1h)}. Strategy execution gated (fail-closed).")
                return

            # Feed the last 'trend_filter_period' bars into the SMA




            # Feed the last N values
            warmup_bars = bars_1h.tail(self.trend_filter_period + 10)

            for ts, close_price in warmup_bars.items():
                ts_ns = int(ts.timestamp() * 1e9)

                # Mock a bar to feed the indicator
                dummy_bar = Bar(

                    bar_type=self.bar_type,
                    open=Price(close_price, 4),
                    high=Price(close_price, 4),
                    low=Price(close_price, 4),
                    close=Price(close_price, 4),
                    volume=Quantity(1.0, 4),
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
                self.trend_filter_sma.handle_bar(dummy_bar)


            if self.trend_filter_sma.initialized:
                self._trend_filter_ready = True
                self._log.info(f"[{self.instrument_id}] Trend Filter SMA ({self.trend_filter_period}) erfolgreich pre-warmed. Letzter Wert: {self.trend_filter_sma.value:.4f}")
            else:
                self._log.error(f"[{self.instrument_id}] Trend Filter SMA ({self.trend_filter_period}) nach Pre-Warming NICHT initialized.")

        except Exception as e:

            self._log.error(f"[{self.instrument_id}] Fehler beim SMA Warmup: {e} \n {traceback.format_exc()}")
            self._trend_filter_ready = False



    def can_go_long(self, bar: Bar) -> bool:
        if self.trend_filter_period == 0:
            return True
        if not self._trend_filter_ready or self.trend_filter_sma is None or not self.trend_filter_sma.initialized:
            return False

        self.trend_filter_sma.handle_bar(bar)
        return float(bar.close) > self.trend_filter_sma.value

    def _get_current_balance(self) -> float:
        """Liefert das Guthaben in der Quote-Currency des Instruments.

        WICHTIG: NautilusTrader's `Account.balances()` ist eine METHODE
        (cpdef dict balances(self) -> dict[Currency, AccountBalance]), KEIN
        Attribut. Der Zugriff via `account.balances` liefert das gebundene
        Methoden-Objekt (truthy, nicht iterierbar) → `TypeError: 'method'
        object is not iterable`. Daher die getypten Accessoren nutzen.
        """
        if not self._account_id:
            accounts = self.cache.accounts()
            if accounts:
                self._account_id = accounts[0].id

        if not self._account_id:
            return 0.0

        account = self.cache.account(self._account_id)
        instrument = self.cache.instrument(self.instrument_id)
        if account is None or instrument is None:
            return 0.0

        currency = instrument.quote_currency

        try:
            money = account.balance_total(currency)
            if money is None:
                money = account.balance_free(currency)
            if money is not None:
                return float(money.as_double())
        except Exception as e:  # defensiv: niemals raise aus on_bar()-Pfad
            self._log.warning(
                f"[{self.instrument_id}] Balance-Auflösung fehlgeschlagen: {e}. Returning 0.0."
            )
            return 0.0

        self._log.warning(
            f"[{self.instrument_id}] Kein Balance-Eintrag für {currency} im Cache. Returning 0.0."
        )
        return 0.0

    def _effective_atr_value(self, atr_val: float, price: float) -> float:
        """Issue #897 Fix 4 — Untergrenze `max(ATR, atr_floor_bps · price)`. Eine ATR von exakt 0
        (Bar mit high == low == prev_close) würde den Trailing-Stop sonst auf den Schlusskurs
        setzen und die Position beim nächsten Tick beenden."""
        floor = (self._atr_floor_bps / 10000.0) * price
        return max(float(atr_val), floor)

    def _check_exits_and_update(self, bar: Bar) -> bool:
        """
        Called at the START of every on_bar() in subclasses.
        Updates ATR, trailing stop level, and bar counter.
        Returns True if an exit order was submitted OR is already in flight (caller should return
        immediately in both cases — no new signals while a close is unresolved).
        Returns False if no exit triggered (caller continues with signal logic).

        NOTE regarding Slippage vs. Native Orders:
        The exit logic deliberately evaluates against the closed bar (`bar.close`) rather than
        submitting native intra-bar `trailing_stop_market` orders to the exchange.
        This prevents the strategy from being stopped out by short, extreme intra-bar noise ("whipsaws").
        Crucially, this ensures absolute consistency between historical backtests (which use 1h bars)
        and the Walk-Forward/Out-of-Sample live execution, maintaining the statistical integrity of the
        gating thresholds.

        Issue #836/#837 — a triggered exit is asynchronous (a resting order may need to be
        cancelled first). All state describing a CONFIRMED transaction (`_in_position`,
        `_bars_in_position`, the trailing stop) is therefore only ever reset in
        `on_position_closed`/`on_position_opened` — never here, and never in `_close_position_base`.
        """
        current_day = pd.Timestamp(bar.ts_init).day
        if self._current_day != current_day:
            self._current_day = current_day
            self._daily_trades = 0

        self._exit_atr.handle_bar(bar)

        positions = self.cache.positions_open(instrument_id=self.instrument_id)

        if not positions:
            if self._in_position:
                self._in_position = False
                self._trailing_stop_price = None
                self._trailing_stop_side = None
                self._bars_in_position = 0
            self._trailing_initialised = False
            self._exit_pending = None
            self._exit_pending_kind = None
            self._exit_pending_bars = 0
            self._exit_market_close_order_id = None
            self._exit_close_retries = 0
            self._pending_cancels.clear()
            return False

        pos = positions[0]

        close = float(bar.close)

        # Initialise on first bar after entry (on_position_opened sets _in_position=False
        # so this block runs exactly once per position)
        if not self._in_position:
            self._in_position = True
            # Issue #717 (GR-04) — ein von on_start() rehydrierter Bar-Zähler (überlebende Position
            # nach Reconnect/Neustart) wird HIER übernommen statt auf 0 zurückgesetzt; danach
            # einmalig konsumiert (kein Effekt auf den nächsten genuinen Neueinstieg).
            if self._bars_in_position_rehydrated is not None:
                self._bars_in_position = self._bars_in_position_rehydrated
                self._bars_in_position_rehydrated = None
            else:
                self._bars_in_position = 0
            self._pending_cancels.clear()

        # Issue #837 — von _in_position entkoppelt: einmalig pro Position, nie erneut ausgelöst
        # durch einen fehlgeschlagenen/verzögerten Exit-Versuch (der _in_position unverändert
        # True lässt).
        if not self._trailing_initialised:
            self._trailing_initialised = True
            self._trailing_stop_side = "LONG" if pos.side == PositionSide.LONG else "SHORT"
            if self._trailing_stop_anchor == "price_extreme":
                if self._position_extreme is None:
                    self._position_extreme = close
                if self._trailing_stop_side == "LONG":
                    self._position_extreme = max(self._position_extreme, float(bar.high))
                else:
                    self._position_extreme = min(self._position_extreme, float(bar.low))
            if self._exit_atr.initialized:
                atr_val = self._effective_atr_value(self._exit_atr.value, close)
                anchor = self._position_extreme if self._trailing_stop_anchor == "price_extreme" else close
                if self._trailing_stop_side == "LONG":
                    self._trailing_stop_price = anchor - self._atr_trailing_multiplier * atr_val
                else:
                    self._trailing_stop_price = anchor + self._atr_trailing_multiplier * atr_val
            else:
                self._trailing_stop_price = None

        # Issue #837 (AK-1) — der Bar-Zähler läuft weiter, solange die Position offen ist, auch
        # während ein Exit-Versuch noch unbestätigt ist (_exit_pending).
        self._bars_in_position += 1

        # Issue #899 — ATR-Telemetrie je Bar (bps des Schlusskurses), Rohmaterial fuer die
        # ATR_median/ATR_min-Tags des schliessenden Orders (#897 Fix 3 Eingangsgroesse).
        if self._exit_atr.initialized and close > 0:
            self._position_atr_bps_readings.append(
                self._effective_atr_value(self._exit_atr.value, close) / close * 10_000.0
            )

        # Update trailing stop.
        # Issue #897 — "price_extreme" (default) rastet ausschliesslich auf dem Kurs-Extremum seit
        # Einstieg; der Stop wird jeden Bar aus dem AKTUELLEN ATR-Wert neu berechnet (keine Ratsche
        # gegen den vorherigen Stop-Wert), damit eine einzelne ruhige Bar den Stop nicht dauerhaft an
        # den Kurs klemmt (Pitfall #285). "close_ratchet" reproduziert das Alt-Verhalten bit-identisch:
        # der Stop rastet zusaetzlich auf der ATR-Schaetzung und gibt nie nach.
        if self._exit_atr.initialized and self._trailing_stop_price is not None:
            atr_val = self._effective_atr_value(self._exit_atr.value, close)
            if self._trailing_stop_anchor == "price_extreme":
                # Defensiv: _position_extreme kann in Tests, die internen Zustand ohne
                # on_position_opened() manipulieren, noch None sein — dann mit dieser Bar seeden.
                if self._position_extreme is None:
                    self._position_extreme = close
                if self._trailing_stop_side == "LONG":
                    self._position_extreme = max(self._position_extreme, float(bar.high))
                    self._trailing_stop_price = self._position_extreme - self._atr_trailing_multiplier * atr_val
                else:
                    self._position_extreme = min(self._position_extreme, float(bar.low))
                    self._trailing_stop_price = self._position_extreme + self._atr_trailing_multiplier * atr_val
            else:
                if self._trailing_stop_side == "LONG":
                    new_stop = close - self._atr_trailing_multiplier * atr_val
                    self._trailing_stop_price = max(self._trailing_stop_price, new_stop)
                else:
                    new_stop = close + self._atr_trailing_multiplier * atr_val
                    self._trailing_stop_price = min(self._trailing_stop_price, new_stop)

        # Issue #836 — ein Exit ist bereits unterwegs: keinen zweiten Exit auslösen (kein
        # Doppel-Close, keine Order-Flut). Der Watchdog erzwingt den Close, falls die
        # Cancel/Fill/Reject-Bestätigung ausbleibt.
        if self._exit_pending is not None:
            self._exit_close_watchdog(bar)
            return True

        # Exit condition 1: ATR Trailing Stop hit
        exit_reason = None
        if self._trailing_stop_price is not None:
            if self._trailing_stop_side == "LONG" and close <= self._trailing_stop_price:
                exit_reason = (
                    f"ATR Trailing Stop LONG hit @ {close:.4f} <= {self._trailing_stop_price:.4f}"
                )
            elif self._trailing_stop_side == "SHORT" and close >= self._trailing_stop_price:
                exit_reason = (
                    f"ATR Trailing Stop SHORT hit @ {close:.4f} >= {self._trailing_stop_price:.4f}"
                )

        exit_kind = ExitReason.TRAILING_STOP if exit_reason is not None else None

        # Exit condition 2: Time-based exit (24 bars, GR-01/#714)
        if exit_reason is None and self._bars_in_position >= self._max_bars_in_trade:
            exit_reason = f"Time-exit after {self._bars_in_position} bars"
            exit_kind = ExitReason.TIME_BOX

        # Issue #838 — min_holding_time greift ausdrücklich NICHT bei TRAILING_STOP oder TIME_BOX
        # (die beiden einzigen Exit-Arten, die dieser Block auslösen kann): der Konstruktor klemmt
        # min_holding_time bereits hart unter max_bars_in_trade (Pitfall #263), und keiner der
        # beiden mechanismuskritischen Exits darf durch eine zukünftige Fehlkonfiguration dauerhaft
        # unterdrückbar sein.

        if exit_reason:
            self._log.info(f"[{self.instrument_id}] EXIT: {exit_reason}")
            self._exit_pending = exit_reason
            self._exit_pending_kind = exit_kind
            self._exit_pending_bars = 0
            self._exit_market_close_order_id = None
            self._exit_close_retries = 0
            self._close_position_base(pos)
            return True

        # Issue #712 — dynamischer Take-Profit: Cancel/Replace der ruhenden Limit-Order je Bar,
        # NUR wenn kein Exit diesen Bar ausgelöst hat (die Position bleibt offen).
        if getattr(self.config, "dyn_tp_enabled", False):
            self._update_dyn_tp_order()

        return False

    def _exit_close_watchdog(self, bar: Bar) -> None:
        """Issue #836 — erzwingt den Markt-Close, falls seit dem Auslösen des Exits
        `exit_close_max_bars` Bars vergangen sind, ohne dass eine Cancel/Fill/Reject-Bestätigung
        `_execute_market_close()` bereits ausgelöst hat. Fail-loud statt still hängen.

        Issue #859 — der Watchdog gilt als entwaffnet, SOLANGE die zuletzt abgesendete Markt-Close-
        Order (per ID) im Cache noch offen ist (``order.is_open``) — nicht ab dem blossen Absenden
        (vorher: ``_exit_market_close_submitted`` wurde beim Absenden gesetzt und nur bei
        erfolgreicher Bestätigung geräumt; ein abgelehnter/verweigerter Close entwaffnete den
        Watchdog dann DAUERHAFT, Pitfall #273 — die Position blieb bis zum Datenende offen). Ein
        abgelehnter/verweigerter Versuch räumt ``_exit_market_close_order_id`` in
        ``on_order_rejected``/``on_order_denied``, sodass der Watchdog hier erneut auslöst."""
        if self._exit_close_unrecoverable:
            return
        if self._exit_market_close_order_id is not None:
            order = self.cache.order(self._exit_market_close_order_id)
            if order is not None and order.is_open:
                return
            # Order nicht mehr offen, aber kein Callback hat die ID geräumt (sollte durch die
            # on_order_rejected/on_order_denied-Zweige unten nicht vorkommen) — fail-safe räumen,
            # damit der Watchdog nicht dauerhaft blockiert bleibt.
            self._exit_market_close_order_id = None
        self._exit_pending_bars += 1
        if self._exit_pending_bars < self._exit_close_max_bars:
            return

        open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
        payload = {
            "instrument": str(self.instrument_id),
            "exit_reason": self._exit_pending,
            "bars_waited": self._exit_pending_bars,
            "open_orders": [str(o.client_order_id) for o in open_orders],
        }
        emit_execution_event(log, "EXIT_CLOSE_STALLED", payload, level=logging.ERROR)
        self._log.error(
            f"[{self.instrument_id}] EXIT_CLOSE_STALLED nach {self._exit_pending_bars} Bars "
            f"(exit_reason={self._exit_pending}) — erzwinge Markt-Close."
        )
        self._pending_cancels.clear()
        self._execute_market_close()

    def _entry_allowed(self) -> bool:
        """Issue #838 — Trade-Caps gehören ausschliesslich in den Entry-Pfad (hier, konsumiert von
        `_compute_quantity`, gemeinsam mit `max_daily_trades`). Ein Cap darf niemals die Auswertung
        von Trailing-Stop, Zeit-Exit oder ATR-Update in `_check_exits_and_update` blockieren."""
        max_cap = getattr(self.config, "max_trades_cap", None)
        if max_cap is not None and self._executed_trades >= max_cap:
            return False
        return True

    def _close_position_base(self, pos) -> None:
        """Submits a market order to close the given position. Cancels pending limits first."""
        open_orders = self.cache.orders_open(instrument_id=self.instrument_id)
        if open_orders:
            for order in open_orders:
                self._pending_cancels.add(order.client_order_id)
                self.cancel_order(order)
            return  # Wait for on_order_canceled

        self._execute_market_close(pos)

    def _execute_market_close(self, pos=None) -> None:
        if pos is None:
            positions = self.cache.positions_open(instrument_id=self.instrument_id)
            if not positions:
                return
            pos = positions[0]

        exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        # Issue #899 — der schliessende Markt-Close-Order traegt die Exit-Klassifikation (und die
        # ATR-Telemetrie der Position) als Tags, damit backtest_runner.extract_metrics sie OHNE
        # Umweg ueber den (abgeschnittenen, #899-Root-Cause) Subprozess-Logger auslesen kann.
        tags = None
        if self._exit_pending_kind is not None:
            tag_list = [f"EXIT_REASON:{self._exit_pending_kind.value}"]
            if self._position_atr_bps_readings:
                tag_list.append(f"ATR_MEDIAN_BPS:{statistics.median(self._position_atr_bps_readings):.4f}")
                tag_list.append(f"ATR_MIN_BPS:{min(self._position_atr_bps_readings):.4f}")
            tags = tag_list
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=exit_side,
            quantity=pos.quantity,
            time_in_force=TimeInForce.GTC,
            tags=tags,
        )
        # Issue #859 — sobald der Markt-Close tatsächlich abgesetzt wird, ist das Fortsetzungs-Token
        # dieses Exit-Versuchs konsumiert: der Watchdog darf nicht erneut auslösen, WÄHREND diese
        # konkrete Order (per ID) noch offen ist. Anders als das vorherige Boolean-Flag räumt ein
        # abgelehnter/verweigerter Callback (on_order_rejected/on_order_denied) diese ID wieder —
        # der Watchdog kann dann einen neuen Versuch auslösen, statt dauerhaft entwaffnet zu bleiben.
        if self._exit_pending is not None:
            self._exit_market_close_order_id = order.client_order_id
        self.submit_order(order)

    # ── Issue #712 — dynamischer Take-Profit (opt-in) ───────────────────────────────────────────

    def _reset_dyn_tp_state(self) -> None:
        self._dyn_tp_order_id = None
        self._dyn_tp_price = None
        self._dyn_tp_entry_price = None
        self._dyn_tp_side = None
        self._dyn_tp_pending_cancel = None
        self._dyn_tp_pending_target = None

    def _compute_dyn_tp_target(self) -> float | None:
        """None, solange die ATR (noch) nicht initialisiert ist (Warmup) — analog zur Trailing-
        Stop-Initialisierung in _check_exits_and_update."""
        if self._dyn_tp_entry_price is None or self._dyn_tp_side is None:
            return None
        if not self._exit_atr.initialized:
            return None
        return compute_dyn_tp_target(
            entry_price=self._dyn_tp_entry_price,
            atr_value=self._exit_atr.value,
            side=self._dyn_tp_side,
            bars_in_pos=self._bars_in_position,
            max_bars_in_trade=self._max_bars_in_trade,
            dyn_tp_lambda=getattr(self.config, "dyn_tp_lambda", 1.0),
            dyn_tp_gamma=getattr(self.config, "dyn_tp_gamma", 1.5),
        )

    def _update_dyn_tp_order(self) -> None:
        """Je-Bar-Update des dynamischen Take-Profit-Ziels. Cancel/Replace NUR bei
        |ΔTarget| > 1 Tick (Order-Sturm-Schutz, #712-Akzeptanzkriterium)."""
        if self._dyn_tp_entry_price is None or self._dyn_tp_side is None:
            return
        if self._dyn_tp_pending_cancel is not None:
            return  # Ein Replace ist bereits im Gange — wartet auf on_order_canceled.
        target = self._compute_dyn_tp_target()
        if target is None:
            return
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            return
        if self._dyn_tp_price is not None:
            tick = float(instrument.price_increment)
            if tick <= 0.0 or abs(target - self._dyn_tp_price) <= _DYN_TP_MIN_TICK_DELTA * tick:
                return
        self._submit_dyn_tp_order(target, instrument)

    def _submit_dyn_tp_order(self, target: float, instrument) -> None:
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        if not positions:
            return
        pos = positions[0]
        if self._dyn_tp_order_id is not None:
            existing = self.cache.order(self._dyn_tp_order_id)
            if existing is not None and existing.is_open:
                self._dyn_tp_pending_cancel = self._dyn_tp_order_id
                self._dyn_tp_pending_target = target
                self.cancel_order(existing)
                return
        price = instrument.make_price(target)
        exit_side = OrderSide.SELL if self._dyn_tp_side == "LONG" else OrderSide.BUY
        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=exit_side,
            quantity=pos.quantity,
            price=price,
            time_in_force=TimeInForce.GTC,
        )
        self._dyn_tp_order_id = order.client_order_id
        self._dyn_tp_price = target
        self.submit_order(order)
        self._log.info(f"[{self.instrument_id}] Dyn-TP Limit-Order @ {float(price):.4f} (bars_in_pos={self._bars_in_position})")

    def on_order_canceled(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] OrderCanceled: {event}")
        if getattr(self, "current_signal", None) is not None and not self.cache.positions_open(instrument_id=self.instrument_id):
             self.current_signal = None
        if event.client_order_id in self._pending_cancels:
            self._pending_cancels.remove(event.client_order_id)
            if event.client_order_id == self._dyn_tp_order_id:
                self._dyn_tp_order_id = None
            if not self._pending_cancels:
                self._execute_market_close()
            return
        if self._dyn_tp_pending_cancel is not None and event.client_order_id == self._dyn_tp_pending_cancel:
            self._dyn_tp_order_id = None
            self._dyn_tp_pending_cancel = None
            target = self._dyn_tp_pending_target
            self._dyn_tp_pending_target = None
            instrument = self.cache.instrument(self.instrument_id)
            if target is not None and instrument is not None and self.cache.positions_open(instrument_id=self.instrument_id):
                self._submit_dyn_tp_order(target, instrument)

    def on_order_filled(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] OrderFilled: {event}")
        if event.client_order_id in self._pending_cancels:
            self._pending_cancels.remove(event.client_order_id)

            # Protect against partial fills: Only clear if the position is fully closed
            positions = self.cache.positions_open(instrument_id=self.instrument_id)
            if not positions or positions[0].quantity == 0:
                self._pending_cancels.clear()
            elif not self._pending_cancels:
                # If there are no more pending cancels but the position is still partially open, close it!
                self._execute_market_close()

    def _handle_exit_close_order_failure(self, event, event_name: str) -> None:
        """Issue #859 — gemeinsame Behandlung eines abgelehnten (``on_order_rejected``) oder vom
        RiskEngine verweigerten (``on_order_denied``) Markt-Close-Versuchs. Root-Cause: die
        Markt-Close-Order steht weder in ``_pending_cancels`` (Cancel-Bestätigungen) noch ist sie
        die Dyn-TP-Order — sie fiel vor diesem Fix durch ALLE bestehenden Zweige, und
        ``on_order_denied`` war in der gesamten Klasse gar nicht implementiert. Ohne diesen Zweig
        blieb der #836-Watchdog dauerhaft entwaffnet (das Absenden allein setzte das alte
        Boolean-Flag, Pitfall #273) — die Position hielt bis zum Datenende, der Trial produzierte
        für den Rest des Fensters keinerlei Signal mehr.

        Räumt die Order-ID (der Watchdog löst im nächsten Bar erneut aus) und zählt den Versuch;
        nach ``exit_close_max_retries`` vergeblichen Versuchen gilt der Trial als terminal
        unrecoverable (``EXIT_CLOSE_UNRECOVERABLE``) — ``backtest_runner`` markiert ihn dann über
        ``inference_diagnostics`` als ``oos_evaluated=False``, statt eine offene Position still
        durch das gesamte Fenster zu tragen."""
        self._exit_market_close_order_id = None
        self._exit_pending_bars = 0
        self._exit_close_retries += 1
        payload = {
            "instrument": str(self.instrument_id),
            "exit_reason": self._exit_pending,
            "attempt": self._exit_close_retries,
            "max_retries": self._exit_close_max_retries,
        }
        emit_execution_event(log, event_name, payload, level=logging.ERROR)
        self._log.error(
            f"[{self.instrument_id}] {event_name} (Versuch {self._exit_close_retries}/"
            f"{self._exit_close_max_retries}) — Watchdog versucht den Markt-Close im nächsten Bar "
            f"erneut."
        )
        if self._exit_close_retries >= self._exit_close_max_retries:
            self._exit_close_unrecoverable = True
            emit_execution_event(log, "EXIT_CLOSE_UNRECOVERABLE", payload, level=logging.ERROR)
            self._log.error(
                f"[{self.instrument_id}] EXIT_CLOSE_UNRECOVERABLE nach "
                f"{self._exit_close_retries} vergeblichen Markt-Close-Versuchen — Trial wird als "
                f"ungültig markiert."
            )

    def on_order_denied(self, event) -> None:
        """Issue #859 — vorher in der gesamten Klasse nicht implementiert: ein vom RiskEngine
        verweigerter Close (bei AccountType.MARGIN und stark negativen Equity-Verläufen real,
        siehe #825) erzeugte gar keinen Callback und wurde von keinem bestehenden Zweig
        aufgefangen."""
        self._log.warning(f"[{self.instrument_id}] OrderDenied: {event}")
        if event.client_order_id == self._exit_market_close_order_id:
            self._handle_exit_close_order_failure(event, "EXIT_CLOSE_DENIED")

    def on_order_rejected(self, event) -> None:
        self._log.warning(f"[{self.instrument_id}] OrderRejected: {event}")
        if getattr(self, "current_signal", None) is not None and not self.cache.positions_open(instrument_id=self.instrument_id):
             self.current_signal = None
        # Issue #859 — die Markt-Close-Order steht in KEINER der beiden unten geprüften Mengen
        # (_pending_cancels, _dyn_tp_order_id) und fiel vorher durch alle Zweige durch.
        if event.client_order_id == self._exit_market_close_order_id:
            self._handle_exit_close_order_failure(event, "EXIT_CLOSE_REJECTED")
            return
        if event.client_order_id in self._pending_cancels:
            self._pending_cancels.remove(event.client_order_id)
            if event.client_order_id == self._dyn_tp_order_id:
                self._dyn_tp_order_id = None
            if not self._pending_cancels:
                self._execute_market_close()
            return
        # Issue #712 — eine abgelehnte Dyn-TP-Order (initial oder Replace) darf den Zustand nicht
        # blockieren: zurücksetzen, das nächste _update_dyn_tp_order-Bar versucht es erneut.
        if event.client_order_id == self._dyn_tp_order_id:
            self._dyn_tp_order_id = None
            self._dyn_tp_price = None
        if self._dyn_tp_pending_cancel is not None and event.client_order_id == self._dyn_tp_pending_cancel:
            self._dyn_tp_order_id = None
            self._dyn_tp_pending_cancel = None
            self._dyn_tp_pending_target = None

    def _resolve_spread_gate_bps(self) -> float:
        """Issue #715 (GR-02) — effektive Spread-Gate-Schwelle (bps). Ein explizit gesetztes
        config.spread_gate_bps übersteuert die Ableitung (manueller Override); sonst
        k_spread · spread_bps_model (Single Source of Truth mit dem Backtest-Kostenmodell,
        #566) — die bereits eingepreiste Normal-Spread wird so nicht doppelt bestraft, nur
        Ausweitungen werden abgefangen."""
        explicit = getattr(self.config, "spread_gate_bps", None)
        if explicit is not None:
            return float(explicit)
        k_spread = float(getattr(self.config, "k_spread", 2.0))
        return k_spread * resolve_spread_bps_model(str(self.instrument_id))

    def _compute_quantity(self, bar: Bar) -> Quantity | None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self._log.error(f"[{self.instrument_id}] Instrument nicht im Cache")
            return None

        price = float(bar.close)
        if price <= 0:
            return None

        # Issue #715 (GR-02) — Pre-Trade-Spread-Gate: vor JEDER Order den effektiven Spread aus
        # dem aktuellen QuoteTick prüfen; Signal bei Überschreitung verwerfen + strukturiert loggen.
        # Kein QuoteTick verfügbar (z. B. Cold-Start) ⇒ Gate inaktiv (fail-open, kein erfundener Wert).
        quote = self.cache.quote_tick(self.instrument_id)
        if quote is not None:
            bid = float(quote.bid_price)
            ask = float(quote.ask_price)
            mid = (bid + ask) / 2.0
            if mid > 0.0:
                spread_bps = (ask - bid) / mid * 10000.0
                threshold_bps = self._resolve_spread_gate_bps()
                if threshold_bps > 0.0 and spread_bps > threshold_bps:
                    self._log.warning(
                        f"[{self.instrument_id}] SPREAD_GATE_REJECT: spread={spread_bps:.2f}bps > "
                        f"threshold={threshold_bps:.2f}bps"
                    )
                    return None

        # Issue #716 (GR-03) — node-weiter Aggregat-Exposure-Cap, ZUSÄTZLICH zum (unverändert
        # bestehenden) per-Strategie max_open_positions-Check in den Subklassen.
        max_agg = getattr(self.config, "max_aggregate_open_positions", None)
        if max_agg is not None:
            n_open = len(self.cache.positions_open())
            if n_open >= max_agg:
                self._log.warning(
                    f"[{self.instrument_id}] AGGREGATE_EXPOSURE_CAP_REJECT: {n_open} offene "
                    f"Positionen (systemweit) >= max_aggregate_open_positions={max_agg}"
                )
                return None

        # Issue #838 — max_trades_cap sperrt AUSSCHLIESSLICH Entries. Ein Early-Return in
        # _check_exits_and_update hätte auch Trailing-Stop/Zeit-Exit/ATR-Update mitgesperrt und
        # jede zu diesem Zeitpunkt offene Position bis Datenreihenende gehalten (#836/#837-Klasse).
        if not self._entry_allowed():
            self._log.warning(
                f"[{self.instrument_id}] MAX_TRADES_CAP_REJECT: {self._executed_trades} Trades "
                f"erreicht (Limit: {self.config.max_trades_cap})."
            )
            return None

        trade_amount_usd_cfg = getattr(self.config, "trade_amount_usd", None)
        trade_amount_pct = getattr(self.config, "trade_amount_pct", None)

        max_daily = getattr(self.config, "max_daily_trades", None)
        if max_daily is not None and self._daily_trades >= max_daily:
            self._log.warning(
                f"[{self.instrument_id}] Sanity Guard blockiert Entry: {self._daily_trades} Trades an diesem Tag erreicht (Limit: {max_daily})."
            )
            return None

        if self.allocator is not None:
            # A: Live-Allocator hat höchste Prio
            balance = self._get_current_balance()
            trade_amount_usd = self.allocator.get_allocation(self.instrument_id, self.cache, balance)
        elif trade_amount_usd_cfg is not None and trade_amount_usd_cfg > 0 and trade_amount_usd_cfg != 100.0:
            # B: Explizit gesetzter USD-Betrag (z.B. vom Runner injiziert, aber nicht der Default)
            trade_amount_usd = trade_amount_usd_cfg
        elif trade_amount_pct is not None and trade_amount_pct > 0:
            # C: Prozentuales Sizing
            balance = self._get_current_balance()
            trade_amount_usd = balance * (trade_amount_pct / 100.0)
        elif trade_amount_usd_cfg is not None and trade_amount_usd_cfg > 0:
            # D: Explizit gesetzter Default USD-Betrag
            trade_amount_usd = trade_amount_usd_cfg
        else:
            # E: Hard Fallback
            trade_amount_usd = 100.0

        # Issue #716 (GR-03) — harter Notional-Deckel: qty = min(equity_fraktion_qty,
        # floor(max_order_notional / price)), hier äquivalent auf dem USD-Betrag VOR der
        # Unit-Umrechnung gedeckelt (units = trade_amount_usd / price).
        max_notional = getattr(self.config, "max_order_notional", None)
        if max_notional is not None and max_notional > 0.0 and trade_amount_usd > max_notional:
            self._log.debug(
                f"[{self.instrument_id}] Notional-Deckel greift: {trade_amount_usd:.2f} USD > "
                f"max_order_notional={max_notional:.2f} USD — auf Deckel gekappt."
            )
            trade_amount_usd = max_notional

        MIN_TRADE_USD = 11.0
        if trade_amount_usd < MIN_TRADE_USD:
            self._log.debug(f"[{self.instrument_id}] Trade amount {trade_amount_usd:.2f} USD < MIN_TRADE_USD ({MIN_TRADE_USD:.2f}). Skipping.")
            return None

        if trade_amount_pct is not None and trade_amount_pct > 0 and self.allocator is None and (trade_amount_usd_cfg is None or trade_amount_usd_cfg <= 0 or trade_amount_usd_cfg == 100.0):
             self._log.info(f"[{self.instrument_id}] Calculated sizing: {trade_amount_usd:.2f} USD ({trade_amount_pct}%) from equity {balance:.2f} USD")

        units = trade_amount_usd / price

        try:
            inc = float(instrument.size_increment)
            prec = instrument.size_precision
            # Strictly align with instrument's tick precision and size_increment
            quantized_units = round(math.floor(units / inc) * inc, prec)

            # Never artificially inflate the quantity if it falls below the minimum increment threshold
            if quantized_units <= 0 or quantized_units < inc:
                self._log.debug(f"[{self.instrument_id}] Quantized units ({quantized_units}) < size increment ({inc}). Skipping trade to prevent risk leveraging.")
                return None

            qty = instrument.make_qty(quantized_units)
            if qty is None or float(qty) == 0:
                 return None
            return qty
        except ValueError as e:
            self._log.warning(f"[{self.instrument_id}] make_qty Fehler: {e}")
            return None

    def on_position_opened(self, event) -> None:
        """Reset exit state when a new position opens. _check_exits_and_update initializes
        trailing stop on the first bar after entry."""
        self._in_position = False  # triggers init block in _check_exits_and_update
        self._trailing_initialised = False  # Issue #837 — entkoppelte Neuverankerung, einmalig pro Position
        self._bars_in_position = 0
        self._pending_cancels.clear()
        self._trailing_stop_price = None
        self._trailing_stop_side = None
        self._take_profit_price = None
        # Issue #897 Fix 1 — Kurs-Extremum-Anker wird AUSSCHLIESSLICH hier auf den Entry-Preis
        # initialisiert (analog _trailing_initialised, #837).
        self._position_extreme = float(event.avg_px_open)
        # Issue #899 — ATR-Telemetrie-Puffer beginnt leer fuer jede neue Position.
        self._position_atr_bps_readings = []
        # Issue #836 — eine neue Position beginnt garantiert ohne einen laufenden Exit-Versuch.
        self._exit_pending = None
        self._exit_pending_kind = None
        self._exit_pending_bars = 0
        self._exit_market_close_order_id = None
        self._exit_close_retries = 0
        self._daily_trades += 1
        self._executed_trades += 1
        self._log.info(f"[{self.instrument_id}] PositionOpened: {event} (Trade {self._daily_trades} today, {self._executed_trades} total)")

        # Submit native limit order for profit target
        if self._profit_target_pct is not None:
            instrument = self.cache.instrument(self.instrument_id)
            if instrument:
                entry_price = float(event.avg_px_open)
                if event.side == PositionSide.LONG:
                    target = entry_price * (1.0 + self._profit_target_pct / 100.0)
                    exit_side = OrderSide.SELL
                else:
                    target = entry_price * (1.0 - self._profit_target_pct / 100.0)
                    exit_side = OrderSide.BUY

                # We format price based on instrument precision
                price = instrument.make_price(target)
                qty = event.quantity

                order = self.order_factory.limit(
                    instrument_id=self.instrument_id,
                    order_side=exit_side,
                    quantity=qty,
                    price=price,
                    time_in_force=TimeInForce.GTC,
                )
                self.submit_order(order)
                self._log.info(f"[{self.instrument_id}] Submitted Take Profit Limit Order at {float(price):.4f}")

        # Issue #712 — dynamischer Take-Profit (opt-in, orthogonal zum statischen
        # profit_target_pct-Pfad oben, der unverändert erhalten bleibt). Initiale Order bei
        # bars_in_pos=0 (voller γ·ATR-Abstand, exp(0)=1); je-Bar-Updates via
        # _update_dyn_tp_order in _check_exits_and_update.
        self._reset_dyn_tp_state()
        if getattr(self.config, "dyn_tp_enabled", False):
            self._dyn_tp_entry_price = float(event.avg_px_open)
            self._dyn_tp_side = "LONG" if event.side == PositionSide.LONG else "SHORT"
            target = self._compute_dyn_tp_target()
            instrument = self.cache.instrument(self.instrument_id)
            if target is not None and instrument is not None:
                self._submit_dyn_tp_order(target, instrument)

    def on_position_closed(self, event) -> None:
        # Issue #837 — dies ist der EINZIGE Ort ausserhalb von on_position_opened, der _in_position
        # auf False setzt: eine bestätigte Transaktion. Ebenso der einzige Ort, der _exit_pending
        # (#836) konsumiert.
        self._in_position = False
        self._trailing_stop_price = None
        self._trailing_stop_side = None
        self._take_profit_price = None
        self._bars_in_position = 0
        self._pending_cancels.clear()
        self._exit_pending = None
        self._exit_pending_kind = None
        self._exit_pending_bars = 0
        self._exit_market_close_order_id = None
        self._exit_close_retries = 0
        self._reset_dyn_tp_state()
        # Issue #859 Fix Punkt 5 — verbliebene ruhende Orders dieses Instruments räumen: der
        # statische ``profit_target_pct``-Limit-Pfad (``on_position_opened``) und ein per
        # ``on_order_canceled`` nach Exit-Auslösung neu eingestellter Dyn-TP-Auftrag überlebten die
        # Positionsschliessung vorher als verwaiste Order (kein Zweig räumte sie explizit auf).
        self.cancel_all_orders(self.instrument_id)
        self._log.info(f"[{self.instrument_id}] PositionClosed: {event}")
