"""Issue #990/#1144 (Katalog #986, Pitfall #412 in AGENTS.md) — der Purge vernichtete den
Champion-Store und damit die einzige Korroborationschance: ``max_corroboration_count = 1`` in
10/10 Studies; die Nachmittags-Wiederholungen von TSLA und PLTR meldeten ``STORE_EMPTY: 9``.

Root-Cause: ``champions.store_champion`` behandelte einen ``simulation_semantics_version``-Mismatch
des GESPEICHERTEN Eintrags identisch zu einem ``params_schema_version``-Mismatch (vollstaendige
Verwerfung + Quarantaene) statt wie einen ``reward_semantics_version``-Mismatch (Migration —
Lifecycle/corroboration_count bleiben erhalten, nur params/quality werden durch den frischen
Kandidaten ersetzt). Das setzte JEDEN betroffenen Store-Eintrag bei jedem Bump auf
``corroboration_count = 1`` zurueck bzw. entfernte ihn ganz, wenn der Nachfolge-Kandidat selbst
nicht speicherwuerdig war -- der bereits vorhandene ``semantics_migrated``-Zaehler
(``report._champions_summary``) blieb fuer diese Achse strukturell bei 0.

Fix:
1. ``champions.store_champion``: ein ``simulation_semantics_version``-Mismatch migriert jetzt wie
   ein ``reward_semantics_version``-Mismatch (Lifecycle ueberlebt, ``semantics_migrated_from``/
   ``semantics_migrated_kind`` dokumentieren die alte Version + Ursache).
2. ``champions.find_stale_champion_entries``/``quarantine_stale_champion_entries``: neue, EXPLIZITE
   Bulk-Operator-Aktion fuer Champion-Eintraege, die auf absehbare Zeit nie mehr automatisch
   migriert werden (Wiederkehr des #821-Quarantaene-Musters, jetzt manuell statt automatisch).
3. ``purge_stale_studies.py``: ``--purge-scope {studies,champions,all}``, Default ``studies``
   (bit-identisch zum Pre-#990-Verhalten) -- Study- und Champion-Store bleiben unabhaengig
   purgebar.

Akzeptanzkriterium laut Issue: ein zweiter Lauf desselben Paares am selben Tag erreicht
``corroboration_count = 2`` und ``written_back >= 1``.
"""
import json

import pytest

from automation.optimizer import champions


class _FakeOptunaStudy:
    def __init__(self, best_value=1.0, directions=None):
        self.best_value = best_value
        self.directions = directions or ["maximize"]


_PROMOTION = {
    "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
    "symbol_params": {"sma_period": 21}, "R_symbol": 0.8, "R_global": 0.0,
    "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
    "metrics_symbol": {}, "metrics_global": {},
}


def _opt_data(*, simulation_version: int, reward_version: int = 19) -> dict:
    return {
        "reward_semantics_version": reward_version,
        "simulation_semantics_version": simulation_version,
        "champion_enabled": True,
        "champion_min_R_symbol": 0.0,
        "champion_min_tuning_edge": 0.0,
        "champion_demote_after_runs": 2,
        "champion_promote_after_runs": 2,
    }


# ── Akzeptanzkriterium: zweiter Lauf desselben Paares erreicht corroboration_count=2, written_back ─
def test_second_run_after_simulation_semantics_bump_reaches_corroboration_2_and_writes_back(
        tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    study = _FakeOptunaStudy()

    # Lauf 1: unter simulation_semantics_version=0.
    path = champions.store_champion(
        study, "SmaCrossoverStrategy", "TSLA.ETORO", _PROMOTION,
        catalog_newest_ns=1000, opt_data=_opt_data(simulation_version=0), run_id="run1")
    assert path is not None
    entry_after_run1 = json.loads(path.read_text("utf-8"))
    assert entry_after_run1["lifecycle"]["corroboration_count"] == 1

    # Lauf 2, SELBER TAG, NACH einem simulation_semantics_version-Bump (0 -> 1) -- die
    # Nachmittags-Wiederholung aus dem Issue-Symptom.
    path2 = champions.store_champion(
        study, "SmaCrossoverStrategy", "TSLA.ETORO", _PROMOTION,
        catalog_newest_ns=2000, opt_data=_opt_data(simulation_version=1), run_id="run2")
    assert path2 is not None
    entry_after_run2 = json.loads(path2.read_text("utf-8"))

    assert entry_after_run2["lifecycle"]["corroboration_count"] == 2
    assert entry_after_run2["lifecycle"]["semantics_migrated_from"] == 0
    assert entry_after_run2["lifecycle"]["semantics_migrated_kind"] == "simulation_semantics_version"

    written_back = champions.maybe_write_back(
        entry_after_run2, _opt_data(simulation_version=1), base_cfg=tmp_path)
    assert written_back is True


def test_store_empty_no_longer_triggered_by_a_single_simulation_semantics_bump(tmp_path, monkeypatch):
    """Direkter Nachweis gegen das Issue-Symptom: nach einem simulation_semantics_version-Bump
    bleibt der Store-Eintrag AKTIV (nicht in _stale/), also existiert weiterhin mindestens ein
    champion_*.json -- load_champion_entry_with_reason kann fuer ANDERE Paare nie mehr faelschlich
    STORE_EMPTY (statt NO_ENTRY_FOR_PAIR) melden, nur weil DIESES Paar einen Bump ueberlebt hat."""
    monkeypatch.setattr(champions, "WORK", tmp_path)
    study = _FakeOptunaStudy()
    champions.store_champion(
        study, "SmaCrossoverStrategy", "TSLA.ETORO", _PROMOTION,
        catalog_newest_ns=1000, opt_data=_opt_data(simulation_version=0), run_id="run1")
    champions.store_champion(
        study, "SmaCrossoverStrategy", "TSLA.ETORO", _PROMOTION,
        catalog_newest_ns=2000, opt_data=_opt_data(simulation_version=1), run_id="run2")
    assert champions._champion_store_has_any_entry() is True


# ── champions.find_stale_champion_entries / quarantine_stale_champion_entries ──────────────────────
def _write_champion(champions_dir, strategy, symbol, *, simulation_version, params_schema_version=None):
    entry = {
        "strategy": strategy, "symbol": symbol,
        "params": {"a": 1},
        "quality": {"R_symbol": 0.5, "R_global": 0.0},
        "provenance": {"status_at_store": "READY_FOR_PR", "holdout_reject_detail": None},
        "integrity": {
            "reward_semantics_version": 19,
            "simulation_semantics_version": simulation_version,
            "params_schema_version": params_schema_version,
        },
        "lifecycle": {"degrade_streak": 0, "corroboration_count": 1},
    }
    path = champions_dir / f"champion_{strategy}_{symbol.replace('.', '_')}.json"
    path.write_text(json.dumps(entry), "utf-8")
    return path


def test_find_stale_champion_entries_flags_simulation_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    champions_dir = tmp_path / "champions"
    champions_dir.mkdir()
    _write_champion(champions_dir, "SmaCrossoverStrategy", "TSLA.ETORO", simulation_version=0)

    stale = champions.find_stale_champion_entries(_opt_data(simulation_version=1))
    assert len(stale) == 1
    assert stale[0]["reason"] == "simulation_semantics_version_mismatch"
    assert stale[0]["strategy"] == "SmaCrossoverStrategy"


def test_find_stale_champion_entries_ignores_current_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    champions_dir = tmp_path / "champions"
    champions_dir.mkdir()
    _write_champion(champions_dir, "SmaCrossoverStrategy", "TSLA.ETORO", simulation_version=1)

    stale = champions.find_stale_champion_entries(_opt_data(simulation_version=1))
    assert stale == []


def test_quarantine_stale_champion_entries_moves_files_and_preserves_history(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    champions_dir = tmp_path / "champions"
    champions_dir.mkdir()
    entry_path = _write_champion(
        champions_dir, "SmaCrossoverStrategy", "TSLA.ETORO", simulation_version=0)

    result = champions.quarantine_stale_champion_entries(_opt_data(simulation_version=1))

    assert len(result) == 1
    assert not entry_path.exists()
    quarantine_dir = champions_dir / "_stale"
    assert quarantine_dir.exists()
    assert len(list(quarantine_dir.glob("*.json"))) == 1


def test_quarantine_stale_champion_entries_dry_run_does_not_move_files(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    champions_dir = tmp_path / "champions"
    champions_dir.mkdir()
    entry_path = _write_champion(
        champions_dir, "SmaCrossoverStrategy", "TSLA.ETORO", simulation_version=0)

    result = champions.quarantine_stale_champion_entries(
        _opt_data(simulation_version=1), dry_run=True)

    assert len(result) == 1
    assert entry_path.exists()
    assert not (champions_dir / "_stale").exists()


# ── purge_stale_studies.py --purge-scope CLI ────────────────────────────────────────────────────
def test_purge_scope_all_calls_both_studies_and_champions(tmp_path, monkeypatch):
    from automation.optimizer import purge_stale_studies as pss

    studies_called = []
    champions_called = []
    monkeypatch.setattr(pss, "purge_stale_studies", lambda **kwargs: studies_called.append(True) or [])
    monkeypatch.setattr(
        champions, "quarantine_stale_champion_entries",
        lambda *a, **kw: champions_called.append(True) or [])

    pss.main(["--purge-scope", "all"])
    assert studies_called == [True]
    assert champions_called == [True]


def test_purge_scope_invalid_value_rejected():
    from automation.optimizer import purge_stale_studies as pss
    with pytest.raises(SystemExit):
        pss.main(["--purge-scope", "bogus"])
