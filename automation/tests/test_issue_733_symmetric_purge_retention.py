"""Issue #733 — Symmetrisches Löschen (purge_stale_studies) + Normalfall-Retention (retention.py).

RC1 (Issue-Katalog #732-#737 §0.3): ``purge_stale_studies`` löschte bislang NUR die `.db`-Datei
einer stale Study, nie den zugehörigen ``WORK/study_name/trial_*/``-Baum — jeder der 7
dokumentierten Pflicht-Purge-Übergänge (``optimizer.json``-Changelog v7→v14) hinterliess dadurch
eine komplette verwaiste Trial-Generation. Zusätzlich gab es im Normalfall (Study einfach
abgeschlossen, kein Semantics-Bump) überhaupt keine Retention.

Dieses Modul deckt beide Fixes ab:
  1. ``purge_stale_studies`` löscht jetzt symmetrisch `.db` UND Trial-Baum.
  2. ``retention.collect_referenced_trial_dirs``/``prune_completed_trial_dirs`` implementieren die
     Normalfall-Retention (alles ausser champions/offenen-Proposal-Referenzen wird entfernt).
"""
import json
from pathlib import Path

import optuna

from automation.optimizer.purge_stale_studies import purge_stale_studies
from automation.optimizer import retention


def _make_study_db(root: Path, study_name: str, *, version, n_trials: int) -> Path:
    sweep_dir = root / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    db_path = sweep_dir / f"{study_name}.db"
    storage = f"sqlite:///{db_path}"
    study = optuna.create_study(study_name=study_name, storage=storage, direction="maximize")
    if version is not None:
        study.set_user_attr("reward_semantics_version", version)
    for i in range(n_trials):
        t = study.ask()
        study.tell(t, float(i))
    return db_path


def _make_trial_dir(root: Path, study_name: str, trial_number: int) -> Path:
    trial_dir = root / study_name / f"trial_{trial_number:04d}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "experiment_manifest.json").write_text("{}", encoding="utf-8")
    return trial_dir


# ---------------------------------------------------------------------------
# purge_stale_studies — symmetrisches Löschen
# ---------------------------------------------------------------------------

def test_purge_stale_studies_removes_trial_dir_tree(tmp_path):
    study_name = "study_SmaStrategy_TSLA_ETORO"
    db_path = _make_study_db(tmp_path, study_name, version=11, n_trials=5)
    trial_dir = _make_trial_dir(tmp_path, study_name, 0)

    result = purge_stale_studies(sweep_dir=tmp_path / "sweep", current_version=12, dry_run=False)

    assert len(result) == 1
    assert not db_path.exists()
    assert not trial_dir.exists()
    assert not (tmp_path / study_name).exists()


def test_purge_stale_studies_dry_run_leaves_trial_dir_tree(tmp_path):
    study_name = "study_SmaStrategy_TSLA_ETORO"
    db_path = _make_study_db(tmp_path, study_name, version=11, n_trials=5)
    trial_dir = _make_trial_dir(tmp_path, study_name, 0)

    purge_stale_studies(sweep_dir=tmp_path / "sweep", current_version=12, dry_run=True)

    assert db_path.exists()
    assert trial_dir.exists()


def test_purge_stale_studies_leaves_fresh_study_trial_dir_untouched(tmp_path):
    stale_name = "study_Stale_AAA_ETORO"
    fresh_name = "study_Fresh_BBB_ETORO"
    _make_study_db(tmp_path, stale_name, version=11, n_trials=5)
    _make_study_db(tmp_path, fresh_name, version=12, n_trials=5)
    stale_trial_dir = _make_trial_dir(tmp_path, stale_name, 0)
    fresh_trial_dir = _make_trial_dir(tmp_path, fresh_name, 0)

    purge_stale_studies(sweep_dir=tmp_path / "sweep", current_version=12, dry_run=False)

    assert not stale_trial_dir.exists()
    assert fresh_trial_dir.exists()


def test_purge_stale_studies_no_trial_dir_on_disk_is_a_no_op(tmp_path):
    """Kein Trial-Verzeichnis vorhanden (z. B. bereits manuell bereinigt) darf den `.db`-Purge
    nicht stören — ``rmtree`` auf einem nicht-existenten Pfad wird übersprungen."""
    study_name = "study_SmaStrategy_TSLA_ETORO"
    db_path = _make_study_db(tmp_path, study_name, version=11, n_trials=5)

    result = purge_stale_studies(sweep_dir=tmp_path / "sweep", current_version=12, dry_run=False)

    assert len(result) == 1
    assert not db_path.exists()


# ---------------------------------------------------------------------------
# retention.collect_referenced_trial_dirs
# ---------------------------------------------------------------------------

def test_collect_referenced_trial_dirs_reads_champion_source_trial_dir(tmp_path):
    referenced = _make_trial_dir(tmp_path, "confirm_Sma_TSLA_ETORO_abcd1234", 0)
    champions_dir = tmp_path / "champions"
    champions_dir.mkdir(parents=True, exist_ok=True)
    (champions_dir / "champion_Sma_TSLA_ETORO.json").write_text(json.dumps({
        "provenance": {"source_trial_dir": str(referenced)},
    }), encoding="utf-8")

    keep = retention.collect_referenced_trial_dirs(work_dir=tmp_path)

    assert referenced.resolve() in keep


def test_collect_referenced_trial_dirs_reads_symbol_proposal_holdout_trial_dir(tmp_path):
    referenced = _make_trial_dir(tmp_path, "confirm_Sma_TSLA_ETORO_abcd1234", 0)
    (tmp_path / "proposal_Sma_TSLA_ETORO.json").write_text(json.dumps({
        "holdout_trial_dir": str(referenced),
    }), encoding="utf-8")

    keep = retention.collect_referenced_trial_dirs(work_dir=tmp_path)

    assert referenced.resolve() in keep


def test_collect_referenced_trial_dirs_reads_global_proposal_nested_holdout_trial_dir(tmp_path):
    referenced = _make_trial_dir(tmp_path, "study_Sma_holdout", 0)
    (tmp_path / "proposal_Sma.json").write_text(json.dumps({
        "holdout": {"trial_dir": str(referenced)},
    }), encoding="utf-8")

    keep = retention.collect_referenced_trial_dirs(work_dir=tmp_path)

    assert referenced.resolve() in keep


def test_collect_referenced_trial_dirs_ignores_broken_json(tmp_path):
    champions_dir = tmp_path / "champions"
    champions_dir.mkdir(parents=True, exist_ok=True)
    (champions_dir / "champion_broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "proposal_broken.json").write_text("{not json", encoding="utf-8")

    keep = retention.collect_referenced_trial_dirs(work_dir=tmp_path)

    assert keep == set()


def test_collect_referenced_trial_dirs_ignores_none_trial_dir(tmp_path):
    (tmp_path / "proposal_Sma.json").write_text(json.dumps({
        "holdout_trial_dir": None,
    }), encoding="utf-8")

    keep = retention.collect_referenced_trial_dirs(work_dir=tmp_path)

    assert keep == set()


def test_collect_referenced_trial_dirs_empty_work_dir(tmp_path):
    assert retention.collect_referenced_trial_dirs(work_dir=tmp_path / "does_not_exist") == set()


# ---------------------------------------------------------------------------
# retention.prune_completed_trial_dirs
# ---------------------------------------------------------------------------

def test_prune_completed_trial_dirs_removes_everything_not_kept(tmp_path):
    study_name = "study_Sma_TSLA_ETORO"
    t0 = _make_trial_dir(tmp_path, study_name, 0)
    t1 = _make_trial_dir(tmp_path, study_name, 1)
    t2 = _make_trial_dir(tmp_path, study_name, 2)

    pruned = retention.prune_completed_trial_dirs(
        study_name, {t1.resolve()}, work_dir=tmp_path)

    assert not t0.exists()
    assert t1.exists()
    assert not t2.exists()
    assert set(pruned) == {t0, t2}


def test_prune_completed_trial_dirs_dry_run_deletes_nothing(tmp_path):
    study_name = "study_Sma_TSLA_ETORO"
    t0 = _make_trial_dir(tmp_path, study_name, 0)

    pruned = retention.prune_completed_trial_dirs(
        study_name, set(), work_dir=tmp_path, dry_run=True)

    assert t0.exists()
    assert pruned == [t0]


def test_prune_completed_trial_dirs_missing_study_dir_is_no_op(tmp_path):
    pruned = retention.prune_completed_trial_dirs(
        "study_does_not_exist", set(), work_dir=tmp_path)
    assert pruned == []


def test_prune_completed_trial_dirs_keeps_all_referenced(tmp_path):
    study_name = "study_Sma_TSLA_ETORO"
    t0 = _make_trial_dir(tmp_path, study_name, 0)
    t1 = _make_trial_dir(tmp_path, study_name, 1)

    pruned = retention.prune_completed_trial_dirs(
        study_name, {t0.resolve(), t1.resolve()}, work_dir=tmp_path)

    assert pruned == []
    assert t0.exists()
    assert t1.exists()


def test_end_to_end_champion_reference_survives_prune(tmp_path):
    """Ein von champions/ referenzierter trial_dir überlebt das Pruning der Study, aus der er
    (hypothetisch) stammt; alle anderen Trial-Verzeichnisse derselben Study werden entfernt."""
    study_name = "study_Sma_TSLA_ETORO"
    kept = _make_trial_dir(tmp_path, study_name, 0)
    removed = _make_trial_dir(tmp_path, study_name, 1)

    champions_dir = tmp_path / "champions"
    champions_dir.mkdir(parents=True, exist_ok=True)
    (champions_dir / "champion_Sma_TSLA_ETORO.json").write_text(json.dumps({
        "provenance": {"source_trial_dir": str(kept)},
    }), encoding="utf-8")

    keep = retention.collect_referenced_trial_dirs(work_dir=tmp_path)
    retention.prune_completed_trial_dirs(study_name, keep, work_dir=tmp_path)

    assert kept.exists()
    assert not removed.exists()
