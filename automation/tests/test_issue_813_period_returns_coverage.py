"""Issue #813 (P1) — #784-Asymmetrie: die rohe Multiplizitätszahl zählt seit dem Fix alle
evaluierten Trials, die Dekorrelation läuft weiterhin nur auf den eligiblen.

Root-Cause: ``sweep._family_n_from_studies`` zählt seit #784 ``oos_evaluated`` (Faktor ~7,7 mehr
Kandidaten). ``sweep._family_period_returns_from_studies`` filtert zwar ebenfalls auf
``oos_evaluated``, findet dort aber keine Renditeserien — ``run_optimization.make_symbol_objective``
stempelte ``trial.user_attrs['oos_period_returns']`` bis #813 NUR für ``oos_eligible``-Trials. Die
Correlation-Declusterung (``cpcv.cluster_effective_configs``), die die grössere Rohzahl statistisch
auffangen soll, operierte damit weiterhin auf der ALTEN, kleinen Menge ⇒ systematische
Über-Deflation (``deflation_n_effective`` stieg um Faktor ~7,7, die tatsächlich declusterte
Config-Zahl blieb konstant).

Fix: ``oos_period_returns`` wird jetzt für JEDEN ``oos_evaluated`` Trial gestempelt, nicht nur für
eligible. Neue Report-Telemetrie ``deflation_cluster_coverage`` (Anteil der gezählten Kandidaten,
für die eine Renditeserie vorlag) + Regressionswächter ``invariants.check_deflation_cluster_
coverage`` (FAIL < 0.9).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import confirm, run_optimization as ro, trial_config
from automation.optimizer import invariants as inv
from automation.optimizer.report import _study_record

CFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ── run_optimization: oos_period_returns wird fuer JEDEN oos_evaluated Trial gestempelt ────────────
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_ineligible_but_evaluated_factory():
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 0, "aggregate_winner": {
            # oos_evaluated=True, oos_eligible=False -- genau die vor #813 luecken-behaftete Kohorte.
            "oos_evaluated": True, "oos_eligible": False, "win_count": 0,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [0.1],
            "oos_metrics": {"sortino_ratio": 0.1, "max_drawdown": 0.05,
                           "period_returns": [0.01, -0.02, 0.03, 0.005]}}}), "utf-8")
        return out
    return _fake


def test_period_returns_stamped_for_evaluated_but_ineligible_trial(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_ineligible_but_evaluated_factory())
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    trial = study.trials[0]
    assert trial.user_attrs["oos_evaluated"] is True
    assert trial.user_attrs["oos_eligible"] is False
    # Akzeptanzkriterium #813: die Serie ist trotz oos_eligible=False vorhanden.
    assert trial.user_attrs.get("oos_period_returns") == [0.01, -0.02, 0.03, 0.005]


def _fake_never_evaluated_factory():
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 0, "aggregate_winner": {
            "oos_evaluated": False, "oos_eligible": False, "win_count": 0,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [],
            "oos_metrics": {}}}), "utf-8")
        return out
    return _fake


def test_period_returns_absent_for_a_never_evaluated_trial(tmp_path, monkeypatch):
    """Zero-Hardcoding-Gegenprobe: ein NIE evaluierter Trial bekommt weiterhin KEINE (auch keine
    leere) oos_period_returns-Serie gestempelt -- #813 erweitert den Filter nicht auf alle Trials,
    nur auf die evaluierten."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_never_evaluated_factory())
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    trial = study.trials[0]
    assert trial.user_attrs["oos_evaluated"] is False
    assert "oos_period_returns" not in trial.user_attrs


# ── confirm.py: deflation_cluster_coverage ─────────────────────────────────────────────────────────
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
    tcfg = {
        "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
        "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
        "oos_min_win_rate": 0.0, "pbo_cluster_threshold": 0.99,
    }
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump(tcfg, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate_confirm(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)


def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods, eligible=True):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": eligible, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": 0.02, "total_trades": 50,
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


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_sortino=0.02):
    import itertools
    _isolate_confirm(monkeypatch, tmp_path)

    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=cohort_sortino, dd=0.05, sortino_period=cohort_sortino, n_periods=200))
    monkeypatch.setattr(ro, "run_backtest", _fake)
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


def test_deflation_cluster_coverage_reflects_the_fraction_of_counted_candidates_with_a_return_series(
    tmp_path, monkeypatch,
):
    """Akzeptanzkriterium #813 (Regressionswächter, nicht das reale Re-Run-Ergebnis): uebergibt man
    NUR 40 von 100 gezaehlten (deflation_n_family) Kandidaten eine Renditeserie (die Vor-#813-Luecke
    nachgestellt), liegt die Coverage bei 0.4 -- deutlich unter der 0.9-Schwelle."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=5)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    family_rows = [[0.001 * i, -0.002 * i, 0.003] for i in range(40)]
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=100,
        deflation_family_period_returns=family_rows,
    )
    assert res["metrics_symbol"]["deflation_cluster_coverage"] == pytest.approx(0.4)


def test_deflation_cluster_coverage_is_near_one_when_every_counted_candidate_has_a_series(
    tmp_path, monkeypatch,
):
    """Post-#813-Zustand: jeder gezaehlte Kandidat traegt eine Renditeserie -> Coverage == 1.0."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=5)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    family_rows = [[0.001 * i, -0.002 * i, 0.003] for i in range(100)]
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=100,
        deflation_family_period_returns=family_rows,
    )
    assert res["metrics_symbol"]["deflation_cluster_coverage"] == pytest.approx(1.0)


def test_deflation_cluster_coverage_is_none_without_a_family_matrix(tmp_path, monkeypatch):
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=5)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=100,
    )
    assert res["metrics_symbol"]["deflation_cluster_coverage"] is None


# ── invariants.check_deflation_cluster_coverage ─────────────────────────────────────────────────
def test_check_passes_at_default_threshold():
    result = inv.check_deflation_cluster_coverage(
        {"deflation_cluster_coverage": 0.95, "deflation_n_family": 100})
    assert result.passed is True


def test_check_fails_below_default_threshold():
    result = inv.check_deflation_cluster_coverage(
        {"deflation_cluster_coverage": 0.4, "deflation_n_family": 100})
    assert result.passed is False
    assert "0.4" in result.detail


def test_check_is_a_noop_without_a_family_cohort():
    assert inv.check_deflation_cluster_coverage({}).passed is True
    assert inv.check_deflation_cluster_coverage(
        {"deflation_cluster_coverage": 0.1, "deflation_n_family": 0}).passed is True


def test_check_respects_custom_threshold():
    result = inv.check_deflation_cluster_coverage(
        {"deflation_cluster_coverage": 0.92, "deflation_n_family": 50}, min_coverage=0.95)
    assert result.passed is False


# ── report._study_record wires the new check in ────────────────────────────────────────────────
class _T:
    def __init__(self):
        self.value = 1.0
        self.user_attrs = {"oos_evaluated": True, "oos_eligible": True}


class _S:
    trials = [_T()]
    best_value = 1.0
    user_attrs = {}


def test_study_record_flags_low_cluster_coverage():
    proposal = {"symbol": "BTC.ETORO", "strategy": "TrendFollow",
               "holdout": {"symbol": {"deflation_cluster_coverage": 0.2, "deflation_n_family": 500}}}
    _record, checks = _study_record(proposal, _S(), CFG)
    by_name = {c.name: c for c in checks}
    assert "check_deflation_cluster_coverage" in by_name
    assert by_name["check_deflation_cluster_coverage"].passed is False


def test_study_record_passes_high_cluster_coverage():
    proposal = {"symbol": "BTC.ETORO", "strategy": "TrendFollow",
               "holdout": {"symbol": {"deflation_cluster_coverage": 0.99, "deflation_n_family": 500}}}
    _record, checks = _study_record(proposal, _S(), CFG)
    by_name = {c.name: c for c in checks}
    assert by_name["check_deflation_cluster_coverage"].passed is True
