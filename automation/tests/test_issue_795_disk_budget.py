"""Issue #795 — kein Speicher-Budget: der Lauf lief in ENOSPC statt vorher fail-loud zu stoppen.

Root-Cause: repo-weit existierte kein Aufruf von ``shutil.disk_usage``/``statvfs`` — der Speicher-
verbrauch von ``data/optimizer`` war eine unkontrollierte Konsequenz der Trial-Zahl. Diese Tests
decken die drei Bausteine ab: (1) ``disk_guard`` selbst (Messung, Status, Preflight), (2) den
laufenden ``run_optimization.disk_budget_callback``, (3) die ``sweep.py``-Verdrahtung
(``sweep_abort_requested`` lässt neue Paare nicht mehr starten, sobald eine Study das Budget
überschritten hat).
"""
import json
import time
from pathlib import Path

import optuna
import pytest

from automation.optimizer import disk_guard


@pytest.fixture(autouse=True)
def _reset_disk_guard():
    disk_guard.reset_for_tests()
    yield
    disk_guard.reset_for_tests()


# ---------------------------------------------------------------------------
# measure_usage / free_bytes / check_budget
# ---------------------------------------------------------------------------

def test_measure_usage_sums_file_sizes(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    sub = tmp_path / "study_X" / "trial_0000"
    sub.mkdir(parents=True)
    (sub / "b.txt").write_bytes(b"y" * 50)

    assert disk_guard.measure_usage(tmp_path, use_cache=False) == 150


def test_measure_usage_is_ttl_cached(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    first = disk_guard.measure_usage(tmp_path)
    (tmp_path / "b.txt").write_bytes(b"y" * 500)  # nach der ersten Messung hinzugefuegt
    cached = disk_guard.measure_usage(tmp_path)
    assert cached == first  # Cache liefert den ALTEN Wert innerhalb der TTL

    fresh = disk_guard.measure_usage(tmp_path, use_cache=False)
    assert fresh == 600


def test_measure_usage_missing_dir_returns_zero(tmp_path):
    assert disk_guard.measure_usage(tmp_path / "does_not_exist") == 0


def test_check_budget_ok_when_well_under_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_guard, "measure_usage", lambda *a, **k: 1 * disk_guard.GIB)
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 500 * disk_guard.GIB)
    status = disk_guard.check_budget(tmp_path, budget_gb=200, reserve_gb=50)
    assert status == disk_guard.STATUS_OK


def test_check_budget_pressure_at_80_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_guard, "measure_usage", lambda *a, **k: 161 * disk_guard.GIB)
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 500 * disk_guard.GIB)
    status = disk_guard.check_budget(tmp_path, budget_gb=200, reserve_gb=50)
    assert status == disk_guard.STATUS_PRESSURE


def test_check_budget_pressure_when_free_space_low(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_guard, "measure_usage", lambda *a, **k: 1 * disk_guard.GIB)
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 99 * disk_guard.GIB)  # < 2*50
    status = disk_guard.check_budget(tmp_path, budget_gb=200, reserve_gb=50)
    assert status == disk_guard.STATUS_PRESSURE


def test_check_budget_exceeded_at_full_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_guard, "measure_usage", lambda *a, **k: 200 * disk_guard.GIB)
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 500 * disk_guard.GIB)
    status = disk_guard.check_budget(tmp_path, budget_gb=200, reserve_gb=50)
    assert status == disk_guard.STATUS_EXCEEDED


def test_check_budget_exceeded_when_free_space_below_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_guard, "measure_usage", lambda *a, **k: 1 * disk_guard.GIB)
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 40 * disk_guard.GIB)  # < 50
    status = disk_guard.check_budget(tmp_path, budget_gb=200, reserve_gb=50)
    assert status == disk_guard.STATUS_EXCEEDED


# ---------------------------------------------------------------------------
# estimate_expected_bytes / assert_preflight_budget
# ---------------------------------------------------------------------------

def test_estimate_expected_bytes_is_pure_arithmetic():
    assert disk_guard.estimate_expected_bytes(1000, 30000) == 30_000_000


def test_preflight_raises_when_expected_exceeds_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 1000 * disk_guard.GIB)
    with pytest.raises(disk_guard.DiskBudgetExceededError, match="Budget"):
        disk_guard.assert_preflight_budget(
            tmp_path, expected_bytes=201 * disk_guard.GIB, budget_gb=200, reserve_gb=50)


def test_preflight_raises_when_expected_exceeds_free_minus_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 60 * disk_guard.GIB)
    with pytest.raises(disk_guard.DiskBudgetExceededError, match="freien Platz"):
        disk_guard.assert_preflight_budget(
            tmp_path, expected_bytes=20 * disk_guard.GIB, budget_gb=200, reserve_gb=50)


def test_preflight_passes_with_ample_budget_and_free_space(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 1000 * disk_guard.GIB)
    disk_guard.assert_preflight_budget(
        tmp_path, expected_bytes=1 * disk_guard.GIB, budget_gb=200, reserve_gb=50)  # no raise


def test_preflight_tiny_budget_aborts_before_any_trial(tmp_path, monkeypatch):
    """Akzeptanzkriterium #795: disk_budget_gb=0.001 bricht ab, ohne einen einzigen Trial
    zu starten (hier: expected_bytes fuer irgendeinen realistischen Lauf > 0.001 GB)."""
    monkeypatch.setattr(disk_guard, "free_bytes", lambda *a, **k: 1000 * disk_guard.GIB)
    expected = disk_guard.estimate_expected_bytes(n_trials=100, bytes_per_trial_estimate=30000)
    with pytest.raises(disk_guard.DiskBudgetExceededError):
        disk_guard.assert_preflight_budget(
            tmp_path, expected_bytes=expected, budget_gb=0.001, reserve_gb=50)


# ---------------------------------------------------------------------------
# run_optimization.disk_budget_callback
# ---------------------------------------------------------------------------

class _FakeStudy:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeTrial:
    def __init__(self, number):
        self.number = number


def test_callback_is_a_noop_outside_the_check_interval(monkeypatch):
    from automation.optimizer import run_optimization as ro
    monkeypatch.setattr(ro.disk_guard, "check_budget", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("check_budget darf ausserhalb des Intervalls nicht aufgerufen werden")))
    study = _FakeStudy()
    ro.disk_budget_callback(study, _FakeTrial(7), opt_data={"disk_check_interval_trials": 200})
    assert not study.stopped


def test_callback_pressure_triggers_orphan_prune(monkeypatch):
    from automation.optimizer import run_optimization as ro
    monkeypatch.setattr(ro.disk_guard, "check_budget", lambda *a, **k: disk_guard.STATUS_PRESSURE)
    pruned_calls = []
    monkeypatch.setattr(ro.retention, "prune_orphaned_trial_dirs",
                        lambda *a, **k: pruned_calls.append(1) or [])
    study = _FakeStudy()
    ro.disk_budget_callback(study, _FakeTrial(199), opt_data={"disk_check_interval_trials": 200})
    assert pruned_calls == [1]
    assert not study.stopped
    assert not disk_guard.sweep_abort_requested.is_set()


def test_callback_exceeded_stops_study_and_sets_abort_flag(monkeypatch):
    from automation.optimizer import run_optimization as ro
    monkeypatch.setattr(ro.disk_guard, "check_budget", lambda *a, **k: disk_guard.STATUS_EXCEEDED)
    study = _FakeStudy()
    ro.disk_budget_callback(study, _FakeTrial(399), opt_data={"disk_check_interval_trials": 200})
    assert study.stopped is True
    assert disk_guard.sweep_abort_requested.is_set()


def test_callback_fails_open_on_internal_error(monkeypatch):
    from automation.optimizer import run_optimization as ro

    def _boom(*a, **k):
        raise RuntimeError("measurement blew up")

    monkeypatch.setattr(ro.disk_guard, "check_budget", _boom)
    study = _FakeStudy()
    # darf NICHT propagieren (fail-open, analog floor_plateau_callback/retention_callback)
    ro.disk_budget_callback(study, _FakeTrial(199), opt_data={"disk_check_interval_trials": 200})
    assert not study.stopped


# ---------------------------------------------------------------------------
# sweep.py wiring: sweep_abort_requested skips remaining pairs cleanly
# ---------------------------------------------------------------------------

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def test_sweep_skips_remaining_pairs_once_abort_flag_is_set(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK"), ("S", "C.ETORO", "OK")]
    # Issue #799 — der Sweep-Fortschritts-Checkpoint schreibt nach WORK; isoliert halten.
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)

    started = []

    def fake_opt(strategy, symbol, **k):
        started.append(symbol)
        if symbol == "A.ETORO":
            disk_guard.sweep_abort_requested.set()  # simuliert einen EXCEEDED-Callback in A
        return object()

    def fake_confirm(study, s, sym, gp, **k):
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    out = sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO", "C.ETORO"], tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )

    assert started == ["A.ETORO"], "B/C duerfen nach dem Abort-Flag nicht mehr gestartet werden"
    # A wurde gestartet und hat ein Proposal; B/C wurden uebersprungen (kein Proposal, kein Crash).
    assert out == [tmp_path / "proposal_A.ETORO.json"]
