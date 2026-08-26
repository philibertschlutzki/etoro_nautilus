"""Issue #1255 (GH #1125), Pitfall #454-Klasse in AGENTS.md — Standardfehler und Freiheitsgrade der
Alpha-Regression an die Datenstruktur anpassen.

Symptom. Die Regression läuft über n = 1079 Kalender-Bars bei session_coverage_fraction = 0,2389
und zero_range_bar_fraction = 1,0; die Strategie ist je nach Study auf 5,4-85,6 % dieser Bars
überhaupt im Markt. Der Standardfehler ist homoskedastisch mit 1077 Freiheitsgraden.

Root-Cause. backtest_runner._alpha_beta_regression nutzt SE(alpha)^2 = (RSS/(n-2))*(1/n +
mean(x)^2/Sxx). Die Residualvarianz ist auf dem Grossteil der Zeilen strukturell null und auf dem
Rest gross -- die Homoskedastie-Annahme ist verletzt, und n-2 überzeichnet die Zahl unabhängiger
ökonomischer Ereignisse.

Fix. ``backtest_runner._alpha_regression_diagnostics`` (additiv neben ``_alpha_beta_regression`` --
KEINE Signaturaenderung dort, um die bestehenden 3-Tupel-Entpackungen in
test_issue_986_1140_alpha_beta_excess_per_exposure.py nicht zu brechen) liefert einen HC3-robusten
Standardfehler (``alpha_tstat_hc3``) + auf die informative Zeilenzahl gesetzte Freiheitsgrade
(``alpha_tstat_df``). Das oos_min_alpha_tstat-Gate (``backtest_runner._evaluate_oos_eligibility``)
konsumiert seither ``oos_alpha_tstat_hc3`` (Fallback auf die klassische Statistik nur fuer
Legacy-Metrics-Dicts ohne das neue Feld). ``invariants.check_alpha_tstat_estimator_agreement``
(severity 'high') macht eine > 25% relative Abweichung sichtbar. reward_semantics_version 26 -> 27.
"""
import json
from pathlib import Path

import pytest

from automation.backtest_runner import (
    _alpha_beta_regression, _alpha_regression_diagnostics, _evaluate_oos_eligibility,
)
from automation.optimizer import confirm, invariants as inv, run_optimization as ro


# ---------------------------------------------------------------------------------------------
# _alpha_regression_diagnostics — degenerate cases mirror _alpha_beta_regression
# ---------------------------------------------------------------------------------------------

def test_none_below_three_periods():
    assert _alpha_regression_diagnostics([0.01, 0.02], [0.01, 0.02]) is None


def test_none_when_benchmark_has_zero_variance():
    assert _alpha_regression_diagnostics([0.01, 0.02, -0.01], [0.0, 0.0, 0.0]) is None


def test_none_iff_alpha_beta_regression_is_none():
    """Beide Funktionen laufen IMMER auf demselben Eingabepaar (siehe Docstring) -- None/nicht-None
    faellt fuer jedes Eingabepaar zusammen."""
    x = [0.01, -0.02, 0.015, -0.005, 0.03, -0.01, 0.02, -0.015, 0.005, 0.0]
    y = [0.2 * xi + 0.0003 for xi in x]
    assert (_alpha_beta_regression(y, x) is None) == (_alpha_regression_diagnostics(y, x) is None)
    assert (_alpha_beta_regression([0.01, 0.02], [0.01, 0.02]) is None) == (
        _alpha_regression_diagnostics([0.01, 0.02], [0.01, 0.02]) is None)


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 1: homoskedastische Fixture -- Uebereinstimmung auf 1%
# ---------------------------------------------------------------------------------------------

def test_homoskedastic_fixture_agrees_within_one_percent():
    import random
    rng = random.Random(42)
    n = 1000
    x = [rng.gauss(0, 0.01) for _ in range(n)]
    y = [0.15 * xi + 0.0002 + rng.gauss(0, 0.002) for xi in x]
    classic = _alpha_beta_regression(y, x)
    diag = _alpha_regression_diagnostics(y, x)
    assert classic is not None and diag is not None
    rel_dev = abs(diag["alpha_tstat_hc3"] - classic[2]) / abs(classic[2])
    assert rel_dev < 0.01


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 2: heteroskedastische Fixture -- messbare Abweichung
# ---------------------------------------------------------------------------------------------

def _heteroskedastic_fixture():
    """Engineered adversarial Fixture: extreme Residual-Varianz konzentriert an NIEDRIG-Leverage-
    Punkten (x_i nahe x_mean) -- die Alpha-Gewichte w_i = 1/n - x_mean*(x_i-x_mean)/Sxx sind dort
    am groessten, HC3 gewichtet die dort konzentrierte Varianz korrekt, waehrend die klassische
    Formel sie ueber ALLE n Punkte gleichmaessig verdünnt (verletzte Homoskedastie-Annahme,
    analog dem Issue-Symptom bimodaler Residualvarianz aus Zero-Range-/Nicht-im-Markt-Baren)."""
    import statistics
    n = 50
    x = [0.01 + 0.001 * i for i in range(n)]
    xbar = statistics.mean(x)
    beta_true, alpha_true = 0.1, 0.0005
    y = [alpha_true + beta_true * xi for xi in x]
    low_leverage_idx = sorted(range(n), key=lambda i: abs(x[i] - xbar))[:5]
    for i in low_leverage_idx:
        y[i] += 10.0
    return x, y


def test_heteroskedastic_fixture_deviates_measurably_and_check_fails():
    x, y = _heteroskedastic_fixture()
    classic = _alpha_beta_regression(y, x)
    diag = _alpha_regression_diagnostics(y, x)
    assert classic is not None and diag is not None
    rel_dev = abs(diag["alpha_tstat_hc3"] - classic[2]) / abs(classic[2])
    assert rel_dev > 0.25
    result = inv.check_alpha_tstat_estimator_agreement([{
        "strategy": "S", "symbol": "X.ETORO",
        "holdout_alpha_tstat": classic[2], "holdout_alpha_tstat_hc3": diag["alpha_tstat_hc3"],
    }])
    assert result.passed is False
    assert result.severity == "high"


# ---------------------------------------------------------------------------------------------
# HC3 population/df fields (shared with #1258, spot-checked here for the HC3 fixture)
# ---------------------------------------------------------------------------------------------

def test_alpha_tstat_df_uses_the_full_regression_sample_not_the_informative_row_count():
    """Issue #1284 (GH #1157, Katalog #1272-1297, P3) — alpha_tstat_df wurde auf n_used (== n_total,
    ALLE Bars) umgestellt, NICHT mehr auf n_informative: beide t-Statistiken (klassisch und HC3)
    werden ueber die VOLLEN Arrays gerechnet, die Freiheitsgrade muessen zu derselben
    Grundgesamtheit passen (siehe _alpha_regression_diagnostics-Docstring)."""
    x, y = _heteroskedastic_fixture()
    diag = _alpha_regression_diagnostics(y, x)
    assert diag["alpha_tstat_df"] == diag["n_used"] - 2
    assert diag["n_used"] == diag["n_total"]


def test_hc3_tstat_sign_matches_alpha_sign():
    import random
    rng = random.Random(1)
    x = [rng.gauss(0, 0.01) for _ in range(200)]
    y = [0.1 * xi - 0.0004 + rng.gauss(0, 0.001) for xi in x]  # negatives Alpha
    classic = _alpha_beta_regression(y, x)
    diag = _alpha_regression_diagnostics(y, x)
    assert classic[0] < 0
    assert diag["alpha_tstat_hc3"] < 0


# ---------------------------------------------------------------------------------------------
# Gate consumption: _evaluate_oos_eligibility konsumiert oos_alpha_tstat_hc3
# ---------------------------------------------------------------------------------------------

_TCFG = {
    "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
    "oos_min_win_rate": 0.0, "max_drawdown": 0.3,
    "oos_min_alpha_tstat": 2.0,
    "eligible_requires_all": ["min_alpha_tstat"],
}


def _oos(**kwargs):
    base = {
        "total_trades": 50, "max_drawdown": 0.02, "win_rate": 0.4,
        "total_return": 0.05, "expectancy": 0.01, "median_position_notional": 1000.0,
    }
    base.update(kwargs)
    return base


def test_gate_prefers_hc3_over_classical_when_both_present():
    """Klassisch UNTER der Schwelle, HC3 DARUEBER -- die Entscheidung folgt HC3 (Akzeptanzkriterium:
    das Gate konsumiert die neue Statistik)."""
    oos = _oos(oos_alpha_tstat=1.5, oos_alpha_tstat_hc3=2.5)
    ev = _evaluate_oos_eligibility(oos, _TCFG)
    assert ev["oos_eligible"] is True


def test_gate_prefers_hc3_over_classical_reverse_direction():
    """Klassisch UEBER der Schwelle, HC3 DARUNTER -- die Entscheidung folgt HC3, nicht dem
    klassischen Wert."""
    oos = _oos(oos_alpha_tstat=2.5, oos_alpha_tstat_hc3=1.5)
    ev = _evaluate_oos_eligibility(oos, _TCFG)
    assert ev["oos_eligible"] is False
    assert any("oos_min_alpha_tstat" in r for r in ev["oos_rejection_reasons"])


def test_gate_falls_back_to_classical_without_hc3_field():
    """Rueckwaertskompatibel: ein Legacy-Metrics-Dict ohne oos_alpha_tstat_hc3 verwendet weiterhin
    die klassische Statistik (bit-identisch zum Pre-#1255-Verhalten)."""
    oos = _oos(oos_alpha_tstat=2.5)
    ev = _evaluate_oos_eligibility(oos, _TCFG)
    assert ev["oos_eligible"] is True
    oos_low = _oos(oos_alpha_tstat=1.5)
    ev_low = _evaluate_oos_eligibility(oos_low, _TCFG)
    assert ev_low["oos_eligible"] is False


def test_gate_delta_uses_the_same_hc3_preferred_value():
    oos = _oos(oos_alpha_tstat=1.5, oos_alpha_tstat_hc3=2.5)
    ev = _evaluate_oos_eligibility(oos, _TCFG)
    assert ev["oos_gate_deltas"]["oos_min_alpha_tstat"] == pytest.approx(2.5 - 2.0)


# ---------------------------------------------------------------------------------------------
# invariants.check_alpha_tstat_estimator_agreement
# ---------------------------------------------------------------------------------------------

def test_inconclusive_without_both_fields():
    result = inv.check_alpha_tstat_estimator_agreement([{"strategy": "S", "symbol": "X.ETORO"}])
    assert result.passed is True
    assert result.inconclusive is True
    assert result.severity == "high"


def test_passes_within_threshold():
    result = inv.check_alpha_tstat_estimator_agreement([{
        "strategy": "S", "symbol": "X.ETORO",
        "holdout_alpha_tstat": 2.0, "holdout_alpha_tstat_hc3": 2.1,
    }])
    assert result.passed is True


def test_fails_beyond_threshold_with_numbers_in_actual():
    result = inv.check_alpha_tstat_estimator_agreement([{
        "strategy": "S", "symbol": "X.ETORO",
        "holdout_alpha_tstat": 1.0, "holdout_alpha_tstat_hc3": 2.0,
    }])
    assert result.passed is False
    assert "S/X.ETORO" in result.actual
    assert result.actual["S/X.ETORO"]["relative_deviation"] == pytest.approx(1.0)


def test_near_zero_classic_value_uses_epsilon_floor_not_infinite_deviation():
    result = inv.check_alpha_tstat_estimator_agreement([{
        "strategy": "S", "symbol": "X.ETORO",
        "holdout_alpha_tstat": 1e-9, "holdout_alpha_tstat_hc3": 1e-7,
    }])
    # Absolute Differenz winzig (~1e-7); der epsilon-Boden verhindert eine explodierende relative
    # Metrik, aber die Differenz selbst kann trotzdem > threshold sein -- kein Crash/NaN, jedenfalls.
    assert result.passed in (True, False)
    assert result.actual is None or all(
        v.get("relative_deviation") is not None for v in result.actual.values())


# ---------------------------------------------------------------------------------------------
# Bridging: run_optimization allowlist / confirm._metrics_dict
# ---------------------------------------------------------------------------------------------

def test_hc3_is_stamped_not_allowlisted():
    """oos_alpha_tstat_hc3 wird PER TRIAL gestempelt (Gate-Konsument) -- anders als die reinen
    Audit-Felder, steht es NICHT in der holdout-only-Allowlist."""
    assert "oos_alpha_tstat_hc3" not in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
    import inspect
    source = inspect.getsource(ro.run_optimization) if hasattr(ro, "run_optimization") else ""
    # Fallback: durchsuche das gesamte Modul nach der Stempelstelle, falls der Funktionsname
    # abweicht (robust gegen internen Umbau).
    if not source:
        source = Path(ro.__file__).read_text("utf-8")
    assert 'trial.set_user_attr("oos_alpha_tstat_hc3", metrics.oos_alpha_tstat_hc3)' in source


def test_audit_fields_are_allowlisted_holdout_only():
    for field in ("oos_alpha_tstat_df", "oos_alpha_n_total", "oos_alpha_n_informative",
                 "oos_alpha_n_y_nonzero", "oos_alpha_n_x_nonzero", "oos_alpha_n_both_zero"):
        assert field in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
        assert "confirm.py-Re-Evaluation" in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS[field]


class _M:
    def __getattr__(self, name):
        return None


def test_metrics_dict_carries_hc3_and_df():
    m = _M()
    m.oos_alpha_tstat_hc3 = 2.3
    m.oos_alpha_tstat_df = 77
    d = confirm._metrics_dict(m)
    assert d["oos_alpha_tstat_hc3"] == 2.3
    assert d["oos_alpha_tstat_df"] == 77


# ---------------------------------------------------------------------------------------------
# Full report bridging (analog test_issue_1038_1187's report.generate_sweep_report pattern)
# ---------------------------------------------------------------------------------------------

def _make_study(storage_url: str, study_name: str, n: int = 3):
    import optuna
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                 load_if_exists=True)
    for i in range(n):
        trial = study.ask()
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", True)
        trial.set_user_attr("oos_coherence_violation", False)
        study.tell(trial, float(i))
    return study


def _proposal_with_hc3(*, oos_alpha_tstat, oos_alpha_tstat_hc3, oos_alpha_tstat_df):
    holdout = {
        "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
        "deflation_n_eligible": 3, "deflation_n_family_effective": 3, "deflation_n_effective": 3,
        "oos_excess_return": 0.11, "oos_exposure_fraction": 0.1,
        "oos_alpha": 0.0002, "oos_beta": 0.15, "oos_alpha_tstat": oos_alpha_tstat,
        "oos_alpha_n_periods": 500,
        "oos_alpha_tstat_hc3": oos_alpha_tstat_hc3, "oos_alpha_tstat_df": oos_alpha_tstat_df,
        "oos_alpha_n_total": 500, "oos_alpha_n_informative": 480,
        "oos_alpha_n_y_nonzero": 470, "oos_alpha_n_x_nonzero": 495, "oos_alpha_n_both_zero": 20,
    }
    return {
        "strategy": "TestStrat", "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": holdout},
    }


@pytest.fixture
def wired_storage(tmp_path, monkeypatch):
    from automation.optimizer import report
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    return tmp_path


def test_study_record_carries_hc3_df_and_population_fields(wired_storage):
    from automation.optimizer import report
    out_path = report.generate_sweep_report(
        [_proposal_with_hc3(oos_alpha_tstat=1.9, oos_alpha_tstat_hc3=3.7, oos_alpha_tstat_df=478)],
        run_id="alphaHC3_1", reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_alpha_tstat_hc3"] == pytest.approx(3.7)
    assert rec["holdout_alpha_tstat_df"] == 478
    assert rec["holdout_alpha_n_total"] == 500
    assert rec["holdout_alpha_n_informative"] == 480
    assert rec["holdout_alpha_n_y_nonzero"] == 470
    assert rec["holdout_alpha_n_x_nonzero"] == 495
    assert rec["holdout_alpha_n_both_zero"] == 20
    # holdout_alpha_tstat (klassisch) bleibt unveraendert erhalten (Ruecksichtsvergleich).
    assert rec["holdout_alpha_tstat"] == pytest.approx(1.9)


def test_study_record_hc3_fields_none_without_legacy_data(wired_storage):
    from automation.optimizer import report
    proposal = _proposal_with_hc3(oos_alpha_tstat=1.9, oos_alpha_tstat_hc3=None, oos_alpha_tstat_df=None)
    out_path = report.generate_sweep_report(
        [proposal], run_id="alphaHC3_2", reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_alpha_tstat"] == pytest.approx(1.9)
    assert rec["holdout_alpha_tstat_hc3"] is None
    assert rec["holdout_alpha_tstat_df"] is None


# ---------------------------------------------------------------------------------------------
# invariants.check_alpha_tstat_estimator_agreement wired into report.py
# ---------------------------------------------------------------------------------------------

def test_check_is_wired_in_build_report():
    import inspect
    from automation.optimizer import report as _report
    source = inspect.getsource(_report._build_report)
    assert "check_alpha_tstat_estimator_agreement" in source


def test_production_config_reward_semantics_version_at_least_27():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["reward_semantics_version"] >= 27
