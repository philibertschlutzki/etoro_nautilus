"""Issue #933 (Pitfall #306) — bei 143 Symbolen und 142-170h Laufzeit (#931) fand die erste
Invarianten-Auswertung erst am SWEEP-ENDE statt (report._build_report). Ein 5,9 MB Log mit 4318
Zeilen enthielt kein einziges INVARIANT_*-Event, keinen SWEEP_COMPLETED/SWEEP_ABORTED-Abschluss,
keinen Zwischenreport. Fix: INVARIANT_RESULT + SWEEP_PROGRESS je Symbol, SWEEP_COMPLETED/
SWEEP_ABORTED als letzte Zeile jedes Laufs, Report-Datei nach jedem Symbol atomar aktualisiert.
"""
import json

import pytest

from automation.optimizer import sweep
from automation.optimizer import invariants as inv


_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


class _FakeTrial:
    def __init__(self, oos_evaluated=True, oos_eligible=False):
        self.user_attrs = {"oos_evaluated": oos_evaluated,
                           "oos_selection_statistic_available": oos_evaluated,
                           "oos_eligible": oos_eligible}


class _FakeStudy:
    def __init__(self, n_trials=10, n_eligible=2):
        self.trials = [_FakeTrial(oos_eligible=(i < n_eligible)) for i in range(n_trials)]
        self._attrs = {"n_trials_budget": n_trials}
        self.best_value = 1.0
        self.directions = ["maximize"]

    @property
    def user_attrs(self):
        return dict(self._attrs)


def _patch_common(monkeypatch, tmp_path, pairs):
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep, "WORK", tmp_path)


def test_sweep_progress_emitted_once_per_symbol_with_cumulative_counters(monkeypatch, tmp_path):
    symbols = ["A.ETORO", "B.ETORO"]
    pairs = [("S", sym, "OK") for sym in symbols]
    _patch_common(monkeypatch, tmp_path, pairs)

    def fake_opt(strategy, symbol, **k):
        return _FakeStudy(n_trials=10, n_eligible=3)

    def fake_confirm(study, s, sym, gp, **k):
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    events = []
    real_emit = sweep.emit_execution_event

    def _capture(log, event_type, payload, **kw):
        events.append((event_type, payload))
        return real_emit(log, event_type, payload, **kw)

    monkeypatch.setattr(sweep, "emit_execution_event", _capture)
    monkeypatch.setattr(
        "automation.optimizer.report.build_probe_report",
        lambda proposals, **k: {"invariant_checks": []})
    monkeypatch.setattr(
        "automation.optimizer.report.generate_sweep_report",
        lambda proposals, **k: tmp_path / "run_x.json")

    sweep.run_per_symbol_sweep(
        ["S"], symbols, tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )

    progress_events = [p for t, p in events if t == "SWEEP_PROGRESS"]
    assert len(progress_events) == 2
    assert progress_events[0]["symbols_done"] == 1
    assert progress_events[0]["symbols_total"] == 2
    assert progress_events[0]["trials_done"] == 10
    assert progress_events[0]["eligible_total"] == 3
    assert progress_events[1]["symbols_done"] == 2
    assert progress_events[1]["trials_done"] == 20
    assert progress_events[1]["eligible_total"] == 6
    # Kumulativ, nicht je Symbol zurückgesetzt.
    assert progress_events[1]["trials_done"] > progress_events[0]["trials_done"]


def test_invariant_result_emitted_from_the_symbol_local_probe(monkeypatch, tmp_path):
    symbols = ["A.ETORO"]
    pairs = [("S", sym, "OK") for sym in symbols]
    _patch_common(monkeypatch, tmp_path, pairs)

    def fake_opt(strategy, symbol, **k):
        return _FakeStudy()

    def fake_confirm(study, s, sym, gp, **k):
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    events = []
    monkeypatch.setattr(sweep, "emit_execution_event",
                        lambda log, t, p, **kw: events.append((t, p)))

    fake_result = inv.InvariantResult(
        name="check_holding_time_cap", passed=False, expected="<=0.1", actual=0.5,
        detail="offending", severity="blocking")
    _captured_proposals = []

    def _fake_probe(proposals, **k):
        _captured_proposals.append(list(proposals))
        return {"invariant_checks": [fake_result.to_dict()]}

    monkeypatch.setattr("automation.optimizer.report.build_probe_report", _fake_probe)
    monkeypatch.setattr(
        "automation.optimizer.report.generate_sweep_report",
        lambda proposals, **k: tmp_path / "run_x.json")

    sweep.run_per_symbol_sweep(
        ["S"], symbols, tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )

    invariant_events = [p for t, p in events if t == "INVARIANT_RESULT"]
    assert len(invariant_events) == 1
    assert invariant_events[0]["name"] == "check_holding_time_cap"
    assert invariant_events[0]["status"] == "FAIL"
    assert invariant_events[0]["severity"] == "blocking"
    assert invariant_events[0]["symbol"] == "A.ETORO"
    # Symbol-lokal: nur DIESES Symbols Proposals, nicht die kumulierte Liste.
    assert len(_captured_proposals[0]) == 1


def test_incremental_report_is_written_after_each_symbol(monkeypatch, tmp_path):
    symbols = ["A.ETORO", "B.ETORO"]
    pairs = [("S", sym, "OK") for sym in symbols]
    _patch_common(monkeypatch, tmp_path, pairs)

    def fake_opt(strategy, symbol, **k):
        return _FakeStudy()

    def fake_confirm(study, s, sym, gp, **k):
        return {"promote": False, "status": "REJECTED", "symbol_params": {}}

    monkeypatch.setattr(
        "automation.optimizer.report.build_probe_report",
        lambda proposals, **k: {"invariant_checks": []})
    write_calls = []

    def _fake_generate(proposals, **k):
        write_calls.append((len(proposals), k.get("run_status"), k.get("symbols_completed")))
        return tmp_path / "run_x.json"

    monkeypatch.setattr("automation.optimizer.report.generate_sweep_report", _fake_generate)

    sweep.run_per_symbol_sweep(
        ["S"], symbols, tier="all", n_jobs=1,
        optimize_symbol=fake_opt, confirm=fake_confirm,
    )

    assert len(write_calls) == 2
    assert write_calls[0] == (1, "in_progress", 1)
    assert write_calls[1] == (2, "in_progress", 2)


def test_main_emits_sweep_completed_or_aborted_as_the_last_structured_event():
    """Issue #933 Fix 3 — sweep.main() muss JEDEN Lauf mit einem terminalen strukturierten Event
    beenden, unabhängig vom Abbruchgrund (sauber, SIGINT/SIGTERM, unerwartete Exception) --
    vorher war ein Snapshot eines laufenden Sweeps nur über die Trial-Kadenz im Log von einem
    abgestürzten unterscheidbar. Source-Inspektion statt vollem main()-Aufruf (CLI-Argparse-
    Oberfläche wäre ein eigener, hier nicht nötiger Mocking-Aufwand).

    Issue #1009/#1161 (Katalog #1170) — der Ternary-Ausdruck aus zwei Werten (SWEEP_COMPLETED/
    SWEEP_ABORTED) wurde durch ``_sweep_completion_event(run_status)`` ersetzt (drei moegliche
    Ausgaenge: SWEEP_COMPLETED/SWEEP_FINISHED/SWEEP_ABORTED, siehe dortiger Docstring — ein Lauf,
    der alle Symbole abschloss, aber z. B. eine blockierende Invariante FAILte, emittiert seither
    das neue SWEEP_FINISHED statt des irrefuehrenden SWEEP_ABORTED). Die #933-Kerngarantie (ein
    terminales Event steht NACH dem Report-Schreib-Versuch, VOR dem Weiterreichen der Exception)
    bleibt unveraendert bestehen, nur ueber die neue Funktion statt inline."""
    import inspect

    source = inspect.getsource(sweep.main)
    assert "_sweep_completion_event(run_status)" in source
    # Muss NACH dem Report-Schreib-Versuch stehen, aber VOR dem Weiterreichen der Exception.
    completed_idx = source.index("_sweep_completion_event(run_status)")
    reraise_idx = source.index("if caught_exc is not None:")
    assert completed_idx < reraise_idx

    event_types = {status: sweep._sweep_completion_event(status)[0] for status in (
        "complete", "aborted_invariant", "aborted_wallclock", "aborted_disk", "aborted_signal",
        "aborted_error", "completed_with_quarantine", "completed_with_failures",
        "resumed_complete", "completed_invalid", "complete_with_blocking_invariants")}
    assert set(event_types.values()) <= {"SWEEP_COMPLETED", "SWEEP_FINISHED", "SWEEP_ABORTED"}
