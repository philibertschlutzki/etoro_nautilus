"""Issue #1073/#1077 (Katalog #866-2, Kohorte D) — Bericht- und Entscheidungs-Semantik.

#1073 — ``holdout_expectancy_winsorized`` wurde telemetriert, aber weder gerankt noch gegatet. §2.1
(Promotionskandidaten) sortiert seit diesem Fix nach der winsorisierten Expectancy;
``deployment_gate`` erhält die Klausel ``expectancy_outlier_robust``; eine neue Invariante
(``check_expectancy_outlier_dependence``) macht einen Vorzeichenwechsel sichtbar.

#1077 — ``summary_de._EXPOSURE_EPSILON`` setzte einen erfundenen Nenner ein, sobald ``exposure``
fehlte/winzig war — die grösste Zahl der Tabelle gehörte der Strategie mit 0 Trades. Fix: kein
Ersatznenner; Studies mit zu geringer Exposition wandern in einen eigenen "nicht bewertbar"-Block.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import summary_de


# ── #1073: check_expectancy_outlier_dependence ───────────────────────────────────────────────────
def test_expectancy_outlier_dependence_fails_on_sign_flip():
    records = [{
        "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
        "holdout_expectancy": 17.23, "holdout_expectancy_winsorized": -1.44,
    }]
    result = inv.check_expectancy_outlier_dependence(records)
    assert result.passed is False
    assert "AdxAtrMomentumStrategy/TSLA.ETORO" in result.actual


def test_expectancy_outlier_dependence_passes_when_signs_agree():
    records = [{
        "strategy": "DonchianRegimeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "holdout_expectancy": 11.35, "holdout_expectancy_winsorized": 16.58,
    }]
    result = inv.check_expectancy_outlier_dependence(records)
    assert result.passed is True


def test_expectancy_outlier_dependence_three_offenders_reference_scenario():
    """Beweis B-8 im #866-Katalog: drei Vorzeichenwechsel in einem Referenzlauf."""
    records = [
        {"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
         "holdout_expectancy": 17.23, "holdout_expectancy_winsorized": -1.44},
        {"strategy": "ComboTrendVwapStrategy", "symbol": "TSLA.ETORO",
         "holdout_expectancy": 3.24, "holdout_expectancy_winsorized": -5.80},
        {"strategy": "MeanReversionStrategy", "symbol": "TSLA.ETORO",
         "holdout_expectancy": -54.76, "holdout_expectancy_winsorized": 0.26},
        {"strategy": "Rsi2ReversionStrategy", "symbol": "TSLA.ETORO",
         "holdout_expectancy": 15.77, "holdout_expectancy_winsorized": 15.92},
    ]
    result = inv.check_expectancy_outlier_dependence(records)
    assert result.passed is False
    # MeanReversion (roh negativ, winsorisiert positiv) ist explizit KEIN Offender dieser
    # Definition (nur roh POSITIV + winsorisiert NICHT-positiv zaehlt) — die Deployment-Klausel
    # deckt diese Richtung zusaetzlich ab.
    assert "AdxAtrMomentumStrategy/TSLA.ETORO" in result.actual
    assert "ComboTrendVwapStrategy/TSLA.ETORO" in result.actual
    assert "Rsi2ReversionStrategy/TSLA.ETORO" not in result.actual


# ── #1073: summary_de section 2.1 ranks by winsorized expectancy ────────────────────────────────
def _minimal_report(**overrides):
    base = {
        "run_id": "run-abc", "run_status": "complete",
        "started_at_utc": "2026-07-30T00:00:00Z", "wallclock_s": 7200.0,
        "cli_args": {"n_jobs": 8, "n_jobs_source": "CLI"},
        "symbols_completed": None, "symbols_planned": None,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_section_2_1_ranks_by_winsorized_expectancy_not_raw_return():
    studies = [
        {"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
         "promotion_outcome": "READY_FOR_PR", "holdout_total_return": 0.036,
         "holdout_expectancy": 17.23, "holdout_expectancy_winsorized": -1.44,
         "holdout_expectancy_outlier_count": 6, "holdout_total_trades": 132},
        {"strategy": "DonchianRegimeBreakoutStrategy", "symbol": "TSLA.ETORO",
         "promotion_outcome": "READY_FOR_PR", "holdout_total_return": 0.004,
         "holdout_expectancy": 11.35, "holdout_expectancy_winsorized": 16.58,
         "holdout_expectancy_outlier_count": 0, "holdout_total_trades": 22},
    ]
    report = _minimal_report(studies=studies)
    text = summary_de.generate_german_summary(report)
    section_2_1 = text.split("### 2.1 ")[1].split("### 2.1b")[0]
    donchian_pos = section_2_1.index("DonchianRegimeBreakoutStrategy")
    adxatr_pos = section_2_1.index("AdxAtrMomentumStrategy")
    assert donchian_pos < adxatr_pos, "Donchian (robust) muss vor AdxAtr (Ausreisser-getrieben) stehen"
    assert "Expectancy (winsorisiert)" in section_2_1
    assert "Ausreisser/Trades" in section_2_1
    assert "6/132" in section_2_1


# ── #1077: summary_de section 2.3 no longer fabricates a denominator ────────────────────────────
def test_section_2_3_no_trade_study_shows_not_evaluable_not_a_huge_ratio():
    studies = [
        {"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
         "holdout_total_return": 0.0, "holdout_buyhold_return": -0.13951897,
         "holdout_excess_return": 0.13951897, "holdout_exposure_fraction": None,
         "holdout_total_trades": 0},
    ]
    report = _minimal_report(studies=studies)
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "1395" not in section_2_3
    assert "Nicht bewertbar" in section_2_3


def test_section_2_3_low_exposure_study_routed_to_not_evaluable_block():
    studies = [
        {"strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
         "holdout_total_return": 0.01, "holdout_buyhold_return": -0.14,
         "holdout_excess_return": 0.14, "holdout_exposure_fraction": 0.017,
         "holdout_total_trades": 6},
    ]
    report = _minimal_report(studies=studies)
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "816" not in section_2_3  # kein erfundenes 816,6%
    assert "Nicht bewertbar" in section_2_3
    assert "SqueezeBreakoutStrategy" in section_2_3.split("Nicht bewertbar")[1]


def test_section_2_3_sufficient_exposure_study_still_shows_a_real_ratio():
    studies = [
        {"strategy": "RealAlpha", "symbol": "X.ETORO", "holdout_total_return": 0.10,
         "holdout_buyhold_return": 0.08, "holdout_excess_return": 0.02,
         "holdout_exposure_fraction": 0.20, "holdout_total_trades": 40},
    ]
    report = _minimal_report(studies=studies)
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    main_table = section_2_3.split("Nicht bewertbar")[0]
    assert "RealAlpha" in main_table
    assert "10.0 %" in main_table  # 0.02 / 0.20 = 0.10
