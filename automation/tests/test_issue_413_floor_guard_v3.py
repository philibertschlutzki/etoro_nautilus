"""Issue #413 — Floor-Guard im v3-Shaping-Regime: evaluable-basiert statt wert-gleichheit-basiert.

Der alte `floor_plateau_callback` (#409) prueft `all(abs(value − (−9.75)) < eps)`. Da #406/#407
unevaluable Trials ABSICHTLICH unter −9.75 verteilen (−9.85…−9.93), ist dieses Praedikat im
geshapeten Regime strukturell unerfuellbar — toter Code. Der neue Guard feuert, wenn KEIN Trial je
evaluable war (`oos_evaluated` User-Attr). Legacy-Wert-Guard bleibt als Fallback fuer alte Studies.
"""
import logging
from pathlib import Path

import optuna

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config

W = {"penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25, "n_startup_trials": 16}


class _FakeTrial:
    def __init__(self, value, oos_evaluated=None, state=optuna.trial.TrialState.COMPLETE):
        self.value = value
        self.state = state
        self.user_attrs = {} if oos_evaluated is None else {"oos_evaluated": oos_evaluated}


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials
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


def test_guard_fires_when_no_trial_evaluable_with_shaped_values():
    """KERNFALL (den der alte Guard verschluckt): Sub-Floor-Werte −9.85…−9.93, alle unevaluable."""
    lg, recs = _capturing_logger("test413a")
    trials = [_FakeTrial(-9.85, oos_evaluated=False),
              _FakeTrial(-9.90, oos_evaluated=False),
              _FakeTrial(-9.93, oos_evaluated=False)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert _warned(recs), "Guard MUSS bei 0 evaluable Trials feuern, auch unter −9.75"


def test_guard_silent_when_some_trial_evaluable():
    lg, recs = _capturing_logger("test413b")
    trials = [_FakeTrial(-9.85, oos_evaluated=False),
              _FakeTrial(0.5, oos_evaluated=True),     # ein evaluable Trial bricht den Kollaps
              _FakeTrial(-9.90, oos_evaluated=False)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert not _warned(recs)


def test_guard_warns_once_per_study():
    lg, recs = _capturing_logger("test413c")
    trials = [_FakeTrial(-9.85, oos_evaluated=False) for _ in range(5)]
    study = _FakeStudy(trials)
    for _ in range(4):
        ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert len(_warned(recs)) == 1


def test_guard_silent_before_startup_threshold():
    lg, recs = _capturing_logger("test413d")
    trials = [_FakeTrial(-9.85, oos_evaluated=False), _FakeTrial(-9.90, oos_evaluated=False)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert not _warned(recs)   # 2 < n_startup=3


def test_legacy_value_guard_still_works_without_attr():
    """Rueckwaerts-Kompat: ohne oos_evaluated-Attr greift der Legacy-Wert-Guard am −9.75-Floor."""
    lg, recs = _capturing_logger("test413e")
    floor = W["penalty_unevaluable_oos"] + W["unevaluable_shaping_span"]  # -9.75
    trials = [_FakeTrial(floor) for _ in range(5)]  # KEIN oos_evaluated-Attr
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert _warned(recs)


# ── Integration: make_symbol_objective legt das oos_evaluated-User-Attr ab ────
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_backtest(payload):
    import json

    def _fake(trial_dir, manifest_path):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), "utf-8")
        return out
    return _fake


def test_make_symbol_objective_sets_oos_evaluated_attr(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
        "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05,
                        "total_trades": 22, "total_return": 0.31}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    assert study.trials[0].user_attrs.get("oos_evaluated") is True


def test_guard_fires_end_to_end_on_all_unevaluable(tmp_path, monkeypatch):
    """optimize_symbol mit durchweg unevaluable Backtests ⇒ evaluable-basierter Guard feuert."""
    _isolate(monkeypatch, tmp_path)
    # aggregate_winner null + reichlich IS-Aktivitaet ⇒ Sub-Floor-Werte, oos_evaluated=False.
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": None,
               "full_results": [{"metrics": {"total_trades": 80, "total_return": 0.1, "win_rate": 0.4}}]}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    lg, recs = _capturing_logger("optimizer")
    ro.optimize_symbol("SmaCrossoverStrategy", "CPRT.ETORO", n_trials=16)
    assert _warned(recs), "evaluable-basierter Guard MUSS bei 0 evaluable Trials feuern"
