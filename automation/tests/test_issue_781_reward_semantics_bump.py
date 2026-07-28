"""Issue #781 (P3) — `reward_semantics_version` 15 → 16 + Pflicht-Purge (letzte Aktion vor dem
Re-Run).

Fünf Auslöser bündeln gestempelte Trial-Wert-/Eligibility-Änderungen aus dem #743/#742-Katalog:
`#771`/`#772` (Renditeserie-Kohärenz), `#774`/`#775` (Kostenmodell), `#776` (Gate-Konsolidierung),
`#784` (DSR-Familien-Multiplizität), `#788` (Win-Rate-/OOS-Metrik-Sentinel-Generierung). Der
`_SCALE_KEYS`-Fingerprint (`test_issue_637`) bleibt unverändert — reiner Wert-/Eligibility-Bump,
analog v10/v11/v13/v15.
"""
import json
from pathlib import Path

import pytest

CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))


def test_reward_semantics_version_is_16():
    assert CFG["reward_semantics_version"] >= 16


def test_version_is_documented_with_v16_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v16" in doc
    for ref in ("#771", "#772", "#774", "#775", "#776", "#784", "#788"):
        assert ref in doc, f"v16-Changelog muss {ref} referenzieren"


def test_v16_changelog_explains_why_other_catalog_issues_did_not_trigger_bump():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v16_segment = doc[doc.index("v16 ="):]
    for ref in ("#768", "#769", "#770", "#773", "#777", "#778", "#780", "#787", "#792"):
        assert ref in v16_segment, f"v16-Changelog muss begründen, warum {ref} keinen Bump auslöste"


def test_v16_changelog_documents_eligibility_and_value_change_not_pure_telemetry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v16_segment = doc[doc.index("v16 ="):]
    assert "eligib" in v16_segment.lower()
    assert "wert" in v16_segment.lower() or "Wert" in v16_segment


def test_scale_keys_fingerprint_unchanged_between_v15_and_v16():
    """#774 widmet einen BESTEHENDEN Skalen-Key (penalty_turnover_weight) zum Fallback um, führt
    aber keinen NEUEN ein — der v9-Fingerprint (test_issue_637) bleibt daher unverändert."""
    from automation.tests.test_issue_637_reward_semantics_bump import _SCALE_KEYS
    assert "penalty_turnover_weight" in _SCALE_KEYS
    for k in _SCALE_KEYS:
        assert k in CFG, f"{k} muss weiterhin ein reales Config-Feld sein (Fingerprint-Stabilität)"


# ── Akzeptanzkriterium #781/1: geladene v15-Study bricht fail-loud ab ─────────────────────────────
def test_stale_v15_study_is_rejected_fail_loud():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 15}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v15"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)


def test_fresh_study_stamps_v16_without_error():
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
    # Issue #815 — nicht mehr hart auf 16 gepinnt (die exakte AKTUELLE Version wird seither in
    # test_issue_815_reward_semantics_bump.py gepinnt, analog zur v9->v10-Uebergabe in #637).
    assert study.user_attrs["reward_semantics_version"] == CFG["reward_semantics_version"]


def test_matching_v16_version_is_a_no_op():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": CFG["reward_semantics_version"]}
            self.trials = [object()] * 20
            self.study_name = "study_v16"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    _check_reward_semantics_version(_FakeStudy(), CFG)  # muss NICHT raisen


# ── Akzeptanzkriterium #781/2: purge_stale_studies erkennt eine v15-Study als stale ───────────────
def test_purge_stale_studies_flags_v15_study_against_v16_current(tmp_path, monkeypatch):
    import optuna
    from automation.optimizer import purge_stale_studies as pss

    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    db_path = sweep_dir / "study_Foo_Bar.db"
    study = optuna.create_study(study_name="study_Foo_Bar",
                                storage=f"sqlite:///{db_path}", load_if_exists=True)
    study.set_user_attr("reward_semantics_version", 15)
    trial = study.ask()
    study.tell(trial, 1.0)

    found = pss.find_stale_study_dbs(sweep_dir=sweep_dir, current_version=16)
    assert any(f["study_name"] == "study_Foo_Bar" for f in found)
