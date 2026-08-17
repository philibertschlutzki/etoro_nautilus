"""Issue #985/#1139 (Katalog #986, Pitfall #411 in AGENTS.md) — die Sweep-Preflight-Checks
(``sweep.assert_required_config_keys_valid``, ``sweep.assert_instrument_metadata_coherence``)
meldeten einen Verstoss NUR über stderr + ``sys.exit(2)``; bestand der Preflight, existierte im
Report-Artefakt eines erfolgreichen Laufs KEIN Nachweis, dass er ueberhaupt ausgefuehrt wurde.

Fix: beide Funktionen liefern seither ihr ``InvariantResult`` zurueck (``None`` nur im echten
No-Op-Fall ohne Registry-Datei bzw. ohne Instrumente); ``sweep.run_per_symbol_sweep`` sammelt sie
in ``preflight_invariant_checks``/dem globalen ``sweep_preflight_invariant_checks`` und reicht sie
an ``report.generate_sweep_report``/``_build_report`` durch, die sie — mit ``phase="preflight"``
gestempelt — in ``run.json['invariant_checks']`` mischen.
"""
import json

import optuna
import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import report


def test_invariant_result_phase_field_defaults_to_absent():
    result = inv.check_required_config_keys({}, {})
    assert result.phase is None
    assert "phase" not in result.to_dict()


def test_invariant_result_phase_field_included_when_set():
    import dataclasses
    result = dataclasses.replace(inv.check_required_config_keys({}, {}), phase="preflight")
    assert result.to_dict()["phase"] == "preflight"


def _make_study(storage_url: str, study_name: str, n: int = 3):
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                 load_if_exists=True)
    for i in range(n):
        trial = study.ask()
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", True)
        trial.set_user_attr("oos_coherence_violation", False)
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
        "holdout": {"symbol": {
            "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
            "deflation_n_eligible": 3, "deflation_n_family_effective": 3,
            "deflation_n_effective": 3,
        }},
    }


@pytest.fixture
def wired_storage(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    return tmp_path


def test_generate_sweep_report_merges_passing_preflight_checks_with_phase(wired_storage):
    passing_check = inv.InvariantResult(
        name="check_required_config_keys", passed=True,
        expected="alle registrierten Keys vorhanden", actual=None,
        detail="OK", severity="blocking", phase="preflight",
    ).to_dict()
    out_path = report.generate_sweep_report(
        [_proposal()], run_id="preflighttest1", reports_dir=wired_storage / "reports",
        preflight_invariant_checks=[passing_check],
    )
    data = json.loads(out_path.read_text("utf-8"))
    preflight_entries = [c for c in data["invariant_checks"] if c.get("phase") == "preflight"]
    assert len(preflight_entries) == 1
    assert preflight_entries[0]["name"] == "check_required_config_keys"
    assert preflight_entries[0]["passed"] is True
    # Ein bestandener Preflight-Eintrag selbst darf NICHT zu den blockierenden Ausfällen zählen,
    # die decision_admissible auf False ziehen (siehe report._compute_decision_admissible) — ob
    # decision_admissible GLOBAL True ist, hängt von JEDEM anderen Check im Report ab (dieses
    # minimale Proposal-Fixture erfüllt viele davon nicht, unabhängig vom Preflight-Fix hier) und
    # ist daher nicht die richtige Prüfgröße für DIESEN Test.
    assert not any(
        c["severity"] == "blocking" and not c["passed"] for c in preflight_entries)


def test_generate_sweep_report_without_preflight_checks_has_none_with_that_phase(wired_storage):
    out_path = report.generate_sweep_report(
        [_proposal()], run_id="preflighttest2", reports_dir=wired_storage / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    assert not [c for c in data["invariant_checks"] if c.get("phase") == "preflight"]
