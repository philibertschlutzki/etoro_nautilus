"""Issue #620 — die #589-Kohärenz-Invariante feuert im Subprozess und war unbeobachtbar.

``_assert_sortino_return_coherence`` läuft im Backtest-Subprozess; ihr ``logging.error`` landete in
einem anderen Handler und das Flag ``oos_coherence_violation`` wurde weder von ``parse_tournament`` in
``TournamentMetrics`` übernommen noch ins ``JSON_EVENT`` gehoben. Fix: durchgereicht + Study-Zähler.
"""
import json

from automation.backtest_runner import _assert_sortino_return_coherence
from automation.optimizer.parsing import parse_tournament


def test_assert_coherence_sets_flag_on_incoherent():
    oos = {"total_return": 0.05, "sortino_ratio": -1.0}   # return > 0, sortino < 0 ⇒ inkohärent
    _assert_sortino_return_coherence(oos)
    assert oos["oos_coherence_violation"] is True


def test_assert_coherence_no_flag_on_coherent():
    oos = {"total_return": 0.05, "sortino_ratio": 1.2}
    _assert_sortino_return_coherence(oos)
    assert "oos_coherence_violation" not in oos


def test_parse_propagates_coherence_violation(tmp_path):
    data = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "oos_metrics": {"sortino_ratio": -1.0, "total_return": 0.05, "max_drawdown": 0.02,
                        "total_trades": 30, "oos_coherence_violation": True}}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), "utf-8")
    m = parse_tournament(p)
    assert m.oos_coherence_violation is True


def test_parse_defaults_false_without_flag(tmp_path):
    data = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "oos_metrics": {"sortino_ratio": 1.0, "total_return": 0.05, "max_drawdown": 0.02,
                        "total_trades": 30}}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), "utf-8")
    assert parse_tournament(p).oos_coherence_violation is False
