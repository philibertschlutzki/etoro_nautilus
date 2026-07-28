"""Issue #792 (P2) — Zwei unabhängige Kollinearitäts-Schwellen-Defaults (0.95 vs. 0.90) für
dieselbe Frage ("sind zwei Gates redundant?").

Root-Cause: `assert_gate_collinearity_guard` (0.95) und `gate_collinearity_redundancy_alarm`/
`assert_eligible_requires_all_not_redundant` (0.90) trugen zwei koexistierende Code-Defaults für
dieselbe Redundanz-Frage — ein Graubereich (0.90-0.95), in dem der [#697]-ERROR feuerte und die
[#667]-WARNUNG schwieg, ohne dass die Differenz irgendwo begründet war (695 [#667]-Warnungen bei
> 0.95 vs. 287 [#697]-Errors bei > 0.90 im selben Lauf).

Fix: EINE deklarative Schwelle `tournament.json['gate_collinearity_threshold']` (Default 0.90, die
schärfere der beiden Werte), gelesen über `reward._gate_collinearity_threshold`; alle drei
Einstiegspunkte lösen `threshold=None` darüber auf. Kein Code-Default mehr.
"""
import json
from pathlib import Path

from automation.optimizer.reward import (
    _gate_collinearity_threshold, assert_gate_collinearity_guard,
    gate_collinearity_redundancy_alarm, assert_eligible_requires_all_not_redundant,
)

CFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ── Genau eine Schwelle in der Config; kein Default im Code ───────────────────────────────────────
def test_config_declares_single_gate_collinearity_threshold():
    assert CFG["gate_collinearity_threshold"] == 0.90


def test_schema_documents_the_threshold():
    doc = CFG["_schema"]["fields"]["gate_collinearity_threshold"]
    assert "#792" in doc


def test_missing_key_falls_back_to_090_not_095():
    assert _gate_collinearity_threshold({}) == 0.90
    assert _gate_collinearity_threshold(None) == 0.90


def test_helper_reads_configured_value():
    assert _gate_collinearity_threshold({"gate_collinearity_threshold": 0.85}) == 0.85


# ── Akzeptanzkriterium #792: Schwelle 0.90 ⇒ dieselbe Kandidatenmenge in allen drei Funktionen ────
def _cohort():
    # oos_min_psr/oos_min_excess_return sind konstruiert kollinear (Spearman ρ≈0.95); oos_max_
    # drawdown ist gegen BEIDE praktisch unkorreliert (|ρ|<0.12) — isoliert das eine redundante
    # Paar, damit die Kandidatenmenge über alle drei Funktionen eindeutig vergleichbar ist.
    excess = [0.01, 0.02, 0.03, 0.04, 0.05, -0.01, 0.015, 0.025]
    psr = [0.01, 0.02, 0.04, 0.03, 0.05, -0.01, 0.02, 0.03]
    drawdown = [0.03, -0.02, 0.01, -0.04, 0.02, -0.01, 0.005, -0.015]
    return [
        {"oos_min_trades": 100.0, "oos_max_drawdown": d, "oos_min_psr": p,
         "oos_min_excess_return": x}
        for d, p, x in zip(drawdown, psr, excess)
    ]


def test_same_threshold_yields_same_candidate_set_across_all_three_entry_points():
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr",
                                      "oos_min_excess_return"],
            "gate_collinearity_threshold": 0.90,
            # Issue #810 — jedes aktive Gate braucht einen Prioritätseintrag.
            "gate_consolidation_priority": ["oos_min_psr", "max_drawdown", "min_trades",
                                            "oos_min_excess_return"],
            "gate_consolidation_protected": ["min_trades", "max_drawdown"]}
    cohort = _cohort()

    guard_result = assert_gate_collinearity_guard(cohort, tcfg)
    over_threshold_pairs = {
        pair for pair, rho in guard_result["correlations"].items()
        if rho is not None and abs(rho) > 0.90
    }

    alarm_result = gate_collinearity_redundancy_alarm(cohort, tcfg)
    alarm_candidates = set(alarm_result["redundant_candidates"])

    not_redundant_result = set(assert_eligible_requires_all_not_redundant(
        cohort, tcfg["eligible_requires_all"], tcfg))

    # Genau ein Paar liegt über der Schwelle (psr/excess_return) — dieselbe Erkenntnis muss in
    # ALLEN DREI Funktionen ankommen, mit derselben (config-gelesenen) Schwelle.
    assert over_threshold_pairs == {("oos_min_psr", "oos_min_excess_return")}
    assert alarm_candidates == {"oos_min_excess_return"}
    assert not_redundant_result == {"oos_min_excess_return"}


def test_explicit_threshold_override_still_wins_over_config():
    cohort = _cohort()
    tcfg = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr",
                                      "oos_min_excess_return"],
            "gate_collinearity_threshold": 0.90,
            # Issue #810 — jedes aktive Gate braucht einen Prioritätseintrag.
            "gate_consolidation_priority": ["oos_min_psr", "max_drawdown", "min_trades",
                                            "oos_min_excess_return"],
            "gate_consolidation_protected": ["min_trades", "max_drawdown"]}
    # Ein Schwellen-Override auf 0.999 lässt denselben (~0.95-korrelierten) Befund verschwinden.
    strict_alarm = gate_collinearity_redundancy_alarm(cohort, tcfg, threshold=0.999)
    assert strict_alarm["alarms"] == []
    strict_not_redundant = assert_eligible_requires_all_not_redundant(
        cohort, tcfg["eligible_requires_all"], tcfg, threshold=0.999)
    assert strict_not_redundant == []


def test_no_hardcoded_095_default_remains_in_reward_module():
    """Akzeptanzkriterium (#792): keine der drei Funktionen traegt mehr einen eigenen
    0.90/0.95-Code-Default in der Signatur — alle resolven ueber _gate_collinearity_threshold."""
    import inspect
    import automation.optimizer.reward as reward_mod
    for fn_name in ("assert_gate_collinearity_guard", "gate_collinearity_redundancy_alarm",
                    "assert_eligible_requires_all_not_redundant"):
        sig = inspect.signature(getattr(reward_mod, fn_name))
        assert sig.parameters["threshold"].default is None
