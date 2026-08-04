"""Issue #854 (P0, Katalog #849-#855, GitHub-Issue #756) — `simulation_semantics_version`
einführen; `reward_semantics_version` 18 → 19 + Pflicht-Purge.

Root-Cause: `reward_semantics_version` war der EINZIGE Invalidierungs-Hebel, aber wurde für ZWEI
semantisch verschiedene Ereignisse verwendet — die Reward-/Gate-FORMEL hat sich geändert (gespeicherte
Trials sind noch gültig, ihr Score nicht) vs. die SIMULATION selbst hat sich geändert (die
gemessenen Metriken sind ungültig, unabhängig von jeder Formel). Kohorte A (#836-#839) ist der
zweite Fall: der Exit-Pfad ändert Haltedauern, Equity-Kurven, Trade-Zahlen — kein Reward-Fix kann
das reparieren, und `champion_quality_stale` (entwertet nur `quality`, `params` bleiben
seed-fähig, #819) zieht hier die FALSCHE Konsequenz.

Fix:
1. `optimizer.json['simulation_semantics_version']` (Startwert 1) — orthogonale Achse zu
   `reward_semantics_version`.
2. `reward_semantics_version` 18 → 19, ausgelöst von #848 (siehe Changelog-Dokumentation für die
   Begründung, warum #845 in dieser Session KEIN Auslöser ist).
3. `champions.champion_simulation_stale`/`champion_is_admissible`: ein
   `simulation_semantics_version`-Mismatch schliesst einen Champion-Eintrag VOLLSTÄNDIG aus
   (params + quality) — anders als ein reiner `reward_semantics_version`-Mismatch (nur quality).
4. `run_optimization._check_simulation_semantics_version`: derselbe fail-loud + Purge-Mechanismus
   wie `_check_reward_semantics_version`, orthogonale Achse, eigener Fehlercode
   (`REJECT_STALE_SIMULATION_SEMANTICS`).
5. `purge_stale_studies.find_stale_study_dbs`/`purge_stale_studies`: erkennt zusätzlich eine
   stale `simulation_semantics_version`.
6. `invariants.check_semantics_version_coherence`: verifiziert, dass kein simulation-stale
   Champion-Eintrag trotzdem als admissible gilt.

Akzeptanzkriterien:
- AK-1: Beide Versionsfelder stehen im #742-Report-Manifest und in jedem Champion-Store-Eintrag.
- AK-2: Ein simulation_semantics_version-Mismatch schliesst einen Champion vollständig aus
  (params + quality); ein reiner reward_semantics_version-Mismatch nur die quality.
- AK-3: Nach dem Purge existiert keine SQLite-Study mit simulation_semantics_version < aktuell.
- AK-4: symbol_coverage.json, diagnosed_pairs_cache.json und logs/champions/ überstehen den Purge
  (bereits durch die bestehende #733/#761-Symmetrie-Architektur gewährleistet — purge_stale_studies
  referenziert ausschliesslich WORK/sweep/*.db + deren Trial-Bäume).
- AK-5: check_semantics_version_coherence PASST im Re-Run.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv

CFG_PATH = Path("automation/config/optimizer.json")
CFG = json.loads(CFG_PATH.read_text("utf-8"))


# ── AK-1: beide Versionsfelder existieren, dokumentiert ─────────────────────────────────────────
def test_reward_semantics_version_is_19():
    assert CFG["reward_semantics_version"] == 19


def test_simulation_semantics_version_starts_at_1():
    assert CFG["simulation_semantics_version"] == 1


def test_version_is_documented_with_v19_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v19" in doc
    assert "#848" in doc


def test_v19_changelog_explains_why_845_did_not_trigger_bump():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v19_segment = doc[doc.index("v19 ="):]
    assert "#845" in v19_segment


def test_simulation_semantics_version_is_documented():
    doc = CFG["_schema"]["fields"]["simulation_semantics_version"]
    for ref in ("#836", "#837", "#838", "#839", "#844"):
        assert ref in doc, f"simulation_semantics_version-Schema muss {ref} referenzieren"
    assert "reward" in doc.lower() and "simulation" in doc.lower() and "params_schema" in doc.lower()


def test_scale_keys_fingerprint_unchanged_between_v18_and_v19():
    from automation.tests.test_issue_637_reward_semantics_bump import _SCALE_KEYS
    for k in _SCALE_KEYS:
        assert k in CFG, f"{k} muss weiterhin ein reales Config-Feld sein (Fingerprint-Stabilität)"


# ── Akzeptanzkriterium #854/1: In-Process-Guard fuer simulation_semantics_version ────────────────
class _FakeStudy:
    def __init__(self, attrs=None, n_trials=0):
        self._attrs = dict(attrs or {})
        self.trials = [object()] * n_trials
        self.study_name = "study_test"
        self._storage = None

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v


def test_stale_simulation_semantics_study_is_rejected_fail_loud():
    from automation.optimizer.run_optimization import _check_simulation_semantics_version
    study = _FakeStudy({"simulation_semantics_version": 0}, n_trials=5)
    with pytest.raises(ValueError, match="REJECT_STALE_SIMULATION_SEMANTICS"):
        _check_simulation_semantics_version(study, CFG)


def test_fresh_study_stamps_simulation_semantics_version_without_error():
    from automation.optimizer.run_optimization import _check_simulation_semantics_version
    study = _FakeStudy()
    _check_simulation_semantics_version(study, CFG)
    assert study.user_attrs["simulation_semantics_version"] == CFG["simulation_semantics_version"]


def test_matching_simulation_semantics_version_is_a_no_op():
    from automation.optimizer.run_optimization import _check_simulation_semantics_version
    study = _FakeStudy({"simulation_semantics_version": CFG["simulation_semantics_version"]}, n_trials=20)
    _check_simulation_semantics_version(study, CFG)  # darf NICHT raisen


def test_simulation_semantics_check_is_a_no_op_when_key_missing():
    """Rueckwaertskompatibel: fehlt der Config-Key, ist die Pruefung ein No-Op."""
    from automation.optimizer.run_optimization import _check_simulation_semantics_version
    study = _FakeStudy({"simulation_semantics_version": 0}, n_trials=5)
    cfg_without_key = {k: v for k, v in CFG.items() if k != "simulation_semantics_version"}
    _check_simulation_semantics_version(study, cfg_without_key)  # darf NICHT raisen


def test_reward_and_simulation_semantics_are_orthogonal_axes():
    """Eine Study kann unter der aktuellen reward_semantics_version, aber einer veralteten
    simulation_semantics_version stehen (oder umgekehrt) -- beide Achsen werden UNABHAENGIG
    geprueft."""
    from automation.optimizer.run_optimization import (
        _check_reward_semantics_version, _check_simulation_semantics_version)
    study = _FakeStudy({
        "reward_semantics_version": CFG["reward_semantics_version"],
        "simulation_semantics_version": 0,
    }, n_trials=5)
    _check_reward_semantics_version(study, CFG)  # reward-Achse: kein Konflikt, kein raise
    with pytest.raises(ValueError, match="REJECT_STALE_SIMULATION_SEMANTICS"):
        _check_simulation_semantics_version(study, CFG)


# ── Akzeptanzkriterium #854/3: purge_stale_studies erkennt simulation_semantics_version ──────────
def test_purge_stale_studies_flags_simulation_semantics_mismatch(tmp_path):
    import optuna
    from automation.optimizer import purge_stale_studies as pss

    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    db_path = sweep_dir / "study_Foo_Bar.db"
    study = optuna.create_study(study_name="study_Foo_Bar",
                                storage=f"sqlite:///{db_path}", load_if_exists=True)
    study.set_user_attr("reward_semantics_version", 19)  # reward-Achse aktuell
    study.set_user_attr("simulation_semantics_version", 0)  # simulation-Achse stale
    trial = study.ask()
    study.tell(trial, 1.0)

    found = pss.find_stale_study_dbs(
        sweep_dir=sweep_dir, current_version=19, current_simulation_version=1)
    assert any(f["study_name"] == "study_Foo_Bar" for f in found)


def test_purge_stale_studies_passes_current_studies_on_both_axes(tmp_path):
    import optuna
    from automation.optimizer import purge_stale_studies as pss

    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    db_path = sweep_dir / "study_Foo_Bar.db"
    study = optuna.create_study(study_name="study_Foo_Bar",
                                storage=f"sqlite:///{db_path}", load_if_exists=True)
    study.set_user_attr("reward_semantics_version", 19)
    study.set_user_attr("simulation_semantics_version", 1)
    trial = study.ask()
    study.tell(trial, 1.0)

    found = pss.find_stale_study_dbs(
        sweep_dir=sweep_dir, current_version=19, current_simulation_version=1)
    assert found == []


def test_find_stale_study_dbs_without_explicit_simulation_version_does_not_check_that_axis(tmp_path):
    """Regressionswaechter: ein Aufrufer, der NUR current_version explizit setzt (wie die
    bestehende #686/#733-Testsuite), darf NICHT ungefragt gegen die echte Config-
    simulation_semantics_version geprueft werden -- sonst wuerden alle bestehenden Fixture-Studies
    (ohne das Attr) faelschlich als stale gelten."""
    import optuna
    from automation.optimizer import purge_stale_studies as pss

    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    db_path = sweep_dir / "study_Foo_Bar.db"
    study = optuna.create_study(study_name="study_Foo_Bar",
                                storage=f"sqlite:///{db_path}", load_if_exists=True)
    study.set_user_attr("reward_semantics_version", 12)  # matches current_version below
    trial = study.ask()
    study.tell(trial, 1.0)

    found = pss.find_stale_study_dbs(sweep_dir=sweep_dir, current_version=12)
    assert found == []


# ── Akzeptanzkriterium #854/2: Champion-Store-Konsequenz ─────────────────────────────────────────
def _champion_entry(*, reward_version, simulation_version, r_symbol=0.5):
    return {
        "params": {"a": 1},
        "quality": {"R_symbol": r_symbol, "R_global": 0.0},
        "provenance": {"status_at_store": "READY_FOR_PR", "holdout_reject_detail": None},
        "integrity": {
            "reward_semantics_version": reward_version,
            "simulation_semantics_version": simulation_version,
            "params_schema_version": None,
        },
        "lifecycle": {"degrade_streak": 0, "corroboration_count": 1},
    }


_OPT_DATA = {
    "reward_semantics_version": 19,
    "simulation_semantics_version": 1,
    "champion_enabled": True,
    "champion_min_R_symbol": 0.0,
    "champion_min_tuning_edge": 0.0,
    "champion_demote_after_runs": 2,
}


def test_simulation_mismatch_excludes_entry_fully():
    from automation.optimizer import champions
    entry = _champion_entry(reward_version=19, simulation_version=0)  # simulation stale
    assert champions.champion_simulation_stale(entry, _OPT_DATA) is True
    ok, reason = champions.champion_is_admissible(entry, _OPT_DATA)
    assert ok is False
    assert reason == "SIMULATION_SEMANTICS_MISMATCH"


def test_reward_only_mismatch_stays_admissible():
    """Regressionswaechter #819: ein reiner reward_semantics_version-Mismatch entwertet NUR die
    Quality, params bleiben seed-faehig -- unveraendertes Verhalten."""
    from automation.optimizer import champions
    entry = _champion_entry(reward_version=17, simulation_version=1)  # nur reward stale
    assert champions.champion_simulation_stale(entry, _OPT_DATA) is False
    assert champions.champion_quality_stale(entry, _OPT_DATA) is True
    ok, reason = champions.champion_is_admissible(entry, _OPT_DATA)
    assert ok is True


def test_matching_both_versions_stays_admissible():
    from automation.optimizer import champions
    entry = _champion_entry(reward_version=19, simulation_version=1)
    ok, reason = champions.champion_is_admissible(entry, _OPT_DATA)
    assert ok is True


def test_legacy_entry_without_simulation_version_is_not_flagged_stale():
    """Ein Pre-#854-Eintrag (integrity.simulation_semantics_version fehlt) wird konservativ NICHT
    als Mismatch gewertet -- kein falscher Datenverlust durch ein Feld, das der Alt-Eintrag noch
    nicht kannte."""
    from automation.optimizer import champions
    entry = _champion_entry(reward_version=19, simulation_version=None)
    assert champions.champion_simulation_stale(entry, _OPT_DATA) is False


def test_store_champion_persists_simulation_semantics_version(tmp_path, monkeypatch):
    from automation.optimizer import champions

    class _FakeOptunaStudy:
        def __init__(self, best_value=1.0, directions=None):
            self.best_value = best_value
            self.directions = directions or ["maximize"]

    monkeypatch.setattr(champions, "WORK", tmp_path)
    study = _FakeOptunaStudy()
    promotion = {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 33}, "R_symbol": 1.0, "R_global": 0.0,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }
    path = champions.store_champion(
        study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
        catalog_newest_ns=1000, opt_data=_OPT_DATA, run_id="run1")
    assert path is not None
    stored = json.loads(path.read_text("utf-8"))
    assert stored["integrity"]["simulation_semantics_version"] == 1


def test_store_champion_quarantines_simulation_stale_existing_entry_when_candidate_inadmissible(
        tmp_path, monkeypatch):
    """AK-2, dieselbe Mechanik wie der bestehende params_schema_version-Mismatch-Pfad (#821): ein
    bestehender Champion mit veralteter simulation_semantics_version wird nach _stale/
    quarantaeniert, WENN der Nachfolge-Kandidat selbst inadmissibel ist (hier: unter dem
    Qualitaets-Floor) -- sonst wuerde der Alt-Eintrag sonst still verloren gehen (weder aktiver
    Store-Eintrag noch archiviert)."""
    from automation.optimizer import champions

    class _FakeOptunaStudy:
        def __init__(self, best_value=1.0, directions=None):
            self.best_value = best_value
            self.directions = directions or ["maximize"]

    monkeypatch.setattr(champions, "WORK", tmp_path)
    champions_dir = tmp_path / "champions"
    champions_dir.mkdir()
    stale_entry = _champion_entry(reward_version=19, simulation_version=0, r_symbol=2.0)
    (champions_dir / "champion_SmaCrossoverStrategy_TSLA_ETORO.json").write_text(
        json.dumps(stale_entry), "utf-8")

    study = _FakeOptunaStudy()
    promotion = {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 33}, "R_symbol": -0.5, "R_global": 0.0,  # unter dem Floor
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }
    result = champions.store_champion(
        study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
        catalog_newest_ns=1000, opt_data=_OPT_DATA, run_id="run1")

    assert result is None
    quarantine_dir = champions_dir / "_stale"
    assert quarantine_dir.exists()
    quarantined_files = list(quarantine_dir.glob("*.json"))
    assert len(quarantined_files) == 1
    quarantined = json.loads(quarantined_files[0].read_text("utf-8"))
    assert quarantined["integrity"]["simulation_semantics_version"] == 0  # unveraendert archiviert


def test_store_champion_replaces_simulation_stale_entry_without_quarantine_when_candidate_admissible(
        tmp_path, monkeypatch):
    """Ist der neue Kandidat trotz simulation_semantics_version-Mismatch admissibel, wird der
    Store-Pfad einfach ueberschrieben (frischer Lifecycle) -- keine Quarantaene noetig, analog
    #821s params_schema_version-Praezedenzfall."""
    from automation.optimizer import champions

    class _FakeOptunaStudy:
        def __init__(self, best_value=1.0, directions=None):
            self.best_value = best_value
            self.directions = directions or ["maximize"]

    monkeypatch.setattr(champions, "WORK", tmp_path)
    champions_dir = tmp_path / "champions"
    champions_dir.mkdir()
    stale_entry = _champion_entry(reward_version=19, simulation_version=0, r_symbol=0.5)
    stale_entry["lifecycle"]["corroboration_count"] = 3
    (champions_dir / "champion_SmaCrossoverStrategy_TSLA_ETORO.json").write_text(
        json.dumps(stale_entry), "utf-8")

    study = _FakeOptunaStudy()
    promotion = {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 21}, "R_symbol": 0.8, "R_global": 0.0,  # admissibel
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }
    result = champions.store_champion(
        study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
        catalog_newest_ns=2000, opt_data=_OPT_DATA, run_id="run1")

    assert result is not None
    path = champions._champion_path("SmaCrossoverStrategy", "TSLA.ETORO")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["params"]["sma_period"] == 21
    assert stored["lifecycle"]["corroboration_count"] == 1  # frischer Lifecycle (Simulations-Bruch)
    assert not (champions_dir / "_stale").exists()


# ── AK-5: invariants.check_semantics_version_coherence ──────────────────────────────────────────
def test_check_semantics_version_coherence_passes_when_zero():
    result = inv.check_semantics_version_coherence(0)
    assert result.passed is True
    assert result.severity == "blocking"


def test_check_semantics_version_coherence_fails_when_nonzero():
    result = inv.check_semantics_version_coherence(3)
    assert result.passed is False
    assert result.actual == 3
