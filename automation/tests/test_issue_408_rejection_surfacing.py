"""Issue #408 (P2) — modale Gate-Drop-Reason in user_attrs / Proposal.

`make_symbol_objective` persistiert pro Trial die kategorisierte Rejection-Reason
(`trial.set_user_attr("rejection_reason", ...)`): trennt IS-Drop ('oos_not_evaluated' — das
Symbol erzeugte nie evaluierbare OOS-Trades, die Pitfall-#75-Signatur) vom OOS-Drop
('oos_gate_rejected') und vom Pass ('none'). `confirm._dominant_rejection` aggregiert die Reasons
ueber alle Trials (Counter) und `export_symbol_proposal` schreibt die modale Reason nach
`proposal["dominant_rejection"]`.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config
from automation.optimizer import confirm


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_backtest(payload):
    def _fake(trial_dir, manifest_path, **_kwargs):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), "utf-8")
        return out
    return _fake


# ---------------------------------------------------------------------------
# make_symbol_objective — per-trial rejection_reason in user_attrs
# ---------------------------------------------------------------------------

def test_is_drop_sets_oos_not_evaluated(tmp_path, monkeypatch):
    """aggregate_winner null, kein OOS ⇒ jeder Trial traegt 'oos_not_evaluated' (IS-Drop)."""
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": None,
               "full_results": [{"metrics": {"total_trades": 120}}]}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "CPRT.ETORO", n_trials=2)
    reasons = [t.user_attrs.get("rejection_reason") for t in study.trials]
    assert reasons and all(r == "oos_not_evaluated" for r in reasons)


def test_oos_gate_rejection_sets_oos_gate_rejected(tmp_path, monkeypatch):
    """single_symbol_oos: OOS evaluiert, aber durchs Gate gefallen ⇒ 'oos_gate_rejected'."""
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": None,
               "single_symbol_oos": {"oos_evaluated": True, "oos_eligible": False,
                                     "oos_rejection_reasons": ["oos_max_drawdown: 0.5 > 0.3"],
                                     "oos_metrics": {"sortino_ratio": 0.5, "max_drawdown": 0.5,
                                                     "total_trades": 10, "total_return": 0.1}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)
    reasons = [t.user_attrs.get("rejection_reason") for t in study.trials]
    assert reasons and all(r == "oos_gate_rejected" for r in reasons)


def test_evaluable_eligible_sets_none(tmp_path, monkeypatch):
    """Voll evaluierbarer & eligibler Trial ⇒ keine Rejection ('none')."""
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
        "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05,
                        "total_trades": 22, "total_return": 0.31}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "BBB.ETORO", n_trials=2)
    reasons = [t.user_attrs.get("rejection_reason") for t in study.trials]
    assert reasons and all(r == "none" for r in reasons)


# ---------------------------------------------------------------------------
# confirm._dominant_rejection — modale Aggregation (Counter)
# ---------------------------------------------------------------------------

class _T:
    def __init__(self, reason):
        self.user_attrs = {"rejection_reason": reason} if reason is not None else {}


class _Study:
    study_name = "study_SmaCrossoverStrategy_CPRT_ETORO"
    best_value = -9.75

    def __init__(self, reasons):
        self.trials = [_T(r) for r in reasons]


def test_dominant_rejection_picks_mode():
    study = _Study(["oos_not_evaluated", "oos_not_evaluated", "oos_gate_rejected"])
    assert confirm._dominant_rejection(study) == "oos_not_evaluated"


def test_dominant_rejection_none_when_no_reasons():
    study = _Study([None, None])
    assert confirm._dominant_rejection(study) is None


# ---------------------------------------------------------------------------
# export_symbol_proposal — proposal["dominant_rejection"]
# ---------------------------------------------------------------------------

def test_export_proposal_surfaces_dominant_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    study = _Study(["oos_not_evaluated", "oos_not_evaluated", "oos_gate_rejected"])
    promotion = {
        "status": "REJECTED_ON_HOLDOUT", "symbol_params": {"sma_period": 10},
        "R_symbol": -9.75, "R_global": -9.0, "promotion_margin": 0.10,
        "metrics_symbol": {}, "metrics_global": {},
    }
    path = confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "CPRT.ETORO", promotion)
    data = json.loads(Path(path).read_text("utf-8"))
    assert data["dominant_rejection"] == "oos_not_evaluated"
