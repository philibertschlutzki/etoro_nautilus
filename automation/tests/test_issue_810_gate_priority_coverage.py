"""Issue #810 — `_GATE_CONSOLIDATION_PRIORITY` wurde bei der #776-Konsolidierung nicht mitgezogen:
`eligible_requires_all` ist seit #776 `['min_trades', 'max_drawdown', 'oos_min_psr']`, aber weder
`min_trades` noch `max_drawdown` standen in der alten Prioritätstabelle — beide fielen über
`priority.get(k, 99)` auf denselben Sentinel, und der Redundanz-Alarm empfahl fälschlich die
Entfernung von `max_drawdown` (der harten Risikogrenze, die #776 ausdrücklich behalten hat) bei
einem Kollinearitäts-Treffer mit `oos_min_psr` — Pitfall #134 in einer Prioritätstabelle statt
einer Kennzahl.

Fix: `gate_consolidation_priority` (deklarativ, `tournament.json`) ersetzt die eingefrorene
Code-Konstante; `gate_consolidation_protected` markiert Gates, die NIE als redundanter Kandidat
gelten (unabhängig von ρ und Priorität); `assert_gate_priority_coverage` bricht fail-loud ab,
sobald ein aktives Gate keinen Prioritätseintrag hat (kein stiller Sentinel mehr).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.reward import (
    assert_gate_priority_coverage,
    gate_collinearity_redundancy_alarm,
    assert_eligible_requires_all_not_redundant,
    _gate_consolidation_priority,
    _gate_consolidation_protected,
)

CFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ── assert_gate_priority_coverage: FAIL-LOUD bei fehlendem Prioritätseintrag ───────────────────────
def test_real_config_passes_priority_coverage():
    assert_gate_priority_coverage(CFG)  # no raise


def test_raises_when_an_active_all_gate_has_no_priority_entry():
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr"],
            "gate_consolidation_priority": ["oos_min_psr"]}  # min_trades/max_drawdown fehlen
    with pytest.raises(ValueError, match="GATE_PRIORITY_COVERAGE_MISSING"):
        assert_gate_priority_coverage(tcfg)


def test_raises_when_any_condition_is_active_but_unlisted():
    tcfg = {"eligible_requires_all": ["oos_min_psr"],
            "eligible_requires_any": ["min_profit_factor"],
            "gate_consolidation_priority": ["oos_min_psr"]}  # any_condition fehlt
    with pytest.raises(ValueError, match="GATE_PRIORITY_COVERAGE_MISSING"):
        assert_gate_priority_coverage(tcfg)


def test_missing_priority_table_entirely_also_raises():
    tcfg = {"eligible_requires_all": ["min_trades"]}
    with pytest.raises(ValueError, match="GATE_CONSOLIDATION_PRIORITY_MISSING"):
        assert_gate_priority_coverage(tcfg)


def test_full_coverage_does_not_raise():
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr"],
            "eligible_requires_any": ["min_profit_factor"],
            "gate_consolidation_priority": ["oos_min_psr", "max_drawdown", "min_trades",
                                            "any_condition"]}
    assert_gate_priority_coverage(tcfg)  # no raise


# ── gate_collinearity_redundancy_alarm: fail-loud statt priority.get(k, 99)-Sentinel ───────────────
def test_alarm_raises_instead_of_silently_defaulting_missing_gates_to_the_same_sentinel():
    """Root-Cause #810 direkt reproduziert: min_trades UND max_drawdown OHNE Prioritätseintrag
    fielen vorher beide auf denselben Sentinel (99) — jetzt bricht der Aufruf fail-loud ab, statt
    eine (Iterationsreihenfolge-abhängige) Zufallsentscheidung zu treffen."""
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr"],
            "gate_consolidation_priority": ["oos_min_psr"]}
    cohort = [{"oos_min_trades": e, "oos_max_drawdown": -e, "oos_min_psr": e}
             for e in [0.01, 0.02, 0.03, 0.04, 0.05, -0.01]]
    with pytest.raises(ValueError, match="GATE_PRIORITY_COVERAGE_MISSING"):
        gate_collinearity_redundancy_alarm(cohort, tcfg)


# ── gate_consolidation_protected: NIE als redundant_candidate, unabhängig von ρ ────────────────────
def _protected_cohort():
    # max_drawdown und oos_min_psr sind perfekt kollinear konstruiert (rho=-1 bzw. +1 vs. e).
    return [{"oos_min_trades": 100.0, "oos_max_drawdown": -e, "oos_min_psr": e}
           for e in [0.01, 0.02, 0.03, 0.04, 0.05, -0.01]]


def test_protected_gate_never_becomes_the_redundant_candidate():
    """Issue #811 — protected Gates werden VOR der Jaccard-Messung VOLLSTAENDIG aus der
    Kandidatenmatrix entfernt (staerker als die #810-Garantie "wird nie Kandidat"): max_drawdown
    taucht daher in KEINEM Paar mehr auf, obwohl es (rho=-1) perfekt mit oos_min_psr korreliert.
    Ein GENUIN Jaccard-redundantes Paar unter den verbleibenden (nicht-protected) Kandidaten
    (oos_min_psr/oos_min_expectancy, identisches Pass-Muster) feuert dagegen normal — die
    Protected-Ausnahme unterdrueckt nur Paare, an denen ein protected Gate beteiligt ist."""
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr", "oos_min_expectancy"],
            "gate_consolidation_priority": ["min_trades", "max_drawdown", "oos_min_psr",
                                            "oos_min_expectancy"],
            "gate_consolidation_protected": ["max_drawdown"]}
    cohort = [{"oos_min_trades": 100.0, "oos_max_drawdown": -e, "oos_min_psr": e,
              "oos_min_expectancy": e} for e in [0.01, 0.02, 0.03, 0.04, 0.05, -0.01]]
    result = gate_collinearity_redundancy_alarm(cohort, tcfg)
    assert "oos_max_drawdown" not in result["redundant_candidates"]
    # oos_min_expectancy hat ein zu oos_min_psr identisches Pass-Muster (Jaccard=1.0) und ist
    # niedriger priorisiert -> wird trotz der protected-Ausnahme fuer max_drawdown zum Kandidaten.
    assert "oos_min_expectancy" in result["redundant_candidates"]
    assert "oos_min_psr" not in result["redundant_candidates"]


def test_min_trades_and_max_drawdown_are_never_flagged_on_the_real_config():
    """Akzeptanzkriterium #810: kein redundant_candidate == 'max_drawdown' oder 'min_trades' in
    irgendeinem Alarm — auf der ECHTEN Config, mit einer Kohorte, die max_drawdown/psr bewusst
    kollinear konstruiert (der historische Root-Cause-Fall)."""
    result = gate_collinearity_redundancy_alarm(_protected_cohort(), CFG)
    assert "oos_max_drawdown" not in result["redundant_candidates"]
    assert "oos_min_trades" not in result["redundant_candidates"]


def test_two_protected_gates_colliding_yields_no_alarm_for_that_pair():
    """Sind BEIDE Gates eines Paares protected, gibt es keine sinnvolle Konsolidierungs-Empfehlung
    -> kein Alarm-Eintrag fuer dieses Paar."""
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown"],
            "gate_consolidation_priority": ["min_trades", "max_drawdown"],
            "gate_consolidation_protected": ["min_trades", "max_drawdown"]}
    cohort = [{"oos_min_trades": e, "oos_max_drawdown": e}
             for e in [0.01, 0.02, 0.03, 0.04, 0.05, -0.01]]
    result = gate_collinearity_redundancy_alarm(cohort, tcfg)
    assert result["alarms"] == []
    assert result["redundant_candidates"] == {}


# ── Akzeptanzkriterium #810: die AKTUELLE Config liefert eine leere unkonsolidierte Liste ─────────
def test_real_config_yields_no_unconsolidated_gates_for_a_clean_cohort():
    cohort = [{"oos_min_trades": 50.0 + i, "oos_max_drawdown": 0.05 + i * 0.001,
              "oos_min_psr": 0.7 + i * 0.01} for i in range(8)]
    result = assert_eligible_requires_all_not_redundant(
        cohort, CFG["eligible_requires_all"], CFG)
    assert result == []


# ── _gate_consolidation_priority / _gate_consolidation_protected: any_condition bleibt unpräfigiert
def test_any_condition_is_not_oos_prefixed_in_the_priority_table():
    priority = _gate_consolidation_priority({"gate_consolidation_priority": ["any_condition", "min_trades"]})
    assert priority == ("any_condition", "oos_min_trades")


def test_any_condition_is_not_oos_prefixed_in_the_protected_set():
    protected = _gate_consolidation_protected({"gate_consolidation_protected": ["any_condition"]})
    assert protected == frozenset({"any_condition"})


def test_missing_protected_key_defaults_to_empty_set():
    assert _gate_consolidation_protected({}) == frozenset()
    assert _gate_consolidation_protected(None) == frozenset()


# ── Produktions-Config ──────────────────────────────────────────────────────────────────────────
def test_real_config_declares_priority_and_protected_lists():
    assert CFG.get("gate_consolidation_priority")
    assert "min_trades" in CFG["gate_consolidation_priority"]
    assert "max_drawdown" in CFG["gate_consolidation_priority"]
    assert set(CFG.get("gate_consolidation_protected") or []) == {"min_trades", "max_drawdown"}


def test_schema_documents_810_for_both_new_keys():
    doc_priority = CFG["_schema"]["fields"]["gate_consolidation_priority"]
    doc_protected = CFG["_schema"]["fields"]["gate_consolidation_protected"]
    assert "#810" in doc_priority
    assert "#810" in doc_protected
