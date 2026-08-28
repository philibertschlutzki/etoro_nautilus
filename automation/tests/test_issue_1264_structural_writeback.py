"""Issue #1264 (GH #1134) — STRUCTURAL_ZERO_ELIGIBLE ohne Rückschrieb ist eine Diagnose ohne
Wirkung.

Symptom. 10 Studies mit STRUCTURAL_ZERO_ELIGIBLE/STRUCTURAL_ALL_UNEVALUABLE und keinem
diagnosed_pairs-Eintrag; diagnosed_pairs enthält genau einen Eintrag. Dieselben 10 Studies
verbrennen in jedem Folgelauf dasselbe Budget (Defizite 4–65 Trials, Σ 198).

Root-Cause. #1244 ist nicht wirksam: der Rückschriebpfad (``run_optimization.py``s
STRUCTURAL_ALL_UNEVALUABLE-/ZERO_ELIGIBLE_PLATEAU-Frühstopp-Zweige) hängt am STRENGEN sequentiellen
Frühstopp-Kriterium — eine Study, die ihr volles Budget durchläuft, OHNE dass die Statistik je
feuert, erreicht den Rückschrieb nie, obwohl ``stop_reason`` sie unzweideutig klassifiziert.

Fix.
1. ``run_optimization.py``: unbedingter ``record_diagnosed_pair``-Aufruf direkt nach
   ``compute_budget_execution``, sobald ``stop_reason ∈ {STRUCTURAL_ZERO_ELIGIBLE,
   STRUCTURAL_ALL_UNEVALUABLE}`` — unabhängig davon, ob der Frühstopp-Pfad bereits schrieb
   (``study_fingerprint``-Dedup in ``record_diagnosed_pair`` verhindert Doppelzählung).
2. ``binding_cause='gate_unreachable'`` (neu): ``REJECT_OOS_MIN_ALPHA_TSTAT`` war zuvor
   unklassifiziert (``classify_rejection_detail_gate_type`` → ``None`` → kein Eintrag). Erzeugt
   ``action='none'`` — KEINE Denylist (die Ursache liegt in der Config, nicht im Paar).
3. ``check_structural_zero_eligible_has_diagnosis`` von ``medium`` auf ``high`` gehoben.
"""
import inspect

from automation.optimizer import invariants as inv
from automation.optimizer.sweep_diagnostics import (
    classify_rejection_detail_gate_type, diagnose_structural_zero_eligible_gate,
)


# ---------------------------------------------------------------------------------------------
# sweep_diagnostics.classify_rejection_detail_gate_type — the new 'gate' category
# ---------------------------------------------------------------------------------------------

def test_reject_oos_min_alpha_tstat_is_gate():
    assert classify_rejection_detail_gate_type("REJECT_OOS_MIN_ALPHA_TSTAT") == "gate"


def test_existing_classifications_unaffected():
    assert classify_rejection_detail_gate_type("REJECT_OOS_MIN_TRADES") == "frequency"
    assert classify_rejection_detail_gate_type("REJECT_OOS_MIN_PSR") == "quality"
    assert classify_rejection_detail_gate_type("REJECT_OOS_MAX_DRAWDOWN") == "quality"
    assert classify_rejection_detail_gate_type("REJECT_OOS_SOME_UNKNOWN_CODE") is None


# ---------------------------------------------------------------------------------------------
# sweep_diagnostics.diagnose_structural_zero_eligible_gate — gate_unreachable branch
# ---------------------------------------------------------------------------------------------

def test_reference_symptom_13_alpha_gate_studies_get_gate_unreachable_no_denylist():
    # Akzeptanzkriterium #1264: "Die 13 alpha-gate-blockierten Studies erhalten gate_unreachable
    # und KEINE Denylist." Issue #1296 (GH #1169, Katalog #1272-1297, P1) Fix Punkt 2 —
    # ``proposed_action`` wurde von ``'none'`` (reine Diagnose ohne Konsequenz, Root-Cause des
    # #1296-Katalogsymptoms) auf ``'budget_deprioritization'`` gehoben: die #1264-Aussage "KEINE
    # Denylist" bleibt unveraendert gueltig (Budget-Deprioritisierung ist keine Denylist), nur die
    # vorherige Untaetigkeit wird ersetzt (siehe report._writeback_gate_unreachable_diagnoses).
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_MIN_ALPHA_TSTAT": 123}, stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
        max_is_trades=None, median_is_trades=None)
    assert diagnosis["binding_cause"] == "gate_unreachable"
    assert diagnosis["proposed_action"] == "budget_deprioritization"
    assert diagnosis["proposed_action"] != "denylist"
    assert diagnosis["gate_type"] == "gate"
    assert diagnosis["dominant_rejection_detail"] == "REJECT_OOS_MIN_ALPHA_TSTAT"
    assert diagnosis["dominant_fraction"] == 1.0


def test_mixed_cohort_below_homogeneity_threshold_stays_unclassified():
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_MIN_ALPHA_TSTAT": 80, "REJECT_OOS_MIN_PSR": 20},
        stop_reason="STRUCTURAL_ZERO_ELIGIBLE", max_is_trades=None, median_is_trades=None)
    assert diagnosis["binding_cause"] == "none"


def test_squeeze_breakout_reference_signal_sparse_still_works():
    # Akzeptanzkriterium #1264: "SqueezeBreakout erhält signal_sparse (median_is_trades = 0,
    # max_is_trades = 4, bereits im Event vorhanden)" — der STRUCTURAL_ALL_UNEVALUABLE-Zweig ist
    # unconditional (kein homogeneity-Torwaechter) und war bereits vor diesem Fix korrekt.
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_WINDOW_UNREACHABLE": 178}, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
        max_is_trades=4, median_is_trades=0)
    assert diagnosis["binding_cause"] == "signal_sparse"
    assert diagnosis["gate_type"] == "frequency"


# ---------------------------------------------------------------------------------------------
# invariants.check_structural_zero_eligible_has_diagnosis — severity high
# ---------------------------------------------------------------------------------------------

def test_severity_is_high():
    r = inv.check_structural_zero_eligible_has_diagnosis(
        [{"strategy": "S", "symbol": "X.ETORO", "stop_reason": "STRUCTURAL_ZERO_ELIGIBLE"}], [])
    assert r.passed is False
    assert r.severity == "high"


def test_gate_unreachable_entry_satisfies_the_check():
    studies_out = [{"strategy": "S", "symbol": "X.ETORO", "stop_reason": "STRUCTURAL_ZERO_ELIGIBLE"}]
    diagnosed_pairs = [{"strategy": "S", "symbol": "X.ETORO", "binding_cause": "gate_unreachable",
                        "action": "none"}]
    r = inv.check_structural_zero_eligible_has_diagnosis(studies_out, diagnosed_pairs)
    assert r.passed is True


# ---------------------------------------------------------------------------------------------
# run_optimization.py wiring — unconditional writeback, reachable regardless of early-stop
# ---------------------------------------------------------------------------------------------

def test_unconditional_writeback_is_wired_after_compute_budget_execution():
    from automation.optimizer import run_optimization as ro
    source = inspect.getsource(ro._emit_study_summary)
    assert "budget_execution = compute_budget_execution(" in source
    write_idx = source.index("diagnose_structural_zero_eligible_gate")
    budget_idx = source.index("budget_execution = compute_budget_execution(")
    assert budget_idx < write_idx, (
        "der unbedingte Rueckschrieb muss NACH budget_execution stehen (braucht stop_reason)")
    assert 'budget_execution["stop_reason"] in ("STRUCTURAL_ZERO_ELIGIBLE", ' \
           '"STRUCTURAL_ALL_UNEVALUABLE")' in source
    assert "record_diagnosed_pair(_structural_rec" in source
    # Dedup-Absicherung gegen doppeltes n_runs_confirmed (siehe record_diagnosed_pair-Docstring
    # #1090): der neue Aufruf muss study_fingerprint mitgeben.
    assert '"study_fingerprint": study_fingerprint(' in source


def test_writeback_is_unconditional_not_gated_on_early_stop_flags():
    # Regressionswaechter gegen genau die #1264-Root-Cause: der neue Block darf NICHT hinter
    # ``should_stop``/``floor_plateau_warned``/``zero_eligible_plateau_warned`` haengen (das waere
    # dieselbe Fruehstopp-Abhaengigkeit, die den urspruenglichen Bug verursachte).
    from automation.optimizer import run_optimization as ro
    source = inspect.getsource(ro._emit_study_summary)
    block_start = source.index("Issue #1264 (GH #1134) Fix Punkt 1")
    block_end = source.index("# Issue #930 (Pitfall #303)")
    block = source[block_start:block_end]
    assert "should_stop" not in block
    assert "floor_plateau_warned" not in block
    assert "zero_eligible_plateau_warned" not in block
