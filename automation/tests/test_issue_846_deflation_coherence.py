"""Issue #846 (P1) — `check_sr0_coherence` FAILt 115× (#651-Wiederkehr, vierter Katalog).

Root-Cause: `deflated_sr0` wird aus der COHORT-Varianz (alle eligiblen Trials der Study,
`deflation_n >= 2`) berechnet; `deflated_dsr`/`deflation_dsr_z` werden dagegen aus dem
SPEZIFISCHEN promoteten (Median-Rang-)Holdout-Trial abgeleitet
(`promoted_m_symbol.oos_sortino_period`). Das sind zwei GETRENNTE Gates auf zwei GETRENNTEN
Datenmengen: fällt der promotete Trial selbst durch sein Gate (kein `oos_sortino_period`), bleibt
`deflation_sr0` gesetzt, obwohl `deflated_dsr`/`deflation_dsr_z` nie berechnet wurden — genau der
Zustand, den `invariants.check_sr0_coherence` als Verletzung meldet.

Fix: `confirm.py` erzwingt an der Export-Grenze dieselbe Kohärenz-Garantie, die #651 bereits für
den (damals anders gelagerten) Bit-Identitäts-Fall forderte — `deflated_sr0` wird NIE ohne ein
begleitendes `deflated_dsr`/`deflation_dsr_z` exportiert, mit einem `deflation_skipped_reason`
(`SMALL_COHORT`/`NO_STATISTIC`) als Erklärung statt eines stillen Teilzustands.

Akzeptanzkriterien:
- AK-1: check_sr0_coherence PASST 826/826 im Re-Run (hier: für jeden konstruierten Fall).
- AK-2: Jeder Frühausstiegspfad hat einen Test, der die Vollständigkeit prüft.
- AK-3: Es existiert keine Zuweisung an deflated_dsr ausserhalb der Konsumstelle (nicht separat
  getestet — struktureller Nebeneffekt der Export-Kohärenz-Wächter-Platzierung).
"""
import itertools
import json
from pathlib import Path

import pytest

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
    return ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=n_trials)


def _assert_coherent(res):
    """AK-1: check_sr0_coherence PASST fuer dieses Ergebnis."""
    result = inv.check_sr0_coherence(res["metrics_symbol"])
    assert result.passed, result.detail


def test_ak1_normal_case_stays_coherent_and_computed(tmp_path, monkeypatch):
    """Regressionswaechter: der Normalfall (Kohorte UND promoteter Trial beide auswertbar) bleibt
    unveraendert -- alle drei Felder gesetzt, kein deflation_skipped_reason."""
    global_params = {"sma_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.05, 0.06])
    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"]["deflated_sr0"] is not None
    assert res["metrics_symbol"]["deflated_dsr"] is not None
    assert res["metrics_symbol"]["deflation_dsr_z"] is not None
    assert res["metrics_symbol"].get("deflation_skipped_reason") is None
    _assert_coherent(res)


def test_ak2_promoted_trial_without_statistic_suppresses_sr0_too(tmp_path, monkeypatch):
    """Der #846-Kernfall: die Kohorte hat genug Mitglieder (deflation_n=2 >= 2, deflation_sr0
    waere berechenbar), aber der PROMOTETE Holdout-Trial selbst traegt kein oos_sortino_period
    (sortino_period=None im Fixture). Vor #846 waere deflated_sr0 gesetzt, deflated_dsr/
    deflation_dsr_z aber None geblieben -- eine Verletzung von check_sr0_coherence. Nach #846 wird
    auch deflated_sr0 unterdrueckt, mit deflation_skipped_reason='NO_STATISTIC'."""
    global_params = {"sma_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.05, 0.06])
    # sortino_period=None -> promoted_m_symbol.oos_sortino_period ist None.
    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=None, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflated_sr0") is None
    assert res["metrics_symbol"].get("deflated_dsr") is None
    assert res["metrics_symbol"].get("deflation_dsr_z") is None
    assert res["metrics_symbol"].get("deflation_skipped_reason") == "NO_STATISTIC"
    _assert_coherent(res)


def test_small_cohort_below_two_sets_skipped_reason(tmp_path, monkeypatch):
    """deflation_n < 2 (nur 1 eligibler Trial in der Kohorte) -> SMALL_COHORT, alle drei DSR-Felder
    bleiben None (unveraendertes Verhalten), aber jetzt mit dokumentiertem Grund."""
    global_params = {"sma_period": 20}
    # n_trials=1 mit genau einem eligiblen Cohort-Mitglied -> deflation_n == 1 < 2.
    study = _build_study(tmp_path, monkeypatch, n_trials=1, cohort_periods=[0.05])
    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflated_sr0") is None
    assert res["metrics_symbol"].get("deflation_skipped_reason") == "SMALL_COHORT"
    _assert_coherent(res)
