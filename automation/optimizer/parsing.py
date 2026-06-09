import json
import statistics
from dataclasses import dataclass
from pathlib import Path

@dataclass
class TournamentMetrics:
    oos_evaluated: bool
    oos_eligible: bool
    is_sortino_median: float
    oos_sortino: float
    oos_max_drawdown: float
    oos_total_trades: int
    win_count: int
    fully_eligible_pairs: int

def parse_tournament(path: Path) -> TournamentMetrics:
    """
    Liest das tournament_result.json typsicher (None-safe).
    - oos_sortino = Median der Liste 'aggregate_winner.oos_fold_sortinos', falls diese Liste
      vorhanden und nicht leer ist. SONST Fallback auf 'aggregate_winner.oos_metrics.sortino_ratio'.
    - is_sortino_median = 'median_is_sortino' bzw. 'median_sortino' unter 'aggregate_winner'.
    Füllt fehlende numerische Werte sicher mit 0.0, boolsche mit False.
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    fully_eligible_pairs = data.get("fully_eligible_pairs", 0)

    aggregate_winner = data.get("aggregate_winner")
    if not aggregate_winner:
        return TournamentMetrics(
            oos_evaluated=False,
            oos_eligible=False,
            is_sortino_median=0.0,
            oos_sortino=0.0,
            oos_max_drawdown=0.0,
            oos_total_trades=0,
            win_count=0,
            fully_eligible_pairs=fully_eligible_pairs
        )

    oos_evaluated = aggregate_winner.get("oos_evaluated", False)
    oos_eligible = aggregate_winner.get("oos_eligible", False)
    win_count = data.get("win_count", aggregate_winner.get("win_count", 0))

    is_sortino_median = aggregate_winner.get("median_is_sortino")
    if is_sortino_median is None:
        is_sortino_median = aggregate_winner.get("median_sortino", 0.0)

    # OOS Sortino calculation
    oos_sortino = None
    oos_fold_sortinos = aggregate_winner.get("oos_fold_sortinos", [])
    if isinstance(oos_fold_sortinos, list) and len(oos_fold_sortinos) > 0:
        valid_folds = [s for s in oos_fold_sortinos if s is not None]
        if valid_folds:
            oos_sortino = statistics.median(valid_folds)

    oos_metrics = aggregate_winner.get("oos_metrics") or {}
    if oos_sortino is None:
        oos_sortino = oos_metrics.get("sortino_ratio")

    oos_max_drawdown = oos_metrics.get("max_drawdown", 0.0)
    oos_total_trades = oos_metrics.get("total_trades", 0)

    # fallback to 0.0 for any remaining Nones on numeric fields
    if is_sortino_median is None: is_sortino_median = 0.0
    if oos_sortino is None: oos_sortino = 0.0
    if oos_max_drawdown is None: oos_max_drawdown = 0.0
    if oos_total_trades is None: oos_total_trades = 0

    return TournamentMetrics(
        oos_evaluated=bool(oos_evaluated),
        oos_eligible=bool(oos_eligible),
        is_sortino_median=float(is_sortino_median),
        oos_sortino=float(oos_sortino),
        oos_max_drawdown=float(oos_max_drawdown),
        oos_total_trades=int(oos_total_trades),
        win_count=int(win_count),
        fully_eligible_pairs=int(fully_eligible_pairs)
    )
