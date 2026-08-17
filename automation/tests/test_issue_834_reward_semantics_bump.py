"""Issue #834 (Katalog #822-#835, GitHub-Issue #751) — `reward_semantics_version` 17 → 18 +
SQLite-Purge (LETZTE Aktion vor dem Re-Run für den #750/#751-Katalog).

Vier Auslöser bündeln gestempelte Trial-Wert-/Eligibility-/Multiplizitäts-Änderungen aus Kohorte B
des #750-Katalogs (je einzeln ausreichend, laut #834-Issue-Text; kritischer Pfad
`#823 → #822 → #824 → #826`): `#822` (n_family zählt Trials mit definierter Teststatistik statt
`oos_evaluated`), `#823` (Sortino-/Downside-Schätzer auf der informativen Teilmenge statt der
vollen Kalender-Bar-Achse), `#824` (PSR-Bootstrap-Standardfehler auf derselben informativen
Teilmenge), `#826` (`promotion_family_scope='per_strategy'` — N1 je Study statt symbolweit
summiert). Der `_SCALE_KEYS`-Fingerprint (`test_issue_637`) bleibt unverändert — reiner
Wert-/Eligibility-/Multiplizitäts-Bump, analog v10/v11/v13/v15/v16/v17.
"""
import json
from pathlib import Path

import pytest

CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))


def test_reward_semantics_version_is_18():
    """Issue #854 bumpte die Version weiter auf 19 (#848-Eligibility-Bump) — dieser Test pinnt nur
    noch die historische UNTERGRENZE (die v18-Migration ist irreversibel abgeschlossen), analog
    test_issue_781/815s eigener Softening beim jeweils naechsten Bump."""
    assert CFG["reward_semantics_version"] >= 18


def test_version_is_documented_with_v18_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v18" in doc
    for ref in ("#822", "#823", "#824", "#826"):
        assert ref in doc, f"v18-Changelog muss {ref} referenzieren"


def test_v18_changelog_explains_why_other_catalog_issues_did_not_trigger_bump():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v18_segment = doc[doc.index("v18 ="):]
    for ref in ("#825", "#827", "#828", "#829", "#830", "#831", "#832", "#833"):
        assert ref in v18_segment, f"v18-Changelog muss begründen, warum {ref} keinen Bump auslöste"


def test_v18_changelog_documents_eligibility_and_value_change_not_pure_telemetry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v18_segment = doc[doc.index("v18 ="):]
    assert "eligib" in v18_segment.lower()
    assert "wert" in v18_segment.lower() or "Wert" in v18_segment


def test_v18_changelog_documents_champion_store_and_diagnosed_pairs_survive_the_purge():
    """Akzeptanzkriterium #834 — der Champion-Store (#819) UND diagnosed_pairs_cache.json (#761)
    werden NICHT mitgepurgt; nur die Optuna-SQLite-Studies sind betroffen."""
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v18_segment = doc[doc.index("v18 ="):]
    assert "champion" in v18_segment.lower()
    assert "diagnosed_pairs_cache" in v18_segment


def test_scale_keys_fingerprint_unchanged_between_v17_and_v18():
    """Keiner der vier v18-Auslöser (#822/#826 Selektions-/Multiplizitäts-Ebene, #823/#824
    Inferenz-Korrektheit der Sortino-/PSR-Schätzer) berührt einen Reward-SKALEN-Key — der
    v9-Fingerprint (test_issue_637) bleibt daher unverändert."""
    from automation.tests.test_issue_637_reward_semantics_bump import _SCALE_KEYS
    for k in _SCALE_KEYS:
        assert k in CFG, f"{k} muss weiterhin ein reales Config-Feld sein (Fingerprint-Stabilität)"


# ── Akzeptanzkriterium #834/1: geladene v17-Study bricht fail-loud ab ─────────────────────────────
def test_stale_v17_study_is_rejected_fail_loud():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 17}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v17"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)


def test_fresh_study_stamps_v18_without_error():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {}
            self.trials = []
            self.study_name = "study_fresh"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    study = _FakeStudy()
    _check_reward_semantics_version(study, CFG)
    assert study.user_attrs["reward_semantics_version"] == CFG["reward_semantics_version"]


def test_matching_v18_version_is_a_no_op():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": CFG["reward_semantics_version"]}
            self.trials = [object()] * 20
            self.study_name = "study_v18"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    _check_reward_semantics_version(_FakeStudy(), CFG)  # muss NICHT raisen


# ── Akzeptanzkriterium #834/2: purge_stale_studies erkennt eine v17-Study als stale ────────────────
def test_purge_stale_studies_flags_v17_study_against_v18_current(tmp_path, monkeypatch):
    import optuna
    from automation.optimizer import purge_stale_studies as pss

    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    db_path = sweep_dir / "study_Foo_Bar.db"
    study = optuna.create_study(study_name="study_Foo_Bar",
                                storage=f"sqlite:///{db_path}", load_if_exists=True)
    study.set_user_attr("reward_semantics_version", 17)
    trial = study.ask()
    study.tell(trial, 1.0)

    found = pss.find_stale_study_dbs(sweep_dir=sweep_dir, current_version=18)
    assert any(f["study_name"] == "study_Foo_Bar" for f in found)


def test_purge_stale_studies_reads_current_version_from_optimizer_json():
    """Akzeptanzkriterium #834: der Bulk-Purge liest reward_semantics_version dynamisch aus
    optimizer.json, nicht aus einer eingefrorenen Konstante -- der #854-Folge-Bump (v19) erforderte
    tatsaechlich keinen Code-Change in purge_stale_studies.py selbst, wie hier vorhergesagt."""
    from automation.optimizer import purge_stale_studies as pss
    assert (pss._current_reward_semantics_version(base_cfg=Path("automation/config"))
           == CFG["reward_semantics_version"])


# ── Akzeptanzkriterium #834/3: Champion-Store übersteht den Purge, report zeigt die Migration ──────
def test_purge_stale_studies_function_never_touches_the_champion_store():
    """purge_stale_studies() (die Bibliotheksfunktion, aufgerufen von --purge-scope studies/all)
    operiert AUSSCHLIESSLICH auf sweep_dir (WORK/sweep/*.db) + dessen Eltern-Trial-Baeume -- der
    Champion-Store liegt unter einem voellig separaten Pfad (data/optimizer/champions/) und wird
    von DIESER Funktion nie referenziert.

    Issue #990/#1144 (Katalog #986, Pitfall #412 in AGENTS.md) — das Modul selbst referenziert
    ``champions`` seither BEWUSST (siehe test_purge_scope_champions_only_touches_champion_store
    unten): ``main()``s neues ``--purge-scope {champions,all}`` quarantäniert EXPLIZIT stale
    Champion-Einträge, als OPT-IN-Ergänzung zum weiterhin unveränderten Default
    ``--purge-scope studies``. Diese Prüfung ist deshalb auf die FUNKTION verengt (statt das ganze
    Modul auf die Abwesenheit des Substrings zu prüfen), um genau die ursprüngliche Garantie zu
    erhalten: der SQLite-Study-Purge selbst berührt den Champion-Store nie."""
    import inspect
    from automation.optimizer import purge_stale_studies as pss
    source = inspect.getsource(pss.purge_stale_studies)
    assert "champions" not in source.lower()


def test_purge_scope_defaults_to_studies_only_and_never_calls_champions_quarantine(
        tmp_path, monkeypatch):
    """Issue #990/#1144 — der CLI-Default (kein --purge-scope) bleibt bit-identisch zum
    Pre-#990-Verhalten: quarantine_stale_champion_entries wird NICHT aufgerufen."""
    from automation.optimizer import purge_stale_studies as pss
    from automation.optimizer import champions as champions_mod

    monkeypatch.setattr(pss, "purge_stale_studies", lambda **kwargs: [])
    called = []
    monkeypatch.setattr(
        champions_mod, "quarantine_stale_champion_entries",
        lambda *a, **kw: called.append(True) or [])

    pss.main([])
    assert called == []


def test_purge_scope_champions_only_touches_champion_store(tmp_path, monkeypatch):
    """Issue #990/#1144 — ``--purge-scope champions`` ruft AUSSCHLIESSLICH die Champion-
    Quarantäne auf, NICHT purge_stale_studies() (der SQLite-Study-Purge bleibt unberührt)."""
    from automation.optimizer import purge_stale_studies as pss
    from automation.optimizer import champions as champions_mod

    studies_called = []
    monkeypatch.setattr(
        pss, "purge_stale_studies", lambda **kwargs: studies_called.append(True) or [])
    monkeypatch.setattr(
        champions_mod, "quarantine_stale_champion_entries", lambda *a, **kw: [])

    pss.main(["--purge-scope", "champions"])
    assert studies_called == []


def test_champions_summary_reports_semantics_migrated_count(tmp_path, monkeypatch):
    """Akzeptanzkriterium #834: report.cross_study.champions.semantics_migrated zaehlt die ueber
    einen reward_semantics_version-Bump hinweg MIGRIERTEN (params ueberlebt, quality stale
    markiert) Champion-Store-Eintraege."""
    import json as _json
    from automation.optimizer import report as report_mod
    from automation.optimizer import champions as champions_mod

    champions_dir = tmp_path / "champions"
    champions_dir.mkdir()
    monkeypatch.setattr(champions_mod, "_champions_dir", lambda: champions_dir)

    migrated_entry = {
        "params": {"a": 1}, "lifecycle": {
            "semantics_migrated_from": 17, "corroboration_count": 0, "writeback_applied": False,
        },
    }
    fresh_entry = {
        "params": {"a": 1}, "lifecycle": {
            "semantics_migrated_from": None, "corroboration_count": 0, "writeback_applied": False,
        },
    }
    (champions_dir / "champion_StratA_AAA.ETORO.json").write_text(_json.dumps(migrated_entry), "utf-8")
    (champions_dir / "champion_StratB_BBB.ETORO.json").write_text(_json.dumps(fresh_entry), "utf-8")
    monkeypatch.setattr(champions_mod, "champion_is_admissible", lambda entry, opt_data: (False, "TEST_SKIP"))

    summary = report_mod._champions_summary({})
    assert summary["semantics_migrated"] == 1
    assert summary["stored"] == 2
