"""Issue #1380 (GH #1282, Pitfall #501) — EINE Registry für per-Symbol-Preflight-Ablehnungen; ``run_status``
und ``decision_admissible`` nutzen dieselbe Funktion."""
import json
import re
from pathlib import Path

import pytest

from automation.optimizer import invariants, report, sweep

_SWEEP_SRC = Path(sweep.__file__).read_text("utf-8")
_REPORT_SRC = Path(report.__file__).read_text("utf-8")


def _check(name, scope, passed=False, severity="blocking"):
    return {"name": name, "check": name, "passed": passed, "severity": severity, "scope": scope,
            "source": "sweep", "expected": "…", "actual": None, "detail": "…"}


def test_registry_maps_every_check_to_its_codes():
    reg = invariants.PER_SYMBOL_PREFLIGHT_REJECTIONS
    assert reg["check_catalog_freshness"] == ("REJECT_DATA_STALE", "REJECT_FUTURE_TICK")
    assert reg["check_catalog_resolution_homogeneity"] == ("REJECT_RESOLUTION_HETEROGENEOUS",)
    assert reg["check_engine_reader_parity"] == ("REJECT_ENGINE_READER_MISMATCH",)
    assert reg["check_tick_population"] == ("REJECT_DATA_UNAVAILABLE",)
    assert reg["check_bar_quality"] == ("REJECT_DATA_DEGENERATE",)


def test_every_reject_code_appended_to_symbols_rejected_is_in_the_registry():
    """Quelltext-Test: jeder an ``_symbols_rejected`` angehängte ``REJECT_*``-Code steht in der Registry."""
    appended = set(re.findall(r'"symbol": _sym,\s*"reason": "(REJECT_[A-Z_]+)"', _SWEEP_SRC))
    appended |= set(re.findall(r'_rejection\["reason"\] = .*?"(REJECT_[A-Z_]+)"', _SWEEP_SRC))
    appended.add("REJECT_FUTURE_TICK")          # dynamisch über _fresh["rejection_code"] (check_catalog_freshness)
    assert 'rejection_code' in _SWEEP_SRC
    assert {"REJECT_DATA_STALE", "REJECT_RESOLUTION_HETEROGENEOUS", "REJECT_ENGINE_READER_MISMATCH",
            "REJECT_DATA_UNAVAILABLE", "REJECT_DATA_DEGENERATE"} <= appended
    assert appended <= invariants.PER_SYMBOL_REJECTION_CODES, appended - invariants.PER_SYMBOL_REJECTION_CODES


def test_there_is_exactly_one_implementation_of_the_exception():
    """Grep-Test: weder ``sweep.py`` noch ``report.py`` halten ein eigenes Check-Set."""
    for src in (_SWEEP_SRC, _REPORT_SRC):
        assert "_per_symbol_preflight_checks" not in src
        assert '"check_tick_population", "check_bar_quality"' not in src
    assert "invariants.is_scoped_preflight_rejection" in _SWEEP_SRC
    assert "_inv.is_scoped_preflight_rejection" in _REPORT_SRC


@pytest.mark.parametrize("name", sorted(invariants.PER_SYMBOL_PREFLIGHT_REJECTIONS))
def test_scoped_rejection_is_exempt_only_when_a_symbol_survived(name):
    c = _check(name, "TSLA.ETORO")
    assert invariants.is_scoped_preflight_rejection(c, any_symbol_survived=True) is True
    assert invariants.is_scoped_preflight_rejection(c, any_symbol_survived=False) is False
    assert invariants.is_scoped_preflight_rejection(_check(name, None), any_symbol_survived=True) is False
    assert invariants.is_scoped_preflight_rejection(_check("check_wallclock_budget", "X"),
                                                    any_symbol_survived=True) is False


def test_three_symbol_run_with_one_heterogeneous_symbol_is_complete_and_admissible(tmp_path):
    checks = [_check("check_catalog_resolution_homogeneity", "GOOGL.ETORO"),
              _check("check_catalog_resolution_homogeneity", "TSLA.ETORO", passed=True),
              _check("check_catalog_resolution_homogeneity", "NVDA.ETORO", passed=True)]
    assert report._compute_decision_admissible(checks, any_symbol_survived=True) is True
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"run_status": "complete", "symbols_planned": 2, "invariant_checks": checks}))
    assert sweep._downgrade_run_status_for_blocking_invariants(path) == "complete"


def test_engine_reader_mismatch_and_future_tick_are_exempt_too(tmp_path):
    checks = [_check("check_engine_reader_parity", "GOOGL.ETORO"), _check("check_catalog_freshness", "NVDA.ETORO")]
    assert report._compute_decision_admissible(checks, any_symbol_survived=True) is True
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"run_status": "complete", "symbols_planned": 1, "invariant_checks": checks}))
    assert sweep._downgrade_run_status_for_blocking_invariants(path) == "complete"


def test_all_symbols_rejected_stays_completed_invalid(tmp_path):
    checks = [_check("check_catalog_resolution_homogeneity", s) for s in ("TSLA.ETORO", "NVDA.ETORO")]
    assert report._compute_decision_admissible(checks, any_symbol_survived=False) is False
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"run_status": "complete", "symbols_planned": 0, "invariant_checks": checks}))
    assert sweep._downgrade_run_status_for_blocking_invariants(path) == "completed_invalid"


def test_unscoped_blocker_still_blocks_even_with_survivors():
    checks = [_check("check_catalog_resolution_homogeneity", None)]
    assert report._compute_decision_admissible(checks, any_symbol_survived=True) is False
