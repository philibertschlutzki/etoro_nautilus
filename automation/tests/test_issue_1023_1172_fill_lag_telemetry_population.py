"""Issue #1023/#1172 (P0) — Fill-Latenz-Grundgesamtheit wird nie als Trial-Attribut gestempelt.

Symptom: ``stop_exit_slippage_bps`` war in 14/14 Studies befuellt, aber
``n_trailing_stop_exits_with_fill_lag_telemetry`` war in 14/14 exakt 0 — zwei Messwerte ohne
Stichprobengroesse. Root-Cause: ``run_optimization.py`` stempelte die beiden Nachbarfelder
(``oos_stop_exit_fill_lag_bars_median``/``oos_stop_exit_slippage_bps_median``) als Trial-User-Attr,
aber NICHT den dazugehoerigen Zaehler ``oos_n_trailing_stop_exits_with_fill_lag_telemetry`` — obwohl
er, wie seine Nachbarn, aus ``trial_attrs`` (Sweep-Phase) summiert wird, nicht aus dem
Holdout-Re-Evaluation-Pfad (die vormalige Allowlist-Begruendung "holdout-only" war falsch).
"""
import ast
from pathlib import Path

from automation.optimizer import parsing
from automation.optimizer import run_optimization as ro
from automation.optimizer.report import _study_record

_RUN_OPTIMIZATION_PATH = Path(ro.__file__)


class _T:
    def __init__(self, user_attrs):
        self.value = 1.0
        self.params = {}
        import optuna
        self.state = optuna.trial.TrialState.COMPLETE
        self.user_attrs = user_attrs


class _S:
    def __init__(self, trials):
        self.trials = trials
        self.best_value = 1.0
        self.user_attrs = {}


def test_run_optimization_stamps_the_fill_lag_telemetry_counter():
    tree = ast.parse(_RUN_OPTIMIZATION_PATH.read_text("utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "set_user_attr"
                and isinstance(func.value, ast.Name) and func.value.id == "trial"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    assert "oos_n_trailing_stop_exits_with_fill_lag_telemetry" in keys


def test_field_is_no_longer_shadowed_by_the_stale_holdout_only_allowlist_entry():
    assert "oos_n_trailing_stop_exits_with_fill_lag_telemetry" not in (
        ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS)
    assert "oos_n_trailing_stop_exits_with_fill_lag_telemetry" in {
        f for f in parsing.TournamentMetrics.__dataclass_fields__ if f.startswith("oos_")
    }


def test_stop_exit_fill_lag_bars_is_none_not_zero_without_any_telemetry():
    # Kein Trial traegt den Zaehler > 0 -> stop_exit_fill_lag_bars faellt auf None, nicht 0.0
    # (Tri-State-Mechanik wie #995/#1147) -- selbst wenn irrtuemlich ein Median-Feld vorhanden waere.
    trials = [_T({
        "oos_evaluated": True, "oos_eligible": True,
        "oos_stop_exit_fill_lag_bars_median": 0.0,
        "oos_n_trailing_stop_exits_with_fill_lag_telemetry": 0,
    }) for _ in range(5)]
    proposal = {"symbol": "TSLA.ETORO", "strategy": "DynamicBreakoutStrategy"}
    record, _checks = _study_record(proposal, _S(trials))
    assert record["n_trailing_stop_exits_with_fill_lag_telemetry"] == 0
    assert record["stop_exit_fill_lag_bars"] is None


def test_stop_exit_fill_lag_bars_populates_when_telemetry_present():
    trials = [_T({
        "oos_evaluated": True, "oos_eligible": True,
        "oos_stop_exit_fill_lag_bars_median": float(i),
        "oos_n_trailing_stop_exits_with_fill_lag_telemetry": 3,
    }) for i in range(1, 6)]
    proposal = {"symbol": "TSLA.ETORO", "strategy": "DynamicBreakoutStrategy"}
    record, _checks = _study_record(proposal, _S(trials))
    assert record["n_trailing_stop_exits_with_fill_lag_telemetry"] == 15
    assert record["stop_exit_fill_lag_bars"] == 3.0
