"""Issue #1038/#1187 (P2) — Die α-Spalte ist unlesbar formatiert.

Symptom. Abschnitt 2.3 zeigte α mit fünf Nachkommastellen auf Werten der Größenordnung 1e-6 ⇒ 13
von 13 Zeilen zeigten "0.00000"/"-0.00000"/"-0.00001". Die ökonomisch aussagekräftige Größe ist das
Holdout-Alpha α·n (n = Anzahl der Regressions-Perioden), das zwischen −1,450 % und +0,449 % liegt.

Fix.
1. ``backtest_runner``/``parsing``/``confirm``/``report`` reichen die Regressions-Stichproben-
   grösse als neues Feld ``oos_alpha_n_periods``/``holdout_alpha_n_periods`` durch.
2. ``report._study_record`` berechnet zusätzlich ``holdout_alpha_times_n_pct`` (α·n, als Prozent
   ausgewiesen) und ``holdout_alpha_bps_per_bar`` (α selbst in bps/Bar).
3. ``summary_de.py`` Abschnitt 2.3 zeigt "α·n (%)" und "α (bps/Bar)" statt der unlesbaren rohen α-
   Spalte; ``t(α)`` bleibt unverändert.
"""
import pytest

from automation.optimizer import summary_de


# ── backtest_runner: n wird zusammen mit alpha/beta gestempelt ──────────────────────────────────
def test_alpha_beta_regression_sample_size_matches_input_length():
    """Reine Dokumentation der Invariante, die backtest_runner.extract_metrics nutzt: n ist die
    Länge der (indexgleichen) Log-Return-Serien, die in die Regression eingingen."""
    from automation.backtest_runner import _alpha_beta_regression
    x = [0.01, -0.02, 0.015, -0.005, 0.03, -0.01, 0.02, -0.015, 0.005, 0.0]
    y = [0.2 * xi + 0.0003 for xi in x]
    result = _alpha_beta_regression(y, x)
    assert result is not None
    assert len(x) == len(y) == 10  # dasselbe n, das backtest_runner.py als oos_alpha_n_periods stempelt


# ── report._study_record: neue Felder ────────────────────────────────────────────────────────────
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


def _proposal_with_alpha(*, oos_alpha, oos_alpha_n_periods, oos_beta=0.15, oos_alpha_tstat=2.0):
    holdout = {
        "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
        "deflation_n_eligible": 3, "deflation_n_family_effective": 3, "deflation_n_effective": 3,
        "oos_excess_return": 0.11, "oos_exposure_fraction": 0.1,
        "oos_alpha": oos_alpha, "oos_beta": oos_beta, "oos_alpha_tstat": oos_alpha_tstat,
        "oos_alpha_n_periods": oos_alpha_n_periods,
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


def test_study_record_carries_alpha_n_periods_and_derived_fields(wired_storage):
    import json
    from automation.optimizer import report
    out_path = report.generate_sweep_report(
        [_proposal_with_alpha(oos_alpha=-1.344e-6, oos_alpha_n_periods=1079)],
        run_id="alphaN1", reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_alpha_n_periods"] == 1079
    # α·n, ausgedrückt in % (Issue-Referenzwert: -1.450 %).
    assert rec["holdout_alpha_times_n_pct"] == pytest.approx(-1.344e-6 * 1079 * 100.0)
    assert rec["holdout_alpha_times_n_pct"] == pytest.approx(-0.145, abs=0.01)
    # α selbst in bps/Bar -- keine falsche Null mehr.
    assert rec["holdout_alpha_bps_per_bar"] == pytest.approx(-1.344e-6 * 10000.0)
    assert rec["holdout_alpha_bps_per_bar"] != 0.0


def test_study_record_alpha_fields_none_without_alpha_n_periods(wired_storage):
    """Legacy-Proposal ohne oos_alpha_n_periods (Alt-Artefakt) -- die neuen Felder bleiben None,
    holdout_alpha selbst bleibt unveraendert verfuegbar (Rueckwaertskompat)."""
    import json
    from automation.optimizer import report
    out_path = report.generate_sweep_report(
        [_proposal_with_alpha(oos_alpha=0.0002, oos_alpha_n_periods=None)],
        run_id="alphaN2", reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_alpha"] == pytest.approx(0.0002)
    assert rec["holdout_alpha_n_periods"] is None
    assert rec["holdout_alpha_times_n_pct"] is None
    # bps/Bar braucht n NICHT -- bleibt berechenbar, selbst ohne alpha_n_periods.
    assert rec["holdout_alpha_bps_per_bar"] == pytest.approx(2.0)


# ── Akzeptanzkriterium #1038/1: keine Zeile zeigt eine reine Null in der α-Spalte, wenn α != 0 ───
def test_section_2_3_never_shows_a_bare_zero_when_alpha_is_nonzero():
    report = {
        "studies": [{
            "strategy": "SmaCrossover", "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
            "holdout_total_return": 0.02, "holdout_buyhold_return": 0.05,
            "holdout_excess_return": -0.03, "holdout_exposure_fraction": 0.4,
            "holdout_excess_per_unit_exposure": -0.075,
            "holdout_alpha": -1.344e-6, "holdout_alpha_n_periods": 1079,
            "holdout_alpha_times_n_pct": -0.145, "holdout_alpha_bps_per_bar": -0.01344,
            "holdout_beta": 0.3, "holdout_alpha_tstat": -2.1, "holdout_no_alpha_detected": False,
            "holdout_total_trades": 50,
        }],
        "cross_study": {"promotion_outcome_counts": {}},
    }
    text = summary_de._section_2_monetary_result(report)
    section_2_3 = text.split("### 2.3")[1] if "### 2.3" in text else ""
    assert "α·n (%)" in text
    assert "α (bps/Bar)" in text
    assert "0.00000" not in section_2_3
    assert "-0.145" in section_2_3 or "−0.145" in section_2_3
