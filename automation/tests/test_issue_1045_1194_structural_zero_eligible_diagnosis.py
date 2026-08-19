"""Issue #1045/#1194 (P2) — Diagnose ohne Rückschrieb: 123 Trials ohne Konsequenz.

Symptom. AdxAtrMomentum/TSLA.ETORO: ``stop_reason = STRUCTURAL_ZERO_ELIGIBLE``, ``n_eligible = 0``
bei 123 Trials, ``is_rejection_detail_counts = {REJECT_OOS_MIN_PSR: 123}`` — 123 von 123 am selben
Gate. Trotzdem: ``diagnosed_pairs: []``.

Root-Cause. Der #679/#829/#830/#831-Rückschriebpfad (``run_optimization.py`` →
``sweep_diagnostics.record_diagnosed_pair``) schreibt AUSSCHLIESSLICH in den automatisch gepflegten
Cache, dessen Schreibpfad seit #1090 global unterdrückt ist
(``optimizer.json['diagnostic_writeback_enabled'] = false``) — eine bewusste Sicherheitsmassnahme
gegen Mehrprozess-Korruption (#1086), die aber auch die REPORT-SICHTBARKEIT der Diagnose selbst
mit abschaltete.

Fix. ``sweep_diagnostics.diagnose_structural_zero_eligible_gate`` klassifiziert das DOMINANTE Gate
(Frequenz vs. Qualität) einer STRUCTURAL_ZERO_ELIGIBLE-Study rein aus ``is_rejection_detail_counts``
(bereits in jedem Study-Record vorhanden). ``report._diagnosed_pairs_section`` leitet daraus LIVE
(cache-unabhängig) einen Vorschlag ab — GESCHRIEBEN (report-sichtbar), nicht ANGEWENDET (der
tatsächliche Denylist-/Bounds-Rückschrieb bleibt weiterhin am gated Cache-Pfad).
"""
from automation.optimizer import invariants as inv, report
from automation.optimizer.sweep_diagnostics import (
    classify_rejection_detail_gate_type, diagnose_structural_zero_eligible_gate,
)


# ── sweep_diagnostics.classify_rejection_detail_gate_type ───────────────────────────────────────
def test_reject_oos_min_trades_is_frequency():
    assert classify_rejection_detail_gate_type("REJECT_OOS_MIN_TRADES") == "frequency"


def test_reject_oos_min_psr_is_quality():
    """Issue-Text explizit: 'oos_min_psr ist Qualität ⇒ Budget-Deprioritisierung'."""
    assert classify_rejection_detail_gate_type("REJECT_OOS_MIN_PSR") == "quality"


def test_reject_oos_max_drawdown_is_quality():
    assert classify_rejection_detail_gate_type("REJECT_OOS_MAX_DRAWDOWN") == "quality"


def test_unknown_detail_is_unclassified():
    assert classify_rejection_detail_gate_type("REJECT_OOS_MICRO_SIZING") is None
    assert classify_rejection_detail_gate_type(None) is None


# ── sweep_diagnostics.diagnose_structural_zero_eligible_gate ────────────────────────────────────
def test_reference_symptom_123_of_123_reject_oos_min_psr():
    """Das woertliche #1194-Referenzsymptom: 123/123 Trials REJECT_OOS_MIN_PSR."""
    diagnosis = diagnose_structural_zero_eligible_gate({"REJECT_OOS_MIN_PSR": 123})
    assert diagnosis["dominant_rejection_detail"] == "REJECT_OOS_MIN_PSR"
    assert diagnosis["dominant_fraction"] == 1.0
    assert diagnosis["gate_type"] == "quality"
    assert diagnosis["binding_cause"] == "signal_quality"
    assert diagnosis["proposed_action"] == "budget_deprioritization"


def test_homogeneous_frequency_gate_proposes_search_space_override():
    diagnosis = diagnose_structural_zero_eligible_gate({"REJECT_OOS_MIN_TRADES": 40})
    assert diagnosis["gate_type"] == "frequency"
    assert diagnosis["binding_cause"] == "signal_sparse"
    assert diagnosis["proposed_action"] == "search_space_override"


def test_non_homogeneous_cohort_stays_none():
    """60/40-Split -- kein dominantes Gate erreicht die 100%-Schwelle -- kein Rateversuch."""
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_MIN_PSR": 60, "REJECT_OOS_MAX_DRAWDOWN": 40})
    assert diagnosis["binding_cause"] == "none"
    assert diagnosis["proposed_action"] == "none"
    assert diagnosis["dominant_rejection_detail"] == "REJECT_OOS_MIN_PSR"  # Telemetrie bleibt.


def test_empty_counts_stays_none():
    diagnosis = diagnose_structural_zero_eligible_gate({})
    assert diagnosis["binding_cause"] == "none"
    assert diagnosis["dominant_rejection_detail"] is None


def test_unclassifiable_dominant_gate_stays_none_despite_full_homogeneity():
    diagnosis = diagnose_structural_zero_eligible_gate({"REJECT_OOS_MICRO_SIZING": 20})
    assert diagnosis["dominant_fraction"] == 1.0
    assert diagnosis["gate_type"] is None
    assert diagnosis["binding_cause"] == "none"


def test_configurable_homogeneity_threshold_allows_near_dominant_gates():
    diagnosis = diagnose_structural_zero_eligible_gate(
        {"REJECT_OOS_MIN_PSR": 95, "REJECT_OOS_MAX_DRAWDOWN": 5}, homogeneity_threshold=0.9)
    assert diagnosis["binding_cause"] == "signal_quality"


# ── report._structural_zero_eligible_diagnosed_pairs / _diagnosed_pairs_section ─────────────────
def _study_record(strategy, symbol, *, stop_reason, is_rejection_detail_counts,
                  budget_executed_fraction=1.0):
    return {"strategy": strategy, "symbol": symbol, "stop_reason": stop_reason,
            "is_rejection_detail_counts": is_rejection_detail_counts,
            "budget_executed_fraction": budget_executed_fraction}


def test_ak1_diagnosed_pairs_contains_the_reference_pair_with_signal_quality(monkeypatch, tmp_path):
    """Akzeptanzkriterium 1: diagnosed_pairs enthält AdxAtrMomentumStrategy/TSLA.ETORO mit
    binding_cause == 'signal_quality', OHNE dass irgendein Cache-Schreibpfad beteiligt ist (der
    Cache bleibt fuer diesen Test schlicht leer/nicht vorhanden -- diagnostic_writeback_enabled
    spielt fuer die LIVE-Ableitung keine Rolle mehr)."""
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)  # kein realer Cache unter tmp_path.
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 123}),
    ]
    section = report._diagnosed_pairs_section(studies_out)
    entry = next(e for e in section if e["strategy"] == "AdxAtrMomentumStrategy"
                and e["symbol"] == "TSLA.ETORO")
    assert entry["binding_cause"] == "signal_quality"
    assert entry["action"] == "budget_deprioritization"
    assert entry["source"] == "live_derivation"


def test_non_zero_eligible_studies_are_not_diagnosed():
    studies_out = [
        _study_record("A", "X.ETORO", stop_reason="BUDGET_EXHAUSTED",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 40}),
    ]
    assert report._structural_zero_eligible_diagnosed_pairs(studies_out) == []


def test_non_homogeneous_structural_zero_eligible_study_yields_no_entry():
    studies_out = [
        _study_record("A", "X.ETORO", stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 50,
                                                  "REJECT_OOS_MAX_DRAWDOWN": 50}),
    ]
    assert report._structural_zero_eligible_diagnosed_pairs(studies_out) == []


def test_cache_entry_overrides_live_entry_for_the_same_pair(monkeypatch, tmp_path):
    from automation.optimizer import manifest
    monkeypatch.setattr(manifest, "WORK", tmp_path)
    cache_dir = tmp_path / "diagnostics"
    cache_dir.mkdir(parents=True)
    (cache_dir / "diagnosed_pairs_cache.json").write_text(
        '{"pairs": [{"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO", '
        '"action": "denylist", "binding_cause": "signal_quality", "n_runs_confirmed": 3, '
        '"expires_after_runs": 3, "budget_executed_fraction": 0.95}]}', "utf-8")
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 123}),
    ]
    section = report._diagnosed_pairs_section(studies_out)
    assert len(section) == 1
    assert section[0]["action"] == "denylist"  # der Cache-Eintrag (mehr Historie) gewinnt.
    assert section[0]["source"] == "diagnosis_cache"
    assert section[0]["n_runs_confirmed"] == 3


# ── invariants.check_structural_zero_eligible_has_diagnosis ─────────────────────────────────────
def test_invariant_fails_matching_1194_reference_symptom():
    """Akzeptanzkriterium 2, im Negativfall reproduziert: leere diagnosed_pairs trotz
    STRUCTURAL_ZERO_ELIGIBLE."""
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 123}),
    ]
    result = inv.check_structural_zero_eligible_has_diagnosis(studies_out, [])
    assert result.passed is False
    assert result.severity == "medium"
    assert "AdxAtrMomentumStrategy/TSLA.ETORO" in result.actual["missing_diagnosis_for"]


def test_invariant_passes_when_diagnosed_pairs_covers_the_study():
    studies_out = [
        _study_record("AdxAtrMomentumStrategy", "TSLA.ETORO",
                      stop_reason="STRUCTURAL_ZERO_ELIGIBLE",
                      is_rejection_detail_counts={"REJECT_OOS_MIN_PSR": 123}),
    ]
    diagnosed_pairs = [{"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO"}]
    result = inv.check_structural_zero_eligible_has_diagnosis(studies_out, diagnosed_pairs)
    assert result.passed is True


def test_invariant_passes_trivially_without_any_structural_zero_eligible_study():
    result = inv.check_structural_zero_eligible_has_diagnosis(
        [_study_record("A", "X.ETORO", stop_reason="BUDGET_EXHAUSTED",
                       is_rejection_detail_counts={})], [])
    assert result.passed is True
