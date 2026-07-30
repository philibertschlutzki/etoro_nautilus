"""Issue #829 (P0, Katalog #828-#835, GitHub-Issue #751) — Closed-Loop-Deadlock: 'signal_absent'
verlangt ≥ 90 % Budgetausführung (#778), #805 kappt genau diese Studies bei 28 %/46 %.

Root-Cause: ``run_optimization.py`` stoppt eine Study mit ``STRUCTURAL_ALL_UNEVALUABLE`` (#805)
bereits bei ``n_startup_trials + structural_min_modelled_trials`` — für AdxAtrMomentum (dim 6)
34/120 = 28 %, für TrendPullback (dim 7) 64/140 = 46 % des Budgets. ``recommend_diagnosis_action``s
#778-Evidenzschwelle (``budget_executed_fraction >= 0.9``) ist für GENAU diese Ursache damit
strukturell nie erreichbar — 138 Meldungen, ausschliesslich diese zwei Strategien, ausschliesslich
``action == 'none'``, über sechs aufeinanderfolgende Sweeps (Pitfall #258: eine Abbruchregel, die
den Budgetanteil kappt, und eine Aktionsregel, die einen Mindest-Budgetanteil verlangt, bilden einen
Deadlock).

Fix: ``sufficient_evidence`` zielt auf das GEMESSENE ERGEBNIS statt auf den Budgetanteil —
``stop_reason == 'STRUCTURAL_ALL_UNEVALUABLE'`` ALLEIN ist bereits ausreichende Evidenz (dieser Wert
beweist, dass die Study ihre eigene ``len(completed) >= n_startup_trials + structural_min_modelled_
trials``-Vorbedingung bereits erfüllt hat), ODER weiterhin ``budget_executed_fraction >= 0.9``.
``n_runs_confirmed >= 2`` bleibt in JEDEM Fall Pflicht. Neuer neunter Invarianten-Check
``check_diagnosis_actionability`` (Pitfall #258) macht die Deadlock-Signatur (viele ``action ==
'none'``-Einträge derselben (strategy, binding_cause)-Kombination) im Report sichtbar.

Bewusst NICHT umgesetzt (Fix Punkte 3/4, dokumentiert für AGENTS.md #835): das reale Eintragen von
AdxAtrMomentumStrategy/TrendPullbackStrategy in ``symbol_strategy_denylist.json`` (erfordert einen
echten, gemessenen Folgelauf mit dem #829-Fix — Fabrizieren dieser Einträge ohne reale Evidenz wäre
selbst ein Verstoss gegen das Prinzip, das #829 durchsetzt) sowie ein automatisierter
``strategy_level_denylist``/``active: false``-Mechanismus (eine grössere Design-Entscheidung, die
eine PR-Review verdient, keine reflexive Umsetzung).
"""
import pytest

from automation.optimizer.sweep_diagnostics import recommend_diagnosis_action
from automation.optimizer import invariants as inv


# ── Akzeptanzkriterium #829 (Unit-Test aus dem Issue-Text) ──────────────────────────────────────────
def test_structural_stop_reason_alone_satisfies_evidence_at_28_percent_budget():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.28, stop_reason="STRUCTURAL_ALL_UNEVALUABLE", n_runs_confirmed=2,
    )
    assert rec["action"] == "denylist"


def test_structural_stop_reason_alone_satisfies_evidence_at_46_percent_budget():
    rec = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.46, stop_reason="STRUCTURAL_ALL_UNEVALUABLE", n_runs_confirmed=2,
    )
    assert rec["action"] == "denylist"


# ── n_runs_confirmed >= 2 bleibt Pflicht, auch mit dem neuen Nachweispfad ───────────────────────────
def test_structural_stop_reason_does_not_bypass_the_multi_run_requirement():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.28, stop_reason="STRUCTURAL_ALL_UNEVALUABLE", n_runs_confirmed=1,
    )
    assert rec["action"] == "none"


def test_structural_stop_reason_does_not_bypass_the_multi_run_requirement_at_zero():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.28, stop_reason="STRUCTURAL_ALL_UNEVALUABLE", n_runs_confirmed=0,
    )
    assert rec["action"] == "none"


# ── Neither evidence path satisfied -> 'none' (kein neues Loch) ────────────────────────────────────
def test_neither_evidence_path_still_yields_none():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.28, stop_reason=None, n_runs_confirmed=5,
    )
    assert rec["action"] == "none"


def test_wrong_stop_reason_does_not_satisfy_evidence():
    """Nur EXAKT 'STRUCTURAL_ALL_UNEVALUABLE' zaehlt -- ein anderer stop_reason (z.B. der der
    ZERO_ELIGIBLE-Klasse) ersetzt den #778-Budgetnachweis nicht."""
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.28, stop_reason="STRUCTURAL_ZERO_ELIGIBLE", n_runs_confirmed=2,
    )
    assert rec["action"] == "none"


# ── Rueckwaertskompat: der #778-Budgetpfad bleibt unveraendert gueltig ──────────────────────────────
def test_legacy_budget_only_path_still_works_without_stop_reason():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.95, n_runs_confirmed=2,
    )
    assert rec["action"] == "denylist"


def test_result_dict_carries_stop_reason_for_report_traceability():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        budget_executed_fraction=0.28, stop_reason="STRUCTURAL_ALL_UNEVALUABLE", n_runs_confirmed=2,
    )
    assert rec["stop_reason"] == "STRUCTURAL_ALL_UNEVALUABLE"


# ── invariants.check_diagnosis_actionability (Fix Punkt 5, Pitfall #258) ────────────────────────────
def _entries(strategy, cause, action, n):
    return [{"strategy": strategy, "binding_cause": cause, "action": action} for _ in range(n)]


def test_check_fails_against_the_reference_deadlock():
    """Akzeptanzkriterium #829: check_diagnosis_actionability FAILt gegen den heutigen Lauf
    (138 x action='none', ueber AdxAtrMomentum UND TrendPullback getrennt gezaehlt -- 69 je
    Strategie ueberschreitet den 50er-Schwellenwert bereits fuer sich)."""
    entries = (_entries("AdxAtrMomentumStrategy", "signal_absent", "none", 69)
              + _entries("TrendPullbackStrategy", "signal_absent", "none", 69))
    result = inv.check_diagnosis_actionability(entries)
    assert result.passed is False
    assert "AdxAtrMomentumStrategy/signal_absent" in result.detail
    assert "TrendPullbackStrategy/signal_absent" in result.detail


def test_check_passes_below_threshold():
    entries = _entries("AdxAtrMomentumStrategy", "signal_absent", "none", 49)
    assert inv.check_diagnosis_actionability(entries).passed is True


def test_check_passes_at_default_threshold_boundary_minus_one():
    entries = _entries("AdxAtrMomentumStrategy", "signal_absent", "none", 50)
    # 50 >= 50 -> FAIL (min_count ist inklusiv als unterste FAIL-Schwelle).
    assert inv.check_diagnosis_actionability(entries).passed is False


def test_check_ignores_denylist_and_search_space_override_actions():
    entries = (_entries("AdxAtrMomentumStrategy", "signal_absent", "denylist", 100)
              + _entries("TrendPullbackStrategy", "signal_sparse", "search_space_override", 100))
    assert inv.check_diagnosis_actionability(entries).passed is True


def test_check_is_a_noop_on_empty_cache():
    assert inv.check_diagnosis_actionability([]).passed is True
    assert inv.check_diagnosis_actionability(None).passed is True


def test_check_respects_custom_min_count():
    entries = _entries("AdxAtrMomentumStrategy", "signal_absent", "none", 10)
    assert inv.check_diagnosis_actionability(entries, min_count=10).passed is False
    assert inv.check_diagnosis_actionability(entries, min_count=11).passed is True


def test_check_counts_strategy_cause_combinations_independently():
    """Zwei verschiedene Strategien mit je 30 'none'-Eintraegen bleiben je fuer sich unter der
    50er-Schwelle -- keine faelschliche Summierung ueber Strategien hinweg."""
    entries = (_entries("AdxAtrMomentumStrategy", "signal_absent", "none", 30)
              + _entries("TrendPullbackStrategy", "signal_absent", "none", 30))
    assert inv.check_diagnosis_actionability(entries).passed is True
