import logging
from unittest.mock import MagicMock

import pandas as pd
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide, PositionSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

logger = logging.getLogger(__name__)

class TickSimulator:
    """
    Replays QuoteTick Parquet data and drives a strategy's signal logic.
    Records all (entry, exit) pairs with timestamps.
    Does NOT send real orders.
    Capital allocation for simulation: fixed $1000 virtual per position.
    """

    def __init__(self, parquet_path: str, instrument_id: str):
        self.parquet_path = parquet_path
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.bar_type_str = f"{instrument_id}-1-MINUTE-MID-INTERNAL"

        self.trades = []
        self._current_position = None  # {side, entry_price, entry_ts, quantity}
        self._current_tick_ts = None

        self._df = None

    def load_data(self) -> bool:
        try:
            self._df = pd.read_parquet(self.parquet_path)
            # Make sure it's sorted by time
            self._df.sort_values(by="ts_event", inplace=True)
            return len(self._df) > 0
        except Exception as e:
            logger.warning(f"Simulator failed to load data from {self.parquet_path}: {e}")
            return False

    def _mock_cache_positions_open(self, instrument_id=None):
        if self._current_position is None:
            return []

        mock_pos = MagicMock()
        mock_pos.side = self._current_position["side"]
        mock_pos.quantity = self._current_position["quantity"]
        return [mock_pos]

    def _mock_instrument_make_qty(self, units: float):
        # We enforce size_precision = 0 for all instruments
        return Quantity(int(units), precision=0)

    def _mock_submit_order(self, order):
        # execute immediately at current price
        # Get current mid price if possible, or fallback
        if not hasattr(self, '_last_close'):
            return

        exec_price = self._last_close
        exec_ts = self._current_tick_ts

        if order.side == OrderSide.BUY:
            if self._current_position is None:
                # Open Long
                self._current_position = {
                    "side": PositionSide.LONG,
                    "entry_price": exec_price,
                    "entry_ts": exec_ts,
                    "quantity": order.quantity
                }
            elif self._current_position["side"] == PositionSide.SHORT:
                # Close Short
                entry = self._current_position
                self.trades.append({
                    "entry_price": entry["entry_price"],
                    "exit_price": exec_price,
                    "side": "SELL", # it was a short
                    "quantity": entry["quantity"],
                    "entry_ts": entry["entry_ts"],
                    "exit_ts": exec_ts
                })
                self._current_position = None

        elif order.side == OrderSide.SELL:
            if self._current_position is None:
                # Open Short (if strategy allows, Nautilus pattern usually does)
                self._current_position = {
                    "side": PositionSide.SHORT,
                    "entry_price": exec_price,
                    "entry_ts": exec_ts,
                    "quantity": order.quantity
                }
            elif self._current_position["side"] == PositionSide.LONG:
                # Close Long
                entry = self._current_position
                self.trades.append({
                    "entry_price": entry["entry_price"],
                    "exit_price": exec_price,
                    "side": "BUY", # it was a long
                    "quantity": entry["quantity"],
                    "entry_ts": entry["entry_ts"],
                    "exit_ts": exec_ts
                })
                self._current_position = None

    def simulate(self, strategy_class) -> list[dict]:
        if self._df is None or len(self._df) == 0:
            return []

        config_class = None
        import sys
        module = sys.modules[strategy_class.__module__]
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and obj.__name__.endswith("Config"):
                config_class = obj
                break

        if not config_class:
            logger.warning(f"Could not find config class for {strategy_class.__name__}")
            return []

        config_kwargs = {}
        config_kwargs["strategy_id"] = f"{strategy_class.__name__}-SIM"

        try:
            import inspect
            sig = inspect.signature(config_class.__init__)
            params = sig.parameters

            for name, param in params.items():
                if name == "self" or name == "args" or name == "kwargs" or param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue
                if name == "strategy_id":
                    config_kwargs[name] = f"{strategy_class.__name__}-SIM"
                elif name == "instrument_id":
                    config_kwargs[name] = str(self.instrument_id)
                elif name == "bar_type":
                    config_kwargs[name] = self.bar_type_str
                elif name == "trade_amount_usd":
                    config_kwargs[name] = 1000.0
                elif name == "max_open_positions":
                    config_kwargs[name] = 1
                elif param.default != inspect.Parameter.empty:
                    # Let default fallback
                    pass
                else:
                    # For anything else required, mock a reasonable value
                    if param.annotation == int:
                        config_kwargs[name] = 10
                    elif param.annotation == float:
                        config_kwargs[name] = 1.0
                    else:
                        config_kwargs[name] = "default"

            config = config_class(**config_kwargs)
        except Exception as e:
            logger.warning(f"Error configuring {strategy_class.__name__}: {e}")
            return []

        # Force attributes on config even if slots or strict
        try:
            object.__setattr__(config, "instrument_id", str(self.instrument_id))
            object.__setattr__(config, "bar_type", self.bar_type_str)
        except Exception:
            pass

        # Strategy instantiation
        # Because Nautilus strategies are actors that check register status, we need to create
        # a dummy node and add it to simulate properly, rather than complex property patching.
        try:
            from nautilus_trader.live.node import TradingNode
            from nautilus_trader.config import TradingNodeConfig

            # create node
            node = TradingNode(config=TradingNodeConfig())
            strategy = strategy_class(config=config)
            node.trader.add_strategy(strategy)

            # Now override methods we need
            strategy.submit_order = self._mock_submit_order

            # Override cache
            import types
            from unittest.mock import patch

            # Use Nautilus's native TestingNode or similar if we could, but patching via class is easier:
            # We already attached the strategy to a node, so it has access to the real cache and log.
            # We can mock its methods that hit the cache without replacing the property.

            def mock_positions_open(*args, **kwargs):
                return self._mock_cache_positions_open(*args, **kwargs)

            def mock_make_qty(units):
                return self._mock_instrument_make_qty(units)

            def mock_instrument_getter(*args, **kwargs):
                mock_inst = MagicMock()
                mock_inst.make_qty.side_effect = mock_make_qty
                return mock_inst

            # patch the cache methods directly
            with patch.object(strategy.cache, "positions_open", side_effect=mock_positions_open):
                with patch.object(strategy.cache, "instrument", side_effect=mock_instrument_getter):
                    try:
                        strategy.on_start()
                    except Exception as e:
                        pass

                    self._df['dt'] = pd.to_datetime(self._df['ts_event'])
                    for name, group in self._df.groupby(pd.Grouper(key='dt', freq='1min')):
                        if len(group) == 0:
                            continue

                        self._current_tick_ts = group['ts_event'].iloc[-1]

                        open_px = float(group['bid_price'].iloc[0])
                        close_px = float(group['bid_price'].iloc[-1])
                        high_px = float(group['ask_price'].max())
                        low_px = float(group['bid_price'].min())
                        vol = float(len(group))

                        self._last_close = close_px

                        bar = MagicMock(spec=Bar)
                        bar.open = Price(open_px, 5)
                        bar.high = Price(high_px, 5)
                        bar.low = Price(low_px, 5)
                        bar.close = Price(close_px, 5)
                        bar.volume = Quantity(vol, 0)

                        try:
                            strategy.on_bar(bar)
                        except Exception as e:
                            logger.warning(f"Strategy {strategy_class.__name__} crashed during on_bar: {e}")
                            break

            # Dispose of node properly
            node.dispose()
        except Exception as e:
            logger.warning(f"Error running node for {strategy_class.__name__}: {e}")
            return []

        # Close any open positions at the end of simulation
        if self._current_position:
            # We don't actually generate a close order from strategy, just forcefully close it
            # to count the current PnL in the tournament
            entry = self._current_position
            side = "BUY" if entry["side"] == PositionSide.LONG else "SELL"
            self.trades.append({
                "entry_price": entry["entry_price"],
                "exit_price": self._last_close,
                "side": side,
                "quantity": entry["quantity"],
                "entry_ts": entry["entry_ts"],
                "exit_ts": self._current_tick_ts
            })
            self._current_position = None

        return self.trades
