"""Issue #957 (Katalog D, P0, Pitfall #292-Durchsetzung) — ``binding_cause='signal_quality'`` (und
der daraus abgeleitete Denylist-/Deprioritized-Rückschrieb) ist keine zulässige Aussage, solange
eine blockierende Invariante der Simulationsschicht (z. B. #955 Zeitbox, #956 Kostenmodell) FAILt.

Root-Cause: #911 setzte den Rückschrieb UNBEDINGT auf 'quarantined_pending_simulation_review' aus —
sicher, aber auch nach einem verifiziert sauberen Lauf nie wieder ein echter 'denylist'-Rückschrieb.
#957 generalisiert das über ``blocking_invariants_failing``: ``None`` (Default) bleibt bit-identisch
zu #911; ``True`` schreibt ``binding_cause`` auf ``'simulation_invalid'`` um und unterdrückt jeden
Rückschrieb; ``False`` (verifiziert sauber) hebt die #911-Aussetzung auf.
"""
from automation.optimizer.sweep_diagnostics import recommend_diagnosis_action


def _diag(cause="signal_quality"):
    return {"binding_cause": cause, "median_oos_trades": 214, "median_is_trades": None}


def test_default_none_is_bit_identical_to_pre_957_behavior():
    """Kein Aufrufer, der blocking_invariants_failing nicht setzt, sieht eine Verhaltensänderung —
    dieselbe Erwartung wie test_issue_830's test_signal_quality_with_two_confirmations_and_full_budget_escalates_to_quarantine."""
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=2, budget_executed_fraction=1.0, simulation_semantics_version=2,
    )
    assert rec["action"] == "quarantined_pending_simulation_review"
    assert rec["binding_cause"] == "signal_quality"
    assert "writeback_suppressed_reason" not in rec


def test_blocking_invariant_failing_relabels_cause_and_suppresses_any_writeback():
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=2, budget_executed_fraction=1.0, simulation_semantics_version=2,
        blocking_invariants_failing=True,
    )
    assert rec["binding_cause"] == "simulation_invalid"
    assert rec["action"] == "none"
    assert rec["writeback_suppressed_reason"] == "blocking_invariant"


def test_blocking_invariant_failing_suppresses_writeback_even_with_thin_evidence():
    """Auch OHNE ausreichende Evidenz (die sonst 'none' ergäbe) muss die Ursache klar als
    'simulation_invalid' erkennbar sein, nicht ununterscheidbar von 'keine Evidenz'."""
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=0, blocking_invariants_failing=True,
    )
    assert rec["binding_cause"] == "simulation_invalid"
    assert rec["action"] == "none"


def test_blocking_invariants_verified_clean_lifts_the_911_suspension():
    """Explizit verifiziert sauber (False, nicht nur 'unbekannt') UND ausreichende Evidenz ⇒ der
    volle 'denylist'-Rückschrieb greift wieder wie vor #911."""
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=2, budget_executed_fraction=1.0,
        blocking_invariants_failing=False,
    )
    assert rec["action"] == "denylist"
    assert rec["binding_cause"] == "signal_quality"


def test_blocking_invariants_verified_clean_but_insufficient_evidence_still_yields_none_or_deprioritized():
    """False allein ist keine Lizenz zum Denylisten — die bestehende Evidenzschwelle bleibt in Kraft."""
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=1, budget_executed_fraction=1.0,
        blocking_invariants_failing=False,
    )
    assert rec["action"] == "deprioritized"


def test_blocking_invariants_failing_only_applies_to_signal_quality_cause():
    """Andere binding_cause-Werte (z. B. 'signal_absent') sind von #957 unberührt — die
    Umschreibung greift ausschliesslich für 'signal_quality'."""
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(cause="signal_absent"),
        n_runs_confirmed=2, budget_executed_fraction=1.0, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
        blocking_invariants_failing=True,
    )
    assert rec["binding_cause"] == "signal_absent"
    assert rec["action"] == "denylist"
