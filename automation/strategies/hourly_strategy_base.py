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

import logging
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

log = logging.getLogger(__name__)


class HourlyStrategyConfig(StrategyConfig, kw_only=True, frozen=True):
    instrument_id: str
    bar_type: str
    trade_amount_usd: float = 100.0
    trade_amount_pct: float | None = None
    max_open_positions: int = 1
    atr_period: int = 14
    atr_trailing_multiplier: float = 1.5
    max_bars_in_trade: int = 48
    profit_target_pct: float | None = None
    cooldown_bars: int = 12
    trend_filter_period: int = 0
    max_daily_trades: int | None = 5
    min_holding_time: int = 0
    min_signal_strength: float = 0.0
    max_trades_cap: int | None = None


DEFAULT_ATR_TRAILING_MULTIPLIER = 1.5
DEFAULT_MAX_BARS_IN_TRADE = 48  # 48 hours with 1h candles


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
        self._pending_cancels: set = set()
        self._max_bars_in_trade = getattr(config, "max_bars_in_trade", None)
        if self._max_bars_in_trade is None:
            self._max_bars_in_trade = DEFAULT_MAX_BARS_IN_TRADE

        self._atr_trailing_multiplier = getattr(config, "atr_trailing_multiplier", None)
        if self._atr_trailing_multiplier is None:
            self._atr_trailing_multiplier = DEFAULT_ATR_TRAILING_MULTIPLIER

        self._profit_target_pct = getattr(config, "profit_target_pct", None)
        self._daily_trades: int = 0
        self._current_day: int | None = None
        self._executed_trades: int = 0

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

    def _check_exits_and_update(self, bar: Bar) -> bool:
        if getattr(self.config, "max_trades_cap", None) and self._executed_trades >= self.config.max_trades_cap:
            return True

        """
        Called at the START of every on_bar() in subclasses.
        Updates ATR, trailing stop level, and bar counter.
        Returns True if an exit order was submitted (caller should return immediately).
        Returns False if no exit triggered (caller continues with signal logic).

        NOTE regarding Slippage vs. Native Orders:
        The exit logic deliberately evaluates against the closed bar (`bar.close`) rather than
        submitting native intra-bar `trailing_stop_market` orders to the exchange.
        This prevents the strategy from being stopped out by short, extreme intra-bar noise ("whipsaws").
        Crucially, this ensures absolute consistency between historical backtests (which use 1h bars)
        and the Walk-Forward/Out-of-Sample live execution, maintaining the statistical integrity of the
        gating thresholds.
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
            self._pending_cancels.clear()
            return False

        pos = positions[0]



        close = float(bar.close)

        # Initialise on first bar after entry (on_position_opened sets _in_position=False
        # so this block runs exactly once per position)
        if not self._in_position:
            self._in_position = True
            self._bars_in_position = 0
            self._pending_cancels.clear()
            self._trailing_stop_side = "LONG" if pos.side == PositionSide.LONG else "SHORT"
            if self._exit_atr.initialized:
                atr_val = self._exit_atr.value
                if self._trailing_stop_side == "LONG":
                    self._trailing_stop_price = close - self._atr_trailing_multiplier * atr_val
                else:
                    self._trailing_stop_price = close + self._atr_trailing_multiplier * atr_val
            else:
                self._trailing_stop_price = None


        self._bars_in_position += 1

        # Update trailing stop (only moves in favourable direction)
        if self._exit_atr.initialized and self._trailing_stop_price is not None:
            atr_val = self._exit_atr.value
            if self._trailing_stop_side == "LONG":
                new_stop = close - self._atr_trailing_multiplier * atr_val
                self._trailing_stop_price = max(self._trailing_stop_price, new_stop)
            else:
                new_stop = close + self._atr_trailing_multiplier * atr_val
                self._trailing_stop_price = min(self._trailing_stop_price, new_stop)

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


        # Exit condition 2: Time-based exit (48 bars)
        if exit_reason is None and self._bars_in_position >= self._max_bars_in_trade:
            exit_reason = f"Time-exit after {self._bars_in_position} bars"

        # Apply min_holding_time guard to exits that are not trailing stops (e.g. time exits, signals, etc)
        if exit_reason is not None and getattr(self.config, "min_holding_time", 0) > 0:
            if "Trailing Stop" not in exit_reason and self._bars_in_position < self.config.min_holding_time:
                exit_reason = None # Ignore the exit if holding time is not reached

        if exit_reason:
            self._log.info(f"[{self.instrument_id}] EXIT: {exit_reason}")
            self._close_position_base(pos)
            self._in_position = False
            self._trailing_stop_price = None
            self._trailing_stop_side = None
            self._take_profit_price = None
            self._bars_in_position = 0
            self._pending_cancels.clear()
            return True

        return False

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
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=exit_side,
            quantity=pos.quantity,
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    def on_order_canceled(self, event) -> None:
        self._log.info(f"[{self.instrument_id}] OrderCanceled: {event}")
        if getattr(self, "current_signal", None) is not None and not self.cache.positions_open(instrument_id=self.instrument_id):
             self.current_signal = None
        if event.client_order_id in self._pending_cancels:
            self._pending_cancels.remove(event.client_order_id)
            if not self._pending_cancels:
                self._execute_market_close()

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

    def on_order_rejected(self, event) -> None:
        self._log.warning(f"[{self.instrument_id}] OrderRejected: {event}")
        if getattr(self, "current_signal", None) is not None and not self.cache.positions_open(instrument_id=self.instrument_id):
             self.current_signal = None
        if event.client_order_id in self._pending_cancels:
            self._pending_cancels.remove(event.client_order_id)
            if not self._pending_cancels:
                self._execute_market_close()

    def _compute_quantity(self, bar: Bar) -> Quantity | None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self._log.error(f"[{self.instrument_id}] Instrument nicht im Cache")
            return None

        price = float(bar.close)
        if price <= 0:
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
        self._bars_in_position = 0
        self._pending_cancels.clear()
        self._trailing_stop_price = None
        self._trailing_stop_side = None
        self._take_profit_price = None
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

    def on_position_closed(self, event) -> None:
        self._in_position = False
        self._trailing_stop_price = None
        self._trailing_stop_side = None
        self._take_profit_price = None
        self._bars_in_position = 0
        self._pending_cancels.clear()
        self._log.info(f"[{self.instrument_id}] PositionClosed: {event}")
