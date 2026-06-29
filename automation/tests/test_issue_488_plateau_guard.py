import json
import logging
from pathlib import Path
import optuna
import pytest
from automation.optimizer.run_optimization import floor_plateau_callback
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

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

    def stop(self):
        pass

def _capturing_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    recs = []

    class _H(logging.Handler):
        def emit(self, r):
            recs.append(r)

    lg.addHandler(_H())
    lg.setLevel(logging.INFO)
    lg.propagate = False
    return lg, recs

def test_json_event():
    W_k = {"penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25, "n_startup_trials": 3, "floor_plateau_k": 5}
    lg, recs = _capturing_logger("test488")
    trials = [_FakeTrial(-9.85, oos_evaluated=False) for _ in range(8)]
    study = _FakeStudy(trials)

    floor_plateau_callback(study, trials[-1], weights=W_k, n_startup_trials=3, logger=lg)

    json_event = [r.getMessage() for r in recs if "JSON_EVENT" in r.getMessage()]
    assert json_event, "Should emit JSON event on stop"

if __name__ == "__main__":
    test_json_event()
