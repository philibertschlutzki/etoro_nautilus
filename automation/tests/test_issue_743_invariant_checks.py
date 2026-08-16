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
    # Issue #927 — gate_distance_penalty muss mitvariieren (sonst als inert geflaggt, es ist NICHT
    # in _CONFIGURED_INACTIVE_REWARD_TERMS); time_box_penalty bleibt bei 0.0 (sein konfiguriertes
    # Gewicht ist 0.0) und wird trotzdem NICHT als inert gelistet, weil es konfiguriert-inaktiv ist.
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.3 * i, "dd_penalty": 0.25 * i,
                "param_pen": 0.2 * i, "turnover": 0.3 * i, "fold_dispersion": 0.25 * i,
                "tie_breaker": 0.2 * i, "gate_distance_penalty": 0.15 * i, "time_box_penalty": 0.0})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    result = inv.check_reward_term_variance(trials)
    assert result.passed is True
    assert result.actual == []


def test_reward_term_variance_fail_inert_term():
    """Ein Term, der über die gesamte Study konstant bleibt, muss als inert gelistet werden.

    Issue #977 — ``dd_penalty`` ist seit diesem Fix DOKUMENTIERT inert (penalty_dd_weight=0.0,
    invariants._CONFIGURED_INACTIVE_REWARD_TERMS) und daher von dieser Prüfung ausgenommen (analog
    ``tie_breaker``/``time_box_penalty``, #927) — ``param_pen`` ist hier der Test-Kandidat für einen
    UNDOKUMENTIERT inerten Term."""
    trials = [
        _trial({"branch": "eligible", "base": b, "divergence": 0.1 * i, "dd_penalty": 0.02 * i,
                "param_pen": 0.0, "turnover": 0.03 * i, "fold_dispersion": 0.01 * i,
                "tie_breaker": 0.001 * i})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    result = inv.check_reward_term_variance(trials)
    assert result.passed is False
    assert "param_pen" in result.actual
    assert "dd_penalty" not in result.actual


def test_reward_term_variance_pass_when_insufficient_data():
    result = inv.check_reward_term_variance([_trial(None)])
    assert result.passed is True
    assert result.actual == []


def test_invariant_result_to_dict_has_expected_keys():
    result = inv.check_sr0_coherence({})
    d = result.to_dict()
    # Issue #849 — "severity" ist seit Kohorte D ein fuenftes Feld (Default "medium"), damit
    # Berichtssektion 5 nach Dringlichkeit statt Auftrittsreihenfolge sortieren kann. "check" ist
    # ein Uebergangs-Alias auf denselben Wert wie "name" (summary_de.py las bislang "check", das
    # to_dict() nie schrieb -- 519x "**None**" im Bericht), bis alle Konsumenten auf "name"
    # migriert sind.
    assert set(d.keys()) == {"name", "check", "passed", "expected", "actual", "detail", "severity"}
    assert d["name"] == d["check"]


# ─── check_censored_statistic_in_decision (#1004, Katalog #858) ───────────────────────────────

def test_censored_statistic_pass_when_not_promoted():
    result = inv.check_censored_statistic_in_decision(
        {"status": "REJECTED_ON_HOLDOUT"}, {"oos_profit_factor_censored": True})
    assert result.passed is True


def test_censored_statistic_pass_when_promoted_and_clean():
    result = inv.check_censored_statistic_in_decision(
        {"status": "READY_FOR_PR"}, {"oos_profit_factor_censored": False})
    assert result.passed is True


def test_censored_statistic_pass_when_promoted_and_no_flags_present():
    result = inv.check_censored_statistic_in_decision({"status": "READY_FOR_PR"}, {})
    assert result.passed is True


def test_censored_statistic_fail_when_promoted_on_censored_profit_factor():
    result = inv.check_censored_statistic_in_decision(
        {"status": "READY_FOR_PR"}, {"oos_profit_factor_censored": True})
    assert result.passed is False
    assert result.severity == "blocking"
    assert "oos_profit_factor_censored" in result.actual["censored_fields"]


def test_censored_statistic_fail_covers_promote_global_default_route_too():
    result = inv.check_censored_statistic_in_decision(
        {"status": "PROMOTE_GLOBAL_DEFAULT"}, {"oos_profit_factor_censored": True})
    assert result.passed is False


def test_censored_statistic_generic_over_any_censored_suffix_field():
    """Zukunftssicher: JEDES ``*_censored``-Flag (nicht nur profit_factor) blockiert eine
    Promotion, ohne dass diese Funktion angepasst werden muss."""
    result = inv.check_censored_statistic_in_decision(
        {"status": "READY_FOR_PR"}, {"some_future_statistic_censored": True})
    assert result.passed is False
    assert result.actual["censored_fields"] == ["some_future_statistic_censored"]


# ── Issue #1038 (Katalog #866): check_worker_utilisation_plausible ──────────────────────────────
def test_worker_utilisation_plausible_passes_at_or_below_one():
    assert inv.check_worker_utilisation_plausible(1.0).passed is True
    assert inv.check_worker_utilisation_plausible(0.87).passed is True


def test_worker_utilisation_plausible_fails_above_one():
    """Beobachtete Werte aus Katalog #866: 151,8 %/246,5 %/332,9 % ueber drei Laeufe."""
    result = inv.check_worker_utilisation_plausible(1.518)
    assert result.passed is False
    assert result.actual == 1.518


def test_worker_utilisation_plausible_none_is_not_applicable():
    result = inv.check_worker_utilisation_plausible(None)
    assert result.passed is True
    assert result.actual is None


# ── Issue #1023 (Katalog #866): check_cohort_clock_drift (bis #1106 check_report_cohort_coherence)
# Update #940/#1106 (Katalog #960): die Zeit-basierten Klauseln leben seit #1106 in
# ``check_cohort_clock_drift`` (severity ``low``, reine Diagnose) — ``check_report_cohort_coherence``
# urteilt seither ueber Kohorten-IDENTITAET, siehe test_issue_940_1106_identity_cohort.py.
def test_cohort_clock_drift_passes_when_all_studies_within_wallclock():
    records = [
        {"study_started_at_utc": "2026-08-12T04:19:20.000+00:00"},
        {"study_started_at_utc": "2026-08-12T04:20:05.000+00:00"},
    ]
    result = inv.check_cohort_clock_drift(records, wallclock_s=2880.0)
    assert result.passed is True


def test_cohort_clock_drift_fails_when_a_study_predates_the_run():
    """Beobachtete Symptomatik: 98 von 112 Studies eines Ein-Symbol-Laufs trugen
    study_started_at_utc 9-12h vor dem Laufbeginn."""
    records = [
        {"study_started_at_utc": "2026-08-11T16:19:34.000+00:00"},
        {"study_started_at_utc": "2026-08-12T04:19:21.000+00:00"},
    ]
    result = inv.check_cohort_clock_drift(records, wallclock_s=2880.0)
    assert result.passed is False
    assert result.severity == "low"


def test_cohort_clock_drift_not_applicable_without_wallclock_or_timestamps():
    assert inv.check_cohort_clock_drift([], wallclock_s=2880.0).passed is True
    assert inv.check_cohort_clock_drift(
        [{"study_started_at_utc": "2026-08-12T04:19:20.000+00:00"}], wallclock_s=None,
    ).passed is True
