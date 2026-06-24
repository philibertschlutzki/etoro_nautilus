"""Issue #411 / #416 — Optuna/SQLite `create_all`-DDL-Race bei parallelen Per-Symbol-Studies.

`RDBStorage.__init__` ruft `metadata.create_all` (check-then-create). Zwei Worker auf derselben
FRISCHEN SQLite-Datei crashen mit `table studies already exists` (`load_if_exists=True` schuetzt
NICHT — es greift erst NACH dem Schema-Bootstrap). Fix: ein prozessweiter Lock serialisiert
`create_study` (`_create_study_with_retry`) mit genau EINEM Retry auf die Race-Signatur, und
`_preinit_study_storage` bootstrappt das Schema seriell vor dem Pool.

Echtes SQLite, KEIN Mock (der bestehende `test_issue_400` ist vollstaendig gemockt und prueft die
Storage-Sicherheit gerade NICHT).
"""
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import optuna
import pytest

from automation.optimizer import run_optimization as ro


def test_concurrent_create_study_same_fresh_db_no_crash(tmp_path):
    """>=8 Threads gegen DIESELBE frische DB ⇒ keine Exception, genau eine Study."""
    storage = f"sqlite:///{tmp_path}/study_X.db"
    errors: list[Exception] = []

    def worker(_):
        try:
            ro._create_study_with_retry(study_name="study_X", storage=storage)
        except Exception as e:  # noqa: BLE001 — Test sammelt, asserted danach leer
            errors.append(e)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(worker, range(8)))

    assert not errors, f"DDL-Race propagierte: {errors}"
    summaries = optuna.get_all_study_summaries(storage)
    assert len(summaries) == 1


def test_preinit_makes_workers_hit_exists_path(tmp_path, monkeypatch):
    """Nach `_preinit_study_storage` existiert die Study ⇒ der nachfolgende `create_study`
    loggt „Using an existing study", nicht „A new study created"."""
    storage = f"sqlite:///{tmp_path}/study_X.db"
    monkeypatch.setenv("ETORO_OPTUNA_STORAGE", storage)  # resolve_storage nutzt ENV verbatim
    study_name = "study_X"

    recs: list[str] = []

    class _H(logging.Handler):
        def emit(self, r):
            recs.append(r.getMessage())

    opt_logger = logging.getLogger("optuna")
    h = _H()
    opt_logger.addHandler(h)
    try:
        ro._preinit_study_storage(study_name)
        optuna.create_study(study_name=study_name, storage=storage,
                            direction="maximize", load_if_exists=True)
    finally:
        opt_logger.removeHandler(h)

    assert any("existing study" in m for m in recs), \
        f"erwartete 'Using an existing study', erhielt: {recs}"
    assert len(optuna.get_all_study_summaries(storage)) == 1


def test_optimize_symbol_retries_on_ddl_race(monkeypatch):
    """`_create_study_with_retry` faengt die `… already exists`-OperationalError GENAU EINMAL ab
    und laedt die Study neu — kein blindes except, kein Propagieren."""
    calls = {"n": 0}
    fake_study = object()

    def flaky_create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("table studies already exists")
        return fake_study

    monkeypatch.setattr(ro.optuna, "create_study", flaky_create)
    out = ro._create_study_with_retry(study_name="s", storage="sqlite:///x.db")
    assert out is fake_study
    assert calls["n"] == 2, "genau ein Retry erwartet"


def test_create_study_does_not_swallow_unrelated_operational_error(monkeypatch):
    """Fail-Fast (Pitfall #66): eine NICHT-Race-OperationalError propagiert hart (kein Retry)."""
    calls = {"n": 0}

    def boom(**kwargs):
        calls["n"] += 1
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ro.optuna, "create_study", boom)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        ro._create_study_with_retry(study_name="s", storage="sqlite:///x.db")
    assert calls["n"] == 1, "kein Retry bei fremdem Fehler"


def test_preinit_idempotent(tmp_path, monkeypatch):
    """`_preinit_study_storage` zweimal aufgerufen ⇒ No-Op beim zweiten Mal (kein Crash)."""
    storage = f"sqlite:///{tmp_path}/study_Y.db"
    monkeypatch.setenv("ETORO_OPTUNA_STORAGE", storage)
    ro._preinit_study_storage("study_Y")
    ro._preinit_study_storage("study_Y")  # darf nicht crashen
    assert len(optuna.get_all_study_summaries(storage)) == 1
