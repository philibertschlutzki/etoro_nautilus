"""Issue #1303 (GH #1180, P0) — STRUCTURAL_ALL_UNEVALUABLE wird nicht mehr unbedingt als
signal_sparse klassifiziert.

Symptom. 70 (Strategie, Symbol)-Paare erhielten ``action="search_space_override"`` aus einem Lauf
ohne einen einzigen Trade — ``diagnose_structural_zero_eligible_gate``s STRUCTURAL_ALL_UNEVALUABLE-
Zweig sah ``is_total_trades``/``max_is_trades`` nie und kannte ``signal_absent`` nicht.

Fix.
1. Neue Pflicht-Keywords ``max_is_trades``/``median_is_trades`` (kein Default): ``max_is_trades==0``
   ⇒ ``binding_cause="signal_absent"``, ``gate_type=None``, ``proposed_action="none"``. Nur bei
   ``max_is_trades > 0`` bleibt der Zweig frequenzseitig (``signal_sparse``/``search_space_
   override``).
2. Ein dominanter ``worker_error`` (>= 50% der gezaehlten Trials, aus Issue #1299/GH #1176) geht
   JEDER anderen Klassifikation vor: ``binding_cause="data_unavailable"``, ``proposed_action="none"``.
3. ``recommend_diagnosis_action`` lehnt ``search_space_override`` für
   ``binding_cause in {signal_absent, data_unavailable, inference_unavailable}`` ab.
"""
import pytest

from automation.optimizer.sweep_diagnostics import (
    diagnose_structural_zero_eligible_gate, diagnose_trade_frequency, recommend_diagnosis_action,
)


# ---------------------------------------------------------------------------------------------
# diagnose_structural_zero_eligible_gate — max_is_trades==0 -> signal_absent
# ---------------------------------------------------------------------------------------------

def test_structural_all_unevaluable_with_zero_max_is_trades_is_signal_absent():
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_WINDOW_UNREACHABLE": 70}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
        max_is_trades=0, median_is_trades=0)
    assert diagnosis["binding_cause"] == "signal_absent"
    assert diagnosis["gate_type"] is None
    assert diagnosis["proposed_action"] == "none"


def test_structural_all_unevaluable_with_positive_max_is_trades_stays_signal_sparse():
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_WINDOW_UNREACHABLE": 70}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
        max_is_trades=7, median_is_trades=0)
    assert diagnosis["binding_cause"] == "signal_sparse"
    assert diagnosis["gate_type"] == "frequency"
    assert diagnosis["proposed_action"] == "search_space_override"


def test_max_is_trades_is_a_mandatory_keyword_argument():
    with pytest.raises(TypeError):
        diagnose_structural_zero_eligible_gate(
            {"REJECT_OOS_WINDOW_UNREACHABLE": 70}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE")


def test_median_is_trades_is_also_a_mandatory_keyword_argument():
    with pytest.raises(TypeError):
        diagnose_structural_zero_eligible_gate(
            {"REJECT_OOS_WINDOW_UNREACHABLE": 70}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
            max_is_trades=0)


# ---------------------------------------------------------------------------------------------
# diagnose_structural_zero_eligible_gate — dominanter worker_error -> data_unavailable
# ---------------------------------------------------------------------------------------------

def test_dominant_worker_error_yields_data_unavailable_independent_of_max_is_trades():
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_WINDOW_UNREACHABLE": 40}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
        max_is_trades=7, median_is_trades=3,  # ohne den worker_error-Vorrang waere das signal_sparse.
        worker_error_counts={"no_ticks_in_window": 40})
    assert diagnosis["binding_cause"] == "data_unavailable"
    assert diagnosis["proposed_action"] == "none"


def test_minority_worker_error_does_not_trigger_data_unavailable():
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_WINDOW_UNREACHABLE": 40}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
        max_is_trades=7, median_is_trades=3,
        worker_error_counts={"no_ticks_in_window": 5, "irrelevant": 35})
    assert diagnosis["binding_cause"] != "data_unavailable"


def test_worker_error_counts_none_is_backward_compatible():
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_WINDOW_UNREACHABLE": 40}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
        max_is_trades=7, median_is_trades=3, worker_error_counts=None)
    assert diagnosis["binding_cause"] == "signal_sparse"


# ---------------------------------------------------------------------------------------------
# diagnose_trade_frequency — derselbe worker_error-Vorrang auf Trial-Ebene
# ---------------------------------------------------------------------------------------------

def test_diagnose_trade_frequency_dominant_worker_error_is_data_unavailable():
    trials = [{"oos_evaluated": False, "worker_error": "no_ticks_in_window"} for _ in range(10)]
    result = diagnose_trade_frequency(trials, oos_min_trades=5)
    assert result["binding_cause"] == "data_unavailable"


def test_diagnose_trade_frequency_minority_worker_error_falls_through():
    trials = (
        [{"oos_evaluated": False, "is_total_trades": 0, "worker_error": "no_ticks_in_window"}]
        + [{"oos_evaluated": False, "is_total_trades": 0} for _ in range(9)]
    )
    result = diagnose_trade_frequency(trials, oos_min_trades=5)
    assert result["binding_cause"] != "data_unavailable"
    assert result["binding_cause"] == "signal_absent"


# ---------------------------------------------------------------------------------------------
# recommend_diagnosis_action — lehnt search_space_override fuer die drei Ursachen ab.
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cause", ["signal_absent", "data_unavailable", "inference_unavailable"])
def test_recommend_diagnosis_action_never_proposes_search_space_override(cause):
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO", {"binding_cause": cause},
        n_runs_confirmed=5, budget_executed_fraction=1.0, stop_reason="STRUCTURAL_ALL_UNEVALUABLE")
    assert rec["action"] != "search_space_override"


def test_recommend_diagnosis_action_data_unavailable_is_none():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO", {"binding_cause": "data_unavailable"})
    assert rec["action"] == "none"
