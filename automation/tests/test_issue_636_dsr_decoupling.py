"""Issue #636 — DSR-Berechnung von der Pass-Kette entkoppelt + Small-Cohort-Fallback.

Vorher lief der DSR-Block in ``confirm_per_symbol_promotion`` nur ``if holdout_passed and ...``.
Jede Strategie scheiterte aber an einem FRÜHEREN Holdout-Gate (Excess-Return #629, negativer
Holdout-Sortino), sodass ``holdout_passed`` bereits False war, BEVOR die DSR exerziert wurde —
``deflated_dsr`` blieb in ALLEN Proposals None (uninformative Telemetrie). Zusätzlich war
``V[ŜR_trials]`` aus einer 2-3-Punkte-Kohorte (VwapExhaustion N=3, Hourly N=2) statistisch
bedeutungslos und konnte SR₀ durch eine zufällig winzige Stichproben-Varianz unterschätzen.

Fix (deflation.py/confirm.py): die DSR wird IMMER berechnet, sobald genug Kohorte (N≥2) und ein
definierter promoteter per-Perioden-Sortino vorliegen — unabhängig vom bisherigen
``holdout_passed``. Unterhalb ``deflation_min_cohort`` greift ein dokumentierter, konservativer
Varianz-Floor (``sr0_multiple_testing_robust``). Der DROP-EFFEKT bleibt an ``holdout_passed``
gekoppelt.
"""
import itertools
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config
from automation.optimizer.deflation import sr0_multiple_testing


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
    # Issue #622 — Randlösungs-Veto ist hier nicht der Testgegenstand; neutralisieren.
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
    """Baut die STUDY-Kohorte: jeder Trial ist eligible mit einem eigenen per-Perioden-Sortino
    (treibt ``deflation_n``/``deflation_var`` in confirm), unabhängig von den gesampelten Params."""
    it = itertools.cycle(sortino_periods)

    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=n_periods))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    """Dispatcht nach exakter Param-Gleichheit mit ``global_params`` — robust unabhängig davon,
    welcher konkrete Study-Trial als Median-Rang promotet wird (alle Nicht-global-Params-Vektoren
    erhalten denselben ``symbol_result``)."""
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_periods, tournament_extra=None):
    _isolate(monkeypatch, tmp_path, tournament_extra)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory(cohort_periods))
    return ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=n_trials)


def test_dsr_value_computed_despite_earlier_gate_failure(tmp_path, monkeypatch):
    """Akzeptanzkriterium (#636): ``deflated_dsr`` ist ein berechneter Wert, obwohl der promotete
    Holdout-Vektor bereits an einem FRÜHEREN Gate (``oos_eligible=False``) scheitert."""
    global_params = {"sma_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.05, 0.06])

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02,
                                    n_periods=200, eligible=False)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01,
                                    n_periods=200, eligible=True)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )

    assert res["holdout_passed"] is False
    assert res["status"] == "REJECTED_ON_HOLDOUT"
    # Vor #636 wäre dieser Block nie erreicht worden ⇒ alle drei Felder None.
    assert res["metrics_symbol"]["deflated_sr0"] is not None
    assert res["metrics_symbol"]["deflated_dsr"] is not None
    assert res["metrics_symbol"]["deflation_dsr_z"] is not None


def test_small_cohort_below_min_triggers_var_floor_fallback(tmp_path, monkeypatch):
    """Akzeptanzkriterium (#636/#653): unter ``deflation_n < deflation_min_cohort`` (Default 10)
    dominiert der theoretisch begründete (Lo 2002), T-bewusste Varianz-Floor die 2-Punkte-
    Stichproben-Varianz (STETIGER Shrinkage, #653 — kein harter Cutover mehr wie vor #653)."""
    global_params = {"sma_period": 20}
    # Zwei eligible Trials mit fast identischem SR ⇒ winzige empirische Varianz, weit unter dem Floor.
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.030, 0.032])

    fixed_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.03, n_periods=200)
    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
    )

    assert res["metrics_symbol"]["deflation_n_eligible"] == 2
    assert res["metrics_symbol"]["deflation_used_var_floor"] is True
    # Issue #653 — die Kohorte trägt n_periods=200 (aus den Trial-Fixtures) ⇒ theoretical_var =
    # lo2002_sharpe_variance(0.0, 200) = 1/200; die winzige 2-Punkte-Stichprobenvarianz (≈1e-6)
    # trägt nur das Rest-Gewicht (1 − λ(2)) bei.
    from automation.optimizer.deflation import lo2002_sharpe_variance, _cohort_shrinkage_weight
    import statistics as _st
    observed_var = _st.pvariance([0.030, 0.032])
    theoretical_var = lo2002_sharpe_variance(0.0, 200)
    weight = _cohort_shrinkage_weight(2, 10)
    expected_var = weight * theoretical_var + (1.0 - weight) * observed_var
    expected_sr0 = sr0_multiple_testing(expected_var, 2)
    assert res["metrics_symbol"]["deflated_sr0"] == pytest.approx(expected_sr0)


def test_trial_passing_all_gates_is_rejected_on_dsr_alone(tmp_path, monkeypatch, caplog):
    """Akzeptanzkriterium (#636): ein Trial, der alle früheren Gates passiert (positiver Punkt-
    Sortino, DD im Cap, R_symbol schlägt R_global deutlich) und NUR an der DSR scheitert, wird via
    ``[DSR-Drop #618]`` korrekt zurückgewiesen."""
    global_params = {"sma_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025])

    # sortino_ratio (Punkt-Gate/Reward-Skala) 2.0 vs. 0.5 — derselbe klare Edge wie in
    # test_per_symbol_promotion.test_promotion_pass. sortino_period (DSR-Skala) 0.03 liegt nahe
    # der Multiple-Testing-Schwelle SR₀ ⇒ DSR weit unter der 0.95-Konfidenz.
    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    with caplog.at_level(logging.WARNING, logger="optimizer"):
        res = confirm.confirm_per_symbol_promotion(
            study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
            # Issue #1091/#1239: resolvierte (minimale) Familien-N haelt diese Fixture aus dem
            # neuen unbedingten Hard-Stop heraus, ohne deflation_n_effective zu veraendern
            # (max(deflation_n, 1) == deflation_n bei deflation_n >= 2) -- dieser Test prueft den
            # DSR-Drop, nicht die Familien-Multiplizitaet.
            deflation_n_family=1,
        )

    assert res["R_symbol"] > res["R_global"] + res["promotion_margin"]
    assert res["metrics_symbol"]["deflated_dsr"] is not None
    assert res["metrics_symbol"]["deflated_dsr"] < 0.95
    assert res["holdout_passed"] is False
    # Issue #1002/#1154 (Katalog #1170) — der DSR-Drop ist eine Deflations-, keine Holdout-Gate-
    # Ablehnung: die Holdout-Stufe selbst war bestanden (siehe R_symbol/R_global-Assertion oben).
    assert res["status"] == "REJECTED_ON_DEFLATION"
    assert res["blocking_stage"] == "deflation"
    assert any("[DSR-Drop #618]" in r.message for r in caplog.records)
