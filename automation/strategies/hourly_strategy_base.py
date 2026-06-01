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

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.indicators import AverageTrueRange
from automation.momentum_ls_allocator import MomentumLSAllocator

log = logging.getLogger(__name__)


class HourlyStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1
    atr_period: int = 14
    atr_trailing_multiplier: float = 1.5
    max_bars_in_trade: int = 48


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
        self._trailing_stop_side: str | None = None
        self._bars_in_position: int = 0
        self._in_position: bool = False
        # Safely extract overrides, guarding against null or empty string injections from JSON
        raw_atr = getattr(config, "atr_trailing_multiplier", None)
        try:
            self._atr_trailing_multiplier: float = float(raw_atr) if raw_atr is not None else DEFAULT_ATR_TRAILING_MULTIPLIER
        except (TypeError, ValueError):
            self._atr_trailing_multiplier = DEFAULT_ATR_TRAILING_MULTIPLIER

        raw_bars = getattr(config, "max_bars_in_trade", None)
        try:
            self._max_bars_in_trade: int = int(raw_bars) if raw_bars is not None else DEFAULT_MAX_BARS_IN_TRADE
        except (TypeError, ValueError):
            self._max_bars_in_trade = DEFAULT_MAX_BARS_IN_TRADE

    def on_start(self):
        """Subclasses MUST call super().on_start() first."""
        if self.allocator is not None:
            accounts = self.cache.accounts()
            if accounts:
                self._account_id = accounts[0].id
            else:
                self._log.warning("No accounts found in cache on start. Allocation might fail.")

    def _get_current_balance(self) -> float:
        if not self._account_id:
            accounts = self.cache.accounts()
            if accounts:
                self._account_id = accounts[0].id

        if not self._account_id:
            return 0.0

        account = self.cache.account(self._account_id)
        instrument = self.cache.instrument(self.instrument_id)

        if account and account.balances and instrument:
            balance_list = list(account.balances) if not isinstance(account.balances, list) else account.balances
            # Check if elements are dict or objects
            if isinstance(account.balances, dict):
                balance_obj = account.balances.get(instrument.quote_currency)
                if balance_obj:
                    return float(getattr(balance_obj, 'total', balance_obj.free if hasattr(balance_obj, 'free') else 0.0))
            else:
                balance_obj = next((b for b in balance_list if getattr(b, 'currency', None) == instrument.quote_currency), None)
                if balance_obj:
                    return float(getattr(balance_obj, 'total', balance_obj.free if hasattr(balance_obj, 'free') else 0.0))

        self._log.warning("Could not resolve free balance from cache. Returning 0.0.")
        return 0.0

    def _check_exits_and_update(self, bar: Bar) -> bool:
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
        self._exit_atr.handle_bar(bar)

        positions = self.cache.positions_open(instrument_id=self.instrument_id)

        if not positions:
            if self._in_position:
                self._in_position = False
                self._trailing_stop_price = None
                self._trailing_stop_side = None
                self._bars_in_position = 0
            return False

        pos = positions[0]
        close = float(bar.close)

        # Initialise on first bar after entry (on_position_opened sets _in_position=False
        # so this block runs exactly once per position)
        if not self._in_position:
            self._in_position = True
            self._bars_in_position = 0
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

        if exit_reason:
            self._log.info(f"[{self.instrument_id}] EXIT: {exit_reason}")
            self._close_position_base(pos)
            self._in_position = False
            self._trailing_stop_price = None
            self._trailing_stop_side = None
            self._bars_in_position = 0
            return True

        return False

    def _close_position_base(self, pos) -> None:
        """Submits a market order to close the given position."""
        exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=exit_side,
            quantity=pos.quantity,
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    def _compute_quantity(self, bar: Bar) -> Quantity | None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self._log.error(f"[{self.instrument_id}] Instrument nicht im Cache")
            return None

        price = float(bar.close)
        if price <= 0:
            return None

        if self.allocator is not None:
            balance = self._get_current_balance()
            trade_amount_usd = self.allocator.get_allocation(self.instrument_id, self.cache, balance)
        else:
            trade_amount_usd = getattr(self.config, "trade_amount_usd", 100.0)

        if trade_amount_usd <= 0:
            return None

        units = trade_amount_usd / price

        try:
            qty = instrument.make_qty(units, round_down=True)
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
        self._trailing_stop_price = None
        self._trailing_stop_side = None
        self._log.info(f"[{self.instrument_id}] PositionOpened: {event}")

    def on_position_closed(self, event) -> None:
        self._in_position = False
        self._trailing_stop_price = None
        self._trailing_stop_side = None
        self._bars_in_position = 0
        self._log.info(f"[{self.instrument_id}] PositionClosed: {event}")
