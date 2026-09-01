"""Issue #1021/#1196 — der Kohorten-Waechter in ``report.py`` verwechselte Store-Wiederverwendung
(sequenzieller Warm-Start, Optuna ``load_if_exists``, der beabsichtigte Normalbetrieb) mit echter
Nebenlaeufigkeit (#1086, zwei Sweep-Prozesse gleichzeitig auf demselben Store). Der Diskriminator
ist jetzt, ob sich die tatsaechlichen Laufzeitfenster der beteiligten ``run_id``s UEBERLAPPEN, nicht
mehr die blosse Anwesenheit einer zweiten ``run_id`` in derselben Study.

Deckt ab:
  - zwei SEQUENZIELLE Sweeps (kein Zeitfenster-Overlap) auf demselben Store erzeugen BEIDE einen
    vollstaendigen Report; der zweite Report zaehlt ausschliesslich seine eigenen Trials
    (Akzeptanzkriterium 1/2/7).
  - ``cross_study.store_reuse`` benennt den Vorlauf (Akzeptanzkriterium 3).
  - zwei UEBERLAPPENDE Sweeps loesen weiterhin ``ReportCohortUnresolvable`` (Subklasse von
    ``RuntimeError``, bestehende ``pytest.raises(RuntimeError, ...)``-Tests bleiben gueltig) aus,
    fatal, mit den ueberlappenden Zeitfenstern im Text (Akzeptanzkriterium 4).
  - ``invariants.check_report_artifact_written`` failt auf einem nachgestellten Lauf ohne Report
    (Akzeptanzkriterium 6).
"""
import json
import time

import optuna
import pytest

from automation.optimizer import invariants, report
from automation.optimizer._contracts import ReportCohortUnresolvable


def _make_sequential_trials(storage_url: str, study_name: str, *, run_id: str, n: int = 5) -> None:
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                 load_if_exists=True)
    for i in range(n):
        trial = study.ask()
        eligible = i < 3
        trial.set_user_attr("run_id", run_id)
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", eligible)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("reward_terms", {
            "branch": "eligible" if eligible else "unevaluable",
            "base": 1.0 + i, "divergence": 0.1, "dd_penalty": 0.1, "param_pen": 0.1,
            "turnover": 0.1, "fold_dispersion": 0.1, "tie_breaker": 0.1,
        })
        study.tell(trial, float(i))


def _proposal(strategy: str, symbol: str) -> dict:
    return {
        "strategy": strategy,
        "symbol": symbol,
        "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {
            "symbol": {
                "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
                "deflation_n_eligible": 3, "deflation_n_family_effective": 3,
                "deflation_n_effective": 3,
            },
        },
    }


def test_sequential_store_reuse_second_run_reports_only_own_trials(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    study_name = "study_TestStrat_TSLA_ETORO"
    storage_url = f"sqlite:///{sweep_dir / 'shared.db'}"

    # Lauf 1: 5 Trials, run_id="run_1".
    _make_sequential_trials(storage_url, study_name, run_id="run_1", n=5)
    # Echte Wanduhr-Luecke, damit Lauf 2s Trial-Zeitfenster nachweislich NACH Lauf 1s endet
    # (dieselbe Konstruktion wie #1021s Beweis: 455ms Abstand, keine Ueberlappung).
    time.sleep(0.2)
    # Lauf 2: 5 weitere Trials auf DEMSELBEN Store (load_if_exists-Warm-Start), run_id="run_2".
    _make_sequential_trials(storage_url, study_name, run_id="run_2", n=5)

    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)

    proposal = _proposal("TestStrat", "TSLA.ETORO")
    out_path = report.generate_sweep_report(
        [proposal], run_id="run_2",
        started_at_utc="2026-08-18T15:13:33.000+00:00",
        wallclock_s=2411, cli_args={"strategies": "TestStrat"}, reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))

    # Akzeptanzkriterium 1/2 — voller Report, ausschliesslich Lauf 2s eigene 5 Trials gezaehlt,
    # nicht die kumulierten 10 des Stores.
    assert len(data["studies"]) == 1
    assert data["studies"][0]["n_trials"] == 5
    assert data["studies_excluded_foreign_run"] == []

    # Akzeptanzkriterium 3 — store_reuse benennt den Vorlauf.
    store_reuse = data["cross_study"]["store_reuse"]
    assert store_reuse["reused"] is True
    assert store_reuse["prior_run_ids"] == ["run_1"]
    assert store_reuse["n_trials_prior"] == 5
    assert store_reuse["n_trials_own"] == 5
    assert store_reuse["studies_affected"] == 1
    assert store_reuse["warm_start_effective"] is True


def test_overlapping_run_ids_raise_fatal_with_windows_in_message(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    study_name = "study_TestStrat_A_ETORO"
    storage_url = f"sqlite:///{sweep_dir / 'mixed.db'}"

    # Zwei run_ids, deren Trials INTERLEAVED (ohne Wanduhr-Luecke) entstehen — ihre Zeitfenster
    # ueberlappen nachweislich (echte Nebenlaeufigkeit, #1086).
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                 load_if_exists=True)
    for i in range(6):
        trial = study.ask()
        run_id = "run_a" if i % 2 == 0 else "run_b"
        trial.set_user_attr("run_id", run_id)
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", i < 3)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("reward_terms", {
            "branch": "eligible", "base": 1.0, "divergence": 0.1, "dd_penalty": 0.1,
            "param_pen": 0.1, "turnover": 0.1, "fold_dispersion": 0.1, "tie_breaker": 0.1,
        })
        study.tell(trial, float(i))

    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)

    with pytest.raises(ReportCohortUnresolvable, match=r"REPORT_COHORT_UNRESOLVABLE") as excinfo:
        report.generate_sweep_report(
            [_proposal("TestStrat", "A.ETORO")], run_id="run_a",
            started_at_utc="2026-08-13T15:45:00.000+00:00",
            wallclock_s=100, cli_args={}, reports_dir=tmp_path / "reports",
        )
    # Die Ausnahme ist weiterhin ein RuntimeError (bestehende Konsumenten, die generisch auf
    # RuntimeError matchen, bleiben unveraendert funktionsfaehig).
    assert isinstance(excinfo.value, RuntimeError)
    assert "run_b" in str(excinfo.value)
    assert "ueberlappen" in str(excinfo.value)


def test_check_report_artifact_written_fails_on_complete_without_report():
    fail = invariants.check_report_artifact_written(run_status="complete", report_written=False)
    assert fail.passed is False
    assert fail.severity == "blocking"

    ok_written = invariants.check_report_artifact_written(run_status="complete", report_written=True)
    assert ok_written.passed is True

    # Issue #1347 (GH #1241, P3) — 'aborted_no_report' ist ein TERMINALER Status (nicht mehr
    # pauschal "nicht anwendbar" wie vor diesem Fix): report_written=False FAILt hier jetzt
    # ebenso blockierend wie bei 'complete', statt stillschweigend als PASS durchzugehen.
    aborted_without_report = invariants.check_report_artifact_written(
        run_status="aborted_no_report", report_written=False)
    assert aborted_without_report.passed is False
    assert aborted_without_report.severity == "blocking"

    # NUR der explizite In-Progress-Marker bleibt "nicht anwendbar" — und traegt seit #1347
    # passed=None (Tri-State), nie mehr passed=True.
    not_applicable = invariants.check_report_artifact_written(
        run_status="in_progress", report_written=False)
    assert not_applicable.passed is None
    assert not_applicable.inconclusive is True
