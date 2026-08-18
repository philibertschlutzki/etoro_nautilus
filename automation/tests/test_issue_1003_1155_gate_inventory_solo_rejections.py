"""Issue #1003/#1155 (Katalog #1170, P1) — ``gate_inventory.n_solo_rejections`` traegt die
eligible-Zahl.

Symptom (B-9): ``n_solo_rejections > n_rejections`` in 7/84 Eintraegen; ``n_solo_rejections ==
n_eligible`` in 27/30.

Root-Cause: ``n_solo_rejections`` wurde aus derselben ``oos_gate_deltas``-Quelle gebildet wie
``marginal_delta`` (``n_eligible_without_gate − n_eligible_with_all`` ist algebraisch IMMER
identisch mit der Anzahl der Trials, deren einziges verletztes Gate genau dieses ist — derselbe
Zaehler unter zwei Namen), UND diese Quelle hat einen dokumentierten blinden Fleck (#956/#1122):
``oos_gate_deltas`` fehlt der Key fuer ein Gate GANZ, sobald dessen Statistik undefiniert war.

Fix:
1. ``n_solo_rejections`` wird primaer aus ``oos_rejection_reasons`` (Trials mit GENAU EINEM
   Rejection-Grund, dessen Gate-Praefix diesem Gate entspricht) gebildet.
2. ``marginal_delta`` wird unabhaengig ueber ``reward.gate_marginal_pass_rate_delta`` (eine
   Wahrscheinlichkeit) berechnet, sobald ``tournament_config`` uebergeben wird.
3. Harte Ordnungs-Invariante ``0 <= n_solo_rejections <= n_rejections <= n_evaluated`` (fail-loud).
"""
import pytest

from automation.optimizer import invariants as inv


def _trial(deltas, reasons=None, evaluated=True):
    d = {"oos_evaluated": evaluated, "oos_gate_deltas": deltas}
    if reasons is not None:
        d["oos_rejection_reasons"] = reasons
    return d


_GATES = ["oos_min_psr", "oos_min_trades"]


def test_reproduces_symptom_fixture_reasons_based_solo_is_bounded_by_n_rejections():
    """Der zentrale B-9-Fall: ein Trial mit undefinierter PSR-Statistik hat KEINEN oos_gate_deltas-
    Eintrag fuer 'oos_min_psr' (blinder Fleck), aber EINEN Rejection-Grund. Ohne den Fix waere er
    ueber die Delta-Luecke 'faelschlich eligible ohne dieses Gate' -- mit dem Fix zaehlt er korrekt."""
    trials = [
        _trial({"oos_min_trades": 5.0}, reasons=["oos_min_psr: 0.5 < 0.75"]),
        _trial({"oos_min_psr": -0.2, "oos_min_trades": -3.0},
               reasons=["oos_min_psr: 0.5 < 0.75", "oos_min_trades: 7 < 10"]),
    ]
    is_rej_counts = {"REJECT_OOS_MIN_PSR": 2, "REJECT_OOS_MIN_TRADES": 1}
    table = inv.gate_inventory_table(trials, _GATES, is_rejection_detail_counts=is_rej_counts)
    psr_row = next(r for r in table if r["gate"] == "oos_min_psr")
    assert psr_row["n_solo_rejections"] == 1
    assert psr_row["n_rejections"] == 2
    assert psr_row["n_solo_rejections"] <= psr_row["n_rejections"]


def test_undefined_statistic_marker_excludes_the_trial_from_solo_attribution():
    """Ein Trial, dessen EINZIGER Grund die #917-Undefiniert-Marke traegt, wird NICHT als solo
    fuer das genannte Gate gezaehlt -- seine Klassifikation ist REJECT_OOS_STATISTIC_UNAVAILABLE,
    nicht das Gate selbst (dieselbe Vorrangregel wie run_optimization._classify_is_rejection_detail)."""
    trials = [_trial({"oos_min_trades": 5.0}, reasons=["oos_min_psr: None (insufficient/guard) < 0.75"])]
    is_rej_counts = {"REJECT_OOS_STATISTIC_UNAVAILABLE": 1}
    table = inv.gate_inventory_table(trials, _GATES, is_rejection_detail_counts=is_rej_counts)
    psr_row = next(r for r in table if r["gate"] == "oos_min_psr")
    assert psr_row["n_solo_rejections"] == 0


def test_hard_ordering_invariant_holds_for_a_realistic_cohort():
    trials = [
        _trial({"oos_min_psr": 0.1, "oos_min_trades": 0.1}, reasons=[]),
        _trial({"oos_min_psr": -0.2, "oos_min_trades": 0.1}, reasons=["oos_min_psr: 0.5 < 0.75"]),
        _trial({"oos_min_psr": 0.1, "oos_min_trades": -0.1}, reasons=["oos_min_trades: 7 < 10"]),
        _trial({"oos_min_psr": -0.3, "oos_min_trades": -0.2},
               reasons=["oos_min_psr: 0.4 < 0.75", "oos_min_trades: 5 < 10"]),
    ]
    is_rej_counts = {"REJECT_OOS_MIN_PSR": 2, "REJECT_OOS_MIN_TRADES": 2}
    table = inv.gate_inventory_table(trials, _GATES, is_rejection_detail_counts=is_rej_counts)
    for row in table:
        assert 0 <= row["n_solo_rejections"] <= row["n_rejections"] <= row["n_evaluated"]


def test_ordering_invariant_raises_on_a_genuinely_inconsistent_input():
    """Fail-loud (Fix Punkt 3): eine injizierte is_rejection_detail_counts-Zahl, die KLEINER als
    die tatsaechliche reasons-basierte Solo-Zaehlung ist, wirft statt still eine falsche Zahl
    weiterzugeben."""
    trials = [_trial({"oos_min_trades": 5.0}, reasons=["oos_min_psr: 0.5 < 0.75"])]
    is_rej_counts = {"REJECT_OOS_MIN_PSR": 0}  # inconsistent: reasons say solo=1
    with pytest.raises(AssertionError, match="Ordnungs-Invariante"):
        inv.gate_inventory_table(trials, _GATES, is_rejection_detail_counts=is_rej_counts)


# --- marginal_delta via reward.gate_marginal_pass_rate_delta --------------------------------------

def test_marginal_delta_uses_probability_based_function_when_tournament_config_given():
    trials = [
        _trial({"oos_min_psr": 0.1, "oos_min_trades": 0.1}),
        _trial({"oos_min_psr": -0.2, "oos_min_trades": 0.1}),
        _trial({"oos_min_psr": 0.1, "oos_min_trades": -0.1}),
        _trial({"oos_min_psr": -0.3, "oos_min_trades": -0.2}),
    ]
    tournament_config = {"eligible_requires_all": _GATES}
    table = inv.gate_inventory_table(trials, _GATES, tournament_config=tournament_config)
    for row in table:
        assert row["marginal_delta"] is None or 0.0 <= row["marginal_delta"] <= 1.0


def test_marginal_delta_is_no_longer_a_copy_of_n_solo_rejections():
    trials = [
        _trial({"oos_min_psr": 0.1, "oos_min_trades": 0.1}),
        _trial({"oos_min_psr": -0.2, "oos_min_trades": 0.1}),
        _trial({"oos_min_psr": 0.1, "oos_min_trades": -0.1}),
        _trial({"oos_min_psr": -0.3, "oos_min_trades": -0.2}),
    ]
    tournament_config = {"eligible_requires_all": _GATES}
    table = inv.gate_inventory_table(trials, _GATES, tournament_config=tournament_config)
    psr_row = next(r for r in table if r["gate"] == "oos_min_psr")
    # marginal_delta ist eine Wahrscheinlichkeit (float in [0,1]), n_solo_rejections ein Integer-
    # Zaehler -- strukturell verschiedene Groessen, keine Kopie mehr.
    assert isinstance(psr_row["marginal_delta"], float)
    assert isinstance(psr_row["n_solo_rejections"], int)


def test_legacy_without_tournament_config_falls_back_to_count_based_marginal_delta():
    trials = [_trial({"oos_min_psr": 0.1, "oos_min_trades": -0.1})]
    table = inv.gate_inventory_table(trials, _GATES)
    psr_row = next(r for r in table if r["gate"] == "oos_min_psr")
    assert isinstance(psr_row["marginal_delta"], int)


# --- check_gate_marginal_contribution aggregates floats correctly ---------------------------------

def test_check_gate_marginal_contribution_does_not_truncate_fractional_deltas():
    """Regressionswaechter: ein int(...)-Cast wuerde jeden Bruchwert < 1.0 auf 0 abschneiden und
    JEDES Gate faelschlich als beitragslos melden."""
    study_records = [
        {"gate_inventory": [
            {"gate": "oos_min_psr", "n_rejections": 5, "n_solo_rejections": 2,
             "marginal_delta": 0.25, "n_evaluated": 600},
        ]},
    ]
    result = inv.check_gate_marginal_contribution(study_records, min_evaluated=500)
    assert result.passed is True


def test_check_gate_marginal_contribution_still_flags_a_genuine_zero_contribution():
    study_records = [
        {"gate_inventory": [
            {"gate": "oos_min_trades", "n_rejections": 0, "n_solo_rejections": 0,
             "marginal_delta": 0.0, "n_evaluated": 600},
        ]},
    ]
    result = inv.check_gate_marginal_contribution(study_records, min_evaluated=500)
    assert result.passed is False
    assert "oos_min_trades" in result.actual
