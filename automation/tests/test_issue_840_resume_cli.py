"""Issue #840 (P0) — Der Resume-Mechanismus hat keinen CLI-Einstieg (6. Wiederkehr Pitfall #237).

Der #799-Fortschritts-Checkpoint ist vollständig implementiert und getestet
(test_issue_799_per_symbol_transaction.py), aber `sweep.main()` hatte weder `--resume` noch
`--run-id` — jeder Aufruf erzeugte über `default_run_id()` eine NEUE run_id, wodurch der
Checkpoint bei jedem Absturz/Laufzeit-Budget-Abbruch nutzlos war.

Akzeptanzkriterien:
- AK-1: Sweep über 5 Symbole, künstlicher Abbruch nach Symbol 3, --resume ⇒ die Symbole 1–3 werden
  nicht erneut optimiert, 4–5 laufen, symbols_completed == 5.
- AK-2: --resume ohne existierenden Checkpoint ⇒ Exit-Code 2, Meldung nennt den Pfad.
- AK-3: --resume mit abweichendem strategies_fingerprint ⇒ Exit-Code 2, Meldung nennt beide
  Fingerprints.
- AK-4: Ein fortgesetzter Lauf erzeugt einen #742-Report mit run_status='resumed_complete'.
- AK-5: Die bestehenden Determinismus-/Familien-Tests (#799/#747/#755/#652) bleiben grün (siehe
  separater Testlauf, keine Assertion hier).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import disk_guard, wallclock_guard
from automation.optimizer import report as _report
from automation.optimizer import sweep


@pytest.fixture(autouse=True)
def _reset_guards():
    disk_guard.reset_for_tests()
    wallclock_guard.reset_for_tests()
    sweep.sweep_fail_fast_invariant = None
    yield
    disk_guard.reset_for_tests()
    wallclock_guard.reset_for_tests()
    sweep.sweep_fail_fast_invariant = None


def _isolate_main(monkeypatch, tmp_path, optimizer_cfg=None):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    (tmp_path / "optimizer.json").write_text(json.dumps(optimizer_cfg or {}), "utf-8")
    monkeypatch.setattr(_report, "WORK", tmp_path)
    monkeypatch.setattr(_report, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(_report, "RUN_FINGERPRINT_INDEX_PATH", Path(tmp_path / "reports") / "run_fingerprints.jsonl")


# ── _strategies_fingerprint: reine Funktion ──────────────────────────────────────────────────────
def test_fingerprint_is_deterministic_and_order_independent():
    f1 = sweep._strategies_fingerprint(["A", "B"], {"reward_semantics_version": 18})
    f2 = sweep._strategies_fingerprint(["B", "A"], {"reward_semantics_version": 18})
    assert f1 == f2


def test_fingerprint_changes_with_reward_semantics_version():
    f1 = sweep._strategies_fingerprint(["A"], {"reward_semantics_version": 18})
    f2 = sweep._strategies_fingerprint(["A"], {"reward_semantics_version": 19})
    assert f1 != f2


def test_fingerprint_changes_with_strategy_set():
    f1 = sweep._strategies_fingerprint(["A"], {})
    f2 = sweep._strategies_fingerprint(["A", "B"], {})
    assert f1 != f2


# ── _wallclock_forecast: reine Funktion ──────────────────────────────────────────────────────────
def test_wallclock_forecast_no_budget_is_always_fully_reachable():
    result = sweep._wallclock_forecast(rate_s_per_symbol=1000.0, n_planned=143, max_wallclock_h=None)
    assert result["reachable_symbols"] == 143
    assert result["shortfall"] == 0


def test_wallclock_forecast_reproduces_katalog_example():
    # 24.7 min/Symbol * 143 Symbole = 58.8h; Budget 24h -> ~58 Symbole erreichbar, 85 nicht.
    rate_s = 24.7 * 60.0
    result = sweep._wallclock_forecast(rate_s_per_symbol=rate_s, n_planned=143, max_wallclock_h=24.0)
    assert result["reachable_symbols"] == 58
    assert result["shortfall"] == 85


def test_wallclock_forecast_sufficient_budget_has_no_shortfall():
    rate_s = 24.7 * 60.0
    result = sweep._wallclock_forecast(rate_s_per_symbol=rate_s, n_planned=143, max_wallclock_h=72.0)
    assert result["shortfall"] == 0
    assert result["reachable_symbols"] >= 143


# ── run_per_symbol_sweep: Checkpoint traegt strategies_fingerprint ───────────────────────────────
def test_checkpoint_carries_strategies_fingerprint(monkeypatch, tmp_path):
    pairs = [("S", "A.ETORO", "OK"), ("S", "B.ETORO", "OK")]
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {
        "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
        "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
    })
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    (tmp_path / "optimizer.json").write_text(json.dumps({"reward_semantics_version": 18}), "utf-8")

    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO"], tier="all", n_jobs=1, run_id="run-fp",
        optimize_symbol=lambda strategy, symbol, **k: object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
    expected = sweep._strategies_fingerprint(["S"], {"reward_semantics_version": 18})
    assert checkpoint["strategies_fingerprint"] == expected


# ── main(): --resume / --run-id ───────────────────────────────────────────────────────────────────
def test_resume_without_checkpoint_exits_with_code_2(monkeypatch, tmp_path):
    _isolate_main(monkeypatch, tmp_path)
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: pytest.fail("should not run"))

    with pytest.raises(SystemExit) as exc_info:
        sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--resume"])
    assert exc_info.value.code == 2


def test_resume_bare_uses_checkpoints_run_id(monkeypatch, tmp_path):
    _isolate_main(monkeypatch, tmp_path)
    (tmp_path / "sweep_progress.json").write_text(json.dumps({
        "run_id": "prior-run-42", "completed_symbols": ["A.ETORO"], "symbols_planned": 2,
    }), "utf-8")

    captured = {}

    def _fake_sweep(*a, **k):
        captured["run_id"] = k.get("run_id")
        return []
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _fake_sweep)

    sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--resume"])
    assert captured["run_id"] == "prior-run-42"


def test_resume_with_explicit_run_id_mismatch_exits_with_code_2(monkeypatch, tmp_path):
    _isolate_main(monkeypatch, tmp_path)
    (tmp_path / "sweep_progress.json").write_text(json.dumps({
        "run_id": "actual-run", "completed_symbols": [], "symbols_planned": 2,
    }), "utf-8")
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: pytest.fail("should not run"))

    with pytest.raises(SystemExit) as exc_info:
        sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--resume", "some-other-run"])
    assert exc_info.value.code == 2


def test_run_id_flag_sets_run_id_explicitly(monkeypatch, tmp_path):
    _isolate_main(monkeypatch, tmp_path)
    captured = {}

    def _fake_sweep(*a, **k):
        captured["run_id"] = k.get("run_id")
        return []
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _fake_sweep)

    sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--run-id", "my-explicit-run"])
    assert captured["run_id"] == "my-explicit-run"


def test_resume_and_run_id_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        sweep.main(["--strategies", "S", "--run-id", "a", "--resume", "b"])


def test_resume_rejects_mismatched_strategies_fingerprint(monkeypatch, tmp_path, capsys):
    _isolate_main(monkeypatch, tmp_path, optimizer_cfg={"reward_semantics_version": 19})
    stale_fingerprint = sweep._strategies_fingerprint(["S"], {"reward_semantics_version": 18})
    (tmp_path / "sweep_progress.json").write_text(json.dumps({
        "run_id": "prior-run", "completed_symbols": [], "symbols_planned": 2,
        "strategies_fingerprint": stale_fingerprint,
    }), "utf-8")
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: pytest.fail("should not run"))

    with pytest.raises(SystemExit) as exc_info:
        sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--resume"])
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert stale_fingerprint in err


def test_resume_allows_missing_fingerprint_pre_840_checkpoint(monkeypatch, tmp_path):
    """Ein Pre-#840-Checkpoint ohne strategies_fingerprint ist nicht prüfbar ⇒ fail-open (mit
    WARNUNG), nicht hart abgelehnt — sonst wäre jeder Alt-Checkpoint sofort wertlos."""
    _isolate_main(monkeypatch, tmp_path)
    (tmp_path / "sweep_progress.json").write_text(json.dumps({
        "run_id": "prior-run", "completed_symbols": [], "symbols_planned": 2,
    }), "utf-8")
    captured = {}

    def _fake_sweep(*a, **k):
        captured["called"] = True
        return []
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", _fake_sweep)

    sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--resume"])
    assert captured.get("called") is True


def test_resumed_run_reports_resumed_complete_status(monkeypatch, tmp_path):
    _isolate_main(monkeypatch, tmp_path)
    (tmp_path / "sweep_progress.json").write_text(json.dumps({
        "run_id": "prior-run", "completed_symbols": [], "symbols_planned": 2,
    }), "utf-8")
    monkeypatch.setattr(sweep, "run_per_symbol_sweep", lambda *a, **k: [])

    sweep.main(["--strategies", "S", "--symbols", "A.ETORO", "--resume"])
    data = json.loads(Path(sweep._LAST_REPORT_PATH).read_text("utf-8"))
    assert data["run_status"] == "resumed_complete"


# ── Integrationstest AK-1: --resume ueberspringt bereits abgeschlossene Symbole ──────────────────
def test_ak1_resume_skips_completed_symbols_across_two_run_per_symbol_sweep_calls(monkeypatch, tmp_path):
    pairs = [("S", sym, "OK") for sym in ["A.ETORO", "B.ETORO", "C.ETORO", "D.ETORO", "E.ETORO"]]
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(sweep, "export_symbol_proposal",
                        lambda study, s, sym, prom: tmp_path / f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {
        "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
        "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
    })
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "config_dir", lambda: tmp_path)
    (tmp_path / "optimizer.json").write_text(json.dumps({}), "utf-8")

    optimize_calls = []

    def _fake_optimize(strategy, symbol, **k):
        optimize_calls.append(symbol)
        if symbol in ("D.ETORO", "E.ETORO"):
            # Simuliert einen Absturz NACH Symbol C (Symbole 4/5 wurden nie gestartet).
            raise RuntimeError("simulated crash boundary — should never fire in the first call")
        return object()

    # Erster Lauf: bricht nach den ersten 3 Symbolen ab (enumerate liefert nur die ersten 3 Paare).
    first_pairs = pairs[:3]
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: first_pairs)
    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO", "C.ETORO"], tier="all", n_jobs=1, run_id="resume-ak1",
        optimize_symbol=lambda strategy, symbol, **k: optimize_calls.append(symbol) or object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert optimize_calls == ["A.ETORO", "B.ETORO", "C.ETORO"]

    # Zweiter Lauf ("--resume"): dieselbe run_id, volle Paarliste (alle 5 Symbole enumeriert) —
    # A/B/C muessen NICHT erneut optimiert werden.
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: pairs)
    optimize_calls.clear()
    sweep.run_per_symbol_sweep(
        ["S"], ["A.ETORO", "B.ETORO", "C.ETORO", "D.ETORO", "E.ETORO"], tier="all", n_jobs=1,
        run_id="resume-ak1",
        optimize_symbol=lambda strategy, symbol, **k: optimize_calls.append(symbol) or object(),
        confirm=lambda *a, **k: {"promote": False, "status": "REJECTED", "symbol_params": {}},
    )
    assert optimize_calls == ["D.ETORO", "E.ETORO"]

    checkpoint = json.loads((tmp_path / "sweep_progress.json").read_text("utf-8"))
    assert sorted(checkpoint["completed_symbols"]) == [
        "A.ETORO", "B.ETORO", "C.ETORO", "D.ETORO", "E.ETORO"]
