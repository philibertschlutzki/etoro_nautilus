import sys
from pathlib import Path
import json

from nautilus_trader.config import StrategyConfig
from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig, DEFAULT_ATR_TRAILING_MULTIPLIER, DEFAULT_MAX_BARS_IN_TRADE

print("Checks passed")
