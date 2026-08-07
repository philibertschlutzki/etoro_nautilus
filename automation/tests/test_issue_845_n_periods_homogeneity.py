"""Issue #845 (P1) — n_periods streut um Faktor 45 über die Familie und macht DSR/PSR
inkommensurabel.

Root-Cause: ``confirm.confirm_per_symbol_promotion`` poolt die per-Trial-Sortinos einer DSR-Kohorte
(``deflation_var = pvariance(cohort_sr)``) so, als traegen alle Trials eine annaehernd konstante
Stichprobengroesse T (``oos_n_periods``) — dieselbe Annahme, die den Lo-2002-T-bewussten
Varianz-Floor motiviert. In der Praxis wurde ein Faktor 45 zwischen dem kleinsten und groessten
``oos_n_periods`` derselben Kohorte beobachtet: die gepoolten Sortinos sind dann nicht mehr
kommensurabel.

Fix (dieser Katalog-Eintrag, bewusst SCOPE-REDUZIERT — siehe backtest_runner.py-Kommentar bei
SORTINO_INSUFFICIENT_DOWNSIDE fuer die dokumentierte Deferral-Entscheidung zu Punkt 2 des
Issue-Texts):
1. ``downside_obs`` (der Downside-Beobachtungs-Nenner aus ``_compute_sortino``) ist jetzt ein
   explizites Feld in ``_calculate_stats``'s Rueckgabedict und ein Trial-User-Attr
   (``oos_downside_obs``), analog zu ``n_periods``/``oos_n_periods``.
2. ``confirm.py`` berechnet ``deflation_n_periods_ratio = max(oos_n_periods)/min(oos_n_periods)``
   ueber die DSR-Kohorte und unterdrueckt ``deflated_sr0``/``deflated_dsr``/``deflation_dsr_z`` mit
   ``deflation_skipped_reason='N_PERIODS_HETEROGENEOUS'``, sobald diese Ratio
   ``deflation_max_n_periods_ratio`` (tournament.json, Default 4.0) ueberschreitet.
3. ``invariants.check_family_n_periods_homogeneity`` ist ein Regressionswaechter, der prueft, dass
   diese Unterdrueckung bei ueberschrittener Ratio tatsaechlich greift.

Akzeptanzkriterien:
- AK-1: ``_calculate_stats`` exportiert ``downside_obs`` (== manuell gezaehlter Downside-Nenner) im
  Erfolgspfad, bleibt aber ``None``, wenn der Trial VOR der Downside-Berechnung ausgestiegen ist.
- AK-2: ``downside_obs`` bleibt als Zahl erhalten, selbst wenn SORTINO_INSUFFICIENT_DOWNSIDE
  auslöst (sortino/psr fallen dabei auf None, downside_obs NICHT).
- AK-3: ``check_family_n_periods_homogeneity`` FAILt genau dann, wenn die Ratio die Schwelle
  überschreitet UND trotzdem ein deflated_dsr/deflation_dsr_z exportiert wurde.
- AK-4: Eine Kohorte mit Faktor-45-n_periods-Streuung unterdrückt deflated_sr0/deflated_dsr/
  deflation_dsr_z vollständig, mit dokumentiertem Grund, und bleibt check_sr0_coherence-konform.
"""
import itertools
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from automation.backtest_runner import _calculate_stats
from automation.optimizer import invariants as inv
from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config


# ── AK-1/AK-2: downside_obs-Telemetrie in _calculate_stats ──────────────────────────────────────
def _mtm_from_rets(rets):
    vals = [1000.0]
    for r in rets:
        vals.append(vals[-1] * (1 + r))
    idx = pd.date_range("2025-01-01", periods=len(vals), freq="1h", tz="UTC")
    return pd.Series(vals, index=idx)


def test_downside_obs_matches_manual_count_in_success_path():
    rng = np.random.default_rng(45)
    rets = rng.normal(0.001, 0.01, 100).tolist()
    expected_downside = sum(1 for r in rets if r < 0.0)
    assert expected_downside > 0

    series = _mtm_from_rets(rets)
    with patch("automation.backtest_runner._read_sortino_mar", return_value=0.0), \
         patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=0), \
         patch("automation.backtest_runner._read_sortino_min_periods_absolute", return_value=0), \
         patch("automation.backtest_runner._read_sortino_numeric_guard_min_periods", return_value=None):
        stats = _calculate_stats(
            pnl_list=[1.0] * 50 + [-1.0] * 50, hold_list=[(3600 * 10**9, 1.0)] * 100,
            starting_capital=1000.0, mtm_series=series, min_trades_for_sortino=10)

    assert stats["sortino_ratio"] is not None
    assert stats["downside_obs"] == expected_downside


def test_downside_obs_none_when_too_few_trades_for_sortino():
    """Fruehester Ausstieg (n < min_trades_sortino) liegt VOR der downside_obs-Berechnung —
    das Feld bleibt None, nicht 0 (kein stillschweigender Fallback)."""
    series = _mtm_from_rets([0.01, -0.01, 0.005])
    stats = _calculate_stats(
        pnl_list=[1.0, -1.0, 1.0], hold_list=[(3600 * 10**9, 1.0)] * 3,
        starting_capital=1000.0, mtm_series=series, min_trades_for_sortino=1000)
    assert stats["sortino_ratio"] is None
    assert stats.get("downside_obs") is None


def test_downside_obs_survives_sortino_downside_shrinkage():
    """Issue #944 (Katalog B) — downside_obs unter der konfigurierten Schwelle fuehrt seit #944
    NICHT MEHR zu SORTINO_INSUFFICIENT_DOWNSIDE/sortino=None (Anti-Selektions-Filter entfernt,
    Pitfall #296): dd_dev wird stattdessen Richtung der Gesamtstreuung geschrumpft
    (SORTINO_DOWNSIDE_SHRUNK). downside_obs bleibt in jedem Fall als Zahl erhalten, nicht None."""
    rng = np.random.default_rng(7)
    # Ueberwiegend positive Rendite -> wenige Downside-Beobachtungen.
    rets = rng.normal(0.02, 0.001, 40).tolist()
    rets[0] = -0.001  # genau 1 Downside-Beobachtung erzwingen
    for i in range(1, len(rets)):
        rets[i] = abs(rets[i])
    expected_downside = sum(1 for r in rets if r < 0.0)
    assert expected_downside == 1

    series = _mtm_from_rets(rets)
    with patch("automation.backtest_runner._read_sortino_mar", return_value=0.0), \
         patch("automation.backtest_runner._read_sortino_min_downside_observations", return_value=30), \
         patch("automation.backtest_runner._read_sortino_numeric_guard_min_periods", return_value=None):
        stats = _calculate_stats(
            pnl_list=[1.0] * 40, hold_list=[(3600 * 10**9, 1.0)] * 40,
            starting_capital=1000.0, mtm_series=series, min_trades_for_sortino=10)

    diag_codes = {d["code"] for d in stats["inference_diagnostics"]}
    assert "SORTINO_INSUFFICIENT_DOWNSIDE" not in diag_codes
    assert "SORTINO_DOWNSIDE_SHRUNK" in diag_codes
    assert stats["downside_obs"] == expected_downside


# ── AK-3: check_family_n_periods_homogeneity (pure Invariante) ──────────────────────────────────
def test_ratio_unknown_is_not_applicable():
    result = inv.check_family_n_periods_homogeneity({})
    assert result.passed is True
    assert result.actual is None


def test_ratio_below_threshold_with_dsr_signal_passes():
    result = inv.check_family_n_periods_homogeneity(
        {"deflation_n_periods_ratio": 2.0, "deflated_dsr": 0.4, "deflation_dsr_z": 1.1},
        max_ratio=4.0)
    assert result.passed is True


def test_ratio_above_threshold_without_dsr_signal_passes():
    """Die korrekt gesuppresste Kombination (Ratio ueber der Schwelle, aber KEIN dsr-Signal
    exportiert) ist der ERWARTETE Zustand nach dem confirm.py-Fix -- PASS."""
    result = inv.check_family_n_periods_homogeneity(
        {"deflation_n_periods_ratio": 45.0, "deflated_dsr": None, "deflation_dsr_z": None},
        max_ratio=4.0)
    assert result.passed is True


def test_ratio_above_threshold_with_dsr_signal_fails():
    """Die #845-Regression: Ratio ueberschritten, aber ein dsr-Signal ist trotzdem gesetzt --
    die Suppression hat nicht gegriffen."""
    result = inv.check_family_n_periods_homogeneity(
        {"deflation_n_periods_ratio": 45.0, "deflated_dsr": 0.3, "deflation_dsr_z": 0.9},
        max_ratio=4.0)
    assert result.passed is False
    assert result.actual == 45.0
    assert result.severity == "high"


# ── AK-4: End-to-End ueber confirm.confirm_per_symbol_promotion ─────────────────────────────────
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


def _hetero_cohort_factory(specs):
    """specs: Liste von (sortino_period, n_periods)-Paaren, je Trial zyklisch zugewiesen."""
    it = itertools.cycle(specs)

    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp, np_ = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=np_))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _assert_coherent(res):
    result = inv.check_sr0_coherence(res["metrics_symbol"])
    assert result.passed, result.detail


def test_heterogeneous_cohort_suppresses_dsr_and_sr0_with_reason(tmp_path, monkeypatch):
    """Der #845-Kernfall: zwei Trials derselben Kohorte mit n_periods=50 und n_periods=2500
    (Ratio=50, weit ueber dem Default 4.0) -- deflated_sr0/deflated_dsr/deflation_dsr_z werden
    vollstaendig unterdrueckt, mit deflation_skipped_reason='N_PERIODS_HETEROGENEOUS'."""
    _isolate(monkeypatch, tmp_path)
    global_params = {"sma_period": 20}
    monkeypatch.setattr(
        ro, "run_backtest", _hetero_cohort_factory([(0.05, 50), (0.06, 2500)]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflated_sr0") is None
    assert res["metrics_symbol"].get("deflated_dsr") is None
    assert res["metrics_symbol"].get("deflation_dsr_z") is None
    assert res["metrics_symbol"].get("deflation_skipped_reason") == "N_PERIODS_HETEROGENEOUS"
    assert res["metrics_symbol"].get("deflation_n_periods_ratio") == pytest.approx(50.0)
    _assert_coherent(res)


def test_homogeneous_cohort_below_ratio_still_computes_dsr(tmp_path, monkeypatch):
    """Regressionswaechter: eine Kohorte mit moderater n_periods-Streuung (Ratio 1.2, unter dem
    Default 4.0) bleibt UNVERAENDERT -- DSR wird ganz normal berechnet (kein False-Positive der
    neuen Suppression)."""
    _isolate(monkeypatch, tmp_path)
    global_params = {"sma_period": 20}
    monkeypatch.setattr(
        ro, "run_backtest", _hetero_cohort_factory([(0.05, 200), (0.06, 240)]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflated_sr0") is not None
    assert res["metrics_symbol"].get("deflated_dsr") is not None
    assert res["metrics_symbol"].get("deflation_dsr_z") is not None
    assert res["metrics_symbol"].get("deflation_skipped_reason") is None
    _assert_coherent(res)


def test_custom_deflation_max_n_periods_ratio_threshold_is_honored(tmp_path, monkeypatch):
    """Ein Betreiber kann die Schwelle ueber tournament.json['deflation_max_n_periods_ratio']
    verschaerfen -- eine Ratio von 1.2, die den Default (4.0) unterschreitet, aber eine eigens
    gesetzte Schwelle von 1.1 ueberschreitet, muss ebenfalls suppressen."""
    _isolate(monkeypatch, tmp_path, tournament_extra={"deflation_max_n_periods_ratio": 1.1})
    global_params = {"sma_period": 20}
    monkeypatch.setattr(
        ro, "run_backtest", _hetero_cohort_factory([(0.05, 200), (0.06, 240)]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflation_skipped_reason") == "N_PERIODS_HETEROGENEOUS"
    _assert_coherent(res)
