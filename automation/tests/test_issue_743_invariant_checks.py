"""Issue #743 — automatisierte mathematische/Konfigurations-Invarianz-Checks.

Jede Prüfung in ``automation.optimizer.invariants`` ist eine reine Funktion über synthetische
``user_attrs``-artige Dicts/Listen — dieser Testfile pinnt PASS UND FAIL für alle 5 Prüfungen.
"""
from automation.optimizer import invariants as inv


# ─── check_sr0_coherence (#651-Regressionswächter) ─────────────────────────────────────────────

def test_sr0_coherence_pass_both_present():
    result = inv.check_sr0_coherence({
        "deflated_sr0": 0.12, "deflated_dsr": 0.97, "deflation_dsr_z": 2.1,
    })
    assert result.passed is True


def test_sr0_coherence_pass_both_absent():
    result = inv.check_sr0_coherence({})
    assert result.passed is True


def test_sr0_coherence_fail_dsr_without_sr0():
    """#651-Root-Cause-Signatur: DSR/dsr_z gesetzt, aber KEIN begleitendes SR0."""
    result = inv.check_sr0_coherence({"deflated_dsr": 0.97, "deflation_dsr_z": 2.1})
    assert result.passed is False
    assert result.name == "check_sr0_coherence"


def test_sr0_coherence_fail_sr0_without_dsr_signal():
    result = inv.check_sr0_coherence({"deflated_sr0": 0.12})
    assert result.passed is False


# ─── check_n_family_consistency (#652/#670-Regressionswächter) ────────────────────────────────

def test_n_family_consistency_pass_matches_formula():
    result = inv.check_n_family_consistency({
        "deflation_n_eligible": 40, "deflation_n_family_effective": 65,
        "deflation_n_effective": 65,
    })
    assert result.passed is True
    assert result.expected == 65


def test_n_family_consistency_pass_when_not_applicable():
    result = inv.check_n_family_consistency({})
    assert result.passed is True


def test_n_family_consistency_fail_wrong_max():
    """#670-Fehlerklasse: Entscheidung nutzte eine andere N-Quelle als die Telemetrie ausweist."""
    result = inv.check_n_family_consistency({
        "deflation_n_eligible": 40, "deflation_n_family_effective": 65,
        "deflation_n_effective": 40,  # sollte 65 sein (max(40, 65))
    })
    assert result.passed is False
    assert result.expected == 65
    assert result.actual == 40


# ─── check_config_key_registry (#649-Regressionswächter) ──────────────────────────────────────

def test_config_key_registry_pass_known_gates():
    result = inv.check_config_key_registry({
        "eligible_requires_all": ["min_trades", "max_drawdown"],
        "eligible_requires_any": ["oos_min_psr", "min_win_rate"],
    })
    assert result.passed is True
    assert result.actual == []


def test_config_key_registry_fail_unknown_gate():
    """#649-Root-Cause-Signatur: ein referenzierter Gate-Key ohne condition_map-Handler."""
    result = inv.check_config_key_registry({
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": ["totally_made_up_gate_key"],
    })
    assert result.passed is False
    assert "totally_made_up_gate_key" in result.actual


# ─── check_rejection_chain_completeness (#654/#671-Regressionswächter) ────────────────────────

def test_rejection_chain_pass_ready_for_pr():
    result = inv.check_rejection_chain_completeness({"status": "READY_FOR_PR", "holdout_reject_detail": None})
    assert result.passed is True


def test_rejection_chain_pass_rejected_with_detail():
    result = inv.check_rejection_chain_completeness({
        "status": "REJECTED_ON_HOLDOUT", "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
    })
    assert result.passed is True


def test_rejection_chain_fail_rejected_without_detail():
    """Ein Proposal darf NIE abgelehnt sein, ohne eine konkrete Ursache zu tragen."""
    result = inv.check_rejection_chain_completeness({
        "status": "REJECTED_ON_HOLDOUT", "holdout_reject_detail": None,
    })
    assert result.passed is False


# ─── check_reward_term_variance (Verallgemeinerung von REWARD_TERM_INERT, #621) ────────────────

def _trial(reward_terms=None, oos_evaluated=True):
    return {"oos_evaluated": oos_evaluated, "reward_terms": reward_terms}


def test_reward_term_variance_pass_all_terms_vary():
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.3 * i, "dd_penalty": 0.25 * i,
                "param_pen": 0.2 * i, "turnover": 0.3 * i, "fold_dispersion": 0.25 * i,
                "tie_breaker": 0.2 * i})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    result = inv.check_reward_term_variance(trials)
    assert result.passed is True
    assert result.actual == []


def test_reward_term_variance_fail_inert_term():
    """Ein Term, der über die gesamte Study konstant bleibt, muss als inert gelistet werden."""
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.1 * i, "dd_penalty": 0.0,
                "param_pen": 0.02 * i, "turnover": 0.03 * i, "fold_dispersion": 0.01 * i,
                "tie_breaker": 0.001 * i})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    result = inv.check_reward_term_variance(trials)
    assert result.passed is False
    assert "dd_penalty" in result.actual


def test_reward_term_variance_pass_when_insufficient_data():
    result = inv.check_reward_term_variance([_trial(None)])
    assert result.passed is True
    assert result.actual == []


def test_invariant_result_to_dict_has_expected_keys():
    result = inv.check_sr0_coherence({})
    d = result.to_dict()
    assert set(d.keys()) == {"name", "passed", "expected", "actual", "detail"}
