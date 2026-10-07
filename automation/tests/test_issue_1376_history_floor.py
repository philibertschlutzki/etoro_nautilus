"""Issue #1376 (GH #1278, Pitfall #497) — EINE Historien-Schwelle für Auflösungs-Check, ETA und Gate 1."""
import datetime as dt
import json
from pathlib import Path

import pytest

from automation.optimizer import gate, invariants, sweep

_WF = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))["walk_forward"]
_FLOOR = gate.history_floor_days(_WF)
_UTC = dt.timezone.utc


def _gate1_cfg(buffer_days=30):
    return {"walk_forward": _WF, "gate1_buffer_days": buffer_days, "min_bars_per_param": 1,
            "min_oos_bars_per_fold": 1}


def test_production_floor_is_444_not_474():
    assert _FLOOR == 444
    assert gate.gate1_history_floor_days(_WF) == 444.0


@pytest.mark.parametrize("span_days", list(range(400, 501, 7)) + [443, 444, 445])
def test_gate1_pass_iff_span_reaches_floor_iff_resolution_check_passes(span_days):
    """Property: Gate 1 (a) PASS ⇔ s ≥ Floor ⇔ Auflösungs-Check PASS (lückenlose synthetische Spanne)."""
    bars = int(span_days * 24)
    gate1_ok, _why = gate.is_symbol_tunable("A.ETORO", 1, available_bars=bars, config=_gate1_cfg())
    resolution_ok = float(span_days) >= float(sweep_floor())
    assert gate1_ok == (span_days >= _FLOOR) == resolution_ok


def sweep_floor():
    return sweep.history_floor_days(_WF)


def test_buffer_is_not_part_of_gate1():
    for buf in (0, 30, 90):
        ok, why = gate.is_symbol_tunable("A.ETORO", 1, available_bars=444 * 24, config=_gate1_cfg(buf))
        assert ok and why == "OK"


def test_eta_is_first_day_all_span_gates_pass():
    """Simulierte Vorwärtsfortschreibung (da16c310: 96,04 d vorhanden, 1 Tag je Tag)."""
    now = dt.datetime(2026, 10, 6, tzinfo=_UTC)
    span = 96.04
    eta = sweep.check_data_depth_eta(span, _FLOOR, freshness_passed=True, now=now)["eta_utc"]
    assert eta == "2027-09-19"
    eta_day = dt.datetime.strptime(eta, "%Y-%m-%d").replace(tzinfo=_UTC)
    days_elapsed = (eta_day - now).days
    ok_on_eta, _ = gate.is_symbol_tunable("A.ETORO", 1, available_bars=int((span + days_elapsed) * 24),
                                         config=_gate1_cfg())
    ok_day_before, _ = gate.is_symbol_tunable("A.ETORO", 1, available_bars=int((span + days_elapsed - 1) * 24),
                                             config=_gate1_cfg())
    assert ok_on_eta and not ok_day_before        # an der ETA besteht Gate 1; einen Tag vorher nicht


def test_floor_coherence_invariant_passes_for_production_geometry():
    res = invariants.check_history_floor_coherence(_WF)
    assert res.passed is True and res.severity == "blocking"


def test_floor_coherence_invariant_flags_two_thresholds():
    res = invariants.check_history_floor_coherence(_WF, gate1_floor_days=474.0)
    assert res.passed is False and "474" in res.detail


def test_floor_coherence_inconclusive_without_geometry():
    assert invariants.check_history_floor_coherence(None).passed is None
