"""Issue #1251 (GH #1121) — ein Gate mit gemessenem Grenzbeitrag null bekommt eine Konsequenz,
nicht eine Empfehlung.

Symptom. ``check_gate_marginal_contribution`` meldet seit mehreren Katalogen Kandidaten für
Entfernung aus ``eligible_requires_all`` (hier ``oos_min_psr``, ``marginal_delta=0.0``, n=1627) —
folgenlos, severity 'medium'.

Root-Cause. Der Check ist reine Diagnose. Dieselbe Gewöhnungs-Dynamik, die #907 für die
Kollinearität behoben hat, besteht für den Grenzbeitrag fort.

Fix.
1. ``tournament.json``: neuer Schlüssel ``gate_zero_marginal_policy ∈ {'warn','require_decision'}``,
   Default ``'require_decision'``; ``gate_redundancy_min_observations`` (Default 500);
   ``gate_zero_marginal_accepted`` (Liste von {gate, rationale, decided_in_issue}).
2. ``invariants.check_gate_zero_marginal_policy`` (severity 'blocking'): FAIL, wenn ein Gate mit
   ``Σ marginal_delta == 0`` über >= ``gate_redundancy_min_observations`` Beobachtungen WEDER
   ``gate_consolidation_protected`` ist NOCH einen ``gate_zero_marginal_accepted``-Eintrag trägt.
3. Erbt die INCONCLUSIVE-Behandlung des bestehenden Pfads (``marginal_delta=None`` zählt nicht).
"""
import json
from pathlib import Path

from automation.optimizer import invariants as inv


CFG_DIR = Path("automation/config")


def _record(gate_inventory):
    return {"gate_inventory": gate_inventory}


def _entry(gate, *, marginal_delta, n_evaluated=600):
    return {"gate": gate, "marginal_delta": marginal_delta, "n_evaluated": n_evaluated}


# ---------------------------------------------------------------------------------------------
# invariants.check_gate_zero_marginal_policy
# ---------------------------------------------------------------------------------------------

def test_no_gate_inventory_is_not_applicable():
    r = inv.check_gate_zero_marginal_policy([])
    assert r.passed is True
    assert r.severity == "blocking"


def test_policy_warn_disables_the_check_even_with_an_undocumented_offender():
    records = [_record([_entry("oos_min_psr", marginal_delta=0.0)])]
    r = inv.check_gate_zero_marginal_policy(records, policy="warn")
    assert r.passed is True


def test_undocumented_zero_marginal_gate_fails_blocking():
    records = [_record([_entry("oos_min_psr", marginal_delta=0.0)])]
    r = inv.check_gate_zero_marginal_policy(records, policy="require_decision")
    assert r.passed is False
    assert r.severity == "blocking"
    assert "oos_min_psr" in r.actual


def test_protected_gate_is_exempt_even_with_zero_marginal_delta():
    records = [_record([_entry("max_drawdown", marginal_delta=0.0)])]
    r = inv.check_gate_zero_marginal_policy(
        records, policy="require_decision", gate_consolidation_protected=["max_drawdown"])
    assert r.passed is True


def test_accepted_gate_is_exempt():
    records = [_record([_entry("oos_min_psr", marginal_delta=0.0)])]
    accepted = [{"gate": "oos_min_psr", "rationale": "kalibrierte Redundanz, siehe #1251",
                "decided_in_issue": 1251}]
    r = inv.check_gate_zero_marginal_policy(
        records, policy="require_decision", accepted_gates=accepted)
    assert r.passed is True


def test_accepted_gate_with_different_name_does_not_exempt():
    records = [_record([_entry("oos_min_psr", marginal_delta=0.0)])]
    accepted = [{"gate": "some_other_gate", "rationale": "x", "decided_in_issue": 1}]
    r = inv.check_gate_zero_marginal_policy(
        records, policy="require_decision", accepted_gates=accepted)
    assert r.passed is False


def test_nonzero_marginal_delta_gate_passes():
    records = [_record([_entry("oos_min_alpha_tstat", marginal_delta=42.0)])]
    r = inv.check_gate_zero_marginal_policy(records, policy="require_decision")
    assert r.passed is True


def test_below_min_observations_is_inconclusive_not_blocking_fail():
    records = [_record([_entry("oos_min_psr", marginal_delta=0.0, n_evaluated=10)])]
    r = inv.check_gate_zero_marginal_policy(
        records, policy="require_decision", min_observations=500)
    assert r.passed is True


def test_marginal_delta_none_is_excluded_from_the_offender_check():
    records = [_record([_entry("oos_min_psr", marginal_delta=None, n_evaluated=0)])]
    r = inv.check_gate_zero_marginal_policy(records, policy="require_decision")
    assert r.passed is True


def test_multiple_offenders_across_studies_are_aggregated():
    records = [
        _record([_entry("oos_min_psr", marginal_delta=0.0, n_evaluated=300)]),
        _record([_entry("oos_min_psr", marginal_delta=0.0, n_evaluated=300)]),
    ]
    r = inv.check_gate_zero_marginal_policy(
        records, policy="require_decision", min_observations=500)
    assert r.passed is False
    assert r.actual["oos_min_psr"]["n_evaluated"] == 600


def test_reference_symptom_oos_min_psr_n1627_fails_without_documentation():
    records = [_record([_entry("oos_min_psr", marginal_delta=0.0, n_evaluated=1627)])]
    r = inv.check_gate_zero_marginal_policy(records, policy="require_decision")
    assert r.passed is False
    assert r.actual["oos_min_psr"]["n_evaluated"] == 1627


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium — nach #1248 (PSR-Gate bereits aus eligible_requires_all entfernt) liefert
# der Check 0 blockierende FAILs auf leerem gate_inventory (kein oos_min_psr mehr konfiguriert).
# ---------------------------------------------------------------------------------------------

def test_empty_gate_inventory_after_1248_removal_yields_zero_fails():
    # Reflektiert den Ist-Zustand nach #1118/#1248: oos_min_psr ist nicht mehr in
    # eligible_requires_all, ein Report ohne dieses Gate im gate_inventory hat also
    # strukturell keinen Kandidaten mehr.
    records = [_record([_entry("oos_min_alpha_tstat", marginal_delta=12.0)]),
               _record([_entry("min_trades", marginal_delta=None, n_evaluated=0)])]
    r = inv.check_gate_zero_marginal_policy(
        records, policy="require_decision", gate_consolidation_protected=["min_trades", "max_drawdown"])
    assert r.passed is True


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_check_is_wired_into_build_report():
    import inspect
    from automation.optimizer import report as rpt
    source = inspect.getsource(rpt._build_report)
    assert "check_gate_zero_marginal_policy(" in source
    assert 'tournament_cfg.get("gate_zero_marginal_policy"' in source


# ---------------------------------------------------------------------------------------------
# tournament.json production config
# ---------------------------------------------------------------------------------------------

def test_production_config_defaults():
    cfg = json.loads((CFG_DIR / "tournament.json").read_text("utf-8"))
    assert cfg.get("gate_zero_marginal_policy") == "require_decision"
    assert cfg.get("gate_redundancy_min_observations") == 500
    assert cfg.get("gate_zero_marginal_accepted") == []


def test_production_config_oos_min_psr_not_in_eligible_requires_all():
    # Akzeptanzkriterium #1251 — nach #1248 ist das Gate ENTFERNT, nicht dokumentiert.
    cfg = json.loads((CFG_DIR / "tournament.json").read_text("utf-8"))
    assert "oos_min_psr" not in cfg.get("eligible_requires_all", [])
