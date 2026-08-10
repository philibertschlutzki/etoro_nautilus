"""Issue #949 (Katalog C, P0 HEADLINE, Pitfall #298) — die Reward-Varianz einer Study wurde vom
Failure-Zweig getragen (EQUITY_NONPOSITIVE/SORTINO_GUARD_TRIPPED-Trials, Reward-Betrag bis Faktor
~50 über der Shaping-Term-Skala), nicht von der Qualitätsordnung innerhalb der zulässigen Region.

Root-Cause-Präzisierung ggü. der Erst-Vermutung ("naiver Reward-Sentinel"): ``run_optimization.py``s
``inference_failure_policy='prune'``-Pfad (#864/#918) pruned Trials mit
SORTINO_GUARD_TRIPPED/EQUITY_NONPOSITIVE/etc. bereits korrekt über ``optuna.TrialPruned()`` — der
TPE-Sampler sieht ihren Reward-Wert NIE. Das eigentliche verbleibende Problem: der VOR dem Prune
bereits berechnete ``reward_terms``-Wert bleibt als ``trial.user_attrs``-Artefakt stehen und
kontaminiert REPORT-LEVEL Varianzdiagnosen (``check_reward_term_variance``, jetzt auch
``check_reward_dynamic_range``), die über ALLE Trials (inkl. gepruneter) rechnen.

Fix: ``_feasible_reward_terms``/``reward_std_feasible`` schliessen Trials mit gesetztem
``trial_pruned_inference_codes`` aus; ``check_reward_dynamic_range`` erzwingt die drei #949-Klauseln.
"""
from automation.optimizer.invariants import (
    _feasible_reward_terms, _evaluated_reward_terms, _reward_std_total_and_feasible,
    check_reward_dynamic_range,
)


def _term(base=1.0, divergence=0.0, dd_penalty=0.0, param_pen=0.0, turnover=0.0,
         fold_dispersion=0.0, tie_breaker=0.0, gate_distance_penalty=0.0, time_box_penalty=0.0):
    return {
        "base": base, "divergence": divergence, "dd_penalty": dd_penalty,
        "param_pen": param_pen, "turnover": turnover, "fold_dispersion": fold_dispersion,
        "tie_breaker": tie_breaker, "gate_distance_penalty": gate_distance_penalty,
        "time_box_penalty": time_box_penalty,
    }


def _trial(reward_terms, *, pruned_codes=None, oos_evaluated=True):
    d = {"oos_evaluated": oos_evaluated, "reward_terms": reward_terms}
    if pruned_codes:
        d["trial_pruned_inference_codes"] = pruned_codes
    return d


# ── _feasible_reward_terms excludes pruned trials, _evaluated_reward_terms includes them ─────────
def test_feasible_reward_terms_excludes_pruned_trials():
    trials = [
        _trial(_term(base=1.0)),
        _trial(_term(base=2.0)),
        _trial(_term(base=999.0), pruned_codes=["EQUITY_NONPOSITIVE"]),
    ]
    feasible = _feasible_reward_terms(trials)
    total = _evaluated_reward_terms(trials)
    assert len(feasible) == 2
    assert len(total) == 3
    assert all(t["base"] != 999.0 for t in feasible)


def test_reward_std_total_exceeds_feasible_when_a_phantom_outlier_is_pruned():
    trials = [
        _trial(_term(base=1.0)),
        _trial(_term(base=1.2)),
        _trial(_term(base=0.9)),
        _trial(_term(base=1.1)),
        _trial(_term(base=-54.0), pruned_codes=["EQUITY_NONPOSITIVE"]),
    ]
    std_total, std_feasible, _ = _reward_std_total_and_feasible(trials)
    assert std_total > std_feasible
    assert std_total > 10 * std_feasible  # der Ausreisser dominiert reward_std_total drastisch


# ── check_reward_dynamic_range ────────────────────────────────────────────────────────────────────
def test_passes_when_feasible_region_has_healthy_dynamic_range():
    import random
    rng = random.Random(1)
    trials = [_trial(_term(base=rng.uniform(0.5, 2.0), param_pen=rng.uniform(0.01, 0.05)))
             for _ in range(20)]
    result = check_reward_dynamic_range(trials)
    assert result.passed is True


def test_fails_when_pruned_outlier_makes_total_dominate_feasible():
    """Regressionsschutz gegen den Katalog-C-Kernbefund: ein einziger gepunter EQUITY_NONPOSITIVE-
    Trial mit einem Reward-Betrag weit ausserhalb der Shaping-Skala darf die
    total/feasible-Ratio-Klausel zum FAIL bringen."""
    import random
    rng = random.Random(2)
    trials = [_trial(_term(base=rng.uniform(0.5, 2.0), param_pen=rng.uniform(0.01, 0.05)))
             for _ in range(20)]
    # EIN gepunter Trial mit extremem Reward -> reward_std_total >> reward_std_feasible.
    trials.append(_trial(_term(base=-350.0), pruned_codes=["EQUITY_NONPOSITIVE"]))
    result = check_reward_dynamic_range(trials, max_total_to_feasible_ratio=3.0)
    assert result.passed is False
    assert "total/reward_std_feasible" in result.detail or "dominiert" in result.detail


def test_fails_when_feasible_region_is_flat():
    trials = [_trial(_term(base=1.0, param_pen=0.0)) for _ in range(10)]
    result = check_reward_dynamic_range(trials, min_reward_std_feasible=0.05)
    assert result.passed is False


def test_not_applicable_with_fewer_than_two_feasible_trials():
    trials = [_trial(_term(base=1.0), pruned_codes=["EQUITY_NONPOSITIVE"])]
    result = check_reward_dynamic_range(trials)
    assert result.passed is True
    assert result.actual is None


def test_pruned_trials_never_cause_a_false_positive_dominance_failure_alone():
    """Ein Lauf, in dem NICHTS geprunt wurde (reward_std_total == reward_std_feasible exakt),
    darf durch die dritte Klausel (ratio <= 3.0) niemals scheitern (ratio == 1.0)."""
    import random
    rng = random.Random(3)
    trials = [_trial(_term(base=rng.uniform(0.5, 2.0), param_pen=rng.uniform(0.01, 0.05)))
             for _ in range(15)]
    result = check_reward_dynamic_range(trials)
    assert result.actual["reward_std_total"] == result.actual["reward_std_feasible"]
