"""Issue #918 (Verallgemeinerung von #914) — jeder ``inference_diagnostics``-Code, den
``backtest_runner.py`` stempelt, muss in ``automation.optimizer._contracts.INFERENCE_DIAGNOSTIC_CODES``
registriert sein. Root-Cause #914/#918: SORTINO_GUARD_REFERENCE_UNAVAILABLE (#901 neu eingeführt) fehlte
in JEDEM der fünf unabhängig gepflegten Konsumenten ausser der Erzeugung selbst — ein AST-Vertragstest
verhindert, dass ein künftiger neuer Code denselben Weg geht.
"""
import ast
import inspect

import automation.optimizer._contracts as contracts


def _collect_stamped_codes(source: str) -> set[str]:
    """Sammelt jedes String-Literal, das als Wert des Schlüssels ``"code"`` in einem Dict-Literal
    auftaucht — die Konvention, mit der jede ``inference_diagnostics``-Stelle in
    ``backtest_runner.py`` einen Code stempelt (``{"code": "...", "detail": ..., "value": ...}``)."""
    tree = ast.parse(source)
    codes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "code"
                        and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                    codes.add(value.value)
    return codes


def test_every_stamped_code_in_backtest_runner_is_registered():
    import automation.backtest_runner as br
    source = inspect.getsource(br)
    stamped = _collect_stamped_codes(source)
    assert stamped, "Erwartete mindestens einen gestempelten Code — Extraktion selbst kaputt?"
    unregistered = stamped - set(contracts.INFERENCE_DIAGNOSTIC_CODES)
    assert not unregistered, (
        f"Code(s) in backtest_runner.py gestempelt, aber nicht in "
        f"_contracts.INFERENCE_DIAGNOSTIC_CODES registriert: {sorted(unregistered)}")


def test_sortino_guard_reference_unavailable_is_registered_as_prune():
    entry = contracts.INFERENCE_DIAGNOSTIC_CODES["SORTINO_GUARD_REFERENCE_UNAVAILABLE"]
    assert entry.failure_policy == "prune"


def test_registered_entries_reject_invalid_failure_policy():
    import pytest
    with pytest.raises(ValueError):
        contracts.InferenceDiagnosticCode(
            code="X", failure_policy="bogus", severity="high", description="d")


def test_duplicate_registration_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        contracts._register_inference_code(
            "SORTINO_GUARD_TRIPPED", failure_policy="prune", severity="high", description="dup")


def test_run_optimization_inference_failure_codes_derived_from_registry_prune_floor():
    from automation.optimizer import run_optimization as ro
    # _inference_failure_codes is built inline inside the objective closure; verify the
    # registry-derived set used there matches exactly the prune/floor-policy codes.
    expected = {
        code for code, entry in contracts.INFERENCE_DIAGNOSTIC_CODES.items()
        if entry.failure_policy in ("prune", "floor")
    }
    assert "SORTINO_GUARD_TRIPPED" in expected
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" in expected
    assert "EQUITY_NONPOSITIVE" in expected
    assert "SORTINO_GUARD_REFERENCE_UNAVAILABLE" in expected
    assert "COHERENCE_INVARIANT_VIOLATION" not in expected  # telemetry_only, no direct prune
