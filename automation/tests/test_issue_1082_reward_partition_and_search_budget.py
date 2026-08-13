"""Issue #1082 (Katalog #866-2, Kohorte E) — die Suche hat über 90 % ihres Budgets kein ordnendes
Ziel; ``reward_std_feasible`` ist keine eigene Messung.

Fix (b): ``reward_std_feasible`` und ``reward_std_total`` waren in 13 von 14 Studies des
#866-2-Referenzlaufs bit-identisch, weil kaum je ein Trial tatsächlich geprunt wurde
(``_feasible_reward_terms`` schliesst nur den Prune-Pfad aus, #949). ``check_reward_dynamic_range``
trägt seither zusätzlich ``reward_std_constraint_feasible`` (die echte #612-Sampler-Feasibility,
``oos_constraint_violations`` ≤ 0 in jeder Komponente) und den Diagnosecode
``REWARD_FEASIBLE_PARTITION_DEGENERATE``, wenn WEDER die alte noch die neue Kohorte einen
Unterschied zu ``reward_std_total`` zeigen. ``passed`` bleibt unverändert (#949-Regressionsschutz).

Fix (a): Studies unter der ``check_objective_branch_coverage``-Schwelle erscheinen als
``cross_study['search_budget_proposal']`` im Report (``report._search_budget_proposal_section``)
und werden von ``sweep._apply_search_budget_proposal`` im NÄCHSTEN Lauf automatisch über den
bestehenden #830-``'deprioritized'``-Pfad budget-reduziert — ohne eine bereits bestehende stärkere
Konsequenz (``'denylist'``/``'search_space_override'``) zu überschreiben.
"""
import json

from automation.optimizer import invariants as inv
from automation.optimizer import report as report_mod
from automation.optimizer import sweep


def _term(base=1.0, **kw):
    d = {"base": base, "divergence": 0.0, "dd_penalty": 0.0, "param_pen": 0.0, "turnover": 0.0,
        "fold_dispersion": 0.0, "tie_breaker": 0.0}
    d.update(kw)
    return d


def _trial(reward_terms, *, constraint_violations=None, oos_evaluated=True):
    d = {"oos_evaluated": oos_evaluated, "reward_terms": reward_terms}
    if constraint_violations is not None:
        d["oos_constraint_violations"] = constraint_violations
    return d


# ── #1082(b): _constraint_feasible_reward_terms / _reward_std_constraint_feasible ───────────────
def test_constraint_feasible_reward_terms_excludes_infeasible_and_untelemetered_trials():
    trials = [
        _trial(_term(base=1.0), constraint_violations=(0.0,)),    # feasible (== 0)
        _trial(_term(base=1.2), constraint_violations=(-0.05,)),  # feasible (< 0)
        _trial(_term(base=5.0), constraint_violations=(0.4,)),    # infeasible (> 0)
        _trial(_term(base=9.0)),                                  # keine Telemetrie -> exkludiert
    ]
    feasible = inv._constraint_feasible_reward_terms(trials)
    assert len(feasible) == 2
    assert all(t["base"] in (1.0, 1.2) for t in feasible)


def test_reward_std_constraint_feasible_none_below_two_trials():
    trials = [_trial(_term(base=1.0), constraint_violations=(0.0,))]
    assert inv._reward_std_constraint_feasible(trials) is None


# ── #1082(b): check_reward_dynamic_range — neues Feld + Degenerations-Diagnose ───────────────────
def test_reward_feasible_partition_degenerate_fires_without_any_constraint_telemetry():
    """Referenzbefund: kein Trial trägt ``oos_constraint_violations`` UND kein Trial ist geprunt ->
    ``reward_std_total == reward_std_feasible`` bit-identisch UND die constraint-basierte Kohorte
    liefert (mangels Telemetrie) ebenfalls kein eigenes Signal -> Diagnosecode feuert."""
    import random
    rng = random.Random(7)
    trials = [_trial(_term(base=rng.uniform(0.5, 2.0), param_pen=rng.uniform(0.01, 0.05)))
             for _ in range(20)]
    result = inv.check_reward_dynamic_range(trials)
    assert result.actual["reward_std_total"] == result.actual["reward_std_feasible"]
    assert result.actual["reward_std_constraint_feasible"] is None
    assert result.actual["reward_feasible_partition_degenerate"] is True
    assert "REWARD_FEASIBLE_PARTITION_DEGENERATE" in result.detail


def test_reward_feasible_partition_not_degenerate_when_constraint_cohort_differs_adxatr_like():
    """AdxAtr-Analogon aus dem #866-2-Katalog: viele Trials insgesamt, aber nur eine Handvoll
    tatsächlich #612-feasible -> ``reward_std_constraint_feasible`` unterscheidet sich messbar von
    ``reward_std_total``, obwohl ``reward_std_feasible`` (nur Prune-Ausschluss) weiterhin identisch
    bleibt — die Akzeptanzkriterium-Alternative ("unterscheidet sich messbar") greift hier."""
    import random
    rng = random.Random(11)
    trials = [
        _trial(_term(base=rng.uniform(0.5, 2.0), param_pen=rng.uniform(0.01, 0.05)),
               constraint_violations=(0.9,))
        for _ in range(132)
    ]
    trials += [
        _trial(_term(base=v), constraint_violations=(0.0,))
        for v in (1.0, 1.05, 0.98, 1.02)
    ]
    result = inv.check_reward_dynamic_range(trials)
    assert result.actual["reward_std_total"] == result.actual["reward_std_feasible"]
    assert result.actual["reward_std_constraint_feasible"] is not None
    assert result.actual["reward_std_constraint_feasible"] != result.actual["reward_std_total"]
    assert result.actual["reward_feasible_partition_degenerate"] is False


def test_reward_dynamic_range_passed_unaffected_by_new_diagnostic_field():
    """Regressionsschutz: das neue Feld darf ``passed`` nicht verändern (#949-Schutz bleibt intakt,
    siehe test_issue_949_reward_dynamic_range.test_pruned_trials_never_cause_a_false_positive_
    dominance_failure_alone)."""
    import random
    rng = random.Random(3)
    trials = [_trial(_term(base=rng.uniform(0.5, 2.0), param_pen=rng.uniform(0.01, 0.05)))
             for _ in range(15)]
    result = inv.check_reward_dynamic_range(trials)
    assert result.passed is True
    assert result.actual["reward_feasible_partition_degenerate"] is True


# ── #1082(a): report._search_budget_proposal_section ────────────────────────────────────────────
def test_search_budget_proposal_section_collects_failing_branch_coverage_studies():
    checks = [
        ("AdxAtrMomentumStrategy/TSLA.ETORO",
         inv.InvariantResult(name="check_objective_branch_coverage", passed=False,
                             expected=">= 0.10", actual=0.0286, detail="FAIL")),
        ("DonchianRegimeBreakoutStrategy/TSLA.ETORO",
         inv.InvariantResult(name="check_objective_branch_coverage", passed=True,
                             expected=">= 0.10", actual=0.42, detail="OK")),
        ("global", inv.InvariantResult(name="check_gate_inventory_coherence", passed=True,
                                       expected="...", actual=None, detail="OK")),
    ]
    section = report_mod._search_budget_proposal_section(checks)
    assert len(section) == 1
    assert section[0]["strategy"] == "AdxAtrMomentumStrategy"
    assert section[0]["symbol"] == "TSLA.ETORO"
    assert section[0]["objective_branch_coverage_fraction"] == 0.0286


def test_search_budget_proposal_section_empty_when_all_pass():
    checks = [
        ("A/X", inv.InvariantResult(name="check_objective_branch_coverage", passed=True,
                                    expected=">= 0.10", actual=0.5, detail="OK")),
    ]
    assert report_mod._search_budget_proposal_section(checks) == []


# ── #1082(a): sweep._apply_search_budget_proposal ────────────────────────────────────────────────
def test_apply_search_budget_proposal_writes_deprioritized_cache_entries(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "report_1.json").write_text(json.dumps({
        "cross_study": {"search_budget_proposal": [
            {"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
             "objective_branch_coverage_fraction": 0.0286},
            {"strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
             "objective_branch_coverage_fraction": 0.0278},
        ]},
    }), encoding="utf-8")
    monkeypatch.setattr(report_mod, "REPORTS_DIR", reports_dir)

    work_dir = tmp_path / "work"
    applied = sweep._apply_search_budget_proposal(work_dir=work_dir, run_id="run-1082")

    assert sorted(applied) == [
        ("AdxAtrMomentumStrategy", "TSLA.ETORO"), ("SqueezeBreakoutStrategy", "TSLA.ETORO"),
    ]
    cache = sweep.load_diagnosed_pairs_cache(work_dir=work_dir)
    entry = cache[("AdxAtrMomentumStrategy", "TSLA.ETORO")]
    assert entry["action"] == "deprioritized"
    assert entry["binding_cause"] == "objective_branch_coverage_low"


def test_apply_search_budget_proposal_never_overrides_a_stronger_existing_consequence(
        tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "report_1.json").write_text(json.dumps({
        "cross_study": {"search_budget_proposal": [
            {"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
             "objective_branch_coverage_fraction": 0.0286},
        ]},
    }), encoding="utf-8")
    monkeypatch.setattr(report_mod, "REPORTS_DIR", reports_dir)

    work_dir = tmp_path / "work"
    sweep.record_diagnosed_pair({
        "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO", "action": "denylist",
        "binding_cause": "signal_absent",
    }, work_dir=work_dir, run_id="run-0")

    applied = sweep._apply_search_budget_proposal(work_dir=work_dir, run_id="run-1082")
    assert applied == []
    cache = sweep.load_diagnosed_pairs_cache(work_dir=work_dir)
    assert cache[("AdxAtrMomentumStrategy", "TSLA.ETORO")]["action"] == "denylist"


def test_apply_search_budget_proposal_empty_without_a_report(tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "REPORTS_DIR", tmp_path / "nonexistent")
    assert sweep._apply_search_budget_proposal(work_dir=tmp_path / "work") == []
