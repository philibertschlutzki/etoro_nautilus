"""Issue #549 — Gate-Sortino und Reward-Sortino müssen DENSELBEN kanonischen Wert nutzen.

Vor dem Fix nutzte das Gate den gepoolten Sortino (ein _calculate_stats über die konkatenierte
OOS-Serie), der Reward aber den Median der Per-Fold-Sortinos. An der Gate-Grenze konnte ein Trial
so vom Gate anders bewertet werden als vom Reward (inkonsistenter Gradient). Der Fix setzt den
Fold-Median an der Quelle (``apply_fold_aggregation``), sodass beide identisch propagieren.
"""
import json
import statistics

from automation.backtest_runner import apply_fold_aggregation, _evaluate_oos_eligibility
from automation.optimizer.parsing import parse_tournament


def _fold(total_return, sortino, win_rate=0.5, expectancy=0.01, profit_factor=1.5, total_trades=10):
    return {
        "total_return": total_return, "sortino_ratio": sortino, "win_rate": win_rate,
        "expectancy": expectancy, "profit_factor": profit_factor, "total_trades": total_trades,
        "max_drawdown": 0.05,
    }


def test_source_sets_sortino_to_fold_median():
    """apply_fold_aggregation überschreibt sortino_ratio mit dem Fold-Median; pooled bleibt erhalten."""
    per_fold = [_fold(0.02, 2.0), _fold(-0.01, -1.0), _fold(0.005, 0.5), _fold(-0.02, -0.5)]
    # Gepoolter Ausgangswert (z. B. aus der konkatenierten OOS-Serie) — soll ÜBERSCHRIEBEN werden.
    oos_metrics = {"sortino_ratio": -0.41656, "win_rate": 0.3, "expectancy": 0.0,
                   "profit_factor": 0.9, "total_return": 0.001, "total_trades": 40,
                   "max_drawdown": 0.1}

    apply_fold_aggregation(oos_metrics, per_fold)

    expected_median = statistics.median([2.0, -1.0, 0.5, -0.5])  # = 0.0
    assert oos_metrics["sortino_ratio"] == expected_median
    assert oos_metrics["sortino_ratio_pooled"] == -0.41656  # forensisch erhalten
    assert oos_metrics["oos_fold_sortinos"] == [2.0, -1.0, 0.5, -0.5]


def test_gate_and_reward_read_identical_sortino(tmp_path):
    """Der im Reject-Grund geloggte oos_min_sortino ist identisch mit dem Reward-Sortino (m.oos_sortino)."""
    per_fold = [_fold(0.02, 2.0), _fold(-0.01, -1.0), _fold(0.005, 0.5), _fold(-0.02, -0.5)]
    oos_metrics = {"sortino_ratio": -0.41656, "win_rate": 0.3, "expectancy": 0.02,
                   "profit_factor": 1.5, "total_return": 0.001, "total_trades": 40,
                   "max_drawdown": 0.1, "median_position_notional": 1000.0}
    apply_fold_aggregation(oos_metrics, per_fold)
    canonical = oos_metrics["sortino_ratio"]

    # Reward-Seite: parse_tournament liest median(oos_fold_sortinos) == canonical.
    result = {
        "universe_size": 1,
        "single_symbol_oos": {
            "oos_evaluated": True, "oos_eligible": False,
            "oos_metrics": oos_metrics, "oos_fold_sortinos": oos_metrics["oos_fold_sortinos"],
            "median_is_sortino": 0.5,
        },
        "full_results": [],
    }
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(result), encoding="utf-8")
    metrics = parse_tournament(p)
    assert metrics.oos_sortino == canonical

    # Gate-Seite: die Sortino-Bedingung bewertet exakt canonical.
    cfg = {"oos_min_sortino": 5.0, "oos_min_trades": 1, "eligible_requires_any": ["min_sortino"]}
    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    joined = " ".join(ev["oos_rejection_reasons"])
    assert f"{canonical:.5f}" in joined, f"Gate-Reason nutzt nicht den kanonischen Sortino: {joined}"


def test_single_fold_is_pooled_identity():
    """splits==1: Fold-Median == pooled ⇒ keine Verschiebung (Holdout-Pfad bit-identisch)."""
    per_fold = [_fold(0.03, 1.234)]
    oos_metrics = {"sortino_ratio": 1.234, "win_rate": 0.6, "expectancy": 0.02,
                   "profit_factor": 2.0, "total_return": 0.03, "total_trades": 12}
    apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["sortino_ratio"] == 1.234
