"""Issue #686 — Bulk-Purge-Werkzeug für Studies mit stale reward_semantics_version.

Ergänzt den bereits vorhandenen In-Process-Guard (run_optimization._check_reward_semantics_version)
um ein Werkzeug, das ALLE betroffenen {WORK}/sweep/*.db-Dateien in EINEM Schritt bereinigt, statt N
einzelne Sweep-Starts nacheinander fail-loud abbrechen zu lassen.
"""
import json

import optuna

from automation.optimizer.purge_stale_studies import find_stale_study_dbs, purge_stale_studies


def _make_study_db(tmp_path, name, *, version, n_trials):
    db_path = tmp_path / f"study_{name}.db"
    storage = f"sqlite:///{db_path}"
    study = optuna.create_study(study_name=name, storage=storage, direction="maximize")
    if version is not None:
        study.set_user_attr("reward_semantics_version", version)
    for i in range(n_trials):
        t = study.ask()
        study.tell(t, float(i))
    return db_path


def test_find_stale_study_dbs_detects_version_mismatch(tmp_path):
    stale_db = _make_study_db(tmp_path, "stale", version=11, n_trials=5)
    fresh_db = _make_study_db(tmp_path, "fresh", version=12, n_trials=5)

    stale = find_stale_study_dbs(sweep_dir=tmp_path, current_version=12)
    stale_paths = {entry["db_path"] for entry in stale}
    assert str(stale_db) in stale_paths
    assert str(fresh_db) not in stale_paths


def test_find_stale_study_dbs_ignores_trial_free_studies(tmp_path):
    """Eine Study OHNE Trials ist nicht 'vergiftet' — nichts zu purgen."""
    _make_study_db(tmp_path, "empty", version=11, n_trials=0)
    stale = find_stale_study_dbs(sweep_dir=tmp_path, current_version=12)
    assert stale == []


def test_find_stale_study_dbs_flags_missing_version_with_trials(tmp_path):
    unversioned_db = _make_study_db(tmp_path, "unversioned", version=None, n_trials=3)
    stale = find_stale_study_dbs(sweep_dir=tmp_path, current_version=12)
    assert any(e["db_path"] == str(unversioned_db) for e in stale)


def test_purge_stale_studies_dry_run_does_not_delete(tmp_path):
    stale_db = _make_study_db(tmp_path, "stale", version=11, n_trials=5)
    result = purge_stale_studies(sweep_dir=tmp_path, current_version=12, dry_run=True)
    assert len(result) == 1
    assert stale_db.exists()  # NICHT gelöscht


def test_purge_stale_studies_deletes_stale_db_files(tmp_path):
    stale_db = _make_study_db(tmp_path, "stale", version=11, n_trials=5)
    fresh_db = _make_study_db(tmp_path, "fresh", version=12, n_trials=5)

    result = purge_stale_studies(sweep_dir=tmp_path, current_version=12, dry_run=False)
    assert len(result) == 1
    assert not stale_db.exists()  # gelöscht
    assert fresh_db.exists()      # unberührt


def test_purge_stale_studies_no_op_when_nothing_stale(tmp_path):
    _make_study_db(tmp_path, "fresh", version=12, n_trials=5)
    result = purge_stale_studies(sweep_dir=tmp_path, current_version=12, dry_run=False)
    assert result == []


def test_find_stale_study_dbs_empty_dir_returns_empty(tmp_path):
    assert find_stale_study_dbs(sweep_dir=tmp_path / "does_not_exist", current_version=12) == []


def test_current_version_read_from_real_optimizer_json():
    # Issue #697 — forward-kompatibel (analog test_issue_686_reward_semantics_bump.py): pinnt nur
    # die historische UNTERGRENZE, der v13-Bump (#697) hebt die LIVE-Version weiter an.
    from automation.optimizer.purge_stale_studies import _current_reward_semantics_version
    assert _current_reward_semantics_version() >= 12
