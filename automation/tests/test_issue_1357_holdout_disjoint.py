"""Issue #1357 (GH #1253, P0) — Holdout-Kontamination: die Selektion endete 45 Tage vor Datenende
(vier Literale in ``run_optimization.py``), der Confirm-Holdout umfasste 60 Tage (``backtest.json``) —
15 Holdout-Tage (25 %) waren Selektionsdaten, und zwischen Selektionsende und Holdout-Beginn lag kein
Embargo.

Fix: ``holdout_days`` nur noch aus der Config (fehlender Key wirft), ``walk_forward.holdout_embargo_days``
(Default 3, Untergrenze ``ceil(max_bars_in_trade_cap / BARS_PER_TRADING_DAY) + 1``), blockierende Invariante
``check_selection_holdout_disjoint`` (Run- und Study-Ebene), Stempel im Study/Proposal, Deployment-Klausel
``holdout_disjoint``.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
from pathlib import Path

import pytest

from automation.optimizer import gate
from automation.optimizer import invariants as inv
from automation.optimizer import trial_config as tc
from automation.optimizer.trial_config import config_dir

_REPO = Path(__file__).resolve().parents[2]
_NOW = dt.datetime(2026, 10, 4, 9, 0, tzinfo=dt.timezone.utc)
_CATALOG_END_NS = int(dt.datetime(2026, 10, 2, 15, 0, tzinfo=dt.timezone.utc).timestamp()) * 1_000_000_000


def _bt() -> dict:
    return json.loads((config_dir() / "backtest.json").read_text("utf-8"))


def test_shipped_config_selection_ends_embargo_days_before_the_holdout():
    g = tc.selection_holdout_geometry(_bt(), now=_NOW, catalog_newest_ns=_CATALOG_END_NS)
    assert g["holdout_days"] == 60 and g["holdout_embargo_days"] == 3
    assert (g["holdout_start_utc"], g["holdout_end_utc"]) == ("2026-08-03T00:00:00Z", "2026-10-02T00:00:00Z")
    assert g["selection_end_utc"] == "2026-07-31T00:00:00Z"
    assert g["holdout_overlap_days"] == 0
    assert g["gap_days"] == g["holdout_embargo_days"]


@pytest.mark.parametrize("holdout_days", [30, 45, 60, 90])
def test_disjoint_for_every_holdout_length(holdout_days):
    bt = copy.deepcopy(_bt())
    bt["walk_forward"]["holdout_days"] = holdout_days
    bt["walk_forward"]["data_history_days"] = 10_000
    g = tc.selection_holdout_geometry(bt, now=_NOW, catalog_newest_ns=_CATALOG_END_NS)
    assert g["holdout_overlap_days"] == 0
    assert g["gap_days"] == bt["walk_forward"]["holdout_embargo_days"]
    assert inv.check_selection_holdout_disjoint(g).passed is True


def test_the_old_45_day_selection_overlapped_15_days_and_fails_the_invariant():
    """Rekonstruktion des Defekts: Selektion mit 45 Tagen gegen einen 60-Tage-Holdout."""
    bt = _bt()
    sel = tc._wf_settings_from_bt_data(bt, holdout_days=45, holdout_embargo_days=0)
    _, selection_end = tc.compute_walk_forward_window(
        now=_NOW, holdout_days=sel["holdout_days"], is_window_days=sel["is_window_days"],
        oos_window_days=sel["oos_window_days"], n_folds=sel["splits"],
        embargo_period_days=sel["embargo_period_days"], catalog_newest_ns=_CATALOG_END_NS)
    holdout = tc.selection_holdout_geometry(bt, now=_NOW, catalog_newest_ns=_CATALOG_END_NS)
    holdout_start = dt.datetime.fromisoformat(holdout["holdout_start_utc"].replace("Z", "+00:00"))
    assert (selection_end - holdout_start).days == 15
    result = inv.check_selection_holdout_disjoint({
        "selection_end_ns": int(selection_end.timestamp()) * 1_000_000_000,
        "holdout_start_ns": holdout["holdout_start_ns"], "holdout_embargo_days": 3,
        "selection_end_utc": selection_end.isoformat(), "holdout_start_utc": holdout["holdout_start_utc"],
        "holdout_overlap_days": 15})
    assert result.passed is False and result.severity == "blocking" and result.provenance


def test_missing_holdout_days_raises_instead_of_defaulting(tmp_path):
    bt = copy.deepcopy(_bt())
    del bt["walk_forward"]["holdout_days"]
    (tmp_path / "backtest.json").write_text(json.dumps(bt), "utf-8")
    with pytest.raises(tc.HoldoutConfigError):
        tc.resolve_holdout_days(tmp_path)
    with pytest.raises(tc.HoldoutConfigError):
        tc._wf_settings_from_bt_data(bt)


def test_holdout_embargo_has_a_floor_and_a_default():
    assert tc.holdout_embargo_floor_days() == 2      # ceil(7 / 7) + 1
    bt = copy.deepcopy(_bt())
    bt["walk_forward"]["holdout_embargo_days"] = 1
    with pytest.raises(tc.HoldoutConfigError, match="Untergrenze"):
        tc._wf_settings_from_bt_data(bt)
    del bt["walk_forward"]["holdout_embargo_days"]
    assert tc._wf_settings_from_bt_data(bt)["holdout_embargo_days"] == tc.HOLDOUT_EMBARGO_DAYS_DEFAULT == 3


def test_required_span_days_adds_the_holdout_embargo():
    wf = _bt()["walk_forward"]
    assert gate.required_span_days(wf) == 180 + 21 + 4 * 45 + 60 + 3
    assert gate.required_span_days({k: v for k, v in wf.items() if k != "holdout_embargo_days"}) == 180 + 21 + 4 * 45 + 60


def test_confirm_trial_kwargs_come_from_the_config():
    kw = tc.confirm_trial_kwargs(config_dir())
    assert kw == {"holdout_days": 0, "n_folds": 1, "oos_window_days_override": 60, "holdout_embargo_days": 0}


def test_build_trial_selection_window_ends_before_the_holdout(tmp_path, monkeypatch):
    """``build_trial`` ohne ``holdout_days``-Argument (der Selektionspfad) liest Holdout + Embargo aus der
    Config — das Manifest-Fensterende ist das Selektionsende der Geometrie."""
    monkeypatch.setattr(tc, "WORK", tmp_path)
    _, manifest_path = tc.build_trial(
        "SmaCrossoverStrategy", {}, study_name="s1357", trial_number=0, seed=1, now=_NOW,
        n_folds=4, catalog_newest_ns=_CATALOG_END_NS)
    manifest = json.loads(Path(manifest_path).read_text("utf-8"))
    g = tc.selection_holdout_geometry(_bt(), now=_NOW, catalog_newest_ns=_CATALOG_END_NS)
    assert manifest["global_settings"]["end_time"] == g["selection_end_utc"]
    assert manifest["global_settings"]["walk_forward"]["holdout_embargo_days"] == 3


def test_study_level_invariant_over_records():
    good = {"strategy": "A", "symbol": "X", "selection_end_ns": 0, "holdout_start_ns": 3 * 86_400_000_000_000,
            "holdout_embargo_days": 3}
    bad = {"strategy": "B", "symbol": "Y", "selection_end_ns": 0, "holdout_start_ns": 2 * 86_400_000_000_000,
           "holdout_embargo_days": 3}
    assert inv.check_selection_holdout_disjoint([good, {"strategy": "C"}]).passed is True
    res = inv.check_selection_holdout_disjoint([good, bad])
    assert res.passed is False and "B/Y" in res.actual
    empty = inv.check_selection_holdout_disjoint([{"strategy": "C"}])
    assert empty.passed is True and empty.inconclusive is True       # keine Evidenz ⇒ kein Abbruch


def test_invariant_is_wired_on_run_and_study_level():
    sweep_src = (_REPO / "automation/optimizer/sweep.py").read_text("utf-8")
    report_src = (_REPO / "automation/optimizer/report.py").read_text("utf-8")
    assert "invariants.check_selection_holdout_disjoint(_geometry)" in sweep_src
    assert "_inv.check_selection_holdout_disjoint(studies_out)" in report_src


def test_stamp_helper_writes_the_geometry_into_study_user_attrs():
    from automation.optimizer.run_optimization import _stamp_selection_holdout_geometry

    class _Study:
        def __init__(self):
            self.user_attrs = {}

        def set_user_attr(self, k, v):
            self.user_attrs[k] = v

    study = _Study()
    _stamp_selection_holdout_geometry(study, config_dir(), catalog_newest_ns=_CATALOG_END_NS, now=_NOW)
    assert study.user_attrs["holdout_overlap_days"] == 0
    assert study.user_attrs["selection_end_utc"] == "2026-07-31T00:00:00Z"
    assert study.user_attrs["holdout_start_utc"] == "2026-08-03T00:00:00Z"


# ─── Deployment-Klausel ───────────────────────────────────────────────────────────────

def test_holdout_disjoint_is_a_deployment_clause_and_fails_closed():
    from automation.optimizer import deployment_gate as dg

    assert "holdout_disjoint" in dg.DEPLOYMENT_CLAUSES
    ok = {"selection_end_utc": "2026-07-31T00:00:00Z", "holdout_start_utc": "2026-08-03T00:00:00Z",
          "holdout_embargo_days": 3, "holdout_overlap_days": 0}
    assert dg._clause_holdout_disjoint(ok) is True
    assert dg._clause_holdout_disjoint({**ok, "holdout_overlap_days": 15}) is False
    assert dg._clause_holdout_disjoint({**ok, "holdout_start_utc": "2026-08-02T00:00:00Z"}) is False
    assert dg._clause_holdout_disjoint({k: v for k, v in ok.items() if k != "holdout_overlap_days"}) is None
    assert dg._clause_holdout_disjoint(None) is None


def test_proposal_fields_reach_the_promotion_record():
    from automation.optimizer import deployment_gate as dg

    record = dg.build_promotion_record_from_proposal({
        "status": "READY_FOR_PR", "selection_end_utc": "2026-07-31T00:00:00Z",
        "holdout_start_utc": "2026-08-03T00:00:00Z", "holdout_embargo_days": 3, "holdout_overlap_days": 0,
        "holdout": {"symbol": {}}})
    assert record["holdout_overlap_days"] == 0 and dg._clause_holdout_disjoint(record) is True


def test_every_proposal_carries_holdout_overlap_days():
    src = (_REPO / "automation/optimizer/confirm.py").read_text("utf-8")
    body = src[src.index("def export_symbol_proposal"):src.index("def export_no_viable_proposal")]
    assert '"holdout_overlap_days"' in body and '"selection_end_utc"' in body and '"holdout_start_utc"' in body


def test_deployment_gate_completeness_covers_the_new_clause():
    from automation.optimizer import deployment_gate as dg

    clause_results = {c: True for c in dg.DEPLOYMENT_CLAUSES}
    winners = {"X.ETORO": {"deployment_gate": {"clause_results": clause_results}}}
    assert inv.check_deployment_gate_completeness(winners).passed is True
    clause_results.pop("holdout_disjoint")
    assert inv.check_deployment_gate_completeness(winners).passed is False


# ─── Grep-Test ────────────────────────────────────────────────────────────────────────

def test_no_holdout_days_number_literal_outside_tests():
    pattern = re.compile(r"holdout_days\s*=\s*\d")
    offenders = []
    for path in (_REPO / "automation").rglob("*.py"):
        if "tests" in path.relative_to(_REPO / "automation").parts:
            continue
        for lineno, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_REPO)}:{lineno}: {line.strip()}")
    assert not offenders, offenders
