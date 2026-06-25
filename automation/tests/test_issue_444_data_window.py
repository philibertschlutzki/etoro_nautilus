"""Issue #444 (P2) — data_window-Block wird ins tournament_result.json geschrieben.

Die Parser-Seite (`parse_tournament`) las den `data_window`-Block bereits (#416), die Schreib-Seite
(`write_tournament_json`) fehlte — daher waren `data_window_*` in der Per-Trial-Telemetrie konstant
`null`. Dieser Round-Trip-Test pinnt: Writer → Parser liefert ein nicht-`None` `data_window_days`
(plus `fill_ts_min/max`), das der geladenen Spanne entspricht.
"""
import json
from pathlib import Path

import pandas as pd

from automation.backtest_runner import write_tournament_json
from automation.optimizer.parsing import parse_tournament

_DAY_NS = 86400 * 1_000_000_000
START_NS = int(pd.Timestamp("2025-05-15", tz="UTC").value)
END_NS = int(pd.Timestamp("2026-05-10", tz="UTC").value)

_TOURN_CFG = {"min_trades": 20, "max_drawdown": 0.3, "oos_min_trades": 3}


def _result(symbol, fill_min, fill_max):
    return {
        "symbol": symbol,
        "strategy": "SmaCrossoverStrategy",
        "metrics": {"total_trades": 30, "sortino_ratio": 1.0, "total_return": 0.1,
                    "win_rate": 0.5, "max_drawdown": 0.1, "profit_factor": 1.2,
                    "calmar_ratio": 1.0, "losses_count": 10},
        "oos_metrics": {"total_trades": 8, "sortino_ratio": 1.0, "total_return": 0.05,
                        "max_drawdown": 0.1, "win_rate": 0.5},
        "strat_params": {},
        "start_capital": 10000.0,
        "_first_tick_ns": fill_min,
        "_last_tick_ns": fill_max,
        "_fill_ts_min": fill_min,
        "_fill_ts_max": fill_max,
    }


def test_data_window_roundtrip(tmp_path):
    out = tmp_path / "tournament_result.json"
    # Zwei Symbole ⇒ Multi-Symbol-Pfad (kein single_symbol_oos-Block); Fill-Spannen unterschiedlich.
    all_results = [
        _result("AAA.ETORO", START_NS + 10 * _DAY_NS, START_NS + 300 * _DAY_NS),
        _result("BBB.ETORO", START_NS + 5 * _DAY_NS, START_NS + 350 * _DAY_NS),
    ]
    write_tournament_json(
        all_results, str(out), per_symbol_winners={}, aggregate_winner=None,
        warnings_list=[], is_eligible_count=0, fully_eligible_count=0,
        tournament_cfg=_TOURN_CFG, start_ns=START_NS, end_ns=END_NS,
    )

    raw = json.loads(out.read_text("utf-8"))
    assert "data_window" in raw, "Writer schreibt keinen data_window-Block (#444)"
    dw = raw["data_window"]
    expected_days = round((END_NS - START_NS) / _DAY_NS, 1)
    assert dw["days"] == expected_days
    # Aggregation über die Worker: min der Mins, max der Maxs.
    assert dw["fill_ts_min"] == START_NS + 5 * _DAY_NS
    assert dw["fill_ts_max"] == START_NS + 350 * _DAY_NS

    # Parser-Seite liefert nicht-None (vorher konstant null).
    m = parse_tournament(Path(out))
    assert m.data_window_days == expected_days
    assert m.data_window_start is not None and m.data_window_end is not None
    assert m.fill_ts_min == START_NS + 5 * _DAY_NS
    assert m.fill_ts_max == START_NS + 350 * _DAY_NS


def test_no_data_window_when_inputs_absent(tmp_path):
    """Rückwärtskompatibel: ohne start_ns/end_ns UND ohne Fill-Spannen wird KEIN Block geschrieben."""
    out = tmp_path / "t.json"
    bare = {"symbol": "X.ETORO", "strategy": "S", "metrics": {"total_trades": 0},
            "oos_metrics": {}, "strat_params": {}, "start_capital": 10000.0}
    write_tournament_json([bare], str(out), per_symbol_winners={}, aggregate_winner=None,
                          warnings_list=[], is_eligible_count=0, fully_eligible_count=0,
                          tournament_cfg=_TOURN_CFG)
    raw = json.loads(out.read_text("utf-8"))
    assert "data_window" not in raw
    assert parse_tournament(Path(out)).data_window_days is None
