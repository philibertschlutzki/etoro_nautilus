"""Issue #804 — die vier (inzwischen: sieben) fail-loud-Diagnosen des Inferenzpfads
(`_calculate_stats`, `assert_return_series_identity`, `_assert_sortino_return_coherence`) laufen im
Backtest-SUBPROZESS und erreichen den Optimizer-Elternprozess-Log nie: der Grep über ein
vollstaendiges 5490-Zeilen-Lauf-Log lieferte 0 Treffer fuer RETURN_SERIES_IDENTITY_VIOLATION/
NON_CONTIGUOUS_FOLD_SEGMENTS/COHERENCE_INVARIANT_VIOLATION/SORTINO_GUARD_TRIPPED, obwohl der
Elternprozess 35 STUDY_ABORTED_ON_INVARIANT sah — die Diagnosen landen in
`trial_dir/logs/backtest_stdout.log`, einer Datei, die kein Aggregator liest und die #794 Sekunden
spaeter loescht.

Fix: `_calculate_stats` sammelt jede Verletzung ZUSAETZLICH in `metrics['inference_diagnostics']`
(strukturierter Rueckkanal, additiv, kein Reward-/Gate-Einfluss) — dasselbe Dict, das
`tournament_result.json` ohnehin persistiert. `parsing.parse_tournament` hebt sie nach
`TournamentMetrics.inference_diagnostics`. `run_optimization._reemit_inference_diagnostics`
re-emittiert jeden Eintrag im ELTERNPROZESS als `INFERENCE_DIAGNOSTIC`-ERROR-Ereignis mit voller
Study-Identitaet (bind_study_context, #780) + Trial-Nummer. `report._study_record` aggregiert je
Study `inference_diagnostics_by_code`, und `invariants.check_inference_diagnostics_absent` ist der
sechste Regressionswaechter im #742-Report.
"""
import json
import logging

import pandas as pd
import pytest

from automation.backtest_runner import (
    _calculate_stats, assert_return_series_identity, _assert_sortino_return_coherence,
)
from automation.optimizer import invariants as inv
from automation.optimizer import run_optimization as ro
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.report import _study_record


# ---------------------------------------------------------------------------
# backtest_runner._calculate_stats populates inference_diagnostics
# ---------------------------------------------------------------------------

def test_equity_ruin_produces_an_equity_nonpositive_diagnostic():
    vals = [1000.0, 500.0, -100.0, 300.0]
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="h")
    series = pd.Series(vals, index=idx)
    stats = _calculate_stats(pnl_list=[1.0] * 3, hold_list=[(3600 * 10**9, 1.0)] * 3,
                             starting_capital=1000.0, mtm_series=series)
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "EQUITY_NONPOSITIVE" in codes


def test_healthy_trial_has_no_diagnostics():
    vals = [1000.0, 1050.0, 980.0, 1100.0]
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="h")
    series = pd.Series(vals, index=idx)
    stats = _calculate_stats(pnl_list=[1.0] * 3, hold_list=[(3600 * 10**9, 1.0)] * 3,
                             starting_capital=1000.0, mtm_series=series)
    assert stats["inference_diagnostics"] == []


def test_non_contiguous_fold_segments_produces_a_diagnostic():
    fold0 = pd.Series([], dtype=float)  # leeres Segment -> uebersprungen
    fold1 = pd.Series([100.0, 105.0], index=pd.date_range("2024-01-01", periods=2, freq="h"))
    stats = _calculate_stats(pnl_list=[1.0], hold_list=[], starting_capital=100.0,
                             mtm_frames=[fold0, fold1])
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "NON_CONTIGUOUS_FOLD_SEGMENTS" in codes


def test_sortino_guard_tripped_produces_a_diagnostic(monkeypatch):
    import automation.backtest_runner as br
    monkeypatch.setattr(br, "_read_sortino_numeric_guard", lambda: 1e-9)  # trivial ueberschritten
    vals = [1000.0]
    rng_vals = [1000.0 * (1.05 ** i) for i in range(1, 30)]
    vals = vals + rng_vals
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="h")
    series = pd.Series(vals, index=idx)
    stats = _calculate_stats(pnl_list=[1.0] * 20, hold_list=[(3600 * 10**9, 1.0)] * 20,
                             starting_capital=1000.0, mtm_series=series, min_trades_for_sortino=2)
    codes = [d["code"] for d in stats["inference_diagnostics"]]
    assert "SORTINO_GUARD_TRIPPED" in codes


# ---------------------------------------------------------------------------
# assert_return_series_identity: diagnostics= param
# ---------------------------------------------------------------------------

def test_assert_return_series_identity_appends_undefined_diagnostic():
    diagnostics = []
    period_rets = pd.Series([0.1, -0.2])
    result = assert_return_series_identity(-1.5, period_rets, diagnostics=diagnostics)
    assert result is True
    assert len(diagnostics) == 1
    assert diagnostics[0]["code"] == "RETURN_SERIES_IDENTITY_UNDEFINED"
    assert diagnostics[0]["value"] == pytest.approx(-1.5)


def test_assert_return_series_identity_appends_violation_diagnostic():
    diagnostics = []
    period_rets = pd.Series([0.5, 0.5])  # log_sum stimmt garantiert nicht mit total_return=0 ueberein
    result = assert_return_series_identity(0.0, period_rets, diagnostics=diagnostics)
    assert result is True
    assert diagnostics[0]["code"] == "RETURN_SERIES_IDENTITY_VIOLATION"


def test_assert_return_series_identity_no_diagnostics_param_is_backward_compatible():
    """Bestandsaufrufer ohne diagnostics= bleiben bit-identisch (kein TypeError, kein Nebeneffekt)."""
    period_rets = pd.Series([0.1, -0.2])
    result = assert_return_series_identity(-1.5, period_rets)
    assert result is True


def test_assert_return_series_identity_no_diagnostics_on_success():
    import numpy as np
    diagnostics = []
    prices = np.array([100.0, 110.0, 90.0, 130.0])
    period_rets = pd.Series(np.diff(np.log(prices)))
    total_return = 130.0 / 100.0 - 1.0
    result = assert_return_series_identity(total_return, period_rets, diagnostics=diagnostics)
    assert result is False
    assert diagnostics == []


# ---------------------------------------------------------------------------
# _assert_sortino_return_coherence: appends into oos_metrics['inference_diagnostics']
# ---------------------------------------------------------------------------

def test_coherence_violation_appends_to_existing_inference_diagnostics_list():
    oos_metrics = {"total_return": 0.05, "sortino_ratio": -1.0, "inference_diagnostics": []}
    _assert_sortino_return_coherence(oos_metrics)
    assert oos_metrics["oos_coherence_violation"] is True
    codes = [d["code"] for d in oos_metrics["inference_diagnostics"]]
    assert "COHERENCE_INVARIANT_VIOLATION" in codes


def test_coherence_violation_creates_the_list_if_absent():
    oos_metrics = {"total_return": 0.05, "sortino_ratio": -1.0}
    _assert_sortino_return_coherence(oos_metrics)
    assert "inference_diagnostics" in oos_metrics
    assert oos_metrics["inference_diagnostics"][0]["code"] == "COHERENCE_INVARIANT_VIOLATION"


# ---------------------------------------------------------------------------
# parsing.parse_tournament lifts inference_diagnostics into TournamentMetrics
# ---------------------------------------------------------------------------

def test_parse_tournament_lifts_inference_diagnostics(tmp_path):
    payload = {
        "fully_eligible_pairs": 0,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": False, "win_count": 0,
            "oos_metrics": {
                "sortino_ratio": None, "total_return": -0.97, "max_drawdown": 1.0,
                "equity_ruined": True,
                "inference_diagnostics": [
                    {"code": "EQUITY_NONPOSITIVE", "detail": "...", "value": -5.0},
                ],
            },
        },
    }
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(payload), "utf-8")
    metrics = parse_tournament(p)
    assert len(metrics.inference_diagnostics) == 1
    assert metrics.inference_diagnostics[0]["code"] == "EQUITY_NONPOSITIVE"


def test_parse_tournament_defaults_to_empty_tuple_for_legacy_json(tmp_path):
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "oos_metrics": {"sortino_ratio": 1.0, "total_return": 0.1, "max_drawdown": 0.05},
        },
    }
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(payload), "utf-8")
    metrics = parse_tournament(p)
    assert metrics.inference_diagnostics == ()


# ---------------------------------------------------------------------------
# run_optimization._reemit_inference_diagnostics: parent-process re-emission
# ---------------------------------------------------------------------------

class _FakeMetrics:
    def __init__(self, diagnostics):
        self.inference_diagnostics = diagnostics


def _capture_json_events(logger_name="optimizer"):
    events = []

    class _H(logging.Handler):
        def emit(self, r):
            msg = r.getMessage()
            if "[JSON_EVENT]" in msg:
                events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))

    lg = logging.getLogger(logger_name)
    lg.addHandler(_H())
    lg.setLevel(logging.INFO)
    return lg, events, _H


def test_reemit_produces_one_event_per_diagnostic_with_trial_number():
    """Akzeptanzkriterium #804: ein Trial mit einer erzwungenen Verletzung erzeugt genau eine
    INFERENCE_DIAGNOSTIC-ERROR-Zeile im Optimizer-Log je Diagnose, mit trial_number."""
    lg, events, handler_cls = _capture_json_events("t804_reemit")
    metrics = _FakeMetrics([
        {"code": "RETURN_SERIES_IDENTITY_VIOLATION", "detail": "gap too large", "value": 0.5},
    ])
    try:
        ro._reemit_inference_diagnostics(lg, metrics, trial_number=7)
    finally:
        for h in list(lg.handlers):
            if isinstance(h, handler_cls):
                lg.removeHandler(h)
    diag_events = [e for e in events if e.get("event_type") == "INFERENCE_DIAGNOSTIC"]
    assert len(diag_events) == 1
    assert diag_events[0]["trial_number"] == 7
    assert diag_events[0]["code"] == "RETURN_SERIES_IDENTITY_VIOLATION"
    assert diag_events[0]["value"] == pytest.approx(0.5)


def test_reemit_carries_study_identity_via_bind_study_context():
    from automation.log_manager import bind_study_context

    lg, events, handler_cls = _capture_json_events("t804_context")
    metrics = _FakeMetrics([{"code": "SORTINO_GUARD_TRIPPED", "detail": "x", "value": 99.0}])
    try:
        with bind_study_context(strategy="MeanReversionStrategy", symbol="ADA.ETORO",
                                study_name="study_MeanReversionStrategy_ADA_ETORO"):
            ro._reemit_inference_diagnostics(lg, metrics, trial_number=3)
    finally:
        for h in list(lg.handlers):
            if isinstance(h, handler_cls):
                lg.removeHandler(h)
    diag = [e for e in events if e.get("event_type") == "INFERENCE_DIAGNOSTIC"][0]
    assert diag["strategy"] == "MeanReversionStrategy"
    assert diag["symbol"] == "ADA.ETORO"
    assert diag["study_name"] == "study_MeanReversionStrategy_ADA_ETORO"


def test_reemit_is_a_noop_with_no_diagnostics():
    lg, events, handler_cls = _capture_json_events("t804_empty")
    try:
        ro._reemit_inference_diagnostics(lg, _FakeMetrics([]), trial_number=1)
    finally:
        for h in list(lg.handlers):
            if isinstance(h, handler_cls):
                lg.removeHandler(h)
    assert [e for e in events if e.get("event_type") == "INFERENCE_DIAGNOSTIC"] == []


# ---------------------------------------------------------------------------
# invariants.check_inference_diagnostics_absent
# ---------------------------------------------------------------------------

def test_check_passes_when_no_trial_has_diagnostics():
    trials = [{"inference_diagnostics": []}, {}]
    result = inv.check_inference_diagnostics_absent(trials)
    assert result.passed is True
    assert result.actual == 0


def test_check_fails_and_counts_across_trials():
    trials = [
        {"inference_diagnostics": [{"code": "EQUITY_NONPOSITIVE"}]},
        {"inference_diagnostics": [{"code": "EQUITY_NONPOSITIVE"}, {"code": "SORTINO_GUARD_TRIPPED"}]},
        {},
    ]
    result = inv.check_inference_diagnostics_absent(trials)
    assert result.passed is False
    assert result.actual == 3
    assert result.name == "check_inference_diagnostics_absent"


# ---------------------------------------------------------------------------
# report._study_record: inference_diagnostics_by_code + the new check wired in
# ---------------------------------------------------------------------------

def test_report_study_record_aggregates_inference_diagnostics_by_code():
    class _T:
        def __init__(self, diags):
            self.value = 1.0
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True,
                               "inference_diagnostics": diags}

    class _S:
        trials = [
            _T([{"code": "EQUITY_NONPOSITIVE"}]),
            _T([{"code": "EQUITY_NONPOSITIVE"}]),
            _T([{"code": "SORTINO_GUARD_TRIPPED"}]),
        ]
        best_value = 1.0
        user_attrs = {}

    record, checks = _study_record({"symbol": "AAA.ETORO", "strategy": "SmaCrossoverStrategy"}, _S())
    assert record["inference_diagnostics_by_code"] == {
        "EQUITY_NONPOSITIVE": 2, "SORTINO_GUARD_TRIPPED": 1,
    }
    names = [c.name for c in checks]
    assert "check_inference_diagnostics_absent" in names
    c = next(c for c in checks if c.name == "check_inference_diagnostics_absent")
    assert c.passed is False
    assert c.actual == 3


def test_report_study_record_empty_dict_when_no_diagnostics():
    class _T:
        def __init__(self):
            self.value = 1.0
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True}

    class _S:
        trials = [_T(), _T()]
        best_value = 1.0
        user_attrs = {}

    record, checks = _study_record({"symbol": "AAA.ETORO", "strategy": "SmaCrossoverStrategy"}, _S())
    assert record["inference_diagnostics_by_code"] == {}
    c = next(c for c in checks if c.name == "check_inference_diagnostics_absent")
    assert c.passed is True
