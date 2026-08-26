"""Issue #1283 (GH #1156, Katalog #1272-1297, P0) — t(α) nur als Promotions-Entscheidung
zulassen, wenn β die bekannte Marktbeteiligung tatsächlich identifiziert.

Symptom (Referenzfall AdxAtr/TSLA, Katalog). ``exposure_fraction=0.8563``,
``trade_amount_pct=15.0`` ⇒ ``beta_expected=0.1285``; gemessen ``beta_measured=-0.0155`` — |β| liegt
bei ~12 % des erwarteten Werts, weit unter der 25-%-Schwelle. t(α) galt trotzdem unbedingt als
Selektionsgate (#1093/#1241), obwohl β die Marktbeteiligung nicht identifizierte — es war aus dem
Artefakt allein nicht zu entscheiden, ob die Benchmark-Serie das Falsche misst, das Fenster zu kurz
ist, oder der tatsächliche Marktkontakt von der nominalen Exposure abweicht.

Fix.
1. ``backtest_runner._alpha_regression_diagnostics`` liefert zusätzlich ``corr_xy``, ``sd_x``,
   ``sd_y``, ``cov_xy``, ``cov_in_market``, ``cov_out_of_market`` (additiv:
   ``cov_in_market + cov_out_of_market == cov_xy``), ``cov_exit_bars`` (bewusst immer ``None``),
   ``n_in_market`` — durchgereicht über ``parsing.TournamentMetrics`` bis in ``report._study_record``
   (``holdout_alpha_*``-Felder).
2. Neue, separate Invariante ``invariants.check_alpha_regression_identifiability``: FAIL ``high``,
   wenn ``|beta_measured| < 0.25 · beta_expected`` bei ``exposure_fraction > 0.3`` (long-only,
   dieselbe Ausnahme für ``allow_short=True`` wie ``check_beta_exposure_plausibility``/#1256), mit
   der Kovarianz-Zerlegung im Offender-Eintrag.
3. Unbedingter Promotions-Guard in ``confirm.confirm_per_symbol_promotion`` (dieselbe #958-
   Konvention: narrower Guard genau an der Promotions-Entscheidung): setzt
   ``holdout_reject_detail = REJECT_OOS_ALPHA_NOT_IDENTIFIED`` statt eines stillen Durchfallens am
   t-Wert.
"""
import inspect

import numpy as np
import pytest

from automation import backtest_runner as bt
from automation.optimizer import confirm, invariants as inv, report as rpt


# ---------------------------------------------------------------------------------------------
# backtest_runner._alpha_regression_diagnostics — neue Kovarianz-Zerlegungsfelder
# ---------------------------------------------------------------------------------------------

def test_cov_in_market_plus_cov_out_of_market_equals_cov_xy_exactly():
    rng = np.random.default_rng(1283)
    n = 200
    x = rng.normal(0.0, 0.001, n)
    y = np.where(rng.random(n) < 0.5, 0.0, x * 0.1 + rng.normal(0.0, 0.0005, n))
    diag = bt._alpha_regression_diagnostics(y, x)
    assert diag is not None
    assert diag["cov_in_market"] + diag["cov_out_of_market"] == pytest.approx(diag["cov_xy"])


def test_cov_exit_bars_is_always_none_by_construction():
    x = np.array([0.001, -0.002, 0.0015, -0.0005, 0.0008])
    y = np.array([0.0002, -0.0003, 0.0, 0.0001, -0.0001])
    diag = bt._alpha_regression_diagnostics(y, x)
    assert diag is not None
    assert diag["cov_exit_bars"] is None


def test_n_in_market_counts_nonzero_y_bars():
    x = np.array([0.001, -0.002, 0.0015, -0.0005, 0.0008])
    y = np.array([0.0002, 0.0, 0.0, 0.0001, -0.0001])
    diag = bt._alpha_regression_diagnostics(y, x)
    assert diag is not None
    assert diag["n_in_market"] == 3


def test_corr_xy_is_none_when_either_series_has_zero_variance():
    x = np.array([0.001, 0.001, 0.001, 0.001])
    y = np.array([0.0002, -0.0003, 0.0001, 0.0])
    diag = bt._alpha_regression_diagnostics(y, x)
    # sxx == 0 -> die Funktion selbst gibt None zurueck (dieselbe Degenerationsregel wie
    # _alpha_beta_regression), keine eigene Ausnahme fuer corr_xy noetig.
    assert diag is None


def test_sd_x_sd_y_are_population_standard_deviations():
    x = np.array([1.0, -1.0, 1.0, -1.0])
    y = np.array([2.0, -2.0, 2.0, -2.0])
    diag = bt._alpha_regression_diagnostics(y, x)
    assert diag is not None
    assert diag["sd_x"] == pytest.approx(1.0)
    assert diag["sd_y"] == pytest.approx(2.0)
    assert diag["corr_xy"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------------------------
# parsing.py / report.py Feld-Durchreichung
# ---------------------------------------------------------------------------------------------

def test_tournament_metrics_carries_all_eight_new_alpha_fields():
    from automation.optimizer.parsing import TournamentMetrics
    fields = {f for f in TournamentMetrics.__dataclass_fields__}
    for name in ("oos_alpha_corr_xy", "oos_alpha_sd_x", "oos_alpha_sd_y", "oos_alpha_cov_xy",
                 "oos_alpha_cov_in_market", "oos_alpha_cov_out_of_market",
                 "oos_alpha_cov_exit_bars", "oos_alpha_n_in_market"):
        assert name in fields


def test_study_record_stamps_holdout_alpha_covariance_fields():
    holdout_metrics = {
        "oos_alpha_corr_xy": 0.42, "oos_alpha_sd_x": 0.01, "oos_alpha_sd_y": 0.02,
        "oos_alpha_cov_xy": 0.0003, "oos_alpha_cov_in_market": 0.0002,
        "oos_alpha_cov_out_of_market": 0.0001, "oos_alpha_cov_exit_bars": None,
        "oos_alpha_n_in_market": 40,
    }
    source = inspect.getsource(rpt._study_record)
    for key in ("holdout_alpha_corr_xy", "holdout_alpha_sd_x", "holdout_alpha_sd_y",
                "holdout_alpha_cov_xy", "holdout_alpha_cov_in_market",
                "holdout_alpha_cov_out_of_market", "holdout_alpha_cov_exit_bars",
                "holdout_alpha_n_in_market"):
        assert key in source


# ---------------------------------------------------------------------------------------------
# invariants.check_alpha_regression_identifiability
# ---------------------------------------------------------------------------------------------

def _record(*, strategy, symbol, beta, exposure, trade_pct=15.0, allow_short=False, **extra):
    beta_expected = (exposure * trade_pct / 100.0) if not allow_short and exposure is not None else None
    rec = {
        "strategy": strategy, "symbol": symbol, "holdout_beta": beta,
        "holdout_exposure_fraction": exposure, "trade_amount_pct": trade_pct,
        "allow_short": allow_short, "beta_expected": beta_expected,
    }
    rec.update(extra)
    return rec


def test_reference_case_adxatr_tsla_fails_and_carries_covariance_decomposition():
    """Katalog-Referenzfall: exposure_fraction=0.8563, trade_amount_pct=15.0 -> beta_expected
    ~0.1285; beta_measured=-0.0155 -> |beta| ~12% des erwarteten Werts, faellt."""
    records = [_record(
        strategy="AdxAtrMomentumStrategy", symbol="TSLA.ETORO", exposure=0.8563, trade_pct=15.0,
        beta=-0.0155,
        holdout_alpha_corr_xy=0.02, holdout_alpha_sd_x=0.001, holdout_alpha_sd_y=0.0004,
        holdout_alpha_cov_xy=8e-10, holdout_alpha_cov_in_market=1e-10,
        holdout_alpha_cov_out_of_market=7e-10, holdout_alpha_cov_exit_bars=None,
        holdout_alpha_n_in_market=120,
    )]
    result = inv.check_alpha_regression_identifiability(records)
    assert result.passed is False
    assert result.severity == "high"
    offender = result.actual["AdxAtrMomentumStrategy/TSLA.ETORO"]
    assert offender["beta_expected"] == pytest.approx(0.8563 * 0.15)
    assert offender["beta_measured"] == pytest.approx(-0.0155)
    assert offender["cov_in_market"] == pytest.approx(1e-10)
    assert offender["cov_out_of_market"] == pytest.approx(7e-10)
    assert offender["cov_exit_bars"] is None
    assert offender["n_in_market"] == 120


def test_beta_at_or_above_quarter_of_expected_passes():
    exposure, trade_pct = 0.5, 15.0
    beta_expected = exposure * trade_pct / 100.0
    records = [_record(strategy="S", symbol="X.ETORO", exposure=exposure, trade_pct=trade_pct,
                        beta=0.25 * beta_expected)]
    result = inv.check_alpha_regression_identifiability(records)
    assert result.passed is True


def test_beta_below_quarter_of_expected_fails_even_if_positive():
    """Im Unterschied zu check_beta_exposure_plausibility zaehlt hier NUR die Amplitude (|beta|),
    nicht das Vorzeichen -- dieselbe Betrags-Schwelle wie der confirm.py-Promotions-Guard."""
    exposure, trade_pct = 0.5, 15.0
    beta_expected = exposure * trade_pct / 100.0
    records = [_record(strategy="S", symbol="X.ETORO", exposure=exposure, trade_pct=trade_pct,
                        beta=0.24 * beta_expected)]
    result = inv.check_alpha_regression_identifiability(records)
    assert result.passed is False


def test_negative_beta_with_sufficient_amplitude_passes():
    """|beta| >= Schwelle passt auch bei negativem Vorzeichen -- diese Invariante misst
    Identifizierbarkeit (Amplitude), nicht Plausibilitaet (Vorzeichen); Letzteres deckt bereits
    check_beta_exposure_plausibility (#1256) ab."""
    exposure, trade_pct = 0.5, 15.0
    beta_expected = exposure * trade_pct / 100.0
    records = [_record(strategy="S", symbol="X.ETORO", exposure=exposure, trade_pct=trade_pct,
                        beta=-0.5 * beta_expected)]
    result = inv.check_alpha_regression_identifiability(records)
    assert result.passed is True


def test_exposure_at_or_below_30_percent_is_not_evaluable():
    records = [_record(strategy="S", symbol="X.ETORO", exposure=0.3, trade_pct=15.0, beta=0.0)]
    result = inv.check_alpha_regression_identifiability(records)
    assert result.passed is True
    assert result.inconclusive is True


def test_exposure_above_30_percent_is_evaluable():
    records = [_record(strategy="S", symbol="X.ETORO", exposure=0.31, trade_pct=15.0, beta=0.0)]
    result = inv.check_alpha_regression_identifiability(records)
    assert result.passed is False
    assert result.inconclusive is False


def test_allow_short_studies_are_exempted_and_named():
    records = [
        _record(strategy="ComboTrendVwapStrategy", symbol="TSLA.ETORO", exposure=0.7,
                trade_pct=15.0, beta=0.0, allow_short=True),
        _record(strategy="SmaCrossoverStrategy", symbol="AAPL.ETORO", exposure=0.5,
                trade_pct=15.0, beta=0.5 * 0.15),
    ]
    result = inv.check_alpha_regression_identifiability(records)
    assert result.passed is True
    assert "ComboTrendVwapStrategy/TSLA.ETORO" not in (result.actual or {})
    assert "ComboTrendVwapStrategy/TSLA.ETORO" in result.actual.get("exempted_allow_short_studies", [])


def test_inconclusive_without_any_evaluable_study():
    result = inv.check_alpha_regression_identifiability([{"strategy": "S", "symbol": "X.ETORO"}])
    assert result.passed is True
    assert result.inconclusive is True
    assert result.severity == "high"


def test_missing_beta_expected_is_skipped_not_crashed():
    records = [{"strategy": "S", "symbol": "X.ETORO", "holdout_beta": -0.5,
                "holdout_exposure_fraction": 0.5, "allow_short": False, "beta_expected": None}]
    result = inv.check_alpha_regression_identifiability(records)
    assert result.passed is True
    assert result.inconclusive is True


def test_wired_in_build_report_check_stream():
    source = inspect.getsource(rpt._build_report)
    assert "check_alpha_regression_identifiability" in source


def test_check_alpha_regression_identifiability_appears_in_stream(tmp_path):
    report = rpt._build_report(
        [], run_id="run-1283-a", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_alpha_regression_identifiability" in names


# ---------------------------------------------------------------------------------------------
# confirm.confirm_per_symbol_promotion — unbedingter Promotions-Guard
# ---------------------------------------------------------------------------------------------

def test_confirm_source_gates_the_new_guard_behind_holdout_passed():
    source = inspect.getsource(confirm.confirm_per_symbol_promotion)
    idx = source.index("REJECT_OOS_ALPHA_NOT_IDENTIFIED")
    # Der Guard-Codeblock muss innerhalb eines ``if holdout_passed:``-Blocks liegen -- dieselbe
    # Reihenfolge-Konvention wie der #958-Guard direkt darueber.
    snippet_before = source[max(0, idx - 1500):idx]
    assert "if holdout_passed:" in snippet_before


def test_confirm_source_excludes_allow_short_strategies():
    source = inspect.getsource(confirm.confirm_per_symbol_promotion)
    idx = source.index("REJECT_OOS_ALPHA_NOT_IDENTIFIED")
    snippet_before = source[max(0, idx - 1500):idx]
    assert "_allow_short_by_strategy" in snippet_before
    assert "not _allow_short_this" in snippet_before


def test_confirm_source_uses_the_same_030_and_025_thresholds():
    source = inspect.getsource(confirm.confirm_per_symbol_promotion)
    idx = source.index("REJECT_OOS_ALPHA_NOT_IDENTIFIED")
    snippet_before = source[max(0, idx - 1500):idx]
    assert "0.3" in snippet_before
    assert "0.25" in snippet_before


def test_reject_oos_alpha_not_identified_is_a_confirm_stage_rejection():
    """#671-Konvention: der modale Per-Trial-IS-Grund erklaert nicht, warum ein Holdout-
    bestandener Kandidat an der Identifizierbarkeits-Pruefung scheiterte."""
    assert "REJECT_OOS_ALPHA_NOT_IDENTIFIED" in confirm._CONFIRM_STAGE_REJECTIONS
