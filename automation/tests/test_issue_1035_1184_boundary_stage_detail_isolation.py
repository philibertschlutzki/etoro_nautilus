"""Issue #1035/#1184 (P1) — Boundary-Stufe stempelt den Holdout-Grund; Bounds-Befund und Veto sind
entkoppelt.

Symptom. In ``decision_chain`` trugen Studies, die SOWOHL das Holdout-Gate verfehlten ALS AUCH
(unabhängig berechnet) an der Boundary klemmten, ``{"stage": "boundary", "passed": false,
"detail": "REJECT_HOLDOUT_GATE"}`` — der Detailtext der Boundary-Stufe war der Grund der
Holdout-Stufe (geerbt von ``is_rejection_detail_override``, der terminalen, prioritätsbasierten
Ursachenzuschreibung).

Fix.
1. Ein dedizierter Detail-Code je Stufe (``REJECT_SELECTION_BOUNDARY`` für die Boundary-Stufe,
   ``REJECT_SELECTION_PBO`` bereits das Vorbild für die PBO-Stufe).
2. ``winner_outside_default_bounds`` -> ``winner_outside_default_bounds_after_override`` umbenannt.
3. ``boundary_veto_evidence`` wird auch dann gefüllt, wenn ``boundary_directions`` bereits gefüllt
   ist, aber die (separat berechnete) volle Evidenz leer blieb.
"""
import itertools
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import report as rpt
from automation.optimizer import trial_config
from automation.optimizer import invariants as inv


def _cfg_dir(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    with open(cfg_dir / "optimizer.json", "w", encoding="utf-8") as f:
        json.dump({
            "promotion_margin": 0.10, "penalty_unevaluable_oos": -10,
            "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0,
            "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
            "bonus_coverage_weight": 1.0, "evaluable_floor_epsilon": 0.001,
            "lambda_reg": 0.25,
        }, f)
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump({
            "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
            "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
            "oos_min_win_rate": 0.0,
        }, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path))


def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods, total_return=0.02):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": total_return, "total_trades": 50,
                "sortino_period": sortino_period, "n_periods": n_periods,
                "ret_skew": 0.0, "ret_kurtosis": 3.0,
            },
        },
    }


def _write_result(trial_dir, payload):
    out = Path(trial_dir) / "tournament_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), "utf-8")
    return out


def _cohort_factory(sortino_periods, n_periods=200):
    it = itertools.cycle(sortino_periods)

    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=n_periods))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _run_confirm(tmp_path, monkeypatch, *, boundary_fraction, boundary_directions,
                 symbol_result, global_result):
    global_params = {"price_breakout_period": 20}
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: boundary_fraction)
    monkeypatch.setattr(ro, "_boundary_hit_directions", lambda *a, **k: boundary_directions)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([0.02, 0.025]))
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)
    return confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )


# ── Fix Punkt 1: dedizierter Detail-Code, kein geerbter Holdout-Grund ────────────────────────────
def test_boundary_stage_detail_no_longer_inherits_holdout_reject_detail(tmp_path, monkeypatch):
    """Kernszenario aus #1035: die Study verfehlt das Holdout-Gate (negativer Sortino) UND klemmt
    unabhängig an der Boundary (frac=0.9 >= 0.5 -> boundary_unresolved). Vor dem Fix erbte
    stage_results['boundary']['detail'] den Holdout-Grund (REJECT_HOLDOUT_GATE) -- danach trägt sie
    ihren eigenen, dedizierten Code."""
    _isolate(monkeypatch, tmp_path)
    failing_holdout = _result_payload(sortino_ratio=-0.5, dd=0.05, sortino_period=-0.5,
                                      n_periods=400, total_return=-0.01)
    res = _run_confirm(
        tmp_path, monkeypatch, boundary_fraction=0.9,
        boundary_directions={"cooldown_bars": "high"},
        symbol_result=failing_holdout, global_result=failing_holdout,
    )
    assert res["holdout_passed"] is False
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_GATE"
    assert res["stage_results"]["holdout"] == {"passed": False, "detail": "REJECT_HOLDOUT_GATE"}
    assert res["stage_results"]["boundary"]["passed"] is False
    assert res["stage_results"]["boundary"]["detail"] == "REJECT_SELECTION_BOUNDARY"
    # Der Kern der Regression: die beiden Stufen dürfen NIE denselben Code tragen.
    assert res["stage_results"]["boundary"]["detail"] != res["stage_results"]["holdout"]["detail"]


def test_boundary_stage_detail_when_boundary_is_the_terminal_cause(tmp_path, monkeypatch):
    """Besteht der Holdout, scheitert aber die Boundary (terminaler REJECTED_BOUNDARY_SOLUTION),
    traegt die Stufe denselben dedizierten Code wie der terminale Grund -- kein Verhaltensbruch am
    Regelfall."""
    _isolate(monkeypatch, tmp_path)
    passing_holdout = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    res = _run_confirm(
        tmp_path, monkeypatch, boundary_fraction=0.4,
        boundary_directions={"cooldown_bars": "high"},
        symbol_result=passing_holdout, global_result=passing_holdout,
    )
    assert res["status"] == "REJECTED_BOUNDARY_SOLUTION"
    assert res["stage_results"]["boundary"] == {
        "passed": False, "detail": "REJECT_SELECTION_BOUNDARY"}


# ── Fix Punkt 3: boundary_veto_evidence gefuellt, sobald boundary_directions gefuellt ist ────────
def test_boundary_veto_evidence_falls_back_to_directions_when_evidence_is_empty(tmp_path, monkeypatch):
    """SmaCrossover-Symptom aus #1035: boundary_directions ist gefuellt, aber
    _boundary_veto_evidence (separat berechnet) liefert None -- der Fallback rekonstruiert
    mindestens die Richtung je klemmendem Parameter, statt den Beleg ganz zu verlieren."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "_boundary_veto_evidence", lambda *a, **k: None)
    passing_holdout = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    res = _run_confirm(
        tmp_path, monkeypatch, boundary_fraction=0.6,
        boundary_directions={"cooldown_bars": "high"},
        symbol_result=passing_holdout, global_result=passing_holdout,
    )
    assert res["metrics_symbol"]["boundary_veto_evidence"] == {
        "cooldown_bars": {"direction": "high"}}


def test_boundary_veto_evidence_untouched_when_already_populated(tmp_path, monkeypatch):
    """Liefert _boundary_veto_evidence bereits die volle Evidenz, greift der Fallback nicht."""
    _isolate(monkeypatch, tmp_path)
    full_evidence = {"cooldown_bars": {
        "direction": "high", "sampled_value": 36, "active_bounds": [2, 36],
        "default_bounds": [2, 36], "distance_to_edge": 0.0}}
    monkeypatch.setattr(ro, "_boundary_veto_evidence", lambda *a, **k: full_evidence)
    passing_holdout = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    res = _run_confirm(
        tmp_path, monkeypatch, boundary_fraction=0.6,
        boundary_directions={"cooldown_bars": "high"},
        symbol_result=passing_holdout, global_result=passing_holdout,
    )
    assert res["metrics_symbol"]["boundary_veto_evidence"] == full_evidence


# ── invariants.check_decision_chain_stage_detail_isolation (Akzeptanzkriterium 1) ────────────────
def test_check_detail_isolation_fails_on_collision():
    chain = [
        {"stage": "holdout", "passed": False, "detail": "REJECT_HOLDOUT_GATE"},
        {"stage": "boundary", "passed": False, "detail": "REJECT_HOLDOUT_GATE"},
    ]
    result = inv.check_decision_chain_stage_detail_isolation(chain)
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual == [{"stages": ["holdout", "boundary"], "detail": "REJECT_HOLDOUT_GATE"}]


def test_check_detail_isolation_passes_for_disjoint_codes():
    chain = [
        {"stage": "holdout", "passed": False, "detail": "REJECT_HOLDOUT_GATE"},
        {"stage": "boundary", "passed": False, "detail": "REJECT_SELECTION_BOUNDARY"},
        {"stage": "pbo", "passed": True, "detail": None},
        {"stage": "deflation", "passed": None, "detail": None},
    ]
    result = inv.check_decision_chain_stage_detail_isolation(chain)
    assert result.passed is True


def test_check_detail_isolation_passes_for_missing_or_empty_chain():
    assert inv.check_decision_chain_stage_detail_isolation(None).passed is True
    assert inv.check_decision_chain_stage_detail_isolation([]).passed is True


def test_check_detail_isolation_wired_into_study_record(tmp_path, monkeypatch):
    """End-to-End: report._study_record ruft die neue Invariante mit dem gebauten decision_chain
    auf -- eine Study mit der #1035-Kollision faellt tatsaechlich im Report auf."""
    _isolate(monkeypatch, tmp_path)
    failing_holdout = _result_payload(sortino_ratio=-0.5, dd=0.05, sortino_period=-0.5,
                                      n_periods=400, total_return=-0.01)
    res = _run_confirm(
        tmp_path, monkeypatch, boundary_fraction=0.9,
        boundary_directions={"cooldown_bars": "high"},
        symbol_result=failing_holdout, global_result=failing_holdout,
    )
    proposal = {
        "status": res["status"], "holdout_reject_detail": res["is_rejection_detail_override"],
        "stage_results": res["stage_results"], "promotion_route": res.get("promotion_route"),
    }
    chain = rpt._decision_chain(proposal, n_eligible=2)
    result = inv.check_decision_chain_stage_detail_isolation(chain)
    assert result.passed is True  # nach dem Fix keine Kollision mehr


# ── check_boundary_veto_has_evidence: Akzeptanzkriterium 2 (stage_results-basiert) ───────────────
def test_boundary_veto_evidence_check_fires_even_when_status_attributed_elsewhere():
    """Die Boundary-Stufe scheiterte (stage_results.boundary.passed=False), aber der terminale
    status wurde (Prioritaetskette) einer ANDEREN Stufe (Holdout) zugeschrieben -- das alte, rein
    status-basierte Gate haette das NIE geprueft."""
    proposal = {
        "status": "REJECTED_ON_HOLDOUT",
        "stage_results": {"boundary": {"passed": False, "detail": "REJECT_SELECTION_BOUNDARY"}},
        "holdout": {"symbol": {"boundary_veto_evidence": None}},
    }
    result = inv.check_boundary_veto_has_evidence(proposal)
    assert result.passed is False
    assert result.severity == "blocking"


def test_boundary_veto_evidence_check_still_not_applicable_without_stage_results():
    """Legacy-Proposal ohne stage_results -- bit-identisches Verhalten zu vor #1035."""
    proposal = {"status": "REJECTED_ON_HOLDOUT", "holdout": {"symbol": {}}}
    result = inv.check_boundary_veto_has_evidence(proposal)
    assert result.passed is True
