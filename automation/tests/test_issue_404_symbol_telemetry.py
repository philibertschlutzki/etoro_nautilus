"""Issue #404 (P0) — Per-Symbol-Telemetrie.

`make_symbol_objective` emittierte bislang KEIN strukturiertes Per-Trial-Event — im Sweep
war Optunas native INFO-Zeile die einzige Rueckmeldung, und der Unevaluable-Floor-Kollaps
(Pitfall #75) damit forensisch unsichtbar. Dieser Test sichert ab, dass jeder Per-Symbol-Trial
ein `optimizer_trial_completed`-Event mit dem geforderten Payload emittiert.

Kernpunkt des Payloads: `oos_eligible` trennt den IS-Drop (Symbol nie IS-eligible ⇒ kein OOS
evaluiert) vom OOS-Drop (OOS evaluiert, aber durchs Gate gefallen). `is_total_trades` /
`is_max_trades` machen die Shaping-Saettigung (Pitfall #75, Defekt 2) sichtbar.

End-to-end mit voll gemocktem Backtest (HI-7: kein echter Subprozess), WORK/config_dir isoliert
analog test_optimize_symbol.
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config

REQUIRED_KEYS = (
    "symbol", "oos_evaluated", "oos_eligible", "oos_total_trades",
    "oos_total_return", "is_total_trades", "is_max_trades", "outcome",
)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_backtest(payload):
    def _fake(trial_dir, manifest_path):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), "utf-8")
        return out
    return _fake


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        msg = record.getMessage()
        if "[JSON_EVENT]" in msg:
            self.events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))


@pytest.fixture
def trial_events():
    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield handler.events
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def _completed(events):
    return [e for e in events if e.get("event_type") == "optimizer_trial_completed"]


def test_evaluable_trial_emits_full_payload(tmp_path, monkeypatch, trial_events):
    """Evaluierbarer Trial: Event vorhanden, alle Pflicht-Keys gesetzt, outcome 'evaluable'."""
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
        "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05,
                        "total_trades": 22, "total_return": 0.31}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))

    ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)

    evts = _completed(trial_events)
    assert evts, "make_symbol_objective MUSS ein optimizer_trial_completed-Event emittieren"
    e = evts[-1]
    for k in REQUIRED_KEYS:
        assert k in e, f"Pflicht-Key '{k}' fehlt im Payload"
    assert e["symbol"] == "TSLA.ETORO"
    assert e["outcome"] == "evaluable"
    assert e["oos_evaluated"] is True
    assert e["oos_eligible"] is True
    assert e["oos_total_trades"] == 22
    assert e["oos_total_return"] == pytest.approx(0.31)


def test_unevaluable_trial_separates_is_from_oos_drop(tmp_path, monkeypatch, trial_events):
    """Pitfall-#75-Szenario: aggregate_winner null ⇒ outcome 'unevaluable', oos_evaluated False,
    aber is_total_trades/is_max_trades reflektieren die reichliche IS-Aktivitaet (Shaping-Quelle).
    """
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": None,
               "full_results": [{"metrics": {"total_trades": 120}},
                                {"metrics": {"total_trades": 90}}]}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))

    ro.optimize_symbol("SmaCrossoverStrategy", "CPRT.ETORO", n_trials=1)

    evts = _completed(trial_events)
    assert evts
    e = evts[-1]
    assert e["symbol"] == "CPRT.ETORO"
    assert e["outcome"] == "unevaluable"
    assert e["oos_evaluated"] is False
    assert e["oos_eligible"] is False
    assert e["oos_total_trades"] == 0
    # IS-Drop vs OOS-Drop: IS handelt reichlich (Saettigungsquelle), OOS evaluiert nie.
    assert e["is_total_trades"] == 210
    assert e["is_max_trades"] == 120


def test_one_event_per_trial(tmp_path, monkeypatch, trial_events):
    """Genau ein Event pro abgeschlossenem Trial (keine Doppel-Emission)."""
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
        "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05,
                        "total_trades": 22, "total_return": 0.31}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))

    ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=3)

    assert len(_completed(trial_events)) == 3
