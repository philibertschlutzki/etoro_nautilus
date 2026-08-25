"""Issue #1256 (GH #1126) — β-Plausibilitäts-Invariante gegen Exposure und Sizing.

Symptom. 13 von 14 Strategien sind long-only bei 15 % Sizing; |β| bleibt in 13/13 Studies unter
0,0503, und vier long-only-Studies tragen ein negatives β (AdxAtr −0,0147 bei 85,6 % Exposure,
erwartet ≈ +0,128).

Root-Cause. Es gab keine Prüfung, ob das geschätzte β mit der bekannten Marktbeteiligung
vereinbar ist.

Fix.
1. ``report._allow_short_by_strategy`` (analog ``_trade_amount_pct_by_strategy``) + ``beta_expected
   = holdout_exposure_fraction · trade_amount_pct/100`` je Study gestempelt (nur für Strategien
   ohne ``allow_short``).
2. ``invariants.check_beta_exposure_plausibility`` (severity 'high'): für long-only-Studies mit
   ``exposure_fraction >= 0,10`` FAIL, wenn ``beta_measured < 0`` ODER ``beta_measured < 0,25 ·
   beta_expected``. ``allow_short=True``-Studies sind ausgenommen und werden als solche ausgewiesen.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv, report as rpt


# ---------------------------------------------------------------------------------------------
# report._allow_short_by_strategy
# ---------------------------------------------------------------------------------------------

def test_production_config_combotrendvwap_allows_short():
    m = rpt._allow_short_by_strategy()
    assert m.get("ComboTrendVwapStrategy") is True


def test_production_config_other_strategies_default_long_only():
    m = rpt._allow_short_by_strategy()
    assert m.get("SmaCrossoverStrategy") is False


def test_allow_short_by_strategy_from_fixture(tmp_path):
    (tmp_path / "strategies.json").write_text(json.dumps({
        "strategies": [
            {"strategy_class": "StratA", "params": {"allow_short": True}},
            {"strategy_class": "StratB", "params": {}},
            {"strategy_class": "StratC"},
        ]
    }), "utf-8")
    m = rpt._allow_short_by_strategy(tmp_path)
    assert m == {"StratA": True, "StratB": False, "StratC": False}


# ---------------------------------------------------------------------------------------------
# invariants.check_beta_exposure_plausibility
# ---------------------------------------------------------------------------------------------

def _record(*, strategy, symbol, beta, exposure, trade_pct=15.0, allow_short=False):
    beta_expected = (exposure * trade_pct / 100.0) if not allow_short and exposure is not None else None
    return {
        "strategy": strategy, "symbol": symbol, "holdout_beta": beta,
        "holdout_exposure_fraction": exposure, "trade_amount_pct": trade_pct,
        "allow_short": allow_short, "beta_expected": beta_expected,
    }


def test_synthetic_long_only_fixture_beta_equals_f_times_sizing_passes():
    """Akzeptanzkriterium: eine synthetische Fixture (Long-only, β = f·sizing) PASSt."""
    records = [
        _record(strategy="StratA", symbol="X.ETORO", exposure=0.5, trade_pct=15.0,
               beta=0.5 * 0.15),
        _record(strategy="StratB", symbol="Y.ETORO", exposure=0.856, trade_pct=15.0,
               beta=0.856 * 0.15),
    ]
    result = inv.check_beta_exposure_plausibility(records)
    assert result.passed is True


def test_reproduces_the_four_offenders_pattern():
    """Reproduziert das #1256-Symptom-Muster (nicht die exakten Produktionszahlen, aber dieselbe
    Struktur): vier long-only Studies mit implausiblem (negativem oder viel zu kleinem) β."""
    records = [
        _record(strategy="AdxAtrMomentumStrategy", symbol="PLTR.ETORO", exposure=0.856,
               trade_pct=15.0, beta=-0.0147),
        _record(strategy="DynamicBreakoutStrategy", symbol="TSLA.ETORO", exposure=0.5,
               trade_pct=15.0, beta=0.0044),
        _record(strategy="MeanReversionStrategy", symbol="NVDA.ETORO", exposure=0.6,
               trade_pct=15.0, beta=-0.02),
        _record(strategy="SmaCrossoverStrategy", symbol="AAPL.ETORO", exposure=0.5,
               trade_pct=15.0, beta=0.01),  # < 0.25 * beta_expected (0.075) -- implausibel klein
        # eine gesunde, plausible Study zum Kontrast (darf NICHT als Offender erscheinen).
        _record(strategy="VwapExhaustionStrategy", symbol="MSFT.ETORO", exposure=0.5,
               trade_pct=15.0, beta=0.5 * 0.15),
    ]
    result = inv.check_beta_exposure_plausibility(records)
    assert result.passed is False
    assert result.severity == "high"
    for key in ("AdxAtrMomentumStrategy/PLTR.ETORO", "DynamicBreakoutStrategy/TSLA.ETORO",
               "MeanReversionStrategy/NVDA.ETORO", "SmaCrossoverStrategy/AAPL.ETORO"):
        assert key in result.actual
    assert "VwapExhaustionStrategy/MSFT.ETORO" not in result.actual


def test_negative_beta_always_fails_regardless_of_magnitude():
    records = [_record(strategy="S", symbol="X.ETORO", exposure=0.856, trade_pct=15.0,
                      beta=-0.0147)]
    result = inv.check_beta_exposure_plausibility(records)
    assert result.passed is False
    assert result.actual["S/X.ETORO"]["beta_measured"] == pytest.approx(-0.0147)
    assert result.actual["S/X.ETORO"]["beta_expected"] == pytest.approx(0.856 * 0.15)


def test_beta_below_quarter_of_expected_fails():
    exposure, trade_pct = 0.5, 15.0
    beta_expected = exposure * trade_pct / 100.0
    records = [_record(strategy="S", symbol="X.ETORO", exposure=exposure, trade_pct=trade_pct,
                      beta=0.24 * beta_expected)]
    result = inv.check_beta_exposure_plausibility(records)
    assert result.passed is False


def test_beta_at_or_above_quarter_of_expected_passes():
    exposure, trade_pct = 0.5, 15.0
    beta_expected = exposure * trade_pct / 100.0
    records = [_record(strategy="S", symbol="X.ETORO", exposure=exposure, trade_pct=trade_pct,
                      beta=0.25 * beta_expected)]
    result = inv.check_beta_exposure_plausibility(records)
    assert result.passed is True


def test_combotrendvwap_allow_short_is_exempted_not_flagged():
    """Akzeptanzkriterium: ComboTrendVwap (allow_short=True) erscheint nicht als Offender."""
    records = [
        _record(strategy="ComboTrendVwapStrategy", symbol="TSLA.ETORO", exposure=0.7,
               trade_pct=15.0, beta=-0.3, allow_short=True),
        _record(strategy="SmaCrossoverStrategy", symbol="AAPL.ETORO", exposure=0.5,
               trade_pct=15.0, beta=0.5 * 0.15),
    ]
    result = inv.check_beta_exposure_plausibility(records)
    assert result.passed is True
    assert "ComboTrendVwapStrategy/TSLA.ETORO" not in (result.actual or {})
    assert result.actual is not None
    assert "ComboTrendVwapStrategy/TSLA.ETORO" in result.actual.get("exempted_allow_short_studies", [])


def test_low_exposure_studies_are_not_evaluable():
    """Unter der 10%-Exposure-Schwelle ist die Regression ohnehin kaum belastbar -- keine
    Bewertung, kein Offender."""
    records = [_record(strategy="S", symbol="X.ETORO", exposure=0.05, trade_pct=15.0, beta=-0.5)]
    result = inv.check_beta_exposure_plausibility(records)
    assert result.passed is True
    assert result.inconclusive is True


def test_inconclusive_without_any_evaluable_study():
    result = inv.check_beta_exposure_plausibility([{"strategy": "S", "symbol": "X.ETORO"}])
    assert result.passed is True
    assert result.inconclusive is True
    assert result.severity == "high"


def test_missing_beta_expected_is_skipped_not_crashed():
    records = [{"strategy": "S", "symbol": "X.ETORO", "holdout_beta": -0.5,
               "holdout_exposure_fraction": 0.5, "allow_short": False, "beta_expected": None}]
    result = inv.check_beta_exposure_plausibility(records)
    assert result.passed is True
    assert result.inconclusive is True


# ---------------------------------------------------------------------------------------------
# Full report bridging: beta_expected/allow_short stamped on the study record
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


def _proposal(*, strategy, beta, exposure):
    holdout = {
        "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
        "deflation_n_eligible": 3, "deflation_n_family_effective": 3, "deflation_n_effective": 3,
        "oos_excess_return": 0.11, "oos_exposure_fraction": exposure,
        "oos_alpha": 0.0002, "oos_beta": beta, "oos_alpha_tstat": 1.9, "oos_alpha_n_periods": 500,
    }
    return {
        "strategy": strategy, "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": holdout},
    }


@pytest.fixture
def wired_storage(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_SmaCrossoverStrategy_A_ETORO")
    monkeypatch.setattr(rpt, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    return tmp_path


def test_study_record_carries_beta_expected_and_allow_short(wired_storage):
    out_path = rpt.generate_sweep_report(
        [_proposal(strategy="SmaCrossoverStrategy", beta=0.856 * 0.15, exposure=0.856)],
        run_id="betaExp1", reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["allow_short"] is False
    assert rec["beta_expected"] == pytest.approx(0.856 * (rec["trade_amount_pct"] / 100.0))
    assert rec["holdout_beta"] == pytest.approx(0.856 * 0.15)


def test_check_beta_exposure_plausibility_wired_in_build_report():
    import inspect
    source = inspect.getsource(rpt._build_report)
    assert "check_beta_exposure_plausibility" in source
