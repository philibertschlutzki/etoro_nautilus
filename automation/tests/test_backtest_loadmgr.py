"""Load-Management für Massen-Backtests: Zeitfenster, Lastgrenzen, Blockgröße, Wiederaufnahme."""
from __future__ import annotations

import datetime as dt

from automation import backtest_loadmgr as lm

UTC = dt.timezone.utc
OK = lm.Snapshot(0.1, 20.0, 100.0, True)


def t(y, m, d, h, mi=10):
    return dt.datetime(y, m, d, h, mi, tzinfo=UTC)


def test_budget_windows():
    assert lm.compute_budget(t(2026, 10, 14, 15)) == 0.0          # Mi, US-Handelszeit
    assert lm.compute_budget(t(2026, 10, 14, 14, 57)) == 0.0      # Stundenwechsel
    assert lm.compute_budget(t(2026, 10, 14, 13, 10)) == lm.BUDGET_SHOULDER
    assert lm.compute_budget(t(2026, 10, 14, 3)) == lm.BUDGET_QUIET
    assert lm.compute_budget(t(2026, 10, 17, 15)) == lm.BUDGET_QUIET   # Samstag


def test_may_run_hard_limits():
    assert lm.may_run(0.6, OK)[0]
    assert not lm.may_run(0.0, OK)[0]
    assert not lm.may_run(0.6, lm.Snapshot(0.9, 20, 100, True))[0]
    assert not lm.may_run(0.6, lm.Snapshot(0.1, 2, 100, True))[0]
    assert not lm.may_run(0.6, lm.Snapshot(0.1, 20, 5, True))[0]
    assert not lm.may_run(0.6, lm.Snapshot(0.1, 20, 100, False))[0]


def test_jobs_respect_budget_and_existing_load():
    assert lm.jobs_for(0.6, lm.Snapshot(0.0, 20, 100, True), cores=24) == 12      # gedeckelt
    assert lm.jobs_for(0.25, lm.Snapshot(0.1, 20, 100, True), cores=24) == 3
    assert lm.jobs_for(0.25, lm.Snapshot(0.5, 20, 100, True), cores=24) == 1      # nie < 1


def test_block_sizer_aimd():
    s = lm.BlockSizer(size=8, target_s=100)
    s.success(5)                       # sehr schnell: verdoppeln
    assert s.size == 16
    s.size = 8
    s.success(30)
    assert s.size == 10
    s.success(95)                      # nah am Zeitziel: keine Steigerung
    assert s.size == 10
    s.failure()
    assert s.size == 5
    for _ in range(5):
        s.failure()
    assert s.size == 1


def test_step_waits_outside_window_and_resumes(tmp_path):
    st = lm.State(pending=["A", "B", "C"], block_size=2)
    sizer = lm.BlockSizer()
    calls = []
    runner = lambda cmd, block, jobs, timeout_s: (calls.append(block) or True, "rc=0")
    assert lm.step(st, sizer, "x", now=t(2026, 10, 14, 15), snap=OK, runner=runner).startswith("waiting")
    assert calls == []
    assert lm.step(st, sizer, "x", now=t(2026, 10, 14, 3), snap=OK, runner=runner) == "ran:ok"
    assert calls == [["A", "B"]] and st.pending == ["C"] and st.done == ["A", "B"]
    p = tmp_path / "s.json"
    st.save(p)
    again = lm.State.load(p, ["A", "B", "C", "D"])
    assert again.pending == ["C", "D"] and again.done == ["A", "B"]


def test_failure_halves_block_and_skips_poison_symbol():
    st = lm.State(pending=["A", "B", "C", "D"], block_size=4)
    sizer = lm.BlockSizer()
    runner = lambda cmd, block, jobs, timeout_s: (False, "rc=1")
    quiet = t(2026, 10, 14, 3)
    lm.step(st, sizer, "x", now=quiet, snap=OK, runner=runner)
    assert st.block_size == 2 and st.pending == ["A", "B", "C", "D"]
    lm.step(st, sizer, "x", now=quiet, snap=OK, runner=runner)
    assert st.block_size == 1
    lm.step(st, sizer, "x", now=quiet, snap=OK, runner=runner)         # A: dritter Fehlversuch
    assert "A" not in st.pending and st.pending[0] == "B"


def test_catalog_symbols_only_instruments_with_data():
    syms = lm.catalog_symbols()
    assert syms == sorted(syms) and all(s.endswith(".ETORO") for s in syms)
