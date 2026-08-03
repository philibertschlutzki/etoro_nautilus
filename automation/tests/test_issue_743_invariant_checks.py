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


# ─── check_config_key_registry: #765 Schema-Drift-Erweiterung ─────────────────────────────────
def test_config_key_registry_fail_stale_all_membership_claim():
    """#765-Root-Cause-Signatur: min_sortino/oos_min_sortino_note-Klasse — der Schema-Text
    behauptet weiterhin 'in eligible_requires_all (HART)', obwohl der Key laengst NICHT MEHR
    gelistet ist."""
    result = inv.check_config_key_registry({
        "eligible_requires_all": ["min_trades", "max_drawdown"],
        "eligible_requires_any": [],
        "_schema": {"fields": {
            "min_sortino": "Mindest-Sortino. SEIT #593 in eligible_requires_all (HART).",
        }},
    })
    assert result.passed is False
    assert any("min_sortino" in p and "eligible_requires_all" in p for p in result.actual)


def test_config_key_registry_fail_stale_any_membership_claim():
    result = inv.check_config_key_registry({
        "eligible_requires_all": [],
        "eligible_requires_any": ["min_win_rate"],
        "_schema": {"fields": {
            "min_profit_factor": "Weiches Filter, in eligible_requires_any (aktiver OR-Arm).",
        }},
    })
    assert result.passed is False
    assert any("min_profit_factor" in p and "eligible_requires_any" in p for p in result.actual)


def test_config_key_registry_pass_accurate_membership_claim():
    """Ein Schema-Text, dessen Marker-Behauptung tatsaechlich zutrifft, ist KEIN Fund."""
    result = inv.check_config_key_registry({
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": [],
        "_schema": {"fields": {
            "min_trades": "Mindestanzahl Trades, in eligible_requires_all (HART).",
        }},
    })
    assert result.passed is True
    assert result.actual == []


def test_config_key_registry_pass_oos_prefixed_claim_matches_unprefixed_list_entry():
    """Marker-Text auf einem oos_-praefigierten Key ist erfuellt, wenn die UNPRAEFIGIERTE Form in
    der Liste steht (dieselbe Normalisierung wie der #649-Handler-Check)."""
    result = inv.check_config_key_registry({
        "eligible_requires_all": ["min_psr"],
        "eligible_requires_any": [],
        "_schema": {"fields": {
            "oos_min_psr": "Mindest-PSR, in eligible_requires_all (HART).",
        }},
    })
    assert result.passed is True


def test_config_key_registry_note_suffix_resolves_to_base_key():
    """Ein '<key>_note'-Begleitfeld wird auf denselben Gate-Key wie sein Stammfeld gepruft (das
    #765-Reproduktionsmuster: oos_min_sortino_note behauptet ueber oos_min_sortino)."""
    result = inv.check_config_key_registry({
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": [],
        "_schema": {"fields": {
            "oos_min_sortino_note": "SEIT #593 wirkt oos_min_sortino in eligible_requires_all (HART).",
        }},
    })
    assert result.passed is False
    assert any("oos_min_sortino_note" in p for p in result.actual)


def test_config_key_registry_pass_negated_or_conditional_mentions_are_not_flagged():
    """Texte, die eligible_requires_all/_any NUR in negierten/bedingten Kontexten erwaehnen (ohne
    den exakten Marker), duerfen NICHT als Falsch-Positive markiert werden — das reale Muster von
    min_expectancy/oos_min_profitable_folds_frac in der Produktions-Config."""
    result = inv.check_config_key_registry({
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": [],
        "_schema": {"fields": {
            "min_expectancy": "SEIT #697 NICHT MEHR in eligible_requires_all gelistet.",
            "oos_min_evaluable_folds": "Muss in eligible_requires_all gelistet sein, um zu greifen.",
        }},
    })
    assert result.passed is True
    assert result.actual == []


def test_config_key_registry_pass_against_real_tournament_config():
    """Nachweis: die tatsaechliche automation/config/tournament.json ist nach dem #765-Fix gruen."""
    import json
    from pathlib import Path
    tcfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    result = inv.check_config_key_registry(tcfg)
    assert result.passed is True, result.detail


# ─── check_rejection_chain_completeness (#654/#671-Regressionswächter) ────────────────────────

def test_rejection_chain_pass_ready_for_pr_with_complete_decision_chain():
    chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "confirm_or_selection", "passed": True, "detail": None},
        {"stage": "holdout", "passed": True, "detail": None},
    ]
    result = inv.check_rejection_chain_completeness(
        {"status": "READY_FOR_PR", "holdout_reject_detail": None}, decision_chain=chain)
    assert result.passed is True


def test_rejection_chain_fails_ready_for_pr_with_missing_stage():
    """Issue #785 — Root-Cause-Regressionstest: status==READY_FOR_PR liess den Check VORHER
    unbedingt durchgehen; genau hier fehlte allen 37 #682-Records die confirm_or_selection-Stufe."""
    chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "holdout", "passed": True, "detail": None},
    ]  # confirm_or_selection fehlt
    result = inv.check_rejection_chain_completeness(
        {"status": "READY_FOR_PR", "holdout_reject_detail": None}, decision_chain=chain)
    assert result.passed is False
    assert "confirm_or_selection" in result.actual["missing_stages"]


def test_rejection_chain_fails_ready_for_pr_with_no_decision_chain_at_all():
    """Fehlt decision_chain komplett (Legacy-Aufrufer ohne Report-Kontext) ⇒ FAIL, kein stiller
    Freifahrtschein fuer promote=True."""
    result = inv.check_rejection_chain_completeness({"status": "READY_FOR_PR", "holdout_reject_detail": None})
    assert result.passed is False


def test_rejection_chain_pass_promote_global_default_with_complete_chain():
    """Issue #783/#785 — die #682-Default-Route traegt jetzt die Stufen is_gate,
    confirm_or_selection(passed=True, detail='GLOBAL_DEFAULT'), holdout(passed=True)."""
    chain = [
        {"stage": "is_gate", "passed": True, "detail": None},
        {"stage": "confirm_or_selection", "passed": True, "detail": "GLOBAL_DEFAULT"},
        {"stage": "holdout", "passed": True, "detail": None},
    ]
    result = inv.check_rejection_chain_completeness(
        {"status": "PROMOTE_GLOBAL_DEFAULT", "holdout_reject_detail": None}, decision_chain=chain)
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
    # Issue #849 — "severity" ist seit Kohorte D ein fuenftes Feld (Default "medium"), damit
    # Berichtssektion 5 nach Dringlichkeit statt Auftrittsreihenfolge sortieren kann.
    assert set(d.keys()) == {"name", "passed", "expected", "actual", "detail", "severity"}
