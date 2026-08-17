"""Issue #986/#1140 (Katalog #986, Pitfall #412 in AGENTS.md) — der Exzess-Ertrag war vollstaendig
durch das Vorzeichen von Buy & Hold erklaert: ``holdout_excess_return = strategy - benchmark`` ist
im fallenden Markt fuer JEDE Strategie mit ``exposure_fraction`` < 100 % positiv, unabhaengig von
jedem Edge (Symptom: 0/39 auf steigenden, 62/65 auf fallenden Symbolen).

Fix:
1. ``backtest_runner._alpha_beta_regression`` — OLS-Regression der Strategie-Perioden-Log-Returns
   gegen die Benchmark-Perioden-Log-Returns, liefert (alpha, beta, alpha_tstat).
2. ``report._study_record`` fuehrt ``holdout_excess_per_unit_exposure`` (primaere Rangfolgengroesse),
   ``holdout_alpha``/``holdout_beta``/``holdout_alpha_tstat`` sowie ``holdout_no_alpha_detected``
   (``|t(alpha)| < 1``).
3. ``summary_de.py`` Abschnitt 2.3 sortiert nach ``excess_per_unit_exposure`` statt
   ``holdout_total_return``, zeigt α/β/t(α) je Zeile und kennzeichnet NO_ALPHA_DETECTED-Kandidaten.

Akzeptanzkriterien laut Issue: Report weist je Study α, β, t(α) aus; die Rangliste in §2.3 sortiert
nach ``excess_per_unit_exposure``; ein Kandidat mit t(α) < 1 (hier: |t(α)| < 1, siehe Docstring in
``report._study_record``) wird als ``NO_ALPHA_DETECTED`` gekennzeichnet.
"""
import pytest

from automation.optimizer import summary_de


# ── backtest_runner._alpha_beta_regression (reine Funktion, OLS-geschlossene Form) ────────────────
# Hinweis: importiert ``automation.backtest_runner``, was in Sandboxen ohne ``nautilus_trader``
# (Python < 3.12) beim Modul-Import fehlschlaegt — dieselbe vorbestehende Umgebungs-Einschraenkung
# wie bei jedem anderen Test dieses Moduls (siehe AGENTS.md).
def test_alpha_beta_regression_matches_ols_closed_form():
    from automation.backtest_runner import _alpha_beta_regression
    # y = 0.0003 + 0.2*x + Rauschen, konstruiert damit beta/alpha exakt nachrechenbar sind.
    x = [0.01, -0.02, 0.015, -0.005, 0.03, -0.01, 0.02, -0.015, 0.005, 0.0]
    y = [0.2 * xi + 0.0003 for xi in x]
    result = _alpha_beta_regression(y, x)
    assert result is not None
    alpha, beta, tstat = result
    assert alpha == pytest.approx(0.0003, abs=1e-9)
    assert beta == pytest.approx(0.2, abs=1e-9)
    # Rauschfrei konstruiert ⇒ perfekter Fit, Residuen 0 ⇒ SE(alpha) 0 ⇒ Implementierung liefert 0.0.
    assert tstat == 0.0


def test_alpha_beta_regression_none_below_three_periods():
    from automation.backtest_runner import _alpha_beta_regression
    assert _alpha_beta_regression([0.01, 0.02], [0.01, 0.02]) is None


def test_alpha_beta_regression_none_when_benchmark_has_zero_variance():
    from automation.backtest_runner import _alpha_beta_regression
    assert _alpha_beta_regression([0.01, 0.02, -0.01], [0.0, 0.0, 0.0]) is None


def test_alpha_beta_regression_partial_long_case_detects_no_alpha():
    """Die Akzeptanz-Illustration aus der Issue: beta ~ 0.2, alpha ~ 0 -> kein Edge, ein
    Teilzeit-Long. |t(alpha)| < 1 markiert das als NO_ALPHA_DETECTED (report._study_record)."""
    from automation.backtest_runner import _alpha_beta_regression
    import random
    random.seed(986)
    x = [random.gauss(0, 0.01) for _ in range(60)]
    y = [0.2 * xi + random.gauss(0, 0.001) for xi in x]  # kein echter Alpha-Term
    result = _alpha_beta_regression(y, x)
    assert result is not None
    alpha, beta, tstat = result
    assert beta == pytest.approx(0.2, abs=0.05)
    assert abs(tstat) < 1.0


# ── report._study_record neue Felder (end-to-end ueber generate_sweep_report) ─────────────────────
# Ebenfalls durch die nautilus_trader-Umgebungs-Einschraenkung betroffen (report.py laedt
# backtest_runner lazy fuer die Kostenmodell-Aufloesung).
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


def _proposal_with_alpha_beta(**holdout_overrides) -> dict:
    holdout = {
        "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
        "deflation_n_eligible": 3, "deflation_n_family_effective": 3, "deflation_n_effective": 3,
        "oos_excess_return": 0.11, "oos_exposure_fraction": 0.1,
        "oos_alpha": 0.0002, "oos_beta": 0.15, "oos_alpha_tstat": 0.5,
    }
    holdout.update(holdout_overrides)
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


def test_study_record_carries_alpha_beta_tstat_and_excess_per_exposure(wired_storage):
    import json
    from automation.optimizer import report
    out_path = report.generate_sweep_report(
        [_proposal_with_alpha_beta()], run_id="alphabetatest1",
        reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_alpha"] == pytest.approx(0.0002)
    assert rec["holdout_beta"] == pytest.approx(0.15)
    assert rec["holdout_alpha_tstat"] == pytest.approx(0.5)
    assert rec["holdout_no_alpha_detected"] is True
    assert rec["holdout_excess_per_unit_exposure"] == pytest.approx(0.11 / 0.1)


def test_study_record_no_alpha_detected_false_when_tstat_at_least_one(wired_storage):
    import json
    from automation.optimizer import report
    out_path = report.generate_sweep_report(
        [_proposal_with_alpha_beta(oos_alpha_tstat=2.4)], run_id="alphabetatest2",
        reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_no_alpha_detected"] is False


def test_study_record_excess_per_unit_exposure_none_below_min_exposure(wired_storage):
    """Dieselbe 0.05-Guard-Schwelle wie summary_de.py — near-Null-Exposure macht die Division
    numerisch bedeutungslos, kein erfundener Nenner (Issue #1077-Konvention)."""
    import json
    from automation.optimizer import report
    out_path = report.generate_sweep_report(
        [_proposal_with_alpha_beta(oos_exposure_fraction=0.01)], run_id="alphabetatest3",
        reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_excess_per_unit_exposure"] is None


# ── summary_de.py Abschnitt 2.3: Ranking nach excess_per_unit_exposure + α/β/t(α)-Spalten ─────────
# KEINE nautilus_trader-Abhaengigkeit — laeuft in jeder Sandbox.
def _minimal_report(**overrides):
    base = {
        "run_id": "run-abc", "run_status": "complete",
        "started_at_utc": "2026-07-30T00:00:00Z", "wallclock_s": 7200.0,
        "cli_args": {"n_jobs": 8, "n_jobs_source": "CLI"},
        "symbols_completed": None, "symbols_planned": None,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {},
            "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_section_2_3_ranks_by_excess_per_unit_exposure_not_total_return():
    """Kern-Akzeptanzkriterium: eine Study mit NIEDRIGEREM Strategie-Return aber HOEHEREM
    excess_per_unit_exposure rankt jetzt VOR einer Study mit hohem Strategie-Return, aber
    niedrigem excess_per_unit_exposure (vorher: Sortierung nach holdout_total_return)."""
    report = _minimal_report(studies=[
        {"strategy": "LowReturnHighRatio", "symbol": "X.ETORO", "holdout_total_return": 0.01,
         "holdout_buyhold_return": -0.10, "holdout_excess_return": 0.11,
         "holdout_exposure_fraction": 0.1, "holdout_excess_per_unit_exposure": 1.1},
        {"strategy": "HighReturnLowRatio", "symbol": "Y.ETORO", "holdout_total_return": 0.20,
         "holdout_buyhold_return": 0.15, "holdout_excess_return": 0.05,
         "holdout_exposure_fraction": 0.9, "holdout_excess_per_unit_exposure": 0.0556},
    ])
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    idx_low = section_2_3.index("LowReturnHighRatio")
    idx_high = section_2_3.index("HighReturnLowRatio")
    assert idx_low < idx_high


def test_section_2_3_falls_back_to_inline_ratio_without_precomputed_field():
    """Rueckwaertskompatibilitaet: Studies-Dicts ohne ``holdout_excess_per_unit_exposure`` (aeltere
    Report-JSONs, handgebaute Fixtures) werden weiterhin korrekt inline berechnet/sortiert."""
    report = _minimal_report(studies=[
        {"strategy": "S", "symbol": "A.ETORO", "holdout_total_return": 0.05,
         "holdout_buyhold_return": 0.03, "holdout_excess_return": 0.02,
         "holdout_exposure_fraction": 0.6},
    ])
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "3.3 %" in section_2_3  # 0.02 / 0.6 inline nachgerechnet


def test_section_2_3_shows_alpha_beta_tstat_columns():
    report = _minimal_report(studies=[
        {"strategy": "S", "symbol": "A.ETORO", "holdout_total_return": 0.05,
         "holdout_buyhold_return": 0.03, "holdout_excess_return": 0.02,
         "holdout_exposure_fraction": 0.6, "holdout_alpha": 0.00045,
         "holdout_beta": 0.42, "holdout_alpha_tstat": 3.1, "holdout_no_alpha_detected": False},
    ])
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "0.00045" in section_2_3
    assert "0.420" in section_2_3
    assert "3.10" in section_2_3


def test_section_2_3_marks_no_alpha_detected_candidates():
    report = _minimal_report(studies=[
        {"strategy": "PartTimeLong", "symbol": "A.ETORO", "holdout_total_return": 0.05,
         "holdout_buyhold_return": 0.03, "holdout_excess_return": 0.02,
         "holdout_exposure_fraction": 0.6, "holdout_alpha": 0.00001,
         "holdout_beta": 0.2, "holdout_alpha_tstat": 0.3, "holdout_no_alpha_detected": True},
    ])
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "NO_ALPHA_DETECTED" in section_2_3
    assert "schlägt Buy & Hold" not in section_2_3
