"""Issue #833 (P1, Katalog #828-#835, GitHub-Issue #751) — der Report entsteht nur am Ende von
main(): ein 62-h-Lauf ohne Abbruch-Artefakt.

Root-Cause: der #742-Report-Aufruf steht in sweep.main() NACH der vollstaendigen Symbol-Schleife.
Jeder Abbruch DAVOR — eine unerwartete Exception, SIGINT/SIGTERM, oder (bereits vor #833 GRACEFUL
behandelt) disk_guard/wallclock_guard — verhinderte bislang jedes Report-Artefakt, obwohl die
bereits exportierten Proposals der abgeschlossenen Symbole durabel auf der Platte liegen
(``proposal_*.json``) und ``report.generate_report_for_run`` (existierte bereits seit #742) genau
daraus rekonstruieren KANN — sie wurde nur nirgends automatisch ausgeloest.

Fix: sweep.main() faengt jede Exception/KeyboardInterrupt um den run_per_symbol_sweep-Aufruf ab,
generiert TROTZDEM einen Report (via generate_report_for_run — die im Speicher gehaltene proposals-
Liste kann bei einer Exception unvollstaendig sein, die Disk-Discovery ist die verlaessliche
Quelle) mit einem passenden run_status, und reicht den urspruenglichen Fehler danach WEITER (das
Artefakt ist ein Nebeneffekt, kein stilles Verschlucken). SIGTERM wird auf denselben
KeyboardInterrupt-Pfad wie SIGINT umgeleitet. --report-only ruft generate_report_for_run direkt auf.

Scope-Entscheidung (bewusst NICHT umgesetzt): die im Issue vorgeschlagenen literalen Datei-Shards
(``data/optimizer/reports/shards/<run_id>/<symbol>.json``) UND ein darauf aufsetzender separater
Shard-Finalizer. Die bereits bestehenden proposal_*.json + die SQLite-Study-Dateien erfuellen
denselben Zweck bereits bit-identisch (generate_report_for_run war schon VOR #833 nachweislich
bit-identisch zu generate_sweep_report, siehe deren Docstrings) — dieses Repos Retention/Purge-
Design (purge_stale_studies.py) loescht SQLite-Studies NUR bei einem expliziten reward_semantics_
version-Bump zwischen Laeufen, nie automatisch waehrend eines laufenden Sweeps. Ein paralleles
Shard-System haette daher kein reales Problem in DIESEM Repo geloest, das nicht schon geloest ist,
bei erheblichem zusaetzlichem Implementierungs-/Testaufwand (inkrementelle cross_study-Aggregate
sind nicht trivial pro Symbol dekomponierbar).
"""
import json
import signal
import time
from pathlib import Path

import pytest

from automation.optimizer import disk_guard, wallclock_guard
from automation.optimizer import report as _report


@pytest.fixture(autouse=True)
def _reset_guards():
    disk_guard.reset_for_tests()
    wallclock_guard.reset_for_tests()
    yield
    disk_guard.reset_for_tests()
    wallclock_guard.reset_for_tests()


# ── report._build_report / generate_sweep_report / generate_report_for_run: neue Felder ────────────
def test_build_report_defaults_to_complete_status():
    report = _report._build_report(
        [], run_id="r1", started_at_utc=None, wallclock_s=None, cli_args=None)
    assert report["run_status"] == "complete"
    assert report["symbols_completed"] is None
    assert report["symbols_planned"] is None


def test_build_report_carries_explicit_abort_status():
    report = _report._build_report(
        [], run_id="r1", started_at_utc=None, wallclock_s=None, cli_args=None,
        run_status="aborted_wallclock", symbols_completed=12, symbols_planned=69)
    assert report["run_status"] == "aborted_wallclock"
    assert report["symbols_completed"] == 12
    assert report["symbols_planned"] == 69


def test_generate_sweep_report_passes_run_status_through(tmp_path):
    out = _report.generate_sweep_report(
        [], run_id="r2", reports_dir=tmp_path, run_status="aborted_disk",
        symbols_completed=3, symbols_planned=10)
    data = json.loads(out.read_text("utf-8"))
    assert data["run_status"] == "aborted_disk"
    assert data["symbols_completed"] == 3
    assert data["symbols_planned"] == 10


def test_generate_report_for_run_discovers_proposals_and_carries_status(tmp_path):
    (tmp_path / "proposal_StratA_AAA.ETORO.json").write_text(
        json.dumps({"symbol": "AAA.ETORO", "strategy": "StratA", "status": "REJECTED"}), "utf-8")
    (tmp_path / "proposal_global.json").write_text(  # kein 'symbol' -> ignoriert
        json.dumps({"strategy": "StratA"}), "utf-8")
    reports_dir = tmp_path / "reports"
    out = _report.generate_report_for_run(
        run_id="r3", proposals_dir=tmp_path, reports_dir=reports_dir,
        run_status="aborted_error", symbols_completed=1, symbols_planned=5)
    data = json.loads(out.read_text("utf-8"))
    assert data["run_status"] == "aborted_error"
    assert len(data["studies"]) == 1
    assert data["studies"][0]["symbol"] == "AAA.ETORO"


# ── sweep.py: Checkpoint traegt symbols_planned ─────────────────────────────────────────────────────
_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _patch_common(monkeypatch, sweep_mod, tmp_path, pairs):
    monkeypatch.setattr(sweep_mod, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep_mod, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep_mod, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep_mod, "WORK", tmp_path)


def test_checkpoint_records_symbols_planned_before_the_first_symbol(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    _patch_common(monkeypatch, sweep, tmp_path, pairs)

    def fake_opt(strategy, symbol, **k):
        # Checkpoint muss VOR dem ersten Symbol bereits existieren.
        checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
        assert checkpoint["symbols_planned"] == 2
        return object()

    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1, run_id="run-plan",
        optimize_symbol=fake_opt,
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
    assert checkpoint["symbols_planned"] == 2
    assert sorted(checkpoint["completed_symbols"]) == ["A.ETORO", "B.ETORO"]


# ── sweep.main(): Abbruch-Resilienz ──────────────────────────────────────────────────────────────
def _isolate_main(monkeypatch, sweep_mod, tmp_path):
    monkeypatch.setattr(sweep_mod, "WORK", tmp_path)
    monkeypatch.setattr(sweep_mod, "config_dir", lambda: tmp_path)
    (tmp_path / "optimizer.json").write_text(json.dumps({}), "utf-8")
    monkeypatch.setattr(_report, "WORK", tmp_path)
    monkeypatch.setattr(_report, "REPORTS_DIR", tmp_path / "reports")


def test_main_generates_a_report_and_reraises_on_unexpected_exception(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    _isolate_main(monkeypatch, sweep, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("simulated crash mid-sweep")
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _boom)

    with pytest.raises(RuntimeError, match="simulated crash mid-sweep"):
        sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--tier", "all"])

    report_path = sweep._LAST_REPORT_PATH
    assert report_path is not None and Path(report_path).exists()
    data = json.loads(Path(report_path).read_text("utf-8"))
    assert data["run_status"] == "aborted_error"


def test_main_generates_a_report_and_reraises_on_keyboard_interrupt(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    _isolate_main(monkeypatch, sweep, tmp_path)

    def _interrupt(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--tier", "all"])

    data = json.loads(Path(sweep._LAST_REPORT_PATH).read_text("utf-8"))
    assert data["run_status"] == "aborted_signal"


def test_main_marks_aborted_wallclock_when_the_guard_fired(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    _isolate_main(monkeypatch, sweep, tmp_path)

    def _fake_sweep(*a, **k):
        wallclock_guard.sweep_wallclock_exceeded.set()
        return []
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _fake_sweep)

    sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--tier", "all"])
    data = json.loads(Path(sweep._LAST_REPORT_PATH).read_text("utf-8"))
    assert data["run_status"] == "aborted_wallclock"


def test_main_marks_aborted_disk_when_the_guard_fired(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    _isolate_main(monkeypatch, sweep, tmp_path)

    def _fake_sweep(*a, **k):
        disk_guard.sweep_abort_requested.set()
        return []
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _fake_sweep)

    sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--tier", "all"])
    data = json.loads(Path(sweep._LAST_REPORT_PATH).read_text("utf-8"))
    assert data["run_status"] == "aborted_disk"


def test_main_normal_completion_is_status_complete(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    _isolate_main(monkeypatch, sweep, tmp_path)
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: [])

    sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--tier", "all"])
    data = json.loads(Path(sweep._LAST_REPORT_PATH).read_text("utf-8"))
    assert data["run_status"] == "complete"


def test_sigterm_is_translated_into_a_keyboard_interrupt_during_the_sweep(monkeypatch, tmp_path):
    """SIGTERM waehrend run_per_symbol_sweep muss denselben aborted_signal-Pfad nehmen wie
    SIGINT/KeyboardInterrupt -- kein sofortiger, unkatalogisierter Prozess-Exit."""
    from automation.optimizer import sweep
    import os

    _isolate_main(monkeypatch, sweep, tmp_path)

    def _send_sigterm_then_hang(*a, **k):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.2)  # dem zugestellten Signal Gelegenheit geben, den Handler auszuloesen
        return []
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _send_sigterm_then_hang)

    with pytest.raises(KeyboardInterrupt):
        sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--tier", "all"])
    data = json.loads(Path(sweep._LAST_REPORT_PATH).read_text("utf-8"))
    assert data["run_status"] == "aborted_signal"


def test_sigterm_handler_is_restored_after_main_returns(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    _isolate_main(monkeypatch, sweep, tmp_path)
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: [])
    prior = signal.getsignal(signal.SIGTERM)
    sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--tier", "all"])
    assert signal.getsignal(signal.SIGTERM) == prior


# ── --report-only ────────────────────────────────────────────────────────────────────────────────
def test_report_only_flag_reconstructs_without_optimizing(monkeypatch, tmp_path):
    from automation.optimizer import sweep

    _isolate_main(monkeypatch, sweep, tmp_path)
    (tmp_path / "proposal_StratA_AAA.ETORO.json").write_text(
        json.dumps({"symbol": "AAA.ETORO", "strategy": "StratA", "status": "REJECTED"}), "utf-8")

    called = []
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: called.append(1) or [])

    out = sweep.main(["--report-only", "some-prior-run"])
    assert called == []  # keine Optimierung ausgeloest
    assert out == []
    data = json.loads(Path(sweep._LAST_REPORT_PATH).read_text("utf-8"))
    assert data["run_id"] == "some-prior-run"
    assert len(data["studies"]) == 1
