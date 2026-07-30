"""Issue #822 (P0, Katalog #822-#827, GitHub-Issue #750) — ``oos_evaluated`` zählt Trials ohne
Teststatistik in ``n_family``.

Root-Cause: ``sweep._family_n_from_studies`` summierte je Symbol alle Trials mit
``oos_evaluated is True``. Mindestens 624 Trials im analysierten Lauf trugen aber KEINEN
verwertbaren Selektionsstatistik-Wert (617 ``SORTINO_GUARD_TRIPPED``, 7 ``EQUITY_NONPOSITIVE`` —
``oos_psr = None`` in beiden Fällen). Die Deflated Sharpe Ratio korrigiert für ``N`` gezogene
Kandidaten MIT definierter Teststatistik — ein Trial ohne Sortino/PSR kann das Maximum unter H₀
nicht beeinflusst haben (dieselbe Argumentation wie #814, eine Ebene tiefer).

Fix: neues Trial-User-Attr ``oos_selection_statistic_available`` (True genau dann, wenn
``oos_psr is not None``); ``_family_n_from_studies`` zählt dieses Attr statt ``oos_evaluated``.
``oos_evaluated`` bleibt unverändert als Aktivitäts-Telemetrie erhalten.
"""
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro, sweep, trial_config
from automation.optimizer.invariants import check_family_n_statistic_coverage


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_backtest(payload):
    def _fake(trial_dir, manifest_path, **_kwargs):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), "utf-8")
        return out
    return _fake


# ── Trial-Attr-Stempelung (Erzeugungsseite) ─────────────────────────────────────────────────────
def test_trial_with_psr_carries_statistic_available_true(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
        "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05, "total_trades": 22,
                        "total_return": 0.31, "win_rate": 0.55, "profit_factor": 1.4,
                        "expectancy": 0.02, "psr": 0.8, "sortino_period": 0.03}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    assert study.trials[0].user_attrs.get("oos_selection_statistic_available") is True


def test_trial_without_psr_but_evaluated_carries_statistic_available_false(tmp_path, monkeypatch):
    """Simuliert SORTINO_GUARD_TRIPPED/EQUITY_NONPOSITIVE: oos_evaluated=True, aber kein PSR."""
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": False, "win_count": 0,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [],
        "oos_metrics": {"sortino_ratio": None, "max_drawdown": 0.05, "total_trades": 22,
                        "total_return": -0.02, "win_rate": 0.3, "profit_factor": 0.9,
                        "expectancy": -0.001, "psr": None, "sortino_period": None}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    attrs = study.trials[0].user_attrs
    assert attrs.get("oos_evaluated") is True
    assert attrs.get("oos_selection_statistic_available") is False
    assert "oos_psr" not in attrs  # unveraendert: kein Sentinel-Wert, der Key entfaellt ganz


def test_not_evaluated_trial_statistic_available_false(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": {
        "oos_evaluated": False, "oos_eligible": False,
        "oos_metrics": {"win_rate": 0.0, "total_trades": 0, "max_drawdown": 0.0,
                        "profit_factor": 0.0, "expectancy": 0.0, "total_return": 0.0,
                        "sortino_ratio": 0.0, "psr": 0.0, "sortino_period": 0.0}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    assert study.trials[0].user_attrs.get("oos_selection_statistic_available") is False


# ── _family_n_from_studies zählt oos_selection_statistic_available (nicht oos_evaluated) ────────
class _FakeTrial:
    def __init__(self, **attrs):
        self.user_attrs = attrs


class _FakeStudy:
    def __init__(self, trials, user_attrs=None):
        self.trials = trials
        self._user_attrs = user_attrs or {}

    @property
    def user_attrs(self):
        return dict(self._user_attrs)


def test_family_n_excludes_trials_without_statistic():
    """Akzeptanzkriterium #822: Study mit 10 Trials, davon 3 mit oos_psr=None (oos_evaluated=True)
    ⇒ n_family liefert 7."""
    trials = (
        [_FakeTrial(oos_evaluated=True, oos_selection_statistic_available=True) for _ in range(7)]
        + [_FakeTrial(oos_evaluated=True, oos_selection_statistic_available=False) for _ in range(3)]
    )
    study = _FakeStudy(trials)
    pairs = [("S", "SYM.ETORO", "OK")]
    result = sweep._family_n_from_studies(pairs, [study])
    assert result["SYM.ETORO"] == 7


def test_family_n_excluded_breakdown_by_reason():
    trials = [
        _FakeTrial(oos_evaluated=True, oos_selection_statistic_available=True),
        _FakeTrial(oos_evaluated=True, oos_selection_statistic_available=False,
                   inference_diagnostics=[{"code": "SORTINO_GUARD_TRIPPED"}]),
        _FakeTrial(oos_evaluated=True, oos_selection_statistic_available=False,
                   inference_diagnostics=[{"code": "SORTINO_GUARD_TRIPPED"}]),
        _FakeTrial(oos_evaluated=True, oos_selection_statistic_available=False,
                   inference_diagnostics=[{"code": "EQUITY_NONPOSITIVE"}]),
        _FakeTrial(oos_evaluated=True, oos_selection_statistic_available=False,
                   inference_diagnostics=[]),
        _FakeTrial(oos_evaluated=False),  # nicht evaluiert -- nicht mitgezaehlt
    ]
    study = _FakeStudy(trials)
    pairs = [("S", "SYM.ETORO", "OK")]
    breakdown = sweep._family_n_excluded_breakdown_from_studies(pairs, [study])
    assert breakdown["SYM.ETORO"] == {"sortino_guard": 2, "equity_ruined": 1, "other": 1}


def test_family_n_sums_across_multiple_strategies_of_same_symbol():
    study1 = _FakeStudy([_FakeTrial(oos_evaluated=True, oos_selection_statistic_available=True)
                        for _ in range(4)])
    study2 = _FakeStudy([_FakeTrial(oos_evaluated=True, oos_selection_statistic_available=True)
                        for _ in range(6)])
    pairs = [("S1", "SYM.ETORO", "OK"), ("S2", "SYM.ETORO", "OK")]
    result = sweep._family_n_from_studies(pairs, [study1, study2])
    assert result["SYM.ETORO"] == 10


# ── check_family_n_statistic_coverage (Invariante) ───────────────────────────────────────────────
def test_invariant_fails_when_raw_exceeds_trials_with_statistic():
    trials = [{"oos_selection_statistic_available": True}] * 5
    result = check_family_n_statistic_coverage(trials, deflation_n_family_raw=8)
    assert result.passed is False


def test_invariant_passes_when_raw_within_bounds():
    trials = [{"oos_selection_statistic_available": True}] * 5
    result = check_family_n_statistic_coverage(trials, deflation_n_family_raw=5)
    assert result.passed is True


def test_invariant_not_applicable_when_raw_is_none():
    result = check_family_n_statistic_coverage([], deflation_n_family_raw=None)
    assert result.passed is True


# ── confirm.py Telemetrie-Feld ───────────────────────────────────────────────────────────────────
def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods, total_return=0.02,
                    total_trades=50):
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


def test_confirm_telemetry_carries_excluded_breakdown(tmp_path, monkeypatch):
    """Integrationstest (angelehnt an test_issue_652): bei aktiver Deflation (deflated_selection=
    True, >= 2 eligible Trials) wird deflation_n_family_excluded_no_statistic unveraendert in
    metrics_symbol durchgereicht."""
    import itertools
    from automation.optimizer import confirm

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "optimizer.json").write_text(json.dumps({
        "promotion_margin": 0.10, "penalty_unevaluable_oos": -10,
        "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0,
        "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
        "bonus_coverage_weight": 1.0, "lambda_reg": 0.25,
    }))
    (cfg_dir / "tournament.json").write_text(json.dumps({
        "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
        "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
        "oos_min_win_rate": 0.0,
    }))
    (cfg_dir / "backtest.json").write_text(json.dumps({
        "walk_forward": {"holdout_days": 45, "is_window_days": 120, "oos_window_days": 45,
                         "splits": 1, "embargo_period_days": 0}}))

    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(confirm, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(trial_config, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)

    cohort_periods = itertools.cycle([0.02 + 0.001 * i for i in range(20)])

    def cohort_fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp = next(cohort_periods)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=200))

    monkeypatch.setattr(ro, "run_backtest", cohort_fake)
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=20)

    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    def holdout_fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        return _write_result(trial_dir, fixed_result)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params={"price_breakout_period": 20},
        run_backtest=holdout_fake, deflation_n_family=496,
        deflation_n_family_excluded_no_statistic={"sortino_guard": 5, "equity_ruined": 1})

    assert res["metrics_symbol"]["deflation_n_family"] == 496
    assert res["metrics_symbol"]["deflation_n_family_excluded_no_statistic"] == {
        "sortino_guard": 5, "equity_ruined": 1}
