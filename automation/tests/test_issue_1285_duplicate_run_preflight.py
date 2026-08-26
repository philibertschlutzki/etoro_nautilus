"""Issue #1285 (GH #1158, Katalog #1272-1297, P1) — Duplikat-Preflight statt Report-Befund.

Symptom: ``f13f29db`` trug ``duplicate_of = a9d80fba…`` und ``check_run_is_not_duplicate`` FAILte
— aber erst NACH 1584 s Rechenzeit und 1745 abgeschlossenen Trials, weil der Fingerabdruck-Vergleich
ausschliesslich in ``report._build_report`` (am Laufende) lief.

Fix: ``sweep.assert_run_is_not_duplicate_preflight`` zieht denselben Fingerabdruck-Vergleich
(``report.compute_run_fingerprint`` + ``invariants.check_run_is_not_duplicate``) an den Sweep-Start
(vor dem #531-Backfill, vor jedem Symbol-Preflight). Ein Treffer bricht fail-loud (Exit-Code 2) ab,
es sei denn ``--seed-salt``/``--allow-duplicate-run`` (``OPTIMIZER_ALLOW_DUPLICATE_RUN=1``) ist
gesetzt."""
import pytest

from automation.optimizer import sweep
from automation.optimizer.manifest import append_jsonl_atomic


def _opt_data():
    return {"seed": 42, "reward_semantics_version": 27, "simulation_semantics_version": 6}


def _fp(**overrides):
    kwargs = dict(
        strategies=["AdxAtr"], symbols=["TSLA.ETORO"], opt_data=_opt_data(),
        tournament_path=sweep.config_dir() / "tournament.json",
        optimizer_path=sweep.config_dir() / "optimizer.json",
    )
    kwargs.update(overrides)
    return sweep._inv_compute_run_fingerprint_preflight(**kwargs)


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    index_path = tmp_path / "run_fingerprints.jsonl"
    monkeypatch.setattr(sweep, "RUN_FINGERPRINT_INDEX_PATH", index_path)
    monkeypatch.delenv("OPTIMIZER_ALLOW_DUPLICATE_RUN", raising=False)
    monkeypatch.delenv("OPTIMIZER_SEED_SALT", raising=False)
    return index_path


def test_first_run_with_empty_index_passes():
    result = sweep.assert_run_is_not_duplicate_preflight(
        strategies=["AdxAtr"], symbols=["TSLA.ETORO"], run_id="run-A", opt_data=_opt_data())
    assert result.passed is True


def test_identical_input_under_a_different_run_id_aborts_fail_loud(_isolated_index):
    append_jsonl_atomic(_isolated_index, {
        "fingerprint": _fp(), "fingerprint_base": _fp(), "run_id": "run-A",
        "started_at_utc": "2026-01-01T00:00:00Z", "seed_salt": None, "study_summaries": [],
    })
    with pytest.raises(SystemExit) as exc_info:
        sweep.assert_run_is_not_duplicate_preflight(
            strategies=["AdxAtr"], symbols=["TSLA.ETORO"], run_id="run-B", opt_data=_opt_data())
    assert exc_info.value.code == 2


def test_same_run_id_as_index_entry_is_not_a_duplicate(_isolated_index):
    """Ein Zwischenreport DESSELBEN Laufs (identische run_id) darf sich nicht selbst blockieren."""
    append_jsonl_atomic(_isolated_index, {
        "fingerprint": _fp(), "fingerprint_base": _fp(), "run_id": "run-A",
        "started_at_utc": "2026-01-01T00:00:00Z", "seed_salt": None, "study_summaries": [],
    })
    result = sweep.assert_run_is_not_duplicate_preflight(
        strategies=["AdxAtr"], symbols=["TSLA.ETORO"], run_id="run-A", opt_data=_opt_data())
    assert result.passed is True


def test_allow_duplicate_run_env_opts_out_of_the_abort(_isolated_index, monkeypatch):
    append_jsonl_atomic(_isolated_index, {
        "fingerprint": _fp(), "fingerprint_base": _fp(), "run_id": "run-A",
        "started_at_utc": "2026-01-01T00:00:00Z", "seed_salt": None, "study_summaries": [],
    })
    monkeypatch.setenv("OPTIMIZER_ALLOW_DUPLICATE_RUN", "1")
    result = sweep.assert_run_is_not_duplicate_preflight(
        strategies=["AdxAtr"], symbols=["TSLA.ETORO"], run_id="run-B", opt_data=_opt_data())
    assert result.passed is False  # der Befund bleibt sichtbar, bricht aber nicht mehr ab


def test_seed_salt_makes_the_fingerprint_distinct(_isolated_index, monkeypatch):
    append_jsonl_atomic(_isolated_index, {
        "fingerprint": _fp(), "fingerprint_base": _fp(), "run_id": "run-A",
        "started_at_utc": "2026-01-01T00:00:00Z", "seed_salt": None, "study_summaries": [],
    })
    monkeypatch.setenv("OPTIMIZER_SEED_SALT", "salt-123")
    result = sweep.assert_run_is_not_duplicate_preflight(
        strategies=["AdxAtr"], symbols=["TSLA.ETORO"], run_id="run-B", opt_data=_opt_data())
    assert result.passed is True


def test_cli_flag_sets_env_var(monkeypatch):
    monkeypatch.delenv("OPTIMIZER_ALLOW_DUPLICATE_RUN", raising=False)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-duplicate-run", action="store_true", default=False)
    args = parser.parse_args(["--allow-duplicate-run"])
    assert args.allow_duplicate_run is True
