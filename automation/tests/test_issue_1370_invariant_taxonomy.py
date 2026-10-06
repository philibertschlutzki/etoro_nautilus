"""Issue #1370 (GH #1267, P3) — Invarianten-Taxonomie: ``check_bar_quality`` war doppelt klassifiziert
(``fail_fast_invariants``, aber Strom-Severity ``high`` ⇒ ``check_fail_fast_invariants_are_blocking`` FAIL in
jedem Lauf), und ``check_any_arm_reachability`` (``list[str]``) / ``check_wallclock_budget`` (``bool``) trugen
``@invariant_scope``, lieferten aber kein ``InvariantResult`` und erschienen nach einer Totalabweisung nie im
Strom (``check_invariant_coverage`` FAIL).

Fix: Severity ``blocking`` für ``check_bar_quality``; Umbenennung der Rohfunktionen ohne ``check_``-Präfix
(``any_arm_reachability_violations``, ``wallclock_budget_exceeded``; ebenso ``any_arm_reachability_live_
violations``, ``mandatory_gate_reachability_live_violations``, ``disk_guard.budget_status``,
``enforce_study_coherence_violation_rate``, ``sweep_diagnostics.bar_quality_profile``) plus je ein
``InvariantResult``-Wrapper unter dem alten ``check_*``-Namen; die zwei laufweiten Checks erscheinen genau
einmal je Lauf im Strom — auch ohne ein einziges geplantes Symbol.
"""
from __future__ import annotations

import importlib
import inspect
import json
import logging

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import sweep

_MODULES = (
    "automation.optimizer.invariants", "automation.optimizer.reward", "automation.optimizer.wallclock_guard",
    "automation.optimizer.disk_guard", "automation.optimizer.sweep_diagnostics",
    "automation.optimizer.run_optimization",
)


def _registered_checks():
    for module_name in _MODULES:
        module = importlib.import_module(module_name)
        for name, obj in vars(module).items():
            if (name.startswith("check_") and callable(obj) and getattr(obj, "_invariant_scope", None)
                    and getattr(obj, "__module__", None) == module_name):
                yield module_name, name, obj


def test_registry_every_scoped_check_returns_an_invariant_result():
    offenders = []
    checks = list(_registered_checks())
    assert len(checks) > 100
    for module_name, name, fn in checks:
        annotation = inspect.signature(fn).return_annotation
        label = getattr(annotation, "__name__", annotation)
        if "InvariantResult" not in str(label):
            offenders.append(f"{module_name}.{name} -> {label}")
    assert not offenders, offenders


def test_the_renamed_wrappers_return_invariant_results_at_runtime(tmp_path):
    from automation.optimizer import disk_guard, reward, sweep_diagnostics, wallclock_guard
    from automation.optimizer import run_optimization as ro

    tcfg = {"eligible_requires_any": ["min_win_rate"], "oos_min_win_rate": 0.5}
    results = [
        reward.check_any_arm_reachability(tcfg),
        reward.check_any_arm_reachability_live(tcfg, {"min_win_rate": [0.1] * 20}, n_evaluated=20),
        reward.check_mandatory_gate_reachability_live({}, {}),
        wallclock_guard.check_wallclock_budget(10.0, max_hours=1.0),
        disk_guard.check_budget(tmp_path, budget_gb=10_000, reserve_gb=0.0),
        sweep_diagnostics.check_bar_quality([1.0] * 30, [1.0] * 30, [1.0] * 30),
        ro.check_study_coherence_violation_rate(type("S", (), {"trials": []})(), {}),
    ]
    assert all(isinstance(r, inv.InvariantResult) for r in results)
    assert results[0].passed is False and results[0].actual == {"unreachable_clauses": ["min_win_rate"]}
    assert reward.any_arm_reachability_violations(tcfg) == ["min_win_rate"]
    assert wallclock_guard.wallclock_budget_exceeded(3600.0, max_hours=1.0) is True
    assert results[5].severity == "blocking" and results[5].passed is False
    assert isinstance(sweep_diagnostics.bar_quality_profile([1.0] * 30, [1.0] * 30, [1.0] * 30), dict)


def test_check_bar_quality_is_blocking_in_the_stream_and_in_fail_fast():
    src = open(sweep.__file__, encoding="utf-8").read()
    start = src.index('"name": "check_bar_quality", "check": "check_bar_quality",\n                "passed": _quality["passed"]')
    assert '"severity": "blocking",' in src[start:start + 3000]
    assert '"severity": _quality.get("severity", "high")' not in src
    opt = json.loads(open("automation/config/optimizer.json", encoding="utf-8").read())
    assert "check_bar_quality" in opt["fail_fast_invariants"]
    stream = [{"name": "check_bar_quality", "severity": "blocking", "passed": False}]
    assert inv.check_fail_fast_invariants_are_blocking(
        stream, fail_fast_invariants=opt["fail_fast_invariants"]).passed is True


def _run_total_rejection_sweep(tmp_path, monkeypatch):
    fake_cfg_dir = tmp_path / "automation" / "config"
    fake_cfg_dir.mkdir(parents=True)
    (fake_cfg_dir / "tournament.json").write_text(json.dumps({"eligible_requires_any": []}), "utf-8")
    monkeypatch.setattr(sweep, "config_dir", lambda: fake_cfg_dir)
    events = []
    monkeypatch.setattr(sweep, "emit_execution_event",
                        lambda logger, event_type, payload, level=logging.INFO: events.append((event_type, payload)))
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda: [])
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: {"walk_forward": {}})
    monkeypatch.setattr(sweep, "count_available_bars", lambda syms, **kw: {})
    try:
        sweep.run_per_symbol_sweep(
            ["NonexistentStrategy"], [], optimize_symbol=lambda pair: None, confirm=lambda *a, **k: None,
            run_id="test-1370-empty")
    except Exception:
        pass
    return events


def test_run_level_checks_appear_exactly_once_without_any_planned_symbol(tmp_path, monkeypatch):
    events = _run_total_rejection_sweep(tmp_path, monkeypatch)
    by_name: dict[str, list[dict]] = {}
    for event_type, payload in events:
        if event_type == "INVARIANT_STREAM_RESULT":
            by_name.setdefault(payload.get("name"), []).append(payload)
    for name in ("check_wallclock_budget", "check_any_arm_reachability"):
        assert len(by_name.get(name, [])) == 1, (name, sorted(by_name))
        assert by_name[name][0]["passed"] is True and by_name[name][0]["source"] == "sweep"
        assert by_name[name][0]["scope"] == "global"


@pytest.mark.parametrize("name", ["check_any_arm_reachability", "check_wallclock_budget"])
def test_defined_check_names_still_contain_the_wrapper_names(name):
    from automation.optimizer import report
    assert name in report._all_defined_check_names()
    assert name not in report._DELIBERATELY_UNWIRED_INVARIANT_CHECKS
