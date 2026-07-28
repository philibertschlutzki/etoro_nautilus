"""Issue #797 — konfigurierbare Subprozess-Log-Persistenz (on_failure | always | never).

Root-Cause: runner._persist_subprocess_logs (#416) schrieb stdout/stderr IMMER vollstaendig,
auch im Erfolgsfall — seit #794 (kontinuierliche Retention) wird trial_dir bei Erfolg Sekunden
spaeter ohnehin geloescht, sodass die Erfolgsfall-Persistenz nur noch I/O-Kosten ohne Nutzen war
(einer der Haupttreiber der 582 KB/Trial). Diese Tests decken die drei Policy-Werte und die
Tail-Kappung ab.
"""
import json
import types

import pytest

from automation.optimizer import runner


def _make_trial(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    mp = tmp_path / "experiment_manifest.json"
    mp.write_text(json.dumps({"global_settings": {"catalog_path": "data/nautilus"}}), "utf-8")
    return tmp_path, mp


def test_default_policy_writes_nothing_on_success(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def ok_run(argv, **kw):
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(returncode=0, stdout="all good", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", ok_run)
    runner.run_backtest(trial_dir, mp)

    logs = list((trial_dir / "logs").iterdir())
    assert logs == [], f"Erfolgsfall mit Default-Policy darf keine Logs schreiben, gefunden: {logs}"


def test_failed_trial_always_persists_full_stderr_regardless_of_policy(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)
    big_stderr = "boom\n" * 5000  # deutlich > tail_bytes, damit die Full-vs-Tail-Unterscheidung greift

    def crash_run(argv, **kw):
        return types.SimpleNamespace(returncode=1, stdout="", stderr=big_stderr)

    monkeypatch.setattr(runner.subprocess, "run", crash_run)
    with pytest.raises(runner.BacktestRunError):
        runner.run_backtest(trial_dir, mp, subprocess_log_policy="on_failure",
                            subprocess_log_tail_bytes=100)

    stderr_log = (trial_dir / "logs" / "backtest_stderr.log").read_text("utf-8")
    assert stderr_log == big_stderr  # voll, NICHT gekappt


def test_never_policy_suppresses_even_the_failure_path(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def crash_run(argv, **kw):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(runner.subprocess, "run", crash_run)
    with pytest.raises(runner.BacktestRunError):
        runner.run_backtest(trial_dir, mp, subprocess_log_policy="never")

    assert not (trial_dir / "logs" / "backtest_stderr.log").exists()


def test_always_policy_truncates_large_success_output_with_marker(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)
    one_mb_stdout = "x" * (1024 * 1024)
    tail_bytes = 32768

    def ok_run(argv, **kw):
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(returncode=0, stdout=one_mb_stdout, stderr="")

    monkeypatch.setattr(runner.subprocess, "run", ok_run)
    runner.run_backtest(trial_dir, mp, subprocess_log_policy="always",
                        subprocess_log_tail_bytes=tail_bytes)

    stdout_log = (trial_dir / "logs" / "backtest_stdout.log").read_text("utf-8")
    assert len(stdout_log.encode("utf-8")) <= tail_bytes + 64
    assert stdout_log.startswith("[TRUNCATED: ")


def test_always_policy_no_marker_when_content_already_fits(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)

    def ok_run(argv, **kw):
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")
        return types.SimpleNamespace(returncode=0, stdout="short output", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", ok_run)
    runner.run_backtest(trial_dir, mp, subprocess_log_policy="always",
                        subprocess_log_tail_bytes=32768)

    stdout_log = (trial_dir / "logs" / "backtest_stdout.log").read_text("utf-8")
    assert stdout_log == "short output"  # kein Marker ohne tatsaechliche Truncation


def test_tail_with_marker_unit():
    from automation.optimizer.runner import _tail_with_marker
    text = "a" * 100
    result = _tail_with_marker(text, tail_bytes=10)
    assert result.startswith("[TRUNCATED: 90 Bytes verworfen]\n")
    assert result.endswith("a" * 10)

    untouched = _tail_with_marker("short", tail_bytes=100)
    assert untouched == "short"
