"""Issue #686 — `reward_semantics_version` 11 → 12 + Pflicht-SQLite-Purge (letzte Aktion vor Re-Run).

Der Issue-Katalog #675–#685 (Sitzung 2026-07-17, Lauf 2) ändert die gestempelte oos_eligible-
Semantik eines Trials über GENAU DREI Fixes: #676 (profitable_folds_frac aus dem Default-Gate
entfernt + Nenner-Bugfix), #677 (evaluable_folds aus demselben Grund entfernt + relativer Modus),
#684 (Expectancy-Gate-Fallback bei fehlender Kosten-Telemetrie kostenrelativ statt statisch-13x-
strenger). Die übrigen Katalog-Issues ändern KEINEN gestempelten Trial-Wert (siehe Changelog).
"""
import json
from pathlib import Path

import pytest

CFG_PATH = Path("automation/config/optimizer.json")
CFG = json.loads(CFG_PATH.read_text("utf-8"))


def test_reward_semantics_version_is_12():
    assert CFG["reward_semantics_version"] == 12


def test_version_is_documented_with_v12_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v12" in doc
    for ref in ("#676", "#677", "#684", "#686"):
        assert ref in doc, f"v12-Changelog muss {ref} referenzieren"


def test_v12_changelog_explains_why_other_catalog_issues_did_not_trigger_bump():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v12_start = doc.index("v12 =")
    v12_segment = doc[v12_start:]
    for ref in ("#675", "#678", "#679", "#680", "#681", "#682", "#683", "#685"):
        assert ref in v12_segment, f"v12-Changelog muss begründen, warum {ref} keinen Bump auslöste"


def test_v12_changelog_references_purge_tool():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v12_segment = doc[doc.index("v12 ="):]
    assert "purge_stale_studies" in v12_segment


def test_stale_pre_v12_study_is_rejected_fail_loud():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 11}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v11"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)


def test_fresh_study_stamps_v12_without_error():
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
    assert study.user_attrs["reward_semantics_version"] == 12


def test_matching_v12_version_is_a_no_op():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 12}
            self.trials = [object()] * 20
            self.study_name = "study_v12"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    _check_reward_semantics_version(_FakeStudy(), CFG)  # muss NICHT raisen
