"""Issue #742 — Sweep-Level-Report-Generator, atomar geschrieben.

Deckt ab:
  - der Report validiert gegen das (Auszugs-)Schema aus der Issue-Beschreibung
  - der Report ist atomar sichtbar (kein ``.tmp``-Rest im Ergebnis-Verzeichnis)
  - ein zweiter, unabhängiger Prozess (``generate_report_for_run``) rekonstruiert denselben
    Report AUSSCHLIESSLICH aus SQLite-Study + Proposal-JSON (Determinismus, kein Live-Lauf nötig)
"""
import json

import optuna
import pytest

from automation.optimizer import report


def _make_study(storage_url: str, study_name: str, n: int = 5):
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                load_if_exists=True)
    for i in range(n):
        trial = study.ask()
        eligible = i < 3
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", eligible)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("oos_gate_deltas", {"min_trades": i - 2})
        trial.set_user_attr("reward_terms", {
            "branch": "eligible" if eligible else "unevaluable",
            "base": 1.0 + i, "divergence": 0.3 * i, "dd_penalty": 0.25 * i, "param_pen": 0.2 * i,
            "turnover": 0.3 * i, "fold_dispersion": 0.25 * i, "tie_breaker": 0.2 * i,
        })
        study.tell(trial, float(i))
    return study


def _proposal() -> dict:
    return {
        "strategy": "TestStrat",
        "symbol": "A.ETORO",
        "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {
            "symbol": {
                "deflated_sr0": 0.1,
                "deflated_dsr": 0.8,
                "deflation_dsr_z": 1.2,
                "deflation_n_eligible": 3,
                "deflation_n_family_effective": 3,
                "deflation_n_effective": 3,
            },
        },
    }


@pytest.fixture
def wired_storage(tmp_path, monkeypatch):
    """Monkeypatcht report.resolve_storage auf eine tmp_path-lokale SQLite-Datei und legt eine
    Study mit realistischen user_attrs an."""
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    study_name = "study_TestStrat_A_ETORO"
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, study_name)
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    return tmp_path


def test_report_schema_and_atomic_write(wired_storage):
    reports_dir = wired_storage / "reports"
    out_path = report.generate_sweep_report(
        [_proposal()], run_id="testrun1", started_at_utc="2026-07-19T00:00:00.000+00:00",
        wallclock_s=42, cli_args={"strategies": "TestStrat", "tier": "all", "symbols": "A.ETORO"},
        reports_dir=reports_dir,
    )

    assert out_path.exists()
    assert out_path.name == "run_testrun1.json"
    assert not list(reports_dir.glob("*.tmp")), "keine Teil-/Tempdatei darf im Ergebnis verbleiben"

    data = json.loads(out_path.read_text("utf-8"))
    assert data["report_schema_version"] == 1
    assert data["run_id"] == "testrun1"
    assert data["wallclock_s"] == 42
    assert data["cli_args"]["strategies"] == "TestStrat"
    assert "git_commit" in data and "tournament_config_sha256" in data

    assert len(data["studies"]) == 1
    rec = data["studies"][0]
    assert rec["symbol"] == "A.ETORO"
    assert rec["strategy"] == "TestStrat"
    assert rec["n_trials"] == 5
    assert rec["n_eligible"] == 3
    assert rec["n_evaluable"] == 5
    assert rec["promotion_outcome"] == "REJECTED_ON_HOLDOUT"

    # Issue #785 — decision_chain attribuiert die Ablehnung praezise auf die STUFE, die sie
    # tatsaechlich verursacht hat (REJECT_HOLDOUT_DSR_DROP ⇒ 'deflation', nicht pauschal
    # 'confirm_or_selection' fuer JEDE Holdout-Ablehnung wie vor #785); is_gate ist hier bestanden
    # (n_eligible=3 > 0), erscheint also nicht mehr in der abgeleiteten rejection_chain-Sicht.
    stages = [c["stage"] for c in rec["rejection_chain"]]
    assert stages == ["deflation"]
    assert rec["rejection_chain"][0]["detail"] == "REJECT_HOLDOUT_DSR_DROP"

    decision_stages = {c["stage"]: c["passed"] for c in rec["decision_chain"]}
    assert decision_stages["is_gate"] is True
    assert decision_stages["confirm_or_selection"] is True
    assert decision_stages["holdout"] is True
    assert decision_stages["deflation"] is False

    assert "cross_study" in data and "n_family" in data["cross_study"]
    assert isinstance(data["invariant_checks"], list) and data["invariant_checks"]
    assert any(c["name"] == "check_config_key_registry" for c in data["invariant_checks"])
    assert any(c["name"] == "check_sr0_coherence" for c in data["invariant_checks"])


def test_second_process_reconstructs_same_report_from_sqlite_and_proposals(wired_storage):
    """Determinismus-Akzeptanzkriterium: ein Report kann nachträglich, standalone, ohne laufenden
    Sweep, ausschliesslich aus den durablen Artefakten (SQLite-Study + Proposal-JSON)
    rekonstruiert werden — bit-identische studies/cross_study/invariant_checks."""
    live_out = report.generate_sweep_report(
        [_proposal()], run_id="live_run", started_at_utc="2026-07-19T00:00:00.000+00:00",
        wallclock_s=10, cli_args={"strategies": "TestStrat"}, reports_dir=wired_storage / "reports",
    )
    live_data = json.loads(live_out.read_text("utf-8"))

    proposals_dir = wired_storage / "proposals"
    proposals_dir.mkdir()
    (proposals_dir / "proposal_TestStrat_A.ETORO.json").write_text(
        json.dumps(_proposal()), encoding="utf-8")

    standalone_out = report.generate_report_for_run(
        run_id="standalone_run", proposals_dir=proposals_dir, reports_dir=wired_storage / "reports",
    )
    standalone_data = json.loads(standalone_out.read_text("utf-8"))

    assert standalone_data["studies"] == live_data["studies"]
    assert standalone_data["cross_study"] == live_data["cross_study"]
    assert standalone_data["invariant_checks"] == live_data["invariant_checks"]
    # Nur der Lauf-Kontext (run_id/wallclock/cli_args) darf legitim divergieren.
    assert standalone_data["run_id"] != live_data["run_id"]


def test_generate_report_for_run_ignores_non_symbol_proposals(wired_storage):
    """proposal_{strategy}.json (kein 'symbol'-Feld, export_no_viable_proposal/export_proposal)
    muss von der Standalone-Discovery uebersprungen werden."""
    proposals_dir = wired_storage / "proposals"
    proposals_dir.mkdir()
    (proposals_dir / "proposal_TestStrat_A.ETORO.json").write_text(
        json.dumps(_proposal()), encoding="utf-8")
    (proposals_dir / "proposal_GlobalStrat.json").write_text(
        json.dumps({"strategy": "GlobalStrat", "status": "NO_VIABLE_TRIAL"}), encoding="utf-8")

    out_path = report.generate_report_for_run(
        run_id="mixed_run", proposals_dir=proposals_dir, reports_dir=wired_storage / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    assert len(data["studies"]) == 1
    assert data["studies"][0]["strategy"] == "TestStrat"
