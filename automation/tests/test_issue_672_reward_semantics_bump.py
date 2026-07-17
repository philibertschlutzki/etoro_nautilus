"""Issue #672 — `reward_semantics_version` 10 → 11 + Pflicht-SQLite-Purge (letzte Aktion vor Re-Run).

Der Issue-Katalog #663–#672 verschiebt die Feasible-Region der Reward-Landschaft NUR über EINEN
tatsächlich default-aktiven Eligibility-Codepfad-Wechsel: #666 (|Benchmark|-bewusstes,
regime-symmetrisches Excess-Return-Gate — im Bär-Markt verlangt es jetzt einen echten positiven
risikoadjustierten Return statt eines im fallenden Markt trivial erfüllten absoluten Excess-Deltas).
Die übrigen Katalog-Issues sind Confirm-/Telemetrie-only (#663/#665/#670/#671, kein gespeicherter
Trial-Reward betroffen) oder bewusst opt-in mit bit-identischem Default (#664/#667/#668/#669).

Fix: `reward_semantics_version` bumpt von 10 auf 11; der bestehende Fail-Loud-Guard
(`_check_reward_semantics_version`, #410/#468/#575/#658) verweigert das Mischen von Trials über die
Versionsgrenze unverändert strukturell (REJECT_STALE_STUDY_SEMANTICS ⇒ Study-Purge) — dieser Test
pinnt den konkreten v11-Bump und beweist die Guard-Wirkung dafür.
"""
import json
from pathlib import Path

import pytest

CFG_PATH = Path("automation/config/optimizer.json")
CFG = json.loads(CFG_PATH.read_text("utf-8"))


def test_reward_semantics_version_is_11():
    assert CFG["reward_semantics_version"] == 11


def test_version_is_documented_with_v11_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v11" in doc
    for ref in ("#666", "#672"):
        assert ref in doc, f"v11-Changelog muss {ref} referenzieren"


def test_v11_changelog_documents_eligibility_not_scale_change():
    """Der v11-Bump ist ausdrücklich als Eligibility-Semantik-Änderung dokumentiert (nicht als
    Reward-Skalen-Änderung) — Unterscheidungsmerkmal für künftige Agenten, die den Changelog lesen."""
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v11_start = doc.index("v11 =")
    v11_segment = doc[v11_start:]
    assert "Eligibility" in v11_segment or "eligib" in v11_segment.lower()


def test_v11_changelog_explains_why_other_catalog_issues_did_not_trigger_bump():
    """Der Changelog muss explizit begründen, warum #663/#665/#670/#671 (Confirm-/Telemetrie-only)
    und #664/#667/#668/#669 (opt-in, bit-identischer Default) NICHT selbst den Bump auslösten —
    sonst bleibt unklar, ob der Katalog vollständig eligibility-neutral verarbeitet wurde."""
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v11_start = doc.index("v11 =")
    v11_segment = doc[v11_start:]
    for ref in ("#663", "#664", "#667", "#668", "#669"):
        assert ref in v11_segment, f"v11-Changelog muss begründen, warum {ref} keinen Bump auslöste"


def test_stale_pre_v11_study_is_rejected_fail_loud():
    """Integrationstest: eine Study, die unter v10 (Pre-#666-Regime-Symmetrie) akkumulierte Trials
    trägt, wird beim Laden gegen die aktuelle v11-Config fail-loud abgelehnt
    (REJECT_STALE_STUDY_SEMANTICS ⇒ Pflicht-Purge)."""
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 10}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v10"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)


def test_fresh_study_stamps_v11_without_error():
    """Eine frische Study (keine Trials) wird klaglos mit der aktuellen Version (11) gestempelt —
    kein Purge nötig, da keine Alt-Trials zu vergiften wären."""
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
    assert study.user_attrs["reward_semantics_version"] == 11


def test_matching_v11_version_is_a_no_op():
    """Eine Study, die bereits mit der aktuellen Version (11) gestempelt ist, wird nicht erneut als
    stale erkannt (kein falsches Positiv bei jedem Trial-Callback)."""
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 11}
            self.trials = [object()] * 20
            self.study_name = "study_v11"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    _check_reward_semantics_version(_FakeStudy(), CFG)  # muss NICHT raisen


def test_rerun_runbook_documents_purge_as_last_step():
    """Akzeptanzkriterium (#672): das Re-Run-Runbook dokumentiert den Purge als letzten Schritt."""
    manual = Path("manuals/strategie_optimierung.md").read_text("utf-8")
    assert "Re-Run-Runbook" in manual
    assert "sweep/*.db" in manual
    assert "LETZTE Aktion" in manual or "letzte Aktion" in manual.lower() or "letzten Schritt" in manual
