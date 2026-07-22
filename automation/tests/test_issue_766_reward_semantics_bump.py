"""Issue #766 (P3) — `reward_semantics_version` 14 → 15 + Pflicht-Purge (letzte Aktion vor Re-Run).

#756 (Log-Returns fuer den Sortino-Zaehler) und #757 (Bootstrap-Standardfehler fuer PSR/DSR) aendern
den NUMERISCHEN Wert bzw. die Eligibility-Entscheidung bereits gespeicherter Trial-Metriken — Alt-
Trials (< v15) sind mit v15 nicht vergleichbar. #764 wird vom Issue-Katalog selbst als dritter
Ausloeser genannt, traegt in DIESER Sitzung aber NICHTS zur Reward-Wert-Aenderung bei (reine additive
Report-Infrastruktur, keine Gewichts-/Term-Aenderung — siehe Changelog-Praezisierung); #756+#757
allein sind bereits hinreichend fuer den Bump (dieselbe Struktur wie v11).
"""
import json
from pathlib import Path

import pytest

CFG_PATH = Path("automation/config/optimizer.json")
CFG = json.loads(CFG_PATH.read_text("utf-8"))


def test_reward_semantics_version_is_15():
    # Forward-kompatibel (analog test_issue_686/658/672/711): ein kuenftiger v16-Bump darf diesen
    # Test nicht brechen — die v15-Changelog-Pruefungen unten bleiben unabhaengig davon exakt pinnbar.
    assert CFG["reward_semantics_version"] >= 15


def test_version_is_documented_with_v15_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v15" in doc
    for ref in ("#756", "#757", "#764", "#766"):
        assert ref in doc, f"v15-Changelog muss {ref} referenzieren"


def test_v15_changelog_explains_why_kohorte_a_did_not_trigger_bump():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v15_segment = doc[doc.index("v15 ="):]
    for ref in ("#753", "#754", "#755"):
        assert ref in v15_segment, f"v15-Changelog muss begruenden, warum {ref} keinen Bump ausloeste"


def test_v15_changelog_clarifies_764_did_not_change_reward_values():
    """Die #764-Praezisierung ist Pflicht: der Issue-Katalog nennt #764 als Ausloeser, die tatsaechlich
    umgesetzte Aenderung in dieser Sitzung war aber reine Report-Infrastruktur ohne Gewichts-/Term-
    Aenderung — das MUSS im Changelog explizit stehen, sonst suggeriert der Bump eine Kalibrierung,
    die nie stattfand."""
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v15_segment = doc[doc.index("v15 ="):]
    assert "KEIN Reward-Term wurde entfernt" in v15_segment or "KEIN Gewicht" in v15_segment
    assert "v16" in v15_segment  # kuenftige echte Rekalibrierung braucht einen eigenen Bump


def test_v15_changelog_references_purge_tool():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v15_segment = doc[doc.index("v15 ="):]
    assert "purge_stale_studies" in v15_segment


def test_stale_pre_v15_study_is_rejected_fail_loud():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 14}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v14"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)


def test_fresh_study_stamps_v15_without_error():
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


def test_matching_v15_version_is_a_no_op():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": CFG["reward_semantics_version"]}
            self.trials = [object()] * 20
            self.study_name = "study_v15"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    _check_reward_semantics_version(_FakeStudy(), CFG)  # muss NICHT raisen
