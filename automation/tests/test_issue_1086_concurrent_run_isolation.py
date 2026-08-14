"""Issue #1086 (Katalog #919) — Kohorten-Isolation ueber ``run_id``, nicht Startzeit-Heuristik.

Root-Cause: mehrere gleichzeitig laufende Sweep-Prozesse (je ein Prozess pro ``--symbols``) teilen
sich denselben ``{WORK}/sweep/``-Store; ``generate_report_for_run`` (Abbruch-/Standalone-Pfad)
entdeckt ALLE ``proposal_*.json`` in ``WORK``, unabhaengig davon, welcher Prozess sie geschrieben
hat. Der ``run_id``-Trial-Stempel (#1015) ist der PRIMAERE, robuste Diskriminator; die
Zeitfenster-Heuristik aus #1023 (siehe ``test_issue_742_sweep_report.py``) bleibt nur noch die
sekundaere Pruefung fuer Studies OHNE jeden ``run_id``-Stempel (Legacy).

Deckt ab:
  - zwei Studies mit verschiedenen ``run_id``-Trial-Stempeln im selben Store ⇒ jeder Report sieht
    genau seine eigene (Akzeptanzkriterium #1086).
  - ``studies_excluded_foreign_run`` traegt den Namen UND die fremde ``run_id`` der ausgeschlossenen
    Study, nicht nur einen Zaehler.
  - eine Study mit VERMISCHTEN ``run_id``-Trials (zwei Laeufe haben dieselbe Study gleichzeitig
    angefasst) bricht den Report mit ``REPORT_COHORT_UNRESOLVABLE`` ab, statt ein Urteil auf
    vermischter Evidenz zu faellen.
  - ``sweep._acquire_sweep_run_lock``/``_release_sweep_run_lock``: ein zweiter Prozess mit
    abweichender ``run_id`` wird mit ``SWEEP_CONCURRENT_RUN_DETECTED`` abgewiesen; derselbe
    ``run_id`` (Resume) und ein verwaister (toter) Halter werden akzeptiert.
"""
import json
import os

import optuna
import pytest

from automation.optimizer import report, sweep


def _make_study(storage_url: str, study_name: str, run_id: str | None = None,
                 mixed_run_id: str | None = None, n: int = 5):
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
        # Issue #1086 — die letzte Trial dieser Study traegt ggf. eine ANDERE run_id (simuliert
        # zwei Laeufe, die dieselbe Study gleichzeitig ohne Lock-Datei angefasst haben).
        if mixed_run_id is not None and i == n - 1:
            trial.set_user_attr("run_id", mixed_run_id)
        elif run_id is not None:
            trial.set_user_attr("run_id", run_id)
        study.tell(trial, float(i))
    return study


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


def test_concurrent_sweeps_each_see_only_their_own_cohort(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()

    own_study_name = "study_TestStrat_TSLA_ETORO"
    own_storage_url = f"sqlite:///{sweep_dir / 'own.db'}"
    _make_study(own_storage_url, own_study_name, run_id="run_tsla")

    foreign_study_name = "study_TestStrat_NVDA_ETORO"
    foreign_storage_url = f"sqlite:///{sweep_dir / 'foreign.db'}"
    _make_study(foreign_storage_url, foreign_study_name, run_id="run_nvda")

    def _resolve(*, study_name, base_cfg=None):
        return own_storage_url if study_name == own_study_name else foreign_storage_url

    monkeypatch.setattr(report, "resolve_storage", _resolve)

    own_proposal = _proposal("TestStrat", "TSLA.ETORO")
    foreign_proposal = _proposal("TestStrat", "NVDA.ETORO")

    out_path = report.generate_sweep_report(
        [own_proposal, foreign_proposal], run_id="run_tsla",
        started_at_utc="2026-08-13T15:45:00.000+00:00",
        wallclock_s=2880, cli_args={"strategies": "TestStrat"}, reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))

    symbols_in_report = {s["symbol"] for s in data["studies"]}
    assert symbols_in_report == {"TSLA.ETORO"}

    assert len(data["studies_excluded_foreign_run"]) == 1
    excluded = data["studies_excluded_foreign_run"][0]
    assert excluded["symbol"] == "NVDA.ETORO"
    assert excluded["run_id_found"] == "run_nvda"
    assert excluded["reason"] == "run_id_mismatch"
    assert excluded["study_name"] == foreign_study_name

    # Der andere Prozess (run_id="run_nvda") sieht spiegelbildlich NUR seine eigene Kohorte.
    out_path_2 = report.generate_sweep_report(
        [own_proposal, foreign_proposal], run_id="run_nvda",
        started_at_utc="2026-08-13T15:51:00.000+00:00",
        wallclock_s=2880, cli_args={"strategies": "TestStrat"}, reports_dir=tmp_path / "reports2",
    )
    data_2 = json.loads(out_path_2.read_text("utf-8"))
    symbols_in_report_2 = {s["symbol"] for s in data_2["studies"]}
    assert symbols_in_report_2 == {"NVDA.ETORO"}


def test_mixed_run_id_evidence_within_one_study_fails_loud(tmp_path, monkeypatch):
    """Eine Study, die Trials von ZWEI verschiedenen run_ids traegt (gleichzeitiger Zugriff ohne
    Lock-Datei), kann keinem der beiden Laeufe sauber zugeordnet werden."""
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'mixed.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO", run_id="run_a", mixed_run_id="run_b")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)

    with pytest.raises(RuntimeError, match=r"REPORT_COHORT_UNRESOLVABLE"):
        report.generate_sweep_report(
            [_proposal("TestStrat", "A.ETORO")], run_id="run_a",
            started_at_utc="2026-08-13T15:45:00.000+00:00",
            wallclock_s=100, cli_args={}, reports_dir=tmp_path / "reports",
        )


def test_lock_rejects_second_process_with_different_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    sweep._acquire_sweep_run_lock("run_a")
    with pytest.raises(RuntimeError, match=r"SWEEP_CONCURRENT_RUN_DETECTED"):
        sweep._acquire_sweep_run_lock("run_b")


def test_lock_allows_resume_with_same_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    sweep._acquire_sweep_run_lock("run_a")
    sweep._acquire_sweep_run_lock("run_a")  # Resume darf sich nicht selbst aussperren.
    lock_path = tmp_path / "sweep" / ".run.lock"
    assert json.loads(lock_path.read_text("utf-8"))["run_id"] == "run_a"


def test_lock_reclaimed_from_dead_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    lock_path = tmp_path / "sweep"
    lock_path.mkdir()
    # Ein PID, der mit an Sicherheit grenzender Wahrscheinlichkeit nicht existiert.
    dead_pid = 2**30
    (lock_path / ".run.lock").write_text(
        json.dumps({"run_id": "run_dead", "pid": dead_pid, "acquired_at_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    sweep._acquire_sweep_run_lock("run_new")
    assert json.loads((lock_path / ".run.lock").read_text("utf-8"))["run_id"] == "run_new"


def test_lock_release_only_own_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    sweep._acquire_sweep_run_lock("run_a")
    sweep._release_sweep_run_lock("run_b")  # fremde run_id -- darf nicht loeschen
    assert (tmp_path / "sweep" / ".run.lock").exists()
    sweep._release_sweep_run_lock("run_a")
    assert not (tmp_path / "sweep" / ".run.lock").exists()
