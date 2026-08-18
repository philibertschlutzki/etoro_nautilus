"""Issue #1007 (Katalog #858) — Invarianten-FAILs waren bislang ein reiner Report-Nachlauf
(``report.generate_sweep_report``) und ein Sweep-Abbruchkriterium (``fail_fast_invariants``), aber
KEIN Eingang in ``confirm.confirm_per_symbol_promotion`` — eine Study konnte gleichzeitig als
informationsfrei (``severity='blocking'``-Invariante FAIL, z. B.
``check_selection_statistic_availability``, ``check_guard_reference_stability``) markiert UND als
Gewinner exportiert werden.

Dieser Test deckt das neue ``study_invariant_results``-Argument ab: eine blockierende, fehlgeschlagene
Invariante muss die Promotion mit ``REJECT_STUDY_INVARIANT_BLOCKING`` ablehnen — als unbedingter
Hard-Stop, den auch ``promotion_correction_mode='dsr_or_robust_pair'`` nicht unterlaufen darf (analog
``REJECT_HOLDOUT_GATE``). Fehlt das Argument (Default ``None``, Legacy-Aufrufer) bleibt das Verhalten
bit-identisch zu vorher.
"""
import itertools
import json
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config


def _cfg_dir(tmp_path, tournament_extra=None):
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
    tcfg = {
        "max_drawdown": 0.30, "deflated_selection": False,
        "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
        "oos_min_win_rate": 0.0, "holdout_bootstrap_ci": False,
        "promotion_correction_mode": "conjunction",
    }
    if tournament_extra:
        tcfg.update(tournament_extra)
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump(tcfg, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate(monkeypatch, tmp_path, tournament_extra=None):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)


def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods, eligible=True,
                     total_return=0.02, total_trades=50):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": eligible, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": total_return, "total_trades": total_trades,
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


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_periods, tournament_extra=None):
    _isolate(monkeypatch, tmp_path, tournament_extra)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory(cohort_periods))
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


def _confirm(tmp_path, monkeypatch, *, study_invariant_results, tournament_extra=None,
             symbol_sortino=2.0, global_sortino=0.5):
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025],
                         tournament_extra=tournament_extra)
    symbol_result = _result_payload(sortino_ratio=symbol_sortino, dd=0.05,
                                    sortino_period=0.05, n_periods=200)
    global_result = _result_payload(sortino_ratio=global_sortino, dd=0.05,
                                    sortino_period=0.01, n_periods=200)
    return confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
        study_invariant_results=study_invariant_results,
    )


def test_no_invariant_results_is_bit_identical_to_legacy_default(tmp_path, monkeypatch):
    """``study_invariant_results=None`` (Default) — bit-identisches Verhalten zu vorher: eine
    ansonsten passierende Study wird weiterhin promotet, kein neues Zusatz-Veto."""
    res = _confirm(tmp_path, monkeypatch, study_invariant_results=None)
    assert res["holdout_passed"] is True
    assert res["status"] == "READY_FOR_PR"
    assert "blocking_invariant_names" not in res["metrics_symbol"]


def test_empty_invariant_results_list_does_not_block(tmp_path, monkeypatch):
    """Eine explizit LEERE Liste (Aufrufer hat geprüft, nichts gefunden) blockiert nicht, wird aber
    (im Unterschied zu ``None``) als geprüft exportiert — Grundlage für deployment_gate's
    ``study_invariants_clean``-Klausel."""
    res = _confirm(tmp_path, monkeypatch, study_invariant_results=[])
    assert res["holdout_passed"] is True
    assert res["status"] == "READY_FOR_PR"
    assert res["metrics_symbol"]["blocking_invariant_names"] == []


def test_blocking_failing_invariant_rejects_promotion(tmp_path, monkeypatch):
    """Der zentrale Fall aus #1007: eine Study, die ``check_selection_statistic_availability``
    (severity='blocking') verletzt, darf NICHT promotet werden — selbst wenn Holdout-Gate und
    R-Edge sonst bestehen wuerden."""
    study_invariant_results = [
        {"name": "check_selection_statistic_availability", "passed": False,
         "severity": "blocking", "actual": 0.2975, "expected": 0.8,
         "detail": "TSLA.ETORO/FlashCrashReversalStrategy unter der Mindestverfuegbarkeit"},
    ]
    res = _confirm(tmp_path, monkeypatch, study_invariant_results=study_invariant_results)
    assert res["holdout_passed"] is False
    # Issue #1002/#1154 (Katalog #1170) — REJECT_STUDY_INVARIANT_BLOCKING erreicht die Holdout-
    # Stufe nie (die Study ist strukturell ungueltig, VOR jeder Holdout-Auswertung) und traegt
    # seither den eigenen Status REJECTED_BEFORE_HOLDOUT statt des geteilten REJECTED_ON_HOLDOUT.
    assert res["status"] == "REJECTED_BEFORE_HOLDOUT"
    assert res["blocking_stage"] == "confirm_or_selection"
    assert res["is_rejection_detail_override"] == "REJECT_STUDY_INVARIANT_BLOCKING"
    assert res["metrics_symbol"]["blocking_invariant_names"] == [
        "check_selection_statistic_availability"]


def test_blocking_but_passed_invariant_does_not_reject(tmp_path, monkeypatch):
    """``passed=True`` — auch severity='blocking' — ist kein Reject-Grund."""
    study_invariant_results = [
        {"name": "check_holding_time_cap", "passed": True, "severity": "blocking",
         "actual": 0.1, "expected": 0.25, "detail": "ok"},
    ]
    res = _confirm(tmp_path, monkeypatch, study_invariant_results=study_invariant_results)
    assert res["holdout_passed"] is True
    assert res["metrics_symbol"]["blocking_invariant_names"] == []


def test_non_blocking_severity_does_not_reject(tmp_path, monkeypatch):
    """Ein FAIL mit severity='high' (oder 'medium') ist weiterhin nur Telemetrie/Report-Signal —
    #1007 aendert ausschliesslich das Verhalten fuer severity='blocking'."""
    study_invariant_results = [
        {"name": "check_family_n_periods_homogeneity", "passed": False, "severity": "high",
         "actual": 9.79, "expected": 4.0, "detail": "heterogen"},
    ]
    res = _confirm(tmp_path, monkeypatch, study_invariant_results=study_invariant_results)
    assert res["holdout_passed"] is True
    assert res["metrics_symbol"]["blocking_invariant_names"] == []


def test_blocking_invariant_is_never_bypassed_by_dsr_or_robust_pair_mode(tmp_path, monkeypatch):
    """Pitfall #343/#344-Konsistenz: der ``dsr_or_robust_pair``-Ersatzpfad ersetzt NIEMALS einen
    blockierenden Invarianten-Reject — REJECT_STUDY_INVARIANT_BLOCKING ist kein Mitglied der
    Ersatzpfad-Allowlist (REJECT_HOLDOUT_DSR_DROP/REJECT_HOLDOUT_BOOTSTRAP_CI), also bleibt die
    Promotion abgelehnt, unabhaengig davon wie stark DSR/PBO/CI sonst waeren."""
    study_invariant_results = [
        {"name": "check_guard_reference_stability", "passed": False, "severity": "blocking",
         "actual": 27, "expected": 1, "detail": "27 verschiedene Ankerwerte"},
    ]
    res = _confirm(tmp_path, monkeypatch, study_invariant_results=study_invariant_results,
                   tournament_extra={"promotion_correction_mode": "dsr_or_robust_pair"})
    assert res["holdout_passed"] is False
    assert res["is_rejection_detail_override"] == "REJECT_STUDY_INVARIANT_BLOCKING"


def test_multiple_blocking_names_are_sorted_and_deduplicated(tmp_path, monkeypatch):
    study_invariant_results = [
        {"name": "check_holding_time_cap", "passed": False, "severity": "blocking"},
        {"name": "check_guard_reference_stability", "passed": False, "severity": "blocking"},
        {"name": "check_holding_time_cap", "passed": False, "severity": "blocking"},
    ]
    res = _confirm(tmp_path, monkeypatch, study_invariant_results=study_invariant_results)
    assert res["metrics_symbol"]["blocking_invariant_names"] == [
        "check_guard_reference_stability", "check_holding_time_cap"]
