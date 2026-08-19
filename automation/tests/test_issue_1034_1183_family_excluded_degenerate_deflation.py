"""Issue #1034/#1183 (P1) — ``FAMILY_EXCLUDED_DEGENERATE`` erzeugt eine Deflation ohne
Multiplizitätskorrektur.

Root-Cause: eine ``excluded_degenerate``-Study (#981/#1135, ``sweep.py`` setzt N1 auf 0, sobald
``oos_n_periods_median < min_oos_periods_for_family``) reicht ``deflation_n_family=0`` an
``confirm_per_symbol_promotion`` durch. Erreicht diese Study TROTZDEM die Deflationsstufe (ihre
EIGENE Kohorte ist gross genug, ``deflation_n >= 2``), maskierte
``deflation_n_effective = max(deflation_n, deflation_n_family_effective)`` die unaufloesbare
Familien-Multiplizitaet lautlos durch das per-Study-N — fail-open an der letzten
Promotionsschranke, ohne jedes Flag.

Fix (Variante b aus #1034, die konservative Fail-Loud-Linie): ein unbedingter Hard-Stop
``REJECT_PROMOTION_FAMILY_UNRESOLVABLE``, sobald die Deflationsstufe mit einer EXPLIZIT (nicht
``None``) auf 0 aufgeloesten Familien-Multiplizitaet erreicht wird. ``deflation_n_family=None``
(Legacy-/Unit-Test-Aufrufer, die den Parameter nie uebergeben) bleibt bit-identisch zum
Pre-#1034-Verhalten.
"""
import itertools
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config
from automation.optimizer import invariants as inv


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
        "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
        "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
        "oos_min_win_rate": 0.0,
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
                     total_return=0.02, total_trades=50, skew=0.0, kurtosis=3.0):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": eligible, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": total_return, "total_trades": total_trades,
                "sortino_period": sortino_period, "n_periods": n_periods,
                "ret_skew": skew, "ret_kurtosis": kurtosis,
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
    return ro.optimize_symbol("SqueezeBreakoutStrategy", "NVDA.ETORO", n_trials=n_trials)


def _confirm_with_family_n(tmp_path, monkeypatch, *, deflation_n_family):
    """Eine Study mit ausreichend eigener Kohorte (deflation_n=20 >= 2), die das Holdout-Gate
    passiert — der einzige Unterschied zwischen den Testfaellen ist ``deflation_n_family``."""
    global_params = {"price_breakout_period": 20}
    cohort_periods = [0.02 + 0.001 * i for i in range(20)]
    study = _build_study(tmp_path, monkeypatch, n_trials=20, cohort_periods=cohort_periods)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    kwargs = {}
    if deflation_n_family is not _SENTINEL_OMIT:
        kwargs["deflation_n_family"] = deflation_n_family
    return confirm.confirm_per_symbol_promotion(
        study, "SqueezeBreakoutStrategy", "NVDA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        **kwargs,
    )


_SENTINEL_OMIT = object()


# ── Akzeptanzkriterium #1034/1: excluded_degenerate-Study, die Holdout passiert -> Hard-Reject ──
def test_excluded_degenerate_family_n_zero_rejects_with_dedicated_detail(tmp_path, monkeypatch):
    """``deflation_n_family=0`` (EXPLIZIT vom Aufrufer aufgeloest, wie sweep._run_confirm_and_export
    es fuer eine ``excluded_degenerate``-Study liefert) darf die Deflationsstufe NICHT stillschweigend
    mit dem per-Study-N ueberdecken — die Study wird nicht promotet und traegt den dedizierten
    Ablehnungsgrund."""
    res = _confirm_with_family_n(tmp_path, monkeypatch, deflation_n_family=0)

    assert res["promote"] is False
    assert res["holdout_passed"] is False
    assert res["is_rejection_detail_override"] == "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"
    assert res["status"] == "REJECTED_ON_DEFLATION"
    # Confirm/Holdout-Stufe waren selbst bestanden -- nur die Deflationsstufe lehnte ab (siehe
    # stage_results, #1001/#1153).
    assert res["stage_results"]["confirm_or_selection"] == {"passed": True, "detail": None}
    assert res["stage_results"]["holdout"] == {"passed": True, "detail": None}
    assert res["stage_results"]["deflation"] == {
        "passed": False, "detail": "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"}
    # Die Study erreichte die Deflationsstufe TATSAECHLICH (SR0 wurde berechnet) -- der Reject ist
    # keine fehlende Berechnung, sondern ein bewusstes Veto trotz vorhandenem SR0.
    assert res["metrics_symbol"]["deflated_sr0"] is not None
    assert res["metrics_symbol"]["deflation_n_family"] == 0


def test_none_deflation_n_family_stays_bit_identical_to_pre_1034(tmp_path, monkeypatch):
    """Ein Legacy-/Unit-Test-Aufrufer, der ``deflation_n_family`` gar nicht uebergibt (``None``,
    Default), loest den neuen Hard-Stop NICHT aus -- nur ein Aufrufer, der eine Familien-
    Multiplizitaet ERMITTELT und EXPLIZIT leer (0) gemeldet hat, tut das. Diese Fixture (kleine,
    eng gestreute Kohorte um den promoteten Wert) lehnt bereits VOR #1034 unveraendert am
    regulaeren DSR-Drop-Gate ab (#618) -- der Beweis fuer #1034 ist, dass sich der Ablehnungsgrund
    NICHT auf den neuen Hard-Stop verschiebt."""
    res = _confirm_with_family_n(tmp_path, monkeypatch, deflation_n_family=_SENTINEL_OMIT)

    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_DSR_DROP"
    assert res["metrics_symbol"]["deflation_n_family"] == 0  # int(None or 0) -- unveraendert #652


def test_positive_deflation_n_family_is_unaffected(tmp_path, monkeypatch):
    """Eine reale, positive Familien-Multiplizitaet loest den Hard-Stop nicht aus (dieselbe
    DSR-Drop-Ablehnung wie im None-Fall, siehe voriger Test -- eine groessere Familien-N macht die
    DSR-Huerde hier sogar noch strenger, niemals den neuen #1034-Grund)."""
    res = _confirm_with_family_n(tmp_path, monkeypatch, deflation_n_family=496)

    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_DSR_DROP"
    assert res["metrics_symbol"]["deflation_n_family"] == 496


# ── Akzeptanzkriterium #1034/2: check_family_n_statistic_coverage bewacht dieselbe Invariante ────
def test_check_family_n_statistic_coverage_fails_on_zero_raw_when_stage_reached():
    result = inv.check_family_n_statistic_coverage([], deflation_n_family_raw=0)
    assert result.passed is False
    assert result.severity == "blocking"
    assert result.actual == 0


def test_check_family_n_statistic_coverage_still_passes_when_stage_not_reached():
    """``deflation_n_family_raw=None`` (die Deflationsstufe wurde nie erreicht -- confirm.py
    schreibt das Feld nur, wenn SR0 tatsaechlich berechnet wurde) bleibt "nicht anwendbar", nicht
    ein FAIL -- unveraendert zum Pre-#1034-Verhalten."""
    result = inv.check_family_n_statistic_coverage([], deflation_n_family_raw=None)
    assert result.passed is True


def test_check_family_n_statistic_coverage_upper_bound_check_unaffected():
    """Positive Werte durchlaufen weiterhin die #822-Obergrenzenpruefung, unveraendert."""
    trials = [{"oos_selection_statistic_available": True} for _ in range(8)]
    result = inv.check_family_n_statistic_coverage(trials, deflation_n_family_raw=5)
    assert result.passed is True
    result_violating = inv.check_family_n_statistic_coverage(trials, deflation_n_family_raw=12)
    assert result_violating.passed is False
