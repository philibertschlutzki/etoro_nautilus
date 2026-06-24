"""Issue #415 — Backtest-Zeitdauer wird nirgends gemessen oder ausgewiesen.

Wall-Clock auf drei Ebenen: (1) `run_backtest` misst die Dauer (beide Modi, Out-Param `timings`),
(2) `optimizer_trial_completed` traegt `backtest_ms`, (3) `optimize_symbol` emittiert
`optimizer_study_completed` (n_trials, evaluable_trials, best_value, backtest_ms_total/median,
wallclock_s), (4) `run_per_symbol_sweep` emittiert `sweep_completed` + Konsolen-Schlusszeile.

Voll gemockt (HI-7).
"""
import json
import logging
import time
import types
from pathlib import Path

from automation.optimizer import runner
from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config
from automation.optimizer import sweep


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        msg = record.getMessage()
        if "[JSON_EVENT]" in msg:
            self.events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))


def _events_of(events, event_type):
    return [e for e in events if e.get("event_type") == event_type]


def _make_trial(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    m = {"global_settings": {"catalog_path": "data/nautilus"}}
    mp = tmp_path / "experiment_manifest.json"
    mp.write_text(json.dumps(m), "utf-8")
    return tmp_path, mp


# ── 1. run_backtest misst die Dauer (Out-Param timings) ──────────────────────
def test_run_backtest_measures_duration(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def slow_run(argv, **kw):
        time.sleep(0.03)
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", slow_run)
    timings: dict = {}
    out = runner.run_backtest(trial_dir, mp, timings=timings)
    assert out == trial_dir / "tournament_result.json"
    assert timings["duration_s"] > 0
    assert timings["backtest_ms"] >= 25  # ~30 ms Schlaf, grosszuegige untere Schranke


def test_run_backtest_timing_is_optional_signature_compatible(tmp_path, monkeypatch):
    """Ohne timings-Param bleibt der Aufruf bit-identisch (Signatur-Kompat, Pitfall #33)."""
    trial_dir, mp = _make_trial(tmp_path)

    def fast_run(argv, **kw):
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fast_run)
    out = runner.run_backtest(trial_dir, mp)   # kein timings-Arg
    assert out == trial_dir / "tournament_result.json"


# ── 2. Per-Trial-Event traegt backtest_ms ────────────────────────────────────
def _fake_backtest(payload):
    def _fake(trial_dir, manifest_path):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), "utf-8")
        return out
    return _fake


def test_trial_event_contains_backtest_ms(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": None,
               "full_results": [{"metrics": {"total_trades": 30}}]}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))

    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        ro.optimize_symbol("SmaCrossoverStrategy", "WDAY.ETORO", n_trials=1)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)

    trial_evts = _events_of(handler.events, "optimizer_trial_completed")
    assert trial_evts
    assert "backtest_ms" in trial_evts[-1]
    assert isinstance(trial_evts[-1]["backtest_ms"], int)


# ── 3. optimize_symbol emittiert optimizer_study_completed ────────────────────
def test_study_summary_emitted(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
        "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05,
                        "total_trades": 22, "total_return": 0.31}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))

    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=2)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)

    summaries = _events_of(handler.events, "optimizer_study_completed")
    assert len(summaries) == 1
    s = summaries[0]
    for key in ("n_trials", "evaluable_trials", "best_value",
                "backtest_ms_total", "backtest_ms_median", "wallclock_s"):
        assert key in s, f"Pflichtfeld '{key}' fehlt"
    assert s["n_trials"] == 2
    assert s["evaluable_trials"] == 2  # beide Trials evaluable (aggregate_winner gesetzt)
    assert s["wallclock_s"] >= 0


# ── 4. run_per_symbol_sweep emittiert sweep_completed ────────────────────────
def test_sweep_summary_emitted(monkeypatch, tmp_path, capsys):
    _GATE_CFG = {"walk_forward": {}, "gate1_buffer_days": 30,
                 "min_bars_per_param": 200, "min_oos_bars_per_fold": 500}
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"p_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})

    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        sweep.run_per_symbol_sweep(
            ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1,
            optimize_symbol=lambda *a, **k: object(),
            confirm=lambda *a, **k: {"promote": False, "status": "X", "symbol_params": {}})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)

    evts = _events_of(handler.events, "sweep_completed")
    assert len(evts) == 1
    assert evts[0]["pairs"] == 2
    assert evts[0]["n_jobs"] == 1
    assert "wallclock_s" in evts[0]
    # menschenlesbare Schlusszeile auf der Konsole
    assert "Sweep fertig" in capsys.readouterr().out
