import pytest
import logging
import sys

# Wir können den Code direkt aus der Datei lesen und als Modul ausführen oder _check_reward_semantics_version einfach mock-extrahieren,
# wenn die Import-Abhängigkeiten problematisch sind, aber optuna.exceptions.ExperimentalWarning ist das Problem.
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    try:
        import optuna
        HAS_OPTUNA = True
    except ImportError:
        HAS_OPTUNA = False

if not HAS_OPTUNA:
    import unittest.mock as mock
    class MockOptuna(mock.MagicMock):
        class exceptions:
            class ExperimentalWarning(Warning):
                pass
    sys.modules["optuna"] = MockOptuna()
    sys.modules["sqlalchemy"] = mock.MagicMock()
    sys.modules["sqlalchemy.exc"] = mock.MagicMock()

from automation.optimizer import run_optimization as ro

def test_check_reward_semantics_guard(monkeypatch):
    class FakeStudy:
        def __init__(self, attrs=None, n_trials=0):
            self._attrs = dict(attrs or {})
            self.trials = [object()] * n_trials

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    lg = logging.getLogger("test")
    opt_data = {"reward_semantics_version": 6}

    # Frische Study ohne Version (No-Op / Stamp)
    s = FakeStudy(n_trials=0)
    ro._check_reward_semantics_version(s, opt_data, logger=lg)
    assert s.user_attrs["reward_semantics_version"] == 6

    # Study mit gleicher Version (Pass)
    s = FakeStudy(attrs={"reward_semantics_version": 6}, n_trials=10)
    ro._check_reward_semantics_version(s, opt_data, logger=lg)

    # Study mit alter Version und Trials (ValueError)
    s = FakeStudy(attrs={"reward_semantics_version": 5}, n_trials=10)
    with pytest.raises(ValueError, match="Reward-Semantik-Versionskonflikt"):
        ro._check_reward_semantics_version(s, opt_data, logger=lg)

    # Study komplett ohne Version, aber mit Trials (ValueError)
    s = FakeStudy(n_trials=10)
    with pytest.raises(ValueError, match="Reward-Semantik-Versionskonflikt"):
        ro._check_reward_semantics_version(s, opt_data, logger=lg)
