import sys; sys.path.insert(0, ".")
import pandas as pd
from automation.backtest_runner import run_single_backtest_worker

start = int(pd.Timestamp("2026-04-29T00:00:00Z").value)
end   = int(pd.Timestamp("2026-05-29T00:00:00Z").value)
strat = {
    "strategy_class":  "SmaCrossoverStrategy",
    "strategy_module": "automation.strategies.sma_crossover",
    "config_class":    "SmaCrossoverConfig",
    "params": {"sma_period": 5, "trade_amount_usd": 1500.0},
}
logf = "/tmp/diag_googl.log"
res = run_single_backtest_worker(
    "GOOGL.ETORO", "GOOGL.ETORO-1-HOUR-MID-INTERNAL", strat,
    "data/nautilus", start, end, 10000.0, False, "/tmp", logf,
)
print("RESULT:", res)
print("="*60)
print(open(logf).read())