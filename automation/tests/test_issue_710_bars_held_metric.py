"""Issue #710 — Haltedauer-Metrik (Bars) in TournamentMetrics verdrahten.

`_calculate_stats` aggregiert je OOS-Trade die Haltedauer (Bars zwischen Entry-Fill und
Exit-Fill, 1h-Bars) als `median_bars_held`/`p95_bars_held`; `parsing.parse_tournament` reicht
sie als `oos_median_bars_held`/`oos_p95_bars_held` durch. Reine Telemetrie — kein Reward-/
Gate-Effekt in dieser Stufe (siehe #711 für den Reward-Term).
"""
import json

from automation.backtest_runner import _calculate_stats
from automation.optimizer.parsing import TournamentMetrics, parse_tournament

_NS_PER_BAR = 3600 * 1_000_000_000


def test_median_and_p95_bars_held_synthetic_fill_history():
    """Fünf synthetische Round-Trips mit Haltedauern 1, 2, 3, 4, 24 Bars — Median und
    p95 müssen exakt reproduziert werden."""
    pnl_list = [10.0, -5.0, 8.0, 3.0, -2.0]
    hold_list = [
        (1 * _NS_PER_BAR, 1.0),
        (2 * _NS_PER_BAR, 1.0),
        (3 * _NS_PER_BAR, 1.0),
        (4 * _NS_PER_BAR, 1.0),
        (24 * _NS_PER_BAR, 1.0),
    ]
    stats = _calculate_stats(pnl_list, hold_list, starting_capital=10000.0)
    assert stats["median_bars_held"] == 3.0
    assert stats["p95_bars_held"] == 24.0


def test_bars_held_empty_trade_history_is_zero():
    stats = _calculate_stats([], [], starting_capital=10000.0)
    assert stats["median_bars_held"] == 0.0
    assert stats["p95_bars_held"] == 0.0


def test_tournament_metrics_default_none_migrationssicher():
    """Legacy-Fixtures ohne das Feld müssen unverändert grün laufen (Optional[float]=None)."""
    m = TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=0.5,
        oos_sortino=1.0, oos_max_drawdown=0.01, oos_total_trades=10,
        win_count=1, fully_eligible_pairs=1, is_total_trades=10,
    )
    assert m.oos_median_bars_held is None
    assert m.oos_p95_bars_held is None


def test_parse_tournament_reads_bars_held(tmp_path):
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True,
            "oos_eligible": True,
            "win_count": 1,
            "median_is_sortino": 0.4,
            "oos_metrics": {
                "sortino_ratio": 1.1,
                "total_trades": 5,
                "median_bars_held": 6.5,
                "p95_bars_held": 22.0,
            },
        },
        "full_results": [],
    }
    path = tmp_path / "tournament_result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = parse_tournament(path)
    assert metrics.oos_median_bars_held == 6.5
    assert metrics.oos_p95_bars_held == 22.0


def test_parse_tournament_missing_bars_held_is_none(tmp_path):
    """Legacy-JSON ohne das Feld (Pre-#710) lädt fehlerfrei mit None."""
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True,
            "oos_eligible": True,
            "win_count": 1,
            "median_is_sortino": 0.4,
            "oos_metrics": {"sortino_ratio": 1.1, "total_trades": 5},
        },
        "full_results": [],
    }
    path = tmp_path / "tournament_result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = parse_tournament(path)
    assert metrics.oos_median_bars_held is None
    assert metrics.oos_p95_bars_held is None
