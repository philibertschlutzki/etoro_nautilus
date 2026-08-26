"""Issue #1296 (GH #1169, Katalog #1272-1297, P1) — Strukturell erschöpfte Paare aus dem Budget
nehmen.

Symptom: 198/200/123/125 Trials Budget-Defizit je Lauf, ausschliesslich ``STRUCTURAL_ZERO_ELIGIBLE``
und ``STRUCTURAL_ALL_UNEVALUABLE``. ``diagnosed_pairs`` ist mit 10-13 Einträgen befüllt
(``binding_cause in {atr_floor_dominant, gate_unreachable, signal_sparse}``),
``diagnosed_pairs_skipped`` ist leer, "Automatisch denylistete Paare: 0", "Budget-deprioritisierte
Paare: 0". SqueezeBreakout liefert in 4/4 Läufen ``n_evaluable = 0`` bei 115-180 Trials und wird im
nächsten Lauf unverändert erneut budgetiert.

Root-Cause: der Diagnose-Rückschrieb (#829/#830/#1219/#1244) erzeugt Einträge, aber keine Wirkung
für zwei ``binding_cause``-Werte, die ``diagnose_structural_zero_eligible_gate`` (statt
``diagnose_trade_frequency``) erzeugt: ``gate_unreachable``/``atr_floor_dominant``. Diese Funktion
speist bislang AUSSCHLIESSLICH eine cache-unabhängige, report-sichtbare "live_derivation" (siehe
``report._structural_zero_eligible_diagnosed_pairs``-Docstring: "NUR GESCHRIEBEN
(Report-Sichtbarkeit)") — nie ``recommend_diagnosis_action``/``record_diagnosed_pair``.

Fix (vier Punkte aus dem Issue-Text):
  1. ``binding_cause='signal_sparse'`` MIT ``n_evaluable=0`` über >= ``max_consecutive_structural_
     runs`` (Default 2) bestätigte Läufe ⇒ ``'denylist'`` (neuer Zweig in
     ``recommend_diagnosis_action``, additiv zur bestehenden #778/#831-Override-Logik).
  2. ``binding_cause='gate_unreachable'`` ⇒ ``'deprioritized'`` ab der ERSTEN Bestätigung, NIE
     ``'denylist'`` (die Ursache liegt im Gate/#1282, nicht im Paar, #1264 bleibt gültig) — über
     einen neuen, report-build-zeitigen Rückschrieb (``report._writeback_gate_unreachable_
     diagnoses``, modelliert auf ``_writeback_search_stagnation_diagnoses``, #1069/#1219).
  3. ``binding_cause='atr_floor_dominant'`` ⇒ bewusst KEINE eigene Budget-Aktion — die Konsequenz
     läuft über #1295 (``atr_floor_dimension_freeze_policy``), nicht über diese Pipeline.
  4. ``invariants.check_diagnosis_actionability`` (Fix Punkt 5, ursprünglich #829) wird PER EINTRAG
     verschärft: ``action == 'none'`` ist nur zulässig, solange ``n_runs_confirmed <
     expires_after_runs``.

Scope: dieselbe SICHERE, report-build-zeit-sequenzielle Rückschrieb-Konvention wie #1069/#1219 (kein
Eingriff in den LIVE, nebenläufigen Per-Trial-Callback in ``run_optimization.py`` — dort wird nur
``n_evaluable`` zusätzlich in den bereits bestehenden ``recommend_diagnosis_action``-Aufruf
durchgereicht, kein neues Verhalten für bestehende Aufrufer ohne dieses Feld).
"""
import json

import pytest

from automation.optimizer import sweep_diagnostics
from automation.optimizer import invariants as inv
from automation.optimizer.sweep_diagnostics import (
    recommend_diagnosis_action, record_diagnosed_pair, load_diagnosed_pairs_cache,
    diagnose_structural_zero_eligible_gate,
)
from automation.optimizer.report import _writeback_gate_unreachable_diagnoses


@pytest.fixture(autouse=True)
def _enable_diagnostic_writeback(monkeypatch):
    monkeypatch.setattr(sweep_diagnostics, "_read_diagnostic_writeback_enabled", lambda: True)


# ---------------------------------------------------------------------------------------------
# Fix Punkt 2: diagnose_structural_zero_eligible_gate's 'gate' branch now proposes a consequence
# ---------------------------------------------------------------------------------------------

def test_gate_branch_now_proposes_budget_deprioritization_not_none():
    result = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_MIN_ALPHA_TSTAT": 40}, stop_reason="STRUCTURAL_ZERO_ELIGIBLE")
    assert result["binding_cause"] == "gate_unreachable"
    assert result["proposed_action"] == "budget_deprioritization"


def test_gate_branch_never_proposes_denylist():
    """#1264 bleibt gültig: die Ursache liegt in der konfigurierten Gate-Schwelle, nicht im Paar."""
    result = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_MIN_ALPHA_TSTAT": 100}, stop_reason="STRUCTURAL_ZERO_ELIGIBLE")
    assert result["proposed_action"] != "denylist"


# ---------------------------------------------------------------------------------------------
# Fix Punkt 2: recommend_diagnosis_action's new 'gate_unreachable' branch
# ---------------------------------------------------------------------------------------------

def test_gate_unreachable_zero_confirmations_is_none():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "gate_unreachable"},
        n_runs_confirmed=0)
    assert rec["action"] == "none"


def test_gate_unreachable_one_confirmation_is_deprioritized():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "gate_unreachable"},
        n_runs_confirmed=1)
    assert rec["action"] == "deprioritized"


def test_gate_unreachable_never_escalates_to_denylist_even_after_many_confirmations():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "gate_unreachable"},
        n_runs_confirmed=50)
    assert rec["action"] == "deprioritized"


# ---------------------------------------------------------------------------------------------
# Fix Punkt 1: recommend_diagnosis_action's signal_sparse + n_evaluable==0 denylist escalation
# ---------------------------------------------------------------------------------------------

def test_signal_sparse_n_evaluable_zero_below_threshold_is_unaffected():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "signal_sparse"},
        n_runs_confirmed=1, n_evaluable=0)
    assert rec["action"] != "denylist"


def test_signal_sparse_n_evaluable_zero_at_threshold_is_denylist():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "signal_sparse"},
        n_runs_confirmed=2, n_evaluable=0)
    assert rec["action"] == "denylist"


def test_signal_sparse_n_evaluable_zero_respects_custom_max_consecutive_structural_runs():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "signal_sparse"},
        n_runs_confirmed=2, n_evaluable=0, max_consecutive_structural_runs=3)
    assert rec["action"] != "denylist"


def test_signal_sparse_nonzero_n_evaluable_never_denylists_via_this_branch():
    """Nur n_evaluable==0 (persistent kein einziger evaluierbarer Trial) qualifiziert -- ein
    Paar, das gelegentlich evaluierbare Trials produziert, bleibt in der bestehenden #831-
    Override-Logik."""
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "signal_sparse"},
        n_runs_confirmed=10, n_evaluable=3)
    assert rec["action"] != "denylist"


def test_signal_sparse_missing_n_evaluable_is_bit_identical_to_pre_1296():
    """Legacy-/Test-Aufrufer ohne n_evaluable (Default None): kein Unterschied zu vorher -- fällt
    auf die bestehende Override-/None-Logik zurück, niemals denylist über diesen neuen Zweig."""
    rec_no_override = recommend_diagnosis_action(
        "AdxAtrMomentum", "TSLA.ETORO", {"binding_cause": "signal_sparse"},
        n_runs_confirmed=10, has_existing_override=True)
    assert rec_no_override["action"] == "none"


def test_hold_duration_cause_unaffected_by_the_new_signal_sparse_only_condition():
    """Die neue Denylist-Eskalation gilt AUSSCHLIESSLICH fuer 'signal_sparse', nicht fuer
    'hold_duration' (dieselbe elif-Verzweigung, aber der cause-Check schliesst 'hold_duration'
    explizit aus der neuen Bedingung aus)."""
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "hold_duration"},
        n_runs_confirmed=10, n_evaluable=0, has_existing_override=True)
    assert rec["action"] != "denylist"


# ---------------------------------------------------------------------------------------------
# Fix Punkt 3: atr_floor_dominant gets no budget action from this pipeline (routed to #1295)
# ---------------------------------------------------------------------------------------------

def test_atr_floor_dominant_is_not_a_recognized_cause_in_recommend_diagnosis_action():
    """atr_floor_dominant wird NIE an recommend_diagnosis_action uebergeben (siehe
    report._atr_floor_dominant_diagnosed_pairs) -- faellt hier auf den generischen else-Zweig
    zurueck, falls doch: KEINE Konsequenz."""
    rec = recommend_diagnosis_action(
        "AdxAtrStrategy", "TSLA.ETORO", {"binding_cause": "atr_floor_dominant"},
        n_runs_confirmed=50)
    assert rec["action"] == "none"


# ---------------------------------------------------------------------------------------------
# Fix Punkt 2: report._writeback_gate_unreachable_diagnoses
# ---------------------------------------------------------------------------------------------

def _study(strategy, symbol, *, stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
           rejection_detail="REJECT_OOS_MIN_ALPHA_TSTAT", n=40, budget_executed_fraction=0.95):
    return {
        "strategy": strategy, "symbol": symbol, "stop_reason": stop_reason,
        "is_rejection_detail_counts": {rejection_detail: n},
        "budget_executed_fraction": budget_executed_fraction,
    }


def test_no_gate_unreachable_studies_is_a_pure_no_op(tmp_path):
    out = _writeback_gate_unreachable_diagnoses([], run_id="run_1", work_dir=tmp_path)
    assert out == []
    assert load_diagnosed_pairs_cache(tmp_path) == {}


def test_non_gate_structural_studies_do_not_trigger_this_writeback(tmp_path):
    """Eine STRUCTURAL_ZERO_ELIGIBLE-Study mit einer FREQUENCY- (nicht GATE-) Rejection-Detail
    erzeugt binding_cause='signal_sparse', nicht 'gate_unreachable' -- dieser Rueckschrieb
    ignoriert sie (das ist Fix Punkt 1s Zustaendigkeit, ueber run_optimization.py)."""
    out = _writeback_gate_unreachable_diagnoses(
        [_study("SqueezeBreakout", "ASML.ETORO", rejection_detail="REJECT_MIN_TRADES")],
        run_id="run_1", work_dir=tmp_path)
    assert out == []


def test_first_observation_writes_action_none(tmp_path):
    out = _writeback_gate_unreachable_diagnoses(
        [_study("SqueezeBreakout", "ASML.ETORO")], run_id="run_1", work_dir=tmp_path)
    assert len(out) == 1
    assert out[0]["strategy"] == "SqueezeBreakout"
    assert out[0]["symbol"] == "ASML.ETORO"
    assert out[0]["binding_cause"] == "gate_unreachable"
    assert out[0]["action"] == "none"
    cache = load_diagnosed_pairs_cache(tmp_path)
    assert cache[("SqueezeBreakout", "ASML.ETORO")]["binding_cause"] == "gate_unreachable"


def test_second_observation_escalates_to_deprioritized(tmp_path):
    _writeback_gate_unreachable_diagnoses(
        [_study("SqueezeBreakout", "ASML.ETORO")], run_id="run_1", work_dir=tmp_path)
    out = _writeback_gate_unreachable_diagnoses(
        [_study("SqueezeBreakout", "ASML.ETORO")], run_id="run_2", work_dir=tmp_path)
    assert out[0]["action"] == "deprioritized"


def test_never_escalates_to_denylist_across_many_runs(tmp_path):
    action = None
    for i in range(10):
        out = _writeback_gate_unreachable_diagnoses(
            [_study("SqueezeBreakout", "ASML.ETORO")], run_id=f"run_{i}", work_dir=tmp_path)
        action = out[0]["action"]
        assert action != "denylist"
    assert action == "deprioritized"


def test_multiple_pairs_are_all_written(tmp_path):
    out = _writeback_gate_unreachable_diagnoses(
        [_study("SqueezeBreakout", "ASML.ETORO"), _study("AdxAtrMomentum", "TSLA.ETORO")],
        run_id="run_1", work_dir=tmp_path)
    pairs_written = {(r["strategy"], r["symbol"]) for r in out}
    assert pairs_written == {("SqueezeBreakout", "ASML.ETORO"), ("AdxAtrMomentum", "TSLA.ETORO")}


def test_prior_entry_with_a_different_binding_cause_does_not_leak_its_n_runs_confirmed(tmp_path):
    record_diagnosed_pair(
        {"strategy": "SqueezeBreakout", "symbol": "ASML.ETORO",
         "action": "search_space_override", "binding_cause": "signal_sparse",
         "n_runs_confirmed": 5},
        work_dir=tmp_path, run_id="run_prior",
    )
    out = _writeback_gate_unreachable_diagnoses(
        [_study("SqueezeBreakout", "ASML.ETORO")], run_id="run_next", work_dir=tmp_path)
    assert out[0]["action"] == "none"


def test_writeback_respects_the_diagnostic_writeback_enabled_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep_diagnostics, "_read_diagnostic_writeback_enabled", lambda: False)
    _writeback_gate_unreachable_diagnoses(
        [_study("SqueezeBreakout", "ASML.ETORO")], run_id="run_1", work_dir=tmp_path)
    assert load_diagnosed_pairs_cache(tmp_path) == {}


def test_missing_strategy_or_symbol_is_skipped_not_fatal(tmp_path):
    out = _writeback_gate_unreachable_diagnoses(
        [_study(None, "ASML.ETORO"), _study("SqueezeBreakout", None)],
        run_id="run_1", work_dir=tmp_path)
    assert out == []


def test_wired_into_build_report():
    import inspect
    from automation.optimizer import report as rpt
    source = inspect.getsource(rpt._build_report)
    assert "_writeback_gate_unreachable_diagnoses" in source


# ---------------------------------------------------------------------------------------------
# Fix Punkt 4: check_diagnosis_actionability tightened per-entry (action='none' only permissible
# while n_runs_confirmed < expires_after_runs)
# ---------------------------------------------------------------------------------------------

def test_none_entry_below_its_own_expiry_still_passes():
    entries = [{"strategy": "SqueezeBreakout", "symbol": "ASML.ETORO", "binding_cause": "gate_unreachable",
                "action": "none", "n_runs_confirmed": 3, "expires_after_runs": 10}]
    assert inv.check_diagnosis_actionability(entries).passed is True


def test_none_entry_at_its_own_expiry_fails():
    entries = [{"strategy": "SqueezeBreakout", "symbol": "ASML.ETORO", "binding_cause": "signal_sparse",
                "action": "none", "n_runs_confirmed": 10, "expires_after_runs": 10}]
    result = inv.check_diagnosis_actionability(entries)
    assert result.passed is False
    assert "SqueezeBreakout/ASML.ETORO/signal_sparse" in result.actual["stale_none_past_expiry"]


def test_none_entry_past_its_own_expiry_fails():
    entries = [{"strategy": "SqueezeBreakout", "symbol": "ASML.ETORO", "binding_cause": "signal_sparse",
                "action": "none", "n_runs_confirmed": 15, "expires_after_runs": 10}]
    assert inv.check_diagnosis_actionability(entries).passed is False


def test_entries_missing_n_runs_confirmed_or_expires_after_runs_are_not_evaluable():
    """Legacy-/Test-Eintraege ohne diese Felder (wie die bestehenden #829-Tests) sowie die
    #1263-atr_floor_dominant-Live-Ableitung (bewusst n_runs_confirmed=None/expires_after_runs=None,
    #1296 Fix Punkt 3) werden uebersprungen, nicht als Verstoss gewertet."""
    entries = [
        {"strategy": "S", "symbol": "X.ETORO", "binding_cause": "atr_floor_dominant", "action": "none",
         "n_runs_confirmed": None, "expires_after_runs": None},
        {"strategy": "S2", "symbol": "Y.ETORO", "binding_cause": "signal_sparse", "action": "none"},
    ]
    assert inv.check_diagnosis_actionability(entries).passed is True


def test_denylisted_and_deprioritized_actions_are_exempt_from_the_expiry_check():
    entries = [{"strategy": "S", "symbol": "X.ETORO", "binding_cause": "signal_sparse",
                "action": "deprioritized", "n_runs_confirmed": 99, "expires_after_runs": 10}]
    assert inv.check_diagnosis_actionability(entries).passed is True


def test_aggregate_deadlock_and_stale_none_offenders_can_both_be_reported_simultaneously():
    def _entries(strategy, cause, action, n):
        return [{"strategy": strategy, "symbol": f"SYM{i}.ETORO", "binding_cause": cause,
                  "action": action} for i in range(n)]

    entries = _entries("AdxAtrMomentumStrategy", "signal_absent", "none", 60)
    entries.append({"strategy": "SqueezeBreakout", "symbol": "ASML.ETORO",
                     "binding_cause": "signal_sparse", "action": "none",
                     "n_runs_confirmed": 10, "expires_after_runs": 10})
    result = inv.check_diagnosis_actionability(entries)
    assert result.passed is False
    assert "aggregate_none_deadlock" in result.actual
    assert "stale_none_past_expiry" in result.actual


def test_reference_829_deadlock_tests_remain_bit_identical_in_passed_and_detail():
    """Regressionsschutz gegen die bestehenden #829-Tests -- dieselben Assertions wie
    test_issue_829_signal_absent_evidence_deadlock.py::test_check_fails_against_the_reference_
    deadlock, hier erneut gegen die verschaerfte Funktion."""
    entries = ([{"strategy": "AdxAtrMomentumStrategy", "binding_cause": "signal_absent",
                 "action": "none"} for _ in range(69)]
               + [{"strategy": "TrendPullbackStrategy", "binding_cause": "signal_absent",
                   "action": "none"} for _ in range(69)])
    result = inv.check_diagnosis_actionability(entries)
    assert result.passed is False
    assert "AdxAtrMomentumStrategy/signal_absent" in result.detail
    assert "TrendPullbackStrategy/signal_absent" in result.detail


# ---------------------------------------------------------------------------------------------
# n_evaluable threading in run_optimization.py (Fix Punkt 1's prerequisite)
# ---------------------------------------------------------------------------------------------

def test_run_optimization_threads_n_evaluable_into_recommend_diagnosis_action():
    import inspect
    from automation.optimizer import run_optimization as ro
    source = inspect.getsource(ro)
    assert "n_evaluable=diagnosis.get(\"n_evaluable\")" in source


# ---------------------------------------------------------------------------------------------
# config schema — production config surfaces no residual atr_floor_dominant/gate_unreachable
# ---------------------------------------------------------------------------------------------

def test_production_optimizer_config_still_has_max_consecutive_structural_runs():
    from pathlib import Path
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert "max_consecutive_structural_runs" in cfg
