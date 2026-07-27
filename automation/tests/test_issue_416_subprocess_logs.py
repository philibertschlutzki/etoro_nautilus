"""Issue #416 — `run_backtest` verschluckte stdout/stderr im Erfolgsfall.

`capture_output=True` + Verwerfen bei `returncode==0` machte die Per-Trial-Diagnostik (Daten-
Fenster, [OOS-Drop]-Trail, Per-Pair-Trade-Counts, Tournament-Rejections) unrekonstruierbar. Fix:
stdout/stderr werden pro Trial nach `trial_dir/logs/` persistiert — auch im Erfolgsfall. Zusaetzlich
traegt das `optimizer_trial_completed`-Event das Daten-Zeitfenster und die rejection_reason.

Voll gemockt (HI-7).
"""
import json
import logging
import types
from pathlib import Path

import pytest

from automation.optimizer import runner
from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config
from automation.optimizer.parsing import parse_tournament


def _make_trial(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    m = {"global_settings": {"catalog_path": "data/nautilus"}}
    mp = tmp_path / "experiment_manifest.json"
    mp.write_text(json.dumps(m), "utf-8")
    return tmp_path, mp


def test_stdout_stderr_persisted_on_success(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def ok_run(argv, **kw):
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(
            returncode=0,
            stdout="📅 Backtest-Zeitfenster: 2024-01-01 bis 2024-06-29 (180.0 Tage)\nfills=42",
            stderr="some warning trail")

    monkeypatch.setattr(runner.subprocess, "run", ok_run)
    out = runner.run_backtest(trial_dir, mp)

    assert out == trial_dir / "tournament_result.json"
    stdout_log = trial_dir / "logs" / "backtest_stdout.log"
    stderr_log = trial_dir / "logs" / "backtest_stderr.log"
    assert stdout_log.exists() and stderr_log.exists()
    assert "Backtest-Zeitfenster" in stdout_log.read_text("utf-8")
    assert "some warning trail" in stderr_log.read_text("utf-8")


def test_crash_path_still_raises_and_prints(tmp_path, monkeypatch, capsys):
    """Regress-Schutz: returncode != 0 ⇒ BacktestRunError + stderr-Ausgabe (Crash-Pfad unveraendert)."""
    trial_dir, mp = _make_trial(tmp_path)

    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="Test error trace"))

    with pytest.raises(runner.BacktestRunError):
        runner.run_backtest(trial_dir, mp)
    out = capsys.readouterr().out
    assert "Test error trace" in out
    # auch im Crash-Fall ist der Output persistiert
    assert (trial_dir / "logs" / "backtest_stderr.log").read_text("utf-8") == "Test error trace"


def test_persist_tolerates_missing_streams(tmp_path, monkeypatch):
    """Mock ohne stdout/stderr-Attribute (SimpleNamespace) darf nicht crashen (getattr-Default)."""
    trial_dir, mp = _make_trial(tmp_path)

    def bare_run(argv, **kw):
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(returncode=0)  # KEIN stdout/stderr

    monkeypatch.setattr(runner.subprocess, "run", bare_run)
    out = runner.run_backtest(trial_dir, mp)
    assert out == trial_dir / "tournament_result.json"
    assert (trial_dir / "logs" / "backtest_stdout.log").read_text("utf-8") == ""


def test_parse_tournament_reads_data_window(tmp_path):
    """parse_tournament hebt den optionalen data_window-Block; fehlt er ⇒ None (None-safe)."""
    p = tmp_path / "tr.json"
    p.write_text(json.dumps({
        "fully_eligible_pairs": 0, "aggregate_winner": None,
        "data_window": {"start": "2024-01-01", "end": "2024-06-29", "days": 180.0}}), "utf-8")
    m = parse_tournament(p)
    assert m.data_window_start == "2024-01-01"
    assert m.data_window_end == "2024-06-29"
    assert m.data_window_days == pytest.approx(180.0)

    p2 = tmp_path / "tr2.json"
    p2.write_text(json.dumps({"fully_eligible_pairs": 0, "aggregate_winner": None}), "utf-8")
    m2 = parse_tournament(p2)
    assert m2.data_window_start is None and m2.data_window_days is None


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


def test_trial_event_carries_data_window_and_rejection(tmp_path, monkeypatch):
    """End-to-end: das Per-Trial-Event traegt data_window_days und rejection_reason."""
    _isolate(monkeypatch, tmp_path)

    def fake_backtest(trial_dir, manifest_path, **_kwargs):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "fully_eligible_pairs": 0, "aggregate_winner": None,
            "full_results": [{"metrics": {"total_trades": 10}}],
            "data_window": {"start": "2024-01-01", "end": "2024-06-29", "days": 180.0}}), "utf-8")
        return out

    monkeypatch.setattr(ro, "run_backtest", fake_backtest)

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

    trial_evts = [e for e in handler.events if e.get("event_type") == "optimizer_trial_completed"]
    assert trial_evts
    e = trial_evts[-1]
    assert e["data_window_days"] == pytest.approx(180.0)
    assert e["data_window_start"] == "2024-01-01"
    assert e["rejection_reason"] == "oos_not_evaluated"  # aggregate_winner null ⇒ IS-Drop
