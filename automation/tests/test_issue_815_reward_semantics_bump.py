"""Issue #815 (P2) — `reward_semantics_version` 16 → 17 + SQLite-Purge (LETZTE Aktion vor dem
Re-Run für den #746-Katalog).

Vier Auslöser bündeln gestempelte Trial-Wert-/Eligibility-Änderungen aus Kohorte A/C des
#746-Katalogs (je einzeln ausreichend, laut #815-Issue-Text): `#801`/`#802` (Log-Return-Identität
gegen NaN/±inf abgesichert + REJECT_OOS_INVALID_METRICS), `#803` (Eligibility-Definition um
trial-lokale Kohärenzverletzungen erweitert), `#812` (Selektionsregel `recalibrate` → `drop_arm`),
`#813`/`#814` (familienweite DSR-Multiplizität ändert sich: oos_period_returns für ALLE
oos_evaluated Trials + `deflation_family_floor_mode` Default `'attempted'`). Der `_SCALE_KEYS`-
Fingerprint (`test_issue_637`) bleibt unverändert — reiner Wert-/Eligibility-Bump, analog
v10/v11/v13/v15/v16.
"""
import json
from pathlib import Path

import pytest

CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))


def test_reward_semantics_version_is_17():
    assert CFG["reward_semantics_version"] >= 17


def test_version_is_documented_with_v17_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v17" in doc
    for ref in ("#801", "#802", "#803", "#812", "#813", "#814"):
        assert ref in doc, f"v17-Changelog muss {ref} referenzieren"


def test_v17_changelog_explains_why_other_catalog_issues_did_not_trigger_bump():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v17_segment = doc[doc.index("v17 ="):]
    for ref in ("#804", "#805", "#806", "#807", "#808", "#809", "#810", "#811"):
        assert ref in v17_segment, f"v17-Changelog muss begründen, warum {ref} keinen Bump auslöste"


def test_v17_changelog_documents_eligibility_and_value_change_not_pure_telemetry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v17_segment = doc[doc.index("v17 ="):]
    assert "eligib" in v17_segment.lower()
    assert "wert" in v17_segment.lower() or "Wert" in v17_segment


def test_scale_keys_fingerprint_unchanged_between_v16_and_v17():
    """Keiner der vier v17-Auslöser (#801/#802/#803 Inferenz-/Eligibility-Ebene, #812/#813/#814
    Selektions-/Multiplizitäts-Ebene) berührt einen Reward-SKALEN-Key — der v9-Fingerprint
    (test_issue_637) bleibt daher unverändert."""
    from automation.tests.test_issue_637_reward_semantics_bump import _SCALE_KEYS
    for k in _SCALE_KEYS:
        assert k in CFG, f"{k} muss weiterhin ein reales Config-Feld sein (Fingerprint-Stabilität)"


# ── Akzeptanzkriterium #815/1: geladene v16-Study bricht fail-loud ab ─────────────────────────────
def test_stale_v16_study_is_rejected_fail_loud():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 16}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v16"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)


def test_fresh_study_stamps_v17_without_error():
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
    # Issue #834 — nicht mehr hart auf 17 gepinnt (die exakte AKTUELLE Version wird seither in
    # test_issue_834_reward_semantics_bump.py gepinnt, analog zur v15->v16-Uebergabe in #781).
    assert study.user_attrs["reward_semantics_version"] == CFG["reward_semantics_version"]


def test_matching_v17_version_is_a_no_op():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": CFG["reward_semantics_version"]}
            self.trials = [object()] * 20
            self.study_name = "study_v17"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    _check_reward_semantics_version(_FakeStudy(), CFG)  # muss NICHT raisen


# ── Akzeptanzkriterium #815/2: purge_stale_studies erkennt eine v16-Study als stale ───────────────
def test_purge_stale_studies_flags_v16_study_against_v17_current(tmp_path, monkeypatch):
    import optuna
    from automation.optimizer import purge_stale_studies as pss

    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    db_path = sweep_dir / "study_Foo_Bar.db"
    study = optuna.create_study(study_name="study_Foo_Bar",
                                storage=f"sqlite:///{db_path}", load_if_exists=True)
    study.set_user_attr("reward_semantics_version", 16)
    trial = study.ask()
    study.tell(trial, 1.0)

    found = pss.find_stale_study_dbs(sweep_dir=sweep_dir, current_version=17)
    assert any(f["study_name"] == "study_Foo_Bar" for f in found)


def test_purge_stale_studies_reads_current_version_from_optimizer_json(tmp_path, monkeypatch):
    """Akzeptanzkriterium #815: der Bulk-Purge liest reward_semantics_version dynamisch aus
    optimizer.json (nicht aus einer eingefrorenen Konstante) -- ein kuenftiger Bump erfordert
    keinen Code-Change in purge_stale_studies.py selbst. Dynamischer Vergleich gegen CFG (Issue
    #834 bumpte 17 -> 18) statt eines hart gepinnten Werts, analog den uebrigen Tests dieser Datei."""
    from automation.optimizer import purge_stale_studies as pss
    assert (pss._current_reward_semantics_version(base_cfg=Path("automation/config"))
           == CFG["reward_semantics_version"])
