"""Issue #1104 (Katalog #937) — ``run_id``-Präfix und ``git_commit`` divergieren im
3910e12b-Referenzlauf (``run_id``-Präfix ``3910e12b``, aber ``git_commit = 'b48024c4'``): eine
EINZIGE ``git_commit()``-Lesung zur Berichtszeit trug bislang beide Bedeutungen ("worauf lief die
Simulation" vs. "worauf wurde DIESER Report gebaut") unter demselben Feldnamen.

Fix: zwei getrennte Pflichtfelder — ``git_commit_simulation`` (Study-User-Attr, vor dem ersten
Trial gestempelt, run_optimization.py) und ``git_commit_report`` (report.py, zur Berichtszeit
gelesen). ``run_id`` (log_manager.default_run_id) trägt seither KEINEN Commit-Bezug mehr (ein
kollisionsfreier Zufallstoken statt des Commit-Präfix). Neue Invariante
``invariants.check_commit_coherence`` FAILt, wenn beide Commits bekannt sind UND divergieren.
"""
import json

import optuna

from automation.optimizer import invariants as inv
from automation.optimizer import report


# ── invariants.check_commit_coherence ────────────────────────────────────────────────────────────
def test_passes_when_commits_match():
    result = inv.check_commit_coherence("b48024c4", "b48024c4")
    assert result.passed is True
    assert result.inconclusive is False


def test_fails_matching_937_reference_symptom():
    """3910e12b-Referenzlauf: die Simulation lief auf 3910e12b, der Report wurde auf b48024c4
    gebaut (nachtraeglich regeneriert, --report-only nach einem git pull)."""
    result = inv.check_commit_coherence("3910e12b", "b48024c4")
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual == {
        "git_commit_simulation": "3910e12b", "git_commit_report": "b48024c4",
    }


def test_inconclusive_when_simulation_commit_unknown():
    """Legacy-Study vor #1104 -- kein git_commit_simulation-Stempel, kein Urteil ohne
    Vergleichsgrundlage (kein stiller FAIL)."""
    result = inv.check_commit_coherence(None, "b48024c4")
    assert result.passed is True
    assert result.inconclusive is True
    assert result.actual is None


def test_inconclusive_when_report_commit_unknown():
    result = inv.check_commit_coherence("b48024c4", None)
    assert result.passed is True
    assert result.inconclusive is True


# ── End-to-End: report.generate_sweep_report ─────────────────────────────────────────────────────
def _make_study(storage_url: str, study_name: str, *, git_commit_simulation: str, n: int = 3):
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                load_if_exists=True)
    study.set_user_attr("git_commit_simulation", git_commit_simulation)
    for i in range(n):
        trial = study.ask()
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", i < 2)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("oos_gate_deltas", {})
        trial.set_user_attr("reward_terms", {
            "branch": "eligible" if i < 2 else "unevaluable",
            "base": 1.0, "divergence": 0.0, "dd_penalty": 0.0, "param_pen": 0.0,
            "turnover": 0.0, "fold_dispersion": 0.0, "tie_breaker": 0.0,
        })
        study.tell(trial, float(i))
    return study


def _proposal() -> dict:
    return {
        "strategy": "TestStrat", "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": {
            "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
            "deflation_n_eligible": 2, "deflation_n_family": 2,
            "deflation_n_family_effective": 2, "deflation_n_effective": 2,
        }},
    }


def test_report_carries_both_commit_fields_and_they_can_diverge(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO", git_commit_simulation="3910e12b")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    monkeypatch.setattr(report, "git_commit", lambda: "b48024c4")

    out_path = report.generate_sweep_report(
        [_proposal()], run_id="testrun-937",
        started_at_utc="2026-08-14T00:00:00.000+00:00", wallclock_s=1,
        cli_args={"strategies": "TestStrat", "tier": "all", "symbols": "A.ETORO"},
        reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    assert data["git_commit_simulation"] == "3910e12b"
    assert data["git_commit_report"] == "b48024c4"
    assert "git_commit" not in data
    assert "3910e12b" not in data["run_id"]
    assert "b48024c4" not in data["run_id"]

    commit_check = next(c for c in data["invariant_checks"] if c["name"] == "check_commit_coherence")
    assert commit_check["passed"] is False
    assert commit_check["severity"] == "high"


def test_report_commit_coherence_passes_when_commits_match(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO", git_commit_simulation="b48024c4")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    monkeypatch.setattr(report, "git_commit", lambda: "b48024c4")

    out_path = report.generate_sweep_report(
        [_proposal()], run_id="testrun-937b",
        started_at_utc="2026-08-14T00:00:00.000+00:00", wallclock_s=1,
        cli_args={"strategies": "TestStrat", "tier": "all", "symbols": "A.ETORO"},
        reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    commit_check = next(c for c in data["invariant_checks"] if c["name"] == "check_commit_coherence")
    assert commit_check["passed"] is True
