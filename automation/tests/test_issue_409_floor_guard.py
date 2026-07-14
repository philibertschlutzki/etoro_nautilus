"""Issue #409 (P2) — Fail-Loud-Guard bei Unevaluable-Floor-Plateau.

`floor_plateau_callback` (Optuna-Callback) warnt, sobald nach `n_startup_trials` ALLE
abgeschlossenen Trials am Unevaluable-Floor kleben (`abs(value - floor) < eps`). Der Floor wird
aus der Config abgeleitet: `penalty_unevaluable_oos + unevaluable_shaping_span` (= −9.75, die
exakte Pitfall-#75-Signatur). Macht den Zero-Gradient-Kollaps laut, statt ihn als teuren
Zufallsgenerator weiterlaufen zu lassen.
"""
import json
import logging
from pathlib import Path

import optuna
import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config

W = {
    "penalty_unevaluable_oos": -10.0,
    "unevaluable_shaping_span": 0.25,
    "n_startup_trials": 16,
}
FLOOR = W["penalty_unevaluable_oos"] + W["unevaluable_shaping_span"]  # -9.75


class _FakeTrial:
    def __init__(self, value, state=optuna.trial.TrialState.COMPLETE):
        self.value = value
        self.state = state


class _FakeStudy:
    def __init__(self, values):
        self.trials = [_FakeTrial(v) for v in values]
        self._attrs = {}

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v


def _capturing_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    recs = []

    class _H(logging.Handler):
        def emit(self, r):
            recs.append(r)

    lg.addHandler(_H())
    lg.setLevel(logging.WARNING)
    lg.propagate = False
    return lg, recs


def _warned(recs):
    return [r for r in recs if "Floor-Plateau" in r.getMessage()]


def test_warns_when_all_trials_at_floor():
    lg, recs = _capturing_logger("test409a")
    study = _FakeStudy([FLOOR] * 5)
    ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert _warned(recs)


def test_no_warning_when_an_evaluable_trial_exists():
    lg, recs = _capturing_logger("test409b")
    study = _FakeStudy([FLOOR, FLOOR, -5.0])   # ein evaluierbarer Trial bricht das Plateau
    ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert not _warned(recs)


def test_no_warning_before_startup_threshold():
    lg, recs = _capturing_logger("test409c")
    study = _FakeStudy([FLOOR, FLOOR])         # 2 < n_startup_trials=3
    ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert not _warned(recs)


def test_floor_is_derived_from_shipped_config():
    """Ohne explizite weights leitet der Callback den Unevaluable-Floor UND n_startup (16) aus
    optimizer.json ab. Issue #591 — die Bänder wurden nach unten verschoben (penalty_unevaluable_oos
    −10 → −20), der Unevaluable-Floor ist damit −19.75 (= −20 + 0.25)."""
    lg, recs = _capturing_logger("test409d")
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    floor = cfg["penalty_unevaluable_oos"] + cfg["unevaluable_shaping_span"]
    study = _FakeStudy([floor] * (cfg["n_startup_trials"] + 2))
    ro.floor_plateau_callback(study, study.trials[-1], logger=lg)   # weights=None -> config
    assert _warned(recs)
    assert floor == pytest.approx(-19.75)


def test_warns_only_once_per_study():
    lg, recs = _capturing_logger("test409e")
    study = _FakeStudy([FLOOR] * 5)
    for _ in range(4):
        ro.floor_plateau_callback(study, study.trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert len(_warned(recs)) == 1   # warn-once via study user_attr (kein Log-Spam)


# ---------------------------------------------------------------------------
# Integration: optimize_symbol registriert den Guard
# ---------------------------------------------------------------------------

def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _saturated_backtest(trial_dir, manifest_path):
    """Jeder Trial liefert exakt den saturierten Unevaluable-Floor (−9.75): aggregate_winner null,
    aber IS handelt reichlich mit Top-Performance ⇒ progress=1.0 ⇒ reward = penalty + span."""
    out = Path(trial_dir) / "tournament_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fully_eligible_pairs": 0, "aggregate_winner": None,
        "full_results": [{"metrics": {"total_trades": 500, "total_return": 1.0, "win_rate": 1.0}}]}), "utf-8")
    return out


def test_optimize_symbol_registers_floor_guard(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _saturated_backtest)
    lg, recs = _capturing_logger("optimizer")

    study = ro.optimize_symbol("SmaCrossoverStrategy", "CPRT.ETORO", n_trials=16)

    assert all(abs(t.value - (-19.75)) < 1e-9 for t in study.trials)  # voller Floor-Kollaps (#591: Band -20..-19.75)
    assert _warned(recs), "optimize_symbol MUSS den floor_plateau_callback registrieren"
