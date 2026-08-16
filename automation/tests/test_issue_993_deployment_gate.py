"""Issue #993 (P0, HEADLINE, Katalog #993-#1002) — Die Deployment-Grenze umgeht die vollstaendige
Confirm-Kette.

Vor diesem Fix entschied ``daily_orchestrator.phase5_live_deployment`` ueber Kapitaleinsatz allein
mit ``winner.get("oos_eligible") is True and winner.get("oos_evaluated") is True`` — ein
Einzelfenster-Gate ohne Multiplizitaetskorrektur, das DSR, PBO, Holdout-Bootstrap-CI, Boundary-Veto,
R_symbol > R_global und Datenstand-Kohaerenz NICHT konsultierte.

Dieser Test deckt ab:
  (a) alle acht Klauseln (Akzeptanzkriterium #993) je in {pass, fail, None} wo dieser Zustand
      erreichbar ist (sechs Klauseln als 3x2-Matrix parametrisiert; ``promotion_record_exists``
      kennt strukturell kein ``None`` [ein Record existiert oder nicht] und ``r_edge`` loest
      dank confirm.py's expliziter R_symbol-/R_global-None-Semantik IMMER zu einem definiten
      Bool auf — beide sind stattdessen ueber dedizierte Tests abgedeckt),
  (b) ein Paar ohne Promotionsrecord wird nicht whitelisted, auch als ``per_symbol_winner``,
  (c) Snapshot-Drift blockiert unabhaengig von allen anderen Klauseln,
  (d) Regressionsfall: die ALTE Bedingung (oos_eligible ∧ oos_evaluated) reicht allein NICHT mehr,
  (e) ``check_deployment_gate_completeness`` (invariants.py) — vollstaendig vs. unvollstaendig,
  (f) der Proposal->Record-Flattener (``build_promotion_record_from_proposal``).
"""
import pytest

from automation.optimizer.deployment_gate import (
    DEPLOYMENT_CLAUSES,
    DeploymentDecision,
    build_promotion_record_from_proposal,
    evaluate_deployment_eligibility,
)
from automation.optimizer.invariants import check_deployment_gate_completeness

_CFG = {"deflation_confidence": 0.95, "oos_min_psr": 0.75, "pbo_min_configs": 10}
_SNAPSHOT = "deadbeef" * 8


def _passing_record(**overrides) -> dict:
    record = {
        "status": "READY_FOR_PR",
        "R_symbol": 1.0,
        "R_global": 0.2,
        "promotion_margin": 0.0,
        "data_snapshot_sha256": _SNAPSHOT,
        "deflated_dsr": 0.97,
        "oos_psr": 0.80,
        "holdout_ci_lower_sortino": 0.05,
        "pbo": 0.30,
        "pbo_n_configs": 40,
        # Issue #1007 (Katalog #858) — neunte Klausel ``study_invariants_clean``: [] == "geprueft,
        # keine blockierende Invariante".
        "blocking_invariant_names": [],
        # Issue #1042 (Katalog #866, E-1) — zehnte Klausel ``cost_stress``: positive Expectancy
        # unter doppelter Kommission.
        "expectancy_cost_stress_2x": 0.001,
        # Issue #1073 (Katalog #866-2) — elfte Klausel ``expectancy_outlier_robust``: winsorisierte
        # Expectancy bleibt positiv UND vorzeichengleich zur rohen Expectancy.
        "holdout_expectancy_notional_weighted": 0.05,
        "holdout_expectancy_winsorized": 0.04,
        "run_id": "run_abc123",
    }
    record.update(overrides)
    return record


def _evaluate(record, *, current_snapshot_sha256: str = _SNAPSHOT) -> DeploymentDecision:
    records = {} if record is None else {("Strat", "SYM.ETORO"): record}
    return evaluate_deployment_eligibility(
        ("Strat", "SYM.ETORO"), records, _CFG, current_snapshot_sha256=current_snapshot_sha256)


def test_fully_passing_record_is_admitted_with_no_blocking_clause():
    decision = _evaluate(_passing_record())
    assert decision.admitted is True
    assert decision.blocking_clause is None
    assert decision.clause_results == {c: True for c in DEPLOYMENT_CLAUSES}
    assert decision.promotion_run_id == "run_abc123"
    assert decision.data_snapshot_sha256 == _SNAPSHOT


def test_missing_promotion_record_is_never_admitted_even_as_per_symbol_winner():
    decision = _evaluate(None)
    assert decision.admitted is False
    assert decision.blocking_clause == "promotion_record_exists"
    assert decision.clause_results["promotion_record_exists"] is False
    # Fail-closed: keine der uebrigen Klauseln darf stillschweigend als erfuellt gelten.
    for clause in DEPLOYMENT_CLAUSES[1:]:
        assert decision.clause_results[clause] is None


def test_regression_old_single_clause_condition_no_longer_sufficient():
    """Der EXAKTE alte Zustand (oos_eligible/oos_evaluated waren Phase-4-Felder, hier durch einen
    Record ohne jede Confirm-Kette simuliert: kein DSR, kein PSR, kein PBO, keine Bootstrap-CI)
    waere unter der Vor-#993-Logik whitelisted worden. Unter der Deployment-Grenze blockiert er."""
    record = {
        "status": "READY_FOR_PR",
        "R_symbol": 1.0,
        "R_global": 0.2,
        "promotion_margin": 0.0,
        "data_snapshot_sha256": _SNAPSHOT,
        # Genau die Groessen, die das alte Phase-4-Gate nie kannte:
        "deflated_dsr": None,
        "oos_psr": None,
        "holdout_ci_lower_sortino": None,
        "pbo": None,
        "pbo_n_configs": None,
    }
    decision = _evaluate(record)
    assert decision.admitted is False
    assert decision.blocking_clause == "dsr"
    assert decision.clause_results["psr"] is None
    assert decision.clause_results["bootstrap_ci"] is None
    assert decision.clause_results["pbo"] is None


def test_synthetic_case_from_issue_acceptance_criteria_dsr_below_threshold():
    """oos_eligible=True, oos_evaluated=True (== status READY_FOR_PR) mit deflated_dsr=0.60 (<
    deflation_confidence=0.95) wird NICHT whitelisted; blocking_clause == 'dsr'."""
    decision = _evaluate(_passing_record(deflated_dsr=0.60))
    assert decision.admitted is False
    assert decision.blocking_clause == "dsr"


def test_promotion_on_stale_snapshot_is_rejected_on_snapshot_drift():
    decision = _evaluate(_passing_record(data_snapshot_sha256="A" * 64), current_snapshot_sha256="B" * 64)
    assert decision.admitted is False
    assert decision.blocking_clause == "snapshot_drift"


def test_r_symbol_none_never_satisfies_r_edge():
    decision = _evaluate(_passing_record(R_symbol=None))
    assert decision.clause_results["r_edge"] is False
    assert decision.admitted is False


def test_r_global_none_trivially_satisfies_r_edge_no_baseline_to_beat():
    decision = _evaluate(_passing_record(R_global=None))
    assert decision.clause_results["r_edge"] is True
    assert decision.admitted is True


def test_pbo_none_with_too_few_configs_satisfies_pbo_clause():
    decision = _evaluate(_passing_record(pbo=None, pbo_n_configs=3))
    assert decision.clause_results["pbo"] is True
    assert decision.admitted is True


def test_pbo_none_with_enough_configs_is_a_definite_fail_not_unknown():
    """pbo=None mit einer BEKANNTEN Config-Zahl >= pbo_min_configs ist kein unbekannter Zustand —
    die Formel ``PBO=None ∧ pbo_n_configs < pbo_min_configs`` ist mit bekanntem pbo_n_configs
    vollstaendig ausgewertet und liefert deterministisch False (keine Exemption, aber auch kein
    berechnetes PBO)."""
    decision = _evaluate(_passing_record(pbo=None, pbo_n_configs=40))
    assert decision.clause_results["pbo"] is False
    assert decision.admitted is False


def test_pbo_none_with_unknown_configs_is_fail_closed():
    decision = _evaluate(_passing_record(pbo=None, pbo_n_configs=None))
    assert decision.clause_results["pbo"] is None
    assert decision.admitted is False


# --- Akzeptanzkriterium: acht Klauseln x {pass, fail, None} = 24 Faelle -----------------------

_CLAUSE_VARIANTS = {
    "status_ready_for_pr": {
        "pass": {"status": "READY_FOR_PR"},
        "fail": {"status": "REJECTED_ON_HOLDOUT"},
        "none": {"status": None},
    },
    "dsr": {
        "pass": {"deflated_dsr": 0.99},
        "fail": {"deflated_dsr": 0.10},
        "none": {"deflated_dsr": None},
    },
    "psr": {
        "pass": {"oos_psr": 0.99},
        "fail": {"oos_psr": 0.10},
        "none": {"oos_psr": None},
    },
    "pbo": {
        "pass": {"pbo": 0.10},
        "fail": {"pbo": 0.90},
        # Genuinely unknown: neither a computed PBO nor a config count to fall back on. A record
        # with pbo=None but a KNOWN pbo_n_configs is not "unknown" — it resolves deterministically
        # (see test_pbo_none_with_enough_configs_is_fail_closed / ..._satisfies_pbo_clause below).
        "none": {"pbo": None, "pbo_n_configs": None},
    },
    "bootstrap_ci": {
        "pass": {"holdout_ci_lower_sortino": 0.10},
        "fail": {"holdout_ci_lower_sortino": -0.10},
        "none": {"holdout_ci_lower_sortino": None},
    },
    # r_edge has NO reachable "unknown" state once a record exists: confirm.py's own R-Edge logic
    # (replicated bit-for-bit in _clause_r_edge) resolves R_symbol=None to a deterministic False
    # (never provably an edge) and R_global=None to a deterministic True (no baseline to beat) —
    # see test_r_symbol_none_never_satisfies_r_edge / test_r_global_none_trivially_satisfies_r_edge.
    # Excluded from the generic pass/fail/none matrix below; covered by its own dedicated tests.
    "snapshot_drift": {
        "pass": {"data_snapshot_sha256": _SNAPSHOT},
        "fail": {"data_snapshot_sha256": "stale" * 8},
        "none": {"data_snapshot_sha256": None},
    },
    # Issue #1007 (Katalog #858) — neunte Klausel.
    "study_invariants_clean": {
        "pass": {"blocking_invariant_names": []},
        "fail": {"blocking_invariant_names": ["check_selection_statistic_availability"]},
        "none": {"blocking_invariant_names": None},
    },
    # Issue #1042 (Katalog #866, E-1) — zehnte Klausel.
    "cost_stress": {
        "pass": {"expectancy_cost_stress_2x": 0.001},
        "fail": {"expectancy_cost_stress_2x": -0.001},
        "none": {"expectancy_cost_stress_2x": None},
    },
    # Issue #1073 (Katalog #866-2) — elfte Klausel.
    "expectancy_outlier_robust": {
        "pass": {"holdout_expectancy_notional_weighted": 0.05, "holdout_expectancy_winsorized": 0.04},
        "fail": {"holdout_expectancy_notional_weighted": 0.05, "holdout_expectancy_winsorized": -0.01},
        "none": {"holdout_expectancy_notional_weighted": None, "holdout_expectancy_winsorized": None},
    },
}


@pytest.mark.parametrize("clause", sorted(_CLAUSE_VARIANTS))
@pytest.mark.parametrize("variant", ["pass", "fail", "none"])
def test_each_clause_pass_fail_none_24_cases(clause, variant):
    overrides = _CLAUSE_VARIANTS[clause][variant]
    decision = _evaluate(_passing_record(**overrides))
    expected = {"pass": True, "fail": False, "none": None}[variant]
    assert decision.clause_results[clause] is expected
    if variant == "pass":
        assert decision.admitted is True
    else:
        assert decision.admitted is False
        # r_edge's "fail" branch is a deterministic False (not None), all other clauses' "none"
        # variant is genuinely not evaluable — either way admitted must be False.
        assert decision.blocking_clause is not None


def test_promotion_record_exists_clause_pass_and_fail():
    # "pass": ein (beliebiger) Record ist vorhanden.
    assert _evaluate(_passing_record()).clause_results["promotion_record_exists"] is True
    # "fail": kein Record.
    assert _evaluate(None).clause_results["promotion_record_exists"] is False
    # promotion_record_exists ist strukturell nie ``None`` (ein Record existiert oder nicht) —
    # bewusst NICHT Teil der 3x8-Matrix oben, siehe DeploymentDecision-Docstring.


# --- check_deployment_gate_completeness (invariants.py) ----------------------------------------

def test_completeness_check_passes_trivially_on_empty_whitelist():
    result = check_deployment_gate_completeness({})
    assert result.passed is True


def test_completeness_check_passes_when_every_entry_has_full_clause_results():
    winner = {"deployment_gate": {"clause_results": {c: True for c in DEPLOYMENT_CLAUSES}}}
    result = check_deployment_gate_completeness({"SYM.ETORO": winner})
    assert result.passed is True


def test_completeness_check_fails_on_missing_deployment_gate_field():
    result = check_deployment_gate_completeness({"SYM.ETORO": {}})
    assert result.passed is False
    assert "SYM.ETORO" in result.actual


def test_completeness_check_fails_on_a_none_clause():
    clause_results = {c: True for c in DEPLOYMENT_CLAUSES}
    clause_results["pbo"] = None
    winner = {"deployment_gate": {"clause_results": clause_results}}
    result = check_deployment_gate_completeness({"SYM.ETORO": winner})
    assert result.passed is False
    assert result.actual["SYM.ETORO"] == ["pbo"]


# --- build_promotion_record_from_proposal -------------------------------------------------------

def test_build_promotion_record_from_proposal_flattens_nested_holdout_metrics():
    proposal = {
        "status": "READY_FOR_PR",
        "R_symbol": 1.2,
        "R_global": 0.3,
        "promotion_margin": 0.05,
        "data_snapshot_sha256": _SNAPSHOT,
        "holdout": {
            "symbol": {
                "deflated_dsr": 0.96,
                "oos_psr": 0.81,
                "holdout_ci_lower_sortino": 0.02,
                "pbo": 0.2,
                "pbo_n_configs": 30,
                "blocking_invariant_names": [],
                "oos_expectancy_cost_stress_2x": 0.001,
                "oos_expectancy": 0.05,
                "oos_expectancy_winsorized": 0.04,
            },
            "global": {},
        },
    }
    record = build_promotion_record_from_proposal(proposal, run_id="run_xyz")
    assert record["status"] == "READY_FOR_PR"
    assert record["deflated_dsr"] == 0.96
    assert record["oos_psr"] == 0.81
    assert record["holdout_ci_lower_sortino"] == 0.02
    assert record["pbo"] == 0.2
    assert record["pbo_n_configs"] == 30
    assert record["blocking_invariant_names"] == []
    assert record["expectancy_cost_stress_2x"] == 0.001
    assert record["holdout_expectancy_notional_weighted"] == 0.05
    assert record["holdout_expectancy_winsorized"] == 0.04
    assert record["data_snapshot_sha256"] == _SNAPSHOT
    assert record["run_id"] == "run_xyz"

    decision = evaluate_deployment_eligibility(
        ("Strat", "SYM.ETORO"), {("Strat", "SYM.ETORO"): record}, _CFG,
        current_snapshot_sha256=_SNAPSHOT)
    assert decision.admitted is True


def test_pair_lookup_accepts_string_and_tuple_form():
    record = _passing_record()
    by_tuple = evaluate_deployment_eligibility(
        ("Strat", "SYM.ETORO"), {("Strat", "SYM.ETORO"): record}, _CFG,
        current_snapshot_sha256=_SNAPSHOT)
    by_string = evaluate_deployment_eligibility(
        "Strat/SYM.ETORO", {"Strat/SYM.ETORO": record}, _CFG,
        current_snapshot_sha256=_SNAPSHOT)
    assert by_tuple.admitted is True
    assert by_string.admitted is True
