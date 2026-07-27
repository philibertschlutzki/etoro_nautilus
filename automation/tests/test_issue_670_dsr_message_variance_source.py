"""Issue #670 — DSR-Small-Cohort-Warnung meldet FALSCHE Varianz-Quelle: reaktiviert das
#653-entfernte Diskontinuitäts-Framing.

Root-Cause: die Log-Message war an ``deflation_used_var_floor`` gekoppelt, das
``sr0_multiple_testing_robust`` als ``floor_dominant = (λ ≥ 0.5)`` zurückgibt — nur "Shrinkage-
Gewicht dominant", NICHT "die 0.0018-Konstante wurde verwendet". Seit #653 ist bei vorhandenem
``n_periods`` die theoretische Referenz IMMER Lo-2002 (T-bewusst), nicht ``var_floor``. Fix: die
Message (und die Telemetrie) koppeln an den TATSÄCHLICH verwendeten ``theoretical_var``-Zweig.

Issue #701 — der ``var_floor``-Legacy-Fallback selbst (wirksam nur ohne ``n_periods``) wurde nach
Verifikation, dass ``n_periods`` an jeder Produktions-Call-Site unconditional verfügbar ist,
ERSATZLOS ENTFERNT: ``n_periods`` ist jetzt ein PFLICHT-Parameter, ``theoretical_var_source`` ist
IMMER ``'lo2002'``. Die vormaligen var_floor-Fallback-Tests dieser Datei sind entsprechend durch
Fail-Loud-/Graceful-Degradation-Tests ersetzt.
"""
import logging

import pytest

from automation.optimizer.deflation import sr0_multiple_testing_robust, _cohort_shrinkage_weight


# ── deflation.sr0_multiple_testing_robust: die präzisen Zusatz-Rückgabewerte ────────────────────
def test_theoretical_var_source_is_lo2002_when_n_periods_present():
    sr0, floor_dominant, lam, source = sr0_multiple_testing_robust(
        1.483e-4, 9, min_cohort=10, n_periods=200)
    assert source == "lo2002"
    assert floor_dominant is True  # N=9 < min_cohort=10 ⇒ trotzdem weiterhin λ ≥ 0.5
    assert lam == pytest.approx(_cohort_shrinkage_weight(9, 10))


def test_missing_n_periods_fails_loud_not_silent_var_floor_fallback():
    """Issue #701 — kein stiller Rückfall mehr auf eine geratene 0.0018-Konstante: ein fehlendes
    n_periods ist ein Bug im Aufrufer und bricht jetzt fail-loud ab."""
    with pytest.raises(ValueError, match="n_periods"):
        sr0_multiple_testing_robust(1.483e-4, 9, min_cohort=10, n_periods=None)


def test_n_periods_of_one_or_less_also_fails_loud():
    with pytest.raises(ValueError, match="n_periods"):
        sr0_multiple_testing_robust(1.483e-4, 9, min_cohort=10, n_periods=1)


def test_theoretical_var_source_is_lo2002_even_when_floor_not_dominant():
    """floor_dominant (λ<0.5) und theoretical_var_source sind ORTHOGONALE Grössen: bei grossem N ist
    λ klein, aber die Referenz-QUELLE bleibt Lo-2002, sobald n_periods bekannt ist."""
    sr0, floor_dominant, lam, source = sr0_multiple_testing_robust(
        0.05, 500, min_cohort=10, n_periods=200)
    assert source == "lo2002"
    assert floor_dominant is False
    assert lam < 0.5


def test_shrinkage_lambda_matches_cohort_shrinkage_weight_with_variance_n_trials():
    sr0, floor_dominant, lam, source = sr0_multiple_testing_robust(
        1e-4, 100, min_cohort=10, n_periods=200, variance_n_trials=9)
    assert lam == pytest.approx(_cohort_shrinkage_weight(9, 10))
    assert floor_dominant is True  # variance_n_trials=9 treibt lambda, nicht n_trials=100


# ── confirm.py: die Log-Message koppelt an theoretical_var_source, nicht an floor_dominant ───────
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro, confirm


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
            "oos_min_win_rate": 0.0, "deflation_min_cohort": 10,
        }, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    from automation.optimizer import trial_config
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)


def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods, total_return=0.02, total_trades=50):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
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


def _cohort_factory(sortino_periods, n_periods):
    import itertools
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


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_periods, n_periods):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory(cohort_periods, n_periods))
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


def test_dsr_message_cites_lo2002_when_n_periods_available(tmp_path, monkeypatch, caplog):
    global_params = {"price_breakout_period": 20}
    # 9 eligible Trials (< deflation_min_cohort=10 ⇒ floor_dominant=True), aber n_periods=200
    # gestempelt ⇒ die theoretische Referenz MUSS Lo-2002 sein.
    study = _build_study(tmp_path, monkeypatch, n_trials=9,
                         cohort_periods=[0.02 + 0.001 * i for i in range(9)], n_periods=200)

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    with caplog.at_level(logging.INFO, logger="optimizer"):
        res = confirm.confirm_per_symbol_promotion(
            study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
        )

    dsr_messages = [r.message for r in caplog.records if "[DSR #618/#653/#670]" in r.message]
    assert dsr_messages, "keine DSR-Message geloggt"
    joined = " | ".join(dsr_messages)
    assert "Lo-2002" in joined
    assert "Varianz-Floor" not in joined
    assert res["metrics_symbol"].get("deflation_theoretical_var_source") == "lo2002"
    assert res["metrics_symbol"].get("deflation_lambda") is not None


def test_missing_n_periods_degrades_gracefully_without_crashing_promotion(tmp_path, monkeypatch, caplog):
    """Issue #701 — ein (nach der Invariante unerreichbares, aber defensiv abgefangenes) fehlendes
    n_periods darf die GESAMTE Promotion-Pipeline nicht abstürzen lassen: DSR bleibt für diesen
    Lauf schlicht unberechnet (kein var_floor-Fallback mehr), statt eine ValueError zu propagieren."""
    global_params = {"price_breakout_period": 20}
    # n_periods=0 gestempelt (künstliche Datenanomalie, die die Invariante verletzt) ⇒
    # deflation_t_periods bleibt None ⇒ der #701-Defense-in-Depth-Zweig greift.
    study = _build_study(tmp_path, monkeypatch, n_trials=9,
                         cohort_periods=[0.02 + 0.001 * i for i in range(9)], n_periods=0)

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=0)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=0)

    with caplog.at_level(logging.INFO, logger="optimizer"):
        res = confirm.confirm_per_symbol_promotion(
            study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
        )

    assert any("[DSR #701]" in r.message for r in caplog.records)
    assert res["metrics_symbol"].get("deflated_sr0") is None
    assert res["metrics_symbol"].get("deflation_theoretical_var_source") is None


def test_no_stale_n_min_discontinuity_vocabulary_in_message(tmp_path, monkeypatch, caplog):
    """Akzeptanzkriterium (#670): die Message darf NICHT das '< N_min ⇒ Fallback'-Diskontinuitäts-
    Framing reaktivieren — es gibt seit #653 keinen Cutover mehr, nur ein stetiges Gewicht λ."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=9,
                         cohort_periods=[0.02 + 0.001 * i for i in range(9)], n_periods=200)
    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    with caplog.at_level(logging.INFO, logger="optimizer"):
        confirm.confirm_per_symbol_promotion(
            study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
        )
    dsr_messages = [r.message for r in caplog.records if "[DSR #618/#653/#670]" in r.message]
    joined = " | ".join(dsr_messages)
    assert "N_eligible=" in joined and "< N_min=" not in joined
