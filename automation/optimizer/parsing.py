import json
import statistics
from dataclasses import dataclass
from pathlib import Path

@dataclass
class TournamentMetrics:
    oos_evaluated: bool
    oos_eligible: bool
    is_sortino_median: float
    oos_sortino: float | None
    oos_max_drawdown: float
    oos_total_trades: int
    win_count: int
    fully_eligible_pairs: int
    is_total_trades: int
    is_max_trades: int

def parse_tournament(path: Path) -> TournamentMetrics:
    """Liest aggregate_winner/oos_metrics typsicher (None-safe).
       oos_sortino = Median von aggregate_winner.oos_fold_sortinos, falls vorhanden,
       sonst oos_metrics.sortino_ratio. is_sortino_median = median_is_sortino bzw. median_sortino."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    fully_eligible_pairs = data.get("fully_eligible_pairs") or 0
    agg = data.get("aggregate_winner") or {}

    oos_evaluated = agg.get("oos_evaluated") or False
    oos_eligible = agg.get("oos_eligible") or False
    win_count = agg.get("win_count") or 0

    # is_sortino_median fallback logic
    is_sortino_median = agg.get("median_is_sortino")
    if is_sortino_median is None:
        is_sortino_median = agg.get("median_sortino", 0.0)

    oos_fold_sortinos = agg.get("oos_fold_sortinos") or []
    oos_metrics = agg.get("oos_metrics") or {}

    if oos_fold_sortinos and isinstance(oos_fold_sortinos, list) and len(oos_fold_sortinos) > 0:
        oos_sortino = statistics.median(oos_fold_sortinos)
    else:
        oos_sortino = oos_metrics.get("sortino_ratio")

    oos_max_drawdown = oos_metrics.get("max_drawdown") or 0.0
    oos_total_trades = oos_metrics.get("total_trades") or 0

    is_total_trades = 0
    is_max_trades = 0
    full_results = data.get("full_results") or []
    if full_results and isinstance(full_results, list):
        trades_list = [r.get("metrics", {}).get("total_trades", 0) for r in full_results if isinstance(r, dict)]
        is_total_trades = sum(trades_list)
        is_max_trades = max(trades_list) if trades_list else 0

    return TournamentMetrics(
        oos_evaluated=bool(oos_evaluated),
        oos_eligible=bool(oos_eligible),
        is_sortino_median=float(is_sortino_median) if is_sortino_median is not None else 0.0,
        oos_sortino=float(oos_sortino) if oos_sortino is not None else None,
        oos_max_drawdown=float(oos_max_drawdown) if oos_max_drawdown is not None else 0.0,
        oos_total_trades=int(oos_total_trades) if oos_total_trades is not None else 0,
        win_count=int(win_count) if win_count is not None else 0,
        fully_eligible_pairs=int(fully_eligible_pairs) if fully_eligible_pairs is not None else 0,
        is_total_trades=int(is_total_trades),
        is_max_trades=int(is_max_trades)
    )
