"""Issue #823 Fix Punkt 4 — Guard-Trips als Diagnose statt stillem Verwerfen: übersteigt der Anteil
der ``SORTINO_GUARD_TRIPPED``-Trips einer Study eine konfigurierbare Schwelle
(``sortino_guard_trip_fraction_warn``), wird die Study als ``STUDY_GUARD_DOMINATED`` markiert
(``study.user_attrs['study_guard_dominated']`` + WARNING-Log) — ihr Ergebnis ist dann NICHT als
reguläres 0-/N-eligible-Resultat interpretierbar (reine Diagnose, keine Gate-/Reward-Wirkung).
"""
import time
from pathlib import Path

import pytest

from automation.optimizer.run_optimization import _emit_study_summary


class _DummyTrial:
    def __init__(self, evaluated=True, eligible=False, guard_tripped=False):
        diagnostics = [{"code": "SORTINO_GUARD_TRIPPED"}] if guard_tripped else []
        self.value = 0.1
        self.user_attrs = {
            "oos_evaluated": evaluated,
            "oos_eligible": eligible,
            "inference_diagnostics": diagnostics,
            "backtest_ms": 10,
        }


class _DummyStudy:
    def __init__(self, trials):
        self.study_name = "study_guard_test"
        self.trials = trials
        self.best_value = 0.1
        self._user_attrs = {}

    def set_user_attr(self, key, value):
        self._user_attrs[key] = value

    @property
    def user_attrs(self):
        return dict(self._user_attrs)


def _isolate(monkeypatch, opt_data=None):
    import automation.optimizer.run_optimization as ro
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    if opt_data is not None:
        import json
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp())
        (tmp_dir / "optimizer.json").write_text(json.dumps(opt_data), "utf-8")
        monkeypatch.setattr(ro, "config_dir", lambda: tmp_dir)


def test_study_marked_guard_dominated_above_threshold(monkeypatch):
    _isolate(monkeypatch, {"sortino_guard_trip_fraction_warn": 0.10})
    # 10 evaluierte Trials, 5 davon SORTINO_GUARD_TRIPPED (50% > 10%-Schwelle).
    trials = ([_DummyTrial(guard_tripped=True)] * 5
             + [_DummyTrial(guard_tripped=False)] * 5)
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter())
    assert study.user_attrs.get("study_guard_dominated") is True


def test_study_not_marked_below_threshold(monkeypatch):
    _isolate(monkeypatch, {"sortino_guard_trip_fraction_warn": 0.50})
    trials = ([_DummyTrial(guard_tripped=True)] * 2
             + [_DummyTrial(guard_tripped=False)] * 8)
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter())
    assert study.user_attrs.get("study_guard_dominated") is None


def test_default_threshold_is_ten_percent(monkeypatch):
    _isolate(monkeypatch, {})  # kein sortino_guard_trip_fraction_warn -> Default 0.10
    trials = ([_DummyTrial(guard_tripped=True)] * 2
             + [_DummyTrial(guard_tripped=False)] * 8)  # exakt 20% > 10%
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter())
    assert study.user_attrs.get("study_guard_dominated") is True


def test_no_evaluated_trials_never_dominated(monkeypatch):
    _isolate(monkeypatch, {"sortino_guard_trip_fraction_warn": 0.10})
    trials = [_DummyTrial(evaluated=False, guard_tripped=False) for _ in range(5)]
    study = _DummyStudy(trials)
    _emit_study_summary(study, "TSLA.ETORO", time.perf_counter())
    assert study.user_attrs.get("study_guard_dominated") is None
