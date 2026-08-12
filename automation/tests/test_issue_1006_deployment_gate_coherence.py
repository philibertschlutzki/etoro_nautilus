"""Issue #1006 (Katalog #858) — "Deploybar" behauptete bislang eine Eigenschaft, die
``deployment_gate.evaluate_deployment_eligibility`` NIE geprüft hatte. ``report._study_record``
ruft seit diesem Fix dieselbe Funktion, die auch Phase 5 aufruft, für jeden Promotionskandidaten auf
und persistiert das Ergebnis als ``deployment_decision``; ``invariants.check_promotion_deployment_
coherence`` macht eine Diskrepanz sichtbar (severity='high') statt sie implizit unter demselben
Status verschwinden zu lassen.
"""
from automation.optimizer import deployment_gate
from automation.optimizer import invariants as inv
from automation.optimizer import report

_SNAPSHOT = "deadbeef" * 8
_TOURNAMENT_CFG = {"deflation_confidence": 0.95, "oos_min_psr": 0.75, "pbo_min_configs": 10}


def _promoted_proposal(**holdout_symbol_overrides) -> dict:
    holdout_symbol = {
        "deflated_dsr": 0.97,
        "oos_psr": 0.80,
        "holdout_ci_lower_sortino": 0.05,
        "pbo": 0.30,
        "pbo_n_configs": 40,
        "blocking_invariant_names": [],
    }
    holdout_symbol.update(holdout_symbol_overrides)
    return {
        "strategy": "S",
        "symbol": "A.ETORO",
        "status": "READY_FOR_PR",
        "R_symbol": 1.0,
        "R_global": 0.2,
        "promotion_margin": 0.0,
        "data_snapshot_sha256": _SNAPSHOT,
        "holdout": {"symbol": holdout_symbol},
    }


# ─── report._study_record wiring ────────────────────────────────────────────────────────────────

def test_study_record_computes_admitted_deployment_decision_for_fully_passing_candidate(monkeypatch):
    monkeypatch.setattr(deployment_gate, "catalog_fingerprint", lambda: _SNAPSHOT)
    proposal = _promoted_proposal()
    record, _checks = report._study_record(proposal, None, _TOURNAMENT_CFG)
    decision = record["deployment_decision"]
    assert decision is not None
    assert decision["admitted"] is True
    assert decision["blocking_clause"] is None


def test_study_record_computes_rejected_deployment_decision_for_dsr_miss(monkeypatch):
    """Der Kern-Fall aus #1006: der Sweep selbst kann einen DSR-Miss ueber
    promotion_correction_mode='dsr_or_robust_pair' ersetzen, deployment_gate NIE — ein solcher
    Kandidat ist READY_FOR_PR, aber deployment_decision.admitted muss False sein."""
    monkeypatch.setattr(deployment_gate, "catalog_fingerprint", lambda: _SNAPSHOT)
    proposal = _promoted_proposal(deflated_dsr=0.10)
    record, checks = report._study_record(proposal, None, _TOURNAMENT_CFG)
    decision = record["deployment_decision"]
    assert decision["admitted"] is False
    assert decision["blocking_clause"] == "dsr"
    coherence = next(c for c in checks if c.name == "check_promotion_deployment_coherence")
    assert coherence.passed is False
    assert coherence.severity == "high"


def test_study_record_deployment_decision_is_none_for_non_candidate(monkeypatch):
    monkeypatch.setattr(deployment_gate, "catalog_fingerprint", lambda: _SNAPSHOT)
    proposal = _promoted_proposal()
    proposal["status"] = "REJECTED_ON_HOLDOUT"
    record, checks = report._study_record(proposal, None, _TOURNAMENT_CFG)
    assert record["deployment_decision"] is None
    coherence = next(c for c in checks if c.name == "check_promotion_deployment_coherence")
    assert coherence.passed is True
    assert coherence.detail == "Nicht anwendbar (status ist keine Promotion)."


def test_study_record_snapshot_drift_blocks_even_when_everything_else_passes(monkeypatch):
    """Regressionsschutz gegen das Gegenteil des Hauptfalls: ein veralteter Datenstand blockiert
    unabhaengig von DSR/PSR/PBO/CI — dieselbe Klausel, die Phase 5 vor einem Live-Deploy auf einem
    inzwischen überholten Snapshot bewahrt."""
    monkeypatch.setattr(deployment_gate, "catalog_fingerprint", lambda: "B" * 64)
    proposal = _promoted_proposal()
    record, _checks = report._study_record(proposal, None, _TOURNAMENT_CFG)
    decision = record["deployment_decision"]
    assert decision["admitted"] is False
    assert decision["blocking_clause"] == "snapshot_drift"


# ─── invariants.check_promotion_deployment_coherence (direct unit tests) ───────────────────────

def test_coherence_pass_when_not_promoted():
    result = inv.check_promotion_deployment_coherence({"status": "REJECTED_ON_HOLDOUT"}, None)
    assert result.passed is True


def test_coherence_pass_when_promoted_and_admitted():
    result = inv.check_promotion_deployment_coherence(
        {"status": "READY_FOR_PR"}, {"admitted": True, "blocking_clause": None})
    assert result.passed is True


def test_coherence_fail_when_promoted_but_not_admitted():
    result = inv.check_promotion_deployment_coherence(
        {"status": "READY_FOR_PR"}, {"admitted": False, "blocking_clause": "dsr"})
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual["blocking_clause"] == "dsr"


def test_coherence_fail_when_promoted_but_no_decision_available():
    """Fail-closed: promote=True OHNE begleitendes DeploymentDecision (deployment_decision=None,
    z. B. eine fehlgeschlagene Bewertung) ist NICHT dasselbe wie 'zulaessig' — kein Bericht darf
    'Deploybar' ohne ein DeploymentDecision-Objekt daneben behaupten (#1006-Akzeptanzkriterium)."""
    result = inv.check_promotion_deployment_coherence({"status": "PROMOTE_GLOBAL_DEFAULT"}, None)
    assert result.passed is False
