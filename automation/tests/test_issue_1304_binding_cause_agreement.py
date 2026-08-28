"""Issue #1304 (GH #1181, P1) — eine Quelle für binding_cause; Report und Ereignisstrom
widersprechen sich nicht mehr.

Symptom. Für dieselbe Study meldet der Ereignisstrom ``signal_absent``, das Report-Artefakt
``signal_sparse`` — zwei unabhängige Implementierungen mit disjunkten Vokabularen
(``sweep_diagnostics.diagnose_trade_frequency`` vs. ``diagnose_structural_zero_eligible_gate``).

Fix.
1. ``report._structural_zero_eligible_diagnosed_pairs`` liest den sweep-seitigen Befund aus dem
   ``STRUCTURAL_ALL_UNEVALUABLE``-Ereignisstrom (``events_path``), statt ihn neu abzuleiten. Der
   Report-Zweig bleibt NUR Fallback für Studies ohne Ereignis (``source="report_fallback"``).
2. ``invariants.check_binding_cause_agreement`` (severity ``high``) FAILt bei Divergenz.
"""
import json
import logging

from automation.optimizer import invariants as inv
from automation.optimizer import report


def _study_record(strategy, symbol, *, stop_reason, is_rejection_detail_counts,
                  budget_executed_fraction=1.0, max_is_trades=None, median_is_trades=None,
                  worker_error_counts=None):
    return {"strategy": strategy, "symbol": symbol, "stop_reason": stop_reason,
            "is_rejection_detail_counts": is_rejection_detail_counts,
            "budget_executed_fraction": budget_executed_fraction,
            "max_is_trades": max_is_trades, "median_is_trades": median_is_trades,
            "worker_error_counts": worker_error_counts}


def _write_structural_all_unevaluable_event(tmp_path, strategy, symbol, binding_cause, **extra):
    p = tmp_path / "events.jsonl"
    event = {
        "event_type": "STRUCTURAL_ALL_UNEVALUABLE", "strategy": strategy, "symbol": symbol,
        "binding_cause": binding_cause, **extra,
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return p


# ---------------------------------------------------------------------------------------------
# report._structural_zero_eligible_diagnosed_pairs — Ereignis gewinnt, source="event"
# ---------------------------------------------------------------------------------------------

def test_event_binding_cause_wins_over_report_derivation(tmp_path):
    events_path = _write_structural_all_unevaluable_event(
        tmp_path, "SqueezeBreakoutStrategy", "TSLA.ETORO", "signal_absent")
    studies_out = [
        _study_record(
            "SqueezeBreakoutStrategy", "TSLA.ETORO", stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
            # Der Report-Zweig würde hier (ohne Ereignis) "signal_sparse" liefern (kein
            # max_is_trades=0 übergeben) — das Ereignis muss trotzdem gewinnen.
            is_rejection_detail_counts={"REJECT_OOS_WINDOW_UNREACHABLE": 70}),
    ]
    pairs = report._structural_zero_eligible_diagnosed_pairs(studies_out, events_path=events_path)
    entry = next(e for e in pairs if e["strategy"] == "SqueezeBreakoutStrategy")
    assert entry["binding_cause"] == "signal_absent"
    assert entry["action"] == "none"  # signal_absent -> nie search_space_override.
    assert entry["source"] == "event"


def test_event_binding_cause_hold_duration_is_representable():
    """diagnose_trade_frequency kann 'hold_duration' liefern — ein Wert, den der Report-Zweig
    (diagnose_structural_zero_eligible_gate) GAR NICHT kennt (disjunktes Vokabular, Root-Cause
    #1304). Das Ereignis muss ihn trotzdem unverändert durchreichen."""
    action, gate_type = (
        report._action_for_structural_binding_cause("hold_duration")[1],
        report._action_for_structural_binding_cause("hold_duration")[0],
    )
    assert gate_type is None
    assert action == "none"


def test_no_event_falls_back_to_report_derivation_with_report_fallback_source():
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
                      is_rejection_detail_counts={"REJECT_OOS_INACTIVE": 30}),
    ]
    pairs = report._structural_zero_eligible_diagnosed_pairs(studies_out, events_path=None)
    entry = next(e for e in pairs if e["strategy"] == "AdxAtrMomentumStrategy")
    assert entry["binding_cause"] == "signal_sparse"
    assert entry["source"] == "report_fallback"


def test_structural_zero_eligible_stays_live_derivation_even_with_events_path(tmp_path):
    """STRUCTURAL_ZERO_ELIGIBLE hat keinen äquivalenten Ereignistyp -> bleibt unveraendert
    'live_derivation', unabhängig davon, ob events_path übergeben wird."""
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("", encoding="utf-8")
    studies_out = [
        _study_record("S", "X.ETORO", stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 123}),
    ]
    pairs = report._structural_zero_eligible_diagnosed_pairs(studies_out, events_path=events_path)
    entry = next(e for e in pairs if e["strategy"] == "S")
    assert entry["source"] == "live_derivation"


# ---------------------------------------------------------------------------------------------
# invariants.check_binding_cause_agreement
# ---------------------------------------------------------------------------------------------

def test_check_binding_cause_agreement_fails_on_divergence_with_both_values_in_actual():
    records = [
        {"strategy": "S", "symbol": "X.ETORO",
         "event_binding_cause": "signal_absent", "report_binding_cause": "signal_sparse"},
    ]
    result = inv.check_binding_cause_agreement(records)
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual == {
        "S/X.ETORO": {"event_binding_cause": "signal_absent",
                      "report_binding_cause": "signal_sparse"},
    }


def test_check_binding_cause_agreement_passes_when_matching():
    records = [
        {"strategy": "S", "symbol": "X.ETORO",
         "event_binding_cause": "signal_sparse", "report_binding_cause": "signal_sparse"},
    ]
    result = inv.check_binding_cause_agreement(records)
    assert result.passed is True
    assert result.actual is None


def test_check_binding_cause_agreement_inconclusive_without_dual_source_studies():
    result = inv.check_binding_cause_agreement([])
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


# ---------------------------------------------------------------------------------------------
# report._binding_cause_agreement_records — end-to-end gegen einen echten Ereignisstrom.
# ---------------------------------------------------------------------------------------------

def test_binding_cause_agreement_records_end_to_end(tmp_path):
    events_path = _write_structural_all_unevaluable_event(
        tmp_path, "SqueezeBreakoutStrategy", "TSLA.ETORO", "signal_absent")
    studies_out = [
        _study_record(
            "SqueezeBreakoutStrategy", "TSLA.ETORO", stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
            is_rejection_detail_counts={"REJECT_OOS_WINDOW_UNREACHABLE": 70}),
    ]
    records = report._binding_cause_agreement_records(studies_out, events_path)
    assert len(records) == 1
    assert records[0]["event_binding_cause"] == "signal_absent"
    assert records[0]["report_binding_cause"] == "signal_sparse"  # die Divergenz.

    result = inv.check_binding_cause_agreement(records)
    assert result.passed is False
    assert "SqueezeBreakoutStrategy/TSLA.ETORO" in result.actual
