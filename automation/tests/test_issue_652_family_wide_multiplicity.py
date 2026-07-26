"""Issue #652 — Multiple-Testing-N ist per-Study, die Selektion aber cross-Study.

Die Promotions-DSR jeder Strategie nutzte bislang ausschliesslich `deflation_n_eligible` DIESER
Study (9–162). Der Lauf wählt aber den besten von mehreren Strategien-Studies je Symbol — die
familienweite Multiplizität (`deflation_n_family`, bereits seit #625 als reine Sweep-Telemetrie
berechnet) floss in KEINE Promotion-Entscheidung ein. SR₀ = √V·E[max_N] wächst monoton in N; das
per-Study-N unterschätzt daher die tatsächliche Anzahl "Schüsse aufs Tor" der Gesamt-Selektion.

Fix: `sweep.run_per_symbol_sweep` berechnet die familienweite N je Symbol JETZT bereits VOR den
Promotion-Entscheidungen (`_family_n_from_studies`, Phase 1 vs. Phase 2) und reicht sie an
`confirm.confirm_per_symbol_promotion(..., deflation_n_family=...)` durch. Dort wird
`deflation_n_effective = max(deflation_n, deflation_n_family)` für die SR₀-Berechnung verwendet
(nie kleiner als das bereits lokal bekannte per-Study-N).
"""
import itertools
import json
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro, confirm, sweep
from automation.optimizer import trial_config
from automation.optimizer.deflation import sr0_multiple_testing, expected_max_standard_normal


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

    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        sp = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=n_periods))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_periods, tournament_extra=None):
    _isolate(monkeypatch, tmp_path, tournament_extra)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory(cohort_periods))
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


# ── Kernkriterium (#652): fixes V, N_family > N_study ⇒ SR0(N_family) > SR0(N_study) ⇒ DSR fällt ──
def test_family_n_raises_sr0_over_per_study_n():
    """Bei fixer Varianz wächst SR₀ = √V·E[max_N] monoton in N (expected_max_standard_normal)."""
    var = 1.8e-3
    sr0_study = sr0_multiple_testing(var, 37)
    sr0_family = sr0_multiple_testing(var, 496)
    assert sr0_family > sr0_study
    assert expected_max_standard_normal(496) > expected_max_standard_normal(37)


def test_dynamicbreakout_fixture_dsr_lower_at_family_n():
    """DynamicBreakout-Fixture (#652-Referenz aus #661): DSR(N=496) < DSR(N=37) bei identischem
    ŜR/T/V — die familienweite Hürde ist strenger."""
    from automation.optimizer.deflation import deflated_sharpe_ratio
    var = 1.8e-3
    sr, n_periods = 0.11386, 202
    sr0_37 = sr0_multiple_testing(var, 37)
    sr0_496 = sr0_multiple_testing(var, 496)
    dsr_37 = deflated_sharpe_ratio(sr, n_periods, sr0=sr0_37)
    dsr_496 = deflated_sharpe_ratio(sr, n_periods, sr0=sr0_496)
    assert dsr_496 < dsr_37


def test_confirm_uses_family_n_for_promotion_decision(tmp_path, monkeypatch):
    """Integrationstest: `confirm_per_symbol_promotion(..., deflation_n_family=...)` verwendet
    nachweislich N >= deflation_n_family für die SR₀-Berechnung (deflation_n_effective in der
    Telemetrie), nicht nur das per-Study-N."""
    global_params = {"price_breakout_period": 20}
    cohort_periods = [0.02 + 0.001 * i for i in range(20)]  # 20 leicht streuende per-Study-Sortinos
    study = _build_study(tmp_path, monkeypatch, n_trials=20, cohort_periods=cohort_periods)

    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    res_no_family = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
    )
    res_with_family = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=496,
    )

    assert res_no_family["metrics_symbol"]["deflation_n_eligible"] == 20
    assert res_no_family["metrics_symbol"]["deflation_n_effective"] == 20
    assert res_no_family["metrics_symbol"]["deflation_n_family"] == 0

    assert res_with_family["metrics_symbol"]["deflation_n_family"] == 496
    assert res_with_family["metrics_symbol"]["deflation_n_effective"] == 496
    # Grössere Multiplizität ⇒ höheres SR₀ ⇒ (bei identischem promotetem ŜR) niedrigere/gleiche DSR.
    assert res_with_family["metrics_symbol"]["deflated_sr0"] > res_no_family["metrics_symbol"]["deflated_sr0"]
    assert res_with_family["metrics_symbol"]["deflated_dsr"] <= res_no_family["metrics_symbol"]["deflated_dsr"]


def test_family_n_never_shrinks_below_per_study_n(tmp_path, monkeypatch):
    """Eine kleinere (oder fehlende) Familien-Zahl darf das bereits lokal bekannte per-Study-N
    NIE unterschreiten — `deflation_n_effective = max(deflation_n, deflation_n_family)`."""
    global_params = {"price_breakout_period": 20}
    cohort_periods = [0.02 + 0.001 * i for i in range(20)]
    study = _build_study(tmp_path, monkeypatch, n_trials=20, cohort_periods=cohort_periods)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=3,   # kleiner als das per-Study-N (20)
    )
    assert res["metrics_symbol"]["deflation_n_effective"] == 20


def test_family_n_from_studies_sums_across_strategies_of_same_symbol():
    """`sweep._family_n_from_studies` summiert die evaluierten Trials (#784 — VERSUCHE, nicht
    ÜBERLEBENDE) über ALLE Strategien-Studies DESSELBEN Symbols — die Grundlage für
    `deflation_n_family` (Phase-1-Ergebnis, vor jeder Promotion bekannt)."""
    class _T:
        def __init__(self, evaluated):
            self.user_attrs = {"oos_evaluated": evaluated}

    class _Study:
        def __init__(self, trials):
            self.trials = trials
            self.user_attrs = {}

    pairs = [
        ("StratA", "TSLA.ETORO", "OK"), ("StratB", "TSLA.ETORO", "OK"),
        ("StratA", "AAPL.ETORO", "OK"),
    ]
    studies = [
        _Study([_T(True), _T(True), _T(False)]),   # 2 evaluiert
        _Study([_T(True)] * 5),                     # 5 evaluiert
        _Study([_T(True)] * 3),                     # 3 evaluiert (anderes Symbol)
    ]
    # 'attempted'-Modus (kein Budget-Floor) reproduziert die reine Summe der evaluierten Trials.
    family_n = sweep._family_n_from_studies(
        pairs, studies, tournament_cfg={"deflation_family_floor_mode": "attempted"})
    assert family_n == {"TSLA.ETORO": 7, "AAPL.ETORO": 3}
