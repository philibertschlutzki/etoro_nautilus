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

def parse_tournament(path: Path) -> TournamentMetrics:
    """Liest aggregate_winner/oos_metrics typsicher (None-safe).
       oos_sortino = Median von aggregate_winner.oos_fold_sortinos, falls vorhanden,
       sonst oos_metrics.sortino_ratio. is_sortino_median = median_is_sortino bzw. median_sortino."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    fully_eligible_pairs = data.get("fully_eligible_pairs", 0)
    agg = data.get("aggregate_winner", {})

    oos_evaluated = agg.get("oos_evaluated", False)
    oos_eligible = agg.get("oos_eligible", False)
    win_count = agg.get("win_count", 0)

    # is_sortino_median fallback logic
    is_sortino_median = agg.get("median_is_sortino")
    if is_sortino_median is None:
        is_sortino_median = agg.get("median_sortino", 0.0)

    oos_fold_sortinos = agg.get("oos_fold_sortinos")
    oos_metrics = agg.get("oos_metrics", {})

    if oos_fold_sortinos and isinstance(oos_fold_sortinos, list) and len(oos_fold_sortinos) > 0:
        oos_sortino = statistics.median(oos_fold_sortinos)
    else:
        oos_sortino = oos_metrics.get("sortino_ratio")

    oos_max_drawdown = oos_metrics.get("max_drawdown", 0.0)
    oos_total_trades = oos_metrics.get("total_trades", 0)

    return TournamentMetrics(
        oos_evaluated=oos_evaluated,
        oos_eligible=oos_eligible,
        is_sortino_median=float(is_sortino_median) if is_sortino_median is not None else 0.0,
        oos_sortino=float(oos_sortino) if oos_sortino is not None else None,
        oos_max_drawdown=float(oos_max_drawdown) if oos_max_drawdown is not None else 0.0,
        oos_total_trades=int(oos_total_trades) if oos_total_trades is not None else 0,
        win_count=int(win_count) if win_count is not None else 0,
        fully_eligible_pairs=int(fully_eligible_pairs) if fully_eligible_pairs is not None else 0
    )
