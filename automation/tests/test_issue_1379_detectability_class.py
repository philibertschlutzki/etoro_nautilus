"""Issue #1379 (GH #1281, Pitfall #500) — "unterpowert" ist nicht "unerreichbar"."""
import datetime as dt
import json
from pathlib import Path

import pytest

from automation import session_windows as sw
from automation.optimizer import invariants

_BT = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
_WINDOW = sw.resolve_session_window("EQUITY", _BT["session_hours_by_asset_class"])


@pytest.mark.parametrize("t,klass,severity,passed", [
    (202, "unattainable", "blocking", False),
    (77, "unattainable", "blocking", False),
    (294, "underpowered", "high", False),
    (300, "underpowered", "high", False),
    (2124, "certifiable", "blocking", True),
])
def test_classes(t, klass, severity, passed):
    res = invariants.check_promotion_confidence_reachability(t, 0.95)
    assert res.actual["detectability_class"] == klass
    assert (res.severity, res.passed) == (severity, passed)


def test_t77_max_attainable_psr():
    assert invariants.max_attainable_psr(77) == pytest.approx(0.839, abs=1e-3)


def test_deflation_confidence_and_target_are_untouched():
    t = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    assert t["deflation_confidence"] == 0.95 and t["promotion_target_annual_sharpe"] == 1.5


def test_required_holdout_days_for_production_geometry_uses_the_nyse_calendar():
    end = int(dt.datetime(2026, 10, 6, 19, 59, 59, tzinfo=dt.timezone.utc).timestamp() * 1e9)
    res = invariants.check_promotion_confidence_reachability(294, 0.95, session_window=_WINDOW, end_ns=end)
    assert res.actual["required_holdout_days_for_target"] == pytest.approx(440, abs=1)


def test_underpowered_only_run_is_admissible_unattainable_is_not():
    from automation.optimizer import report

    def _check(t):
        res = invariants.check_promotion_confidence_reachability(t, 0.95)
        return {"name": "check_promotion_confidence_reachability", "passed": res.passed,
                "severity": res.severity, "scope": None, "actual": res.actual}

    assert report._compute_decision_admissible([_check(294)]) is True        # underpowered: nur ein Hinweis
    assert report._compute_decision_admissible([_check(202)]) is False       # unattainable: blockierend
    assert report._compute_decision_admissible([_check(2124)]) is True


def test_promotion_record_carries_mds_and_class():
    from automation.optimizer import deployment_gate
    rec = deployment_gate.build_promotion_record_from_proposal(
        {"status": "READY_FOR_PR", "holdout_mds_annual": 4.09, "detectability_class": "underpowered"})
    assert rec["holdout_mds_annual"] == 4.09 and rec["detectability_class"] == "underpowered"


def test_stamp_detectability_sets_user_attrs():
    from automation.optimizer import run_optimization

    class _Study:
        def __init__(self):
            self.user_attrs = {}

        def set_user_attr(self, k, v):
            self.user_attrs[k] = v

    study = _Study()
    end = int(dt.datetime(2026, 10, 6, 19, 59, 59, tzinfo=dt.timezone.utc).timestamp() * 1e9)
    stamp = run_optimization._stamp_detectability(study, Path("automation/config"), "TSLA.ETORO",
                                                  catalog_newest_ns=end)
    assert study.user_attrs["detectability_class"] == "underpowered" == stamp["detectability_class"]
    assert study.user_attrs["holdout_mds_annual"] > 4.0
