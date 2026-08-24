"""Issue #995/#1147 (Katalog #1170, Pitfall #413 in AGENTS.md) — INCONCLUSIVE wird als
``passed=True`` berichtet (zwei ``blocking``-Checks).

Symptom: ``check_effective_stop_distance`` (blocking), ``check_stop_loss_vs_bar_range`` (blocking)
und ``check_trailing_stop_risk_calibration_acceptance`` (high) meldeten ``passed=True`` bei leerer
Grundgesamtheit — ununterscheidbar von einem echten PASS fuer jeden Konsumenten, der nur ``passed``
liest (``report._compute_decision_admissible``, ``confirm.py``s ``REJECT_STUDY_INVARIANT_BLOCKING``).

Fix: die drei Checks liefern bei fehlender Evidenz jetzt ``passed=None``, ``evaluable=False``
(zusaetzlich zum bereits bestehenden ``inconclusive=True``/``evaluability``-Dict). Die
POST-HOC-Konsumenten (``_compute_decision_admissible``, ``confirm.py``, ``summary_de.py`` Abschnitt
5.1) behandeln das als Befund; der LIVE-Fail-Fast-Abbruch
(``sweep._first_failing_fail_fast_invariant``) bleibt bewusst UNVERAENDERT auf ein nachgewiesenes
``passed is False`` beschraenkt (Pitfall #404 — kein Abbruch mitten im Sweep allein wegen
fehlender Evidenz)."""
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sd
from automation.optimizer import sweep as sw


# --- die drei betroffenen Checks: passed=None/evaluable=False bei leerer Grundgesamtheit ---------

def test_check_effective_stop_distance_no_candidates_is_not_a_pass():
    result = inv.check_effective_stop_distance([{"strategy": "A", "symbol": "X.ETORO"}])
    assert result.passed is None
    assert result.evaluable is False
    assert result.severity == "blocking"
    assert result.evaluability["n_candidates"] == 0


def test_check_effective_stop_distance_insufficient_stop_exits_is_not_a_pass():
    study = {
        "strategy": "A", "symbol": "X.ETORO",
        "atr_median_bps": 10.0, "atr_trailing_multiplier_median": 2.0,
        "gross_loss_median_bps_trailing_stop": None,
        "oos_gross_loss_mean_bps_pooled": 5.0,
        "oos_n_trailing_stop_losses": 2,
    }
    result = inv.check_effective_stop_distance([study])
    assert result.passed is None
    assert result.evaluable is False
    assert result.evaluability["n_candidates"] == 1
    assert result.evaluability["n_measured"] == 0


def test_check_effective_stop_distance_measured_case_still_passes_normally():
    study = {
        "strategy": "A", "symbol": "X.ETORO",
        "atr_median_bps": 10.0, "atr_trailing_multiplier_median": 2.0,
        "gross_loss_median_bps_trailing_stop": 15.0,
        "oos_n_trailing_stop_losses": 40,
        # Issue #1081/#1229 — der Legacy-Fallback konsumiert seither die GEMESSENE Distanz statt
        # k*ATR; bewusst identisch zu 10.0*2.0 gesetzt (bit-identische Testerwartung).
        "stop_distance_bps_measured": 20.0,
    }
    result = inv.check_effective_stop_distance([study])
    assert result.passed is True
    assert result.evaluable is True
    assert result.evaluability["n_measured"] == 1


def test_check_stop_loss_vs_bar_range_no_candidates_is_not_a_pass():
    result = inv.check_stop_loss_vs_bar_range([{"strategy": "A", "symbol": "X.ETORO"}])
    assert result.passed is None
    assert result.evaluable is False
    assert result.severity == "blocking"
    assert result.evaluability["evaluable"] is False


def test_check_trailing_stop_risk_calibration_acceptance_below_3_studies_is_not_a_pass():
    study = {
        "strategy": "A", "symbol": "X.ETORO",
        "atr_median_bps": 10.0, "atr_trailing_multiplier_median": 2.0,
        "gross_loss_median_bps_trailing_stop": 15.0,
        "oos_n_trailing_stop_losses": 40,
    }
    result = inv.check_trailing_stop_risk_calibration_acceptance([study])
    assert result.passed is None
    assert result.evaluable is False
    assert result.severity == "high"
    # Issue #1056/#1205 — dieses Gate ist die (unveraenderte) >= 3-Studies-Evidenzschranke fuer
    # Kriterien 2/3 (Ratio-Band/TRAILING_STOP-Anteil); die evaluability traegt seither
    # "n_candidates" statt des vormaligen "n_measured" (das an ``k_atr_values``, die alte
    # Study-Median-Spearman-Stichprobe, gebunden war und mit dieser entfallen ist).
    assert result.evaluability["n_candidates"] == 1


# --- InvariantResult.to_dict() surfaces the new field ---------------------------------------------

def test_to_dict_carries_evaluable_false_but_omits_it_when_true():
    not_evaluable = inv.check_stop_loss_vs_bar_range([])
    d = not_evaluable.to_dict()
    assert d["evaluable"] is False
    assert d["passed"] is None

    evaluable_default = inv.InvariantResult(
        name="x", passed=True, expected=1, actual=1, detail="OK")
    assert "evaluable" not in evaluable_default.to_dict()


# --- report._compute_decision_admissible treats evaluable=False as blocking ----------------------

def test_decision_admissible_false_when_blocking_check_is_not_evaluable():
    checks = [
        {"name": "check_stop_loss_vs_bar_range", "severity": "blocking", "passed": None,
         "evaluable": False},
    ]
    assert rpt._compute_decision_admissible(checks) is False


def test_decision_admissible_true_when_only_non_blocking_check_is_not_evaluable():
    checks = [
        {"name": "check_trailing_stop_risk_calibration_acceptance", "severity": "high",
         "passed": None, "evaluable": False},
    ]
    assert rpt._compute_decision_admissible(checks) is True


def test_decision_admissible_still_true_for_a_clean_pass():
    checks = [{"name": "check_effective_stop_distance", "severity": "blocking", "passed": True}]
    assert rpt._compute_decision_admissible(checks) is True


# --- sweep fail-fast stays strictly on a definitive FAIL, per Pitfall #404 -----------------------

def test_fail_fast_does_not_trigger_on_evaluable_false():
    checks = [
        {"name": "check_effective_stop_distance", "severity": "blocking", "passed": None,
         "evaluable": False},
    ]
    assert sw._first_failing_fail_fast_invariant(
        checks, fail_fast_invariants=["check_effective_stop_distance"]) is None


def test_fail_fast_still_triggers_on_a_definitive_fail():
    checks = [{"name": "check_effective_stop_distance", "severity": "blocking", "passed": False}]
    assert sw._first_failing_fail_fast_invariant(
        checks, fail_fast_invariants=["check_effective_stop_distance"]) == "check_effective_stop_distance"


# --- summary_de.py Section 5.1 keeps "nicht auswertbar" separate from real FAILs ------------------

def _report_with_checks(checks):
    return {"run_id": "r", "invariant_checks": checks, "studies": [],
            "cross_study": {}}


def test_section_5_separates_inconclusive_from_real_fails():
    checks = [
        {"name": "check_stop_loss_vs_bar_range", "check": "check_stop_loss_vs_bar_range",
         "severity": "blocking", "passed": None, "evaluable": False, "scope": "global",
         "detail": "nicht auswertbar"},
        {"name": "check_holding_time_cap", "check": "check_holding_time_cap",
         "severity": "blocking", "passed": False, "scope": "A/X.ETORO", "detail": "FAIL"},
    ]
    text = sd._section_5_anomalies(_report_with_checks(checks))
    assert "### 5.1 Übersicht — Invarianten-FAILs (1)" in text
    assert "check_holding_time_cap" in text
    assert "### 5.1b Nicht auswertbar (1 Checks)" in text
    assert "check_stop_loss_vs_bar_range" in text
    # der nicht auswertbare Check darf NICHT in der FAIL-Tabellenzeile mitgezaehlt werden
    fail_section = text.split("### 5.1b")[0]
    assert "check_stop_loss_vs_bar_range" not in fail_section


def test_section_5_reports_none_inconclusive_when_all_clean():
    checks = [{"name": "check_x", "check": "check_x", "severity": "medium", "passed": True,
               "scope": "global", "detail": "OK"}]
    text = sd._section_5_anomalies(_report_with_checks(checks))
    assert "### 5.1b Nicht auswertbar (0 Checks)" in text
