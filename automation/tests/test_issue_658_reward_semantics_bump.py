"""Issue #658 — `reward_semantics_version` 9 → 10 + Pflicht-Purge.

#649/#650/#657 (Kohorte A) ändern die EFFEKTIVE Eligibility-Definition (welche Trials feasible sind)
und damit die Feasible-Region der Reward-Landschaft materiell — auch ohne die Reward-SKALA selbst
(`compute_reward`/`optimizer.json`-Gewichte) zu berühren. Alt-Trials in bestehenden SQLite-Studies
wurden unter der ALTEN (defekten) Gate-Semantik bewertet und vergiften den TPE-Surrogat, wenn die
Study weiterverwendet wird.

Fix: `reward_semantics_version` bumpt von 9 auf 10; der bestehende Fail-Loud-Guard
(`_check_reward_semantics_version`, #410/#468/#575) verweigert das Mischen von Trials über die
Versionsgrenze unverändert strukturell (REJECT_STALE_STUDY_SEMANTICS ⇒ Study-Purge) — dieser Test
pinnt den konkreten v10-Bump und beweist die Guard-Wirkung dafür.
"""
import json
from pathlib import Path

import pytest

CFG_PATH = Path("automation/config/optimizer.json")
CFG = json.loads(CFG_PATH.read_text("utf-8"))


def test_reward_semantics_version_is_10():
    assert CFG["reward_semantics_version"] == 10


def test_version_is_documented_with_v10_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v10" in doc
    for ref in ("#649", "#650", "#657", "#658"):
        assert ref in doc, f"v10-Changelog muss {ref} referenzieren"


def test_v10_changelog_documents_eligibility_not_scale_change():
    """Der v10-Bump ist ausdrücklich als Eligibility-Semantik-Änderung dokumentiert (nicht als
    Reward-Skalen-Änderung) — Unterscheidungsmerkmal für künftige Agenten, die den Changelog lesen."""
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v10_start = doc.index("v10 =")
    v10_segment = doc[v10_start:]
    assert "Eligibility" in v10_segment or "eligib" in v10_segment.lower()


def test_stale_pre_v10_study_is_rejected_fail_loud():
    """Integrationstest: eine Study, die unter v9 (Pre-Kohorte-A-Gate-Semantik) akkumulierte Trials
    trägt, wird beim Laden gegen die aktuelle v10-Config fail-loud abgelehnt
    (REJECT_STALE_STUDY_SEMANTICS ⇒ Pflicht-Purge)."""
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 9}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v9"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)


def test_fresh_study_stamps_v10_without_error():
    """Eine frische Study (keine Trials) wird klaglos mit der aktuellen Version gestempelt — kein
    Purge nötig, da keine Alt-Trials zu vergiften wären."""
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
    assert study.user_attrs["reward_semantics_version"] == 10


def test_matching_version_is_a_no_op():
    """Eine Study, die bereits mit der aktuellen Version (10) gestempelt ist, wird nicht erneut
    als stale erkannt (kein falsches Positiv bei jedem Trial-Callback)."""
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 10}
            self.trials = [object()] * 20
            self.study_name = "study_v10"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    _check_reward_semantics_version(_FakeStudy(), CFG)  # muss NICHT raisen
