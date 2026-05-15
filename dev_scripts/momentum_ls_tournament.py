import argparse
import importlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from dev_scripts.momentum_ls_simulator import TickSimulator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sortino_ratio": 0.0,
            "calmar_ratio": 0.0,
            "max_drawdown": 0.0,
            "total_return": 0.0,
        }

    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    returns = []

    cumulative_return = 1.0
    peak = 1.0
    max_drawdown = 0.0

    min_ts = trades[0]["entry_ts"]
    max_ts = trades[-1]["exit_ts"]

    for t in trades:
        ep = float(t["entry_price"])
        xp = float(t["exit_price"])

        # BUY indicates we opened LONG
        if t["side"] == "BUY":
            ret = (xp / ep) - 1.0
        else:
            ret = 1.0 - (xp / ep)

        returns.append(ret)

        cumulative_return *= (1.0 + ret)
        if cumulative_return > peak:
            peak = cumulative_return

        dd = (peak - cumulative_return) / peak
        if dd > max_drawdown:
            max_drawdown = dd

        if ret > 0:
            wins += 1
            gross_profit += ret
        else:
            gross_loss += abs(ret)

    total_return = cumulative_return - 1.0
    win_rate = wins / len(trades)

    if gross_loss == 0.0:
        profit_factor = 999.0 if gross_profit > 0 else 0.0
    else:
        profit_factor = gross_profit / gross_loss

    # Sortino
    # Downside deviation
    downside_squared = [min(r, 0)**2 for r in returns]
    import math
    mean_downside = sum(downside_squared) / len(downside_squared)
    dd_dev = math.sqrt(mean_downside)

    if dd_dev == 0:
        sortino = 0.0
    else:
        mean_ret = sum(returns) / len(returns)
        sortino_daily = mean_ret / dd_dev
        sortino = sortino_daily * math.sqrt(252 * 390)

    # Calmar
    holding_days = (max_ts - min_ts) / 1e9 / 86400
    if holding_days <= 0:
        holding_days = 1.0

    annualized_return = total_return * (365.0 / holding_days)
    if max_drawdown == 0:
        calmar = 0.0
    else:
        calmar = annualized_return / max_drawdown

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "sortino_ratio": round(sortino, 4),
        "calmar_ratio": round(calmar, 4),
        "max_drawdown": round(max_drawdown, 4),
        "total_return": round(total_return, 4),
    }


def _discover_strategies() -> list[type]:
    strategies_dir = Path(__file__).resolve().parent.parent / "strategies"

    strategy_classes = []

    for file in os.listdir(strategies_dir):
        if file.endswith(".py") and file != "__init__.py":
            module_name = f"strategies.{file[:-3]}"
            try:
                module = importlib.import_module(module_name)
                for name in dir(module):
                    obj = getattr(module, name)
                    if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy" and not name.startswith("MomentumLS"):
                        strategy_classes.append(obj)
            except Exception as e:
                logger.warning(f"Failed to load {module_name}: {e}")

    return strategy_classes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="data/universe/momentum_ls.json")
    parser.add_argument("--data-dir", default="data/nautilus/data/quote_tick")
    parser.add_argument("--months", type=int, default=6)

    date_str = datetime.now().strftime("%Y-%m-%d")
    parser.add_argument("--output", default=f"logs/tournament_{date_str}.json")
    args = parser.parse_args()

    try:
        with open(args.universe, "r") as f:
            universe_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load universe {args.universe}: {e}")
        sys.exit(1)

    strategy_classes = _discover_strategies()
    if not strategy_classes:
        logger.error("No strategies found.")
        sys.exit(1)

    logger.info(f"Loaded {len(strategy_classes)} strategies.")

    results = []

    for symbol_entry in universe_data.get("universe", []):
        symbol = symbol_entry.get("symbol")
        if not symbol:
            continue

        parquet_path = Path(args.data_dir) / symbol / "data.parquet"
        if not parquet_path.exists():
            logger.warning(f"No data for {symbol} at {parquet_path}, skipping.")
            continue

        simulator = TickSimulator(str(parquet_path), symbol)
        if not simulator.load_data():
            continue

        for strategy_class in strategy_classes:
            try:
                trades = simulator.simulate(strategy_class)
                metrics = compute_metrics(trades)

                results.append({
                    "symbol": symbol,
                    "strategy": strategy_class.__name__,
                    "metrics": metrics,
                })
            except Exception as e:
                logger.warning(f"Simulation failed for {symbol} with {strategy_class.__name__}: {e}")

    # Ranking
    total_pairs = len(results)
    eligible_results = [r for r in results if r["metrics"]["profit_factor"] > 1.5]

    per_symbol_winners = {}

    for r in eligible_results:
        symbol = r["symbol"]
        if symbol not in per_symbol_winners:
            per_symbol_winners[symbol] = []
        per_symbol_winners[symbol].append(r)

    final_winners = {}
    strategy_win_counts = {}
    strategy_sortinos = {}

    print("\nSymbol         | Strategy              | Sortino | Calmar | PF    | Win?")
    print("-" * 76)

    for r in results:
        sym = r["symbol"]
        strat = r["strategy"]
        m = r["metrics"]

        is_winner = False
        if sym in per_symbol_winners:
            # Sort eligible by Sortino (desc), then Calmar (desc)
            sorted_candidates = sorted(
                per_symbol_winners[sym],
                key=lambda x: (x["metrics"]["sortino_ratio"], x["metrics"]["calmar_ratio"]),
                reverse=True
            )
            winner = sorted_candidates[0]
            if winner["strategy"] == strat and m["profit_factor"] > 1.5:
                is_winner = True
                final_winners[sym] = {
                    "strategy": strat,
                    "metrics": m
                }
                strategy_win_counts[strat] = strategy_win_counts.get(strat, 0) + 1

        if m["profit_factor"] > 1.5:
            if strat not in strategy_sortinos:
                strategy_sortinos[strat] = []
            strategy_sortinos[strat].append(m["sortino_ratio"])

        win_mark = "✓" if is_winner else ""
        print(f"{sym:<14} | {strat:<21} | {m['sortino_ratio']:>7.2f} | {m['calmar_ratio']:>6.2f} | {m['profit_factor']:>5.2f} | {win_mark}")

    aggregate_winner_data = None
    if strategy_win_counts:
        # Find aggregate winner
        max_wins = max(strategy_win_counts.values())
        top_strats = [s for s, w in strategy_win_counts.items() if w == max_wins]

        if len(top_strats) == 1:
            agg_strat = top_strats[0]
        else:
            # tie break
            agg_strat = max(top_strats, key=lambda s: sum(strategy_sortinos[s])/len(strategy_sortinos[s]))

        mean_sortino = sum(strategy_sortinos[agg_strat])/len(strategy_sortinos[agg_strat])

        aggregate_winner_data = {
            "strategy": agg_strat,
            "win_count": strategy_win_counts[agg_strat],
            "mean_sortino": round(mean_sortino, 4)
        }

    output_json = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_snapshot": universe_data.get("fetched_at", ""),
        "total_symbol_strategy_pairs": total_pairs,
        "eligible_pairs": len(eligible_results),
        "per_symbol_winners": final_winners,
        "aggregate_winner": aggregate_winner_data,
        "full_results": results
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output_json, f, indent=2)

    logger.info(f"Tournament finished. Wrote results to {out_path}")

if __name__ == "__main__":
    main()
