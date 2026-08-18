"""Issue #1002/#1154 (Katalog #1170) — ``REJECTED_ON_HOLDOUT`` deckt drei verschiedene Stufen ab.

Symptom: der Status trug in den Referenzlaeufen drei disjunkte Ursachen (``is_gate:
REJECT_OOS_MIN_PSR``, ``holdout:REJECT_HOLDOUT_GATE``, ``deflation:REJECT_HOLDOUT_DSR_DROP``) —
Abschnitt 2.2 zeigte fuer ALLE "REJECTED_ON_HOLDOUT", auch fuer Studies, die den Holdout nie
erreicht hatten.

Fix: eigene Statuswerte ``REJECTED_BEFORE_HOLDOUT`` (kein eligibler Trial/keine Statistik) und
``REJECTED_ON_DEFLATION``; ``summary_de.py`` Abschnitt 2.2 zeigt zusaetzlich ``blocking_stage``
(#1152) als eigene Spalte; ``promotion_outcome_counts`` unterscheidet die drei Klassen automatisch
(dieselbe Zaehlung ueber den jetzt disjunkten ``status``-Wert).
"""
from automation.optimizer import confirm
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sd


# --- export_symbol_proposal carries blocking_stage/all_failed_stages through ----------------------

def test_export_symbol_proposal_carries_blocking_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    promotion = {
        "status": "REJECTED_ON_DEFLATION",
        "is_rejection_detail_override": "REJECT_HOLDOUT_DSR_DROP",
        "blocking_stage": "deflation",
        "all_failed_stages": ["deflation"],
        "symbol_params": {}, "R_symbol": 0.1, "R_global": 0.05, "promotion_margin": 0.1,
        "trial_dir": None, "metrics_symbol": {}, "metrics_global": {},
    }

    class _FakeStudy:
        trials = []
        user_attrs = {}

        @property
        def best_value(self):
            raise ValueError("no trials")

    import json
    path = confirm.export_symbol_proposal(_FakeStudy(), "S", "SYM.ETORO", promotion)
    payload = json.loads(path.read_text("utf-8"))
    assert payload["blocking_stage"] == "deflation"
    assert payload["all_failed_stages"] == ["deflation"]
    assert payload["status"] == "REJECTED_ON_DEFLATION"


# --- report._study_record propagates the fields from the proposal ---------------------------------

def test_study_record_propagates_blocking_stage_from_proposal():
    proposal = {
        "symbol": "SYM.ETORO", "strategy": "S", "status": "REJECTED_BEFORE_HOLDOUT",
        "blocking_stage": "confirm_or_selection", "all_failed_stages": ["confirm_or_selection"],
    }

    class _FakeStudy:
        trials = []
        user_attrs = {}
        best_trials = []

    record, _ = rpt._study_record(proposal, _FakeStudy(), tournament_cfg={})
    assert record["blocking_stage"] == "confirm_or_selection"
    assert record["all_failed_stages"] == ["confirm_or_selection"]


def test_study_record_defaults_to_none_and_empty_list_without_proposal_fields():
    proposal = {"symbol": "SYM.ETORO", "strategy": "S", "status": "READY_FOR_PR"}

    class _FakeStudy:
        trials = []
        user_attrs = {}
        best_trials = []

    record, _ = rpt._study_record(proposal, _FakeStudy(), tournament_cfg={})
    assert record["blocking_stage"] is None
    assert record["all_failed_stages"] == []


# --- summary_de.py Section 2.2 shows the blocking_stage column ------------------------------------

def _report_with_studies(studies):
    return {"run_id": "r", "studies": studies, "invariant_checks": [], "cross_study": {}}


def test_section_2_2_shows_blocking_stage_column():
    studies = [
        {"strategy": "A", "symbol": "X.ETORO", "promotion_outcome": "REJECTED_ON_HOLDOUT",
         "blocking_stage": "holdout", "holdout_total_return": -0.02},
        {"strategy": "B", "symbol": "Y.ETORO", "promotion_outcome": "REJECTED_ON_DEFLATION",
         "blocking_stage": "deflation", "holdout_total_return": 0.01},
        {"strategy": "C", "symbol": "Z.ETORO", "promotion_outcome": "REJECTED_BEFORE_HOLDOUT",
         "blocking_stage": "confirm_or_selection", "holdout_total_return": None},
    ]
    text = sd._section_2_monetary_result(_report_with_studies(studies))
    assert "| Strategie | Symbol | Holdout-Return (simuliert) | Ablehnungsgrund | Stufe |" in text
    assert "| A | X.ETORO |" in text and "| holdout |" in text
    assert "| deflation |" in text
    assert "| confirm_or_selection |" in text


def test_promotion_outcome_counts_distinguishes_the_three_classes():
    studies_out = [
        {"promotion_outcome": "REJECTED_BEFORE_HOLDOUT"},
        {"promotion_outcome": "REJECTED_BEFORE_HOLDOUT"},
        {"promotion_outcome": "REJECTED_ON_HOLDOUT"},
        {"promotion_outcome": "REJECTED_ON_DEFLATION"},
        {"promotion_outcome": "READY_FOR_PR"},
    ]
    counts = rpt._promotion_outcome_counts(studies_out)
    assert counts["REJECTED_BEFORE_HOLDOUT"] == 2
    assert counts["REJECTED_ON_HOLDOUT"] == 1
    assert counts["REJECTED_ON_DEFLATION"] == 1
    assert counts["READY_FOR_PR"] == 1
