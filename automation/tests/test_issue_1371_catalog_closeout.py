"""Issue #1371 (GH #1268, P0, letzte Aktion) — Katalog-Abschluss: Sammel-Bump ``simulation_semantics_version``
8 → 9, ``reward_semantics_version`` bleibt 27, ``catalog_schema_version`` bleibt 2, AGENTS.md-Pitfalls
#483–#493 und ein Änderungsprotokoll-Eintrag; der Purge ist die letzte Aktion vor dem Re-Run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CFG = json.loads((_REPO / "automation" / "config" / "optimizer.json").read_text("utf-8"))
_AGENTS = (_REPO / "automation" / "AGENTS.md").read_text("utf-8")


def test_simulation_semantics_version_is_9_with_the_trigger_reasoning():
    assert _CFG["simulation_semantics_version"] == 9
    doc = _CFG["_schema"]["fields"]["simulation_semantics_version"]
    v9 = doc[doc.index("v9 = "):]
    for trigger in ("#1354", "#1355", "#1356", "#1357", "#1359", "#1366"):
        assert trigger in v9, trigger
    assert "KEINE Ausloeser" in v9 and "purge_stale_studies" in v9


def test_reward_and_catalog_schema_versions_are_unchanged():
    from automation.api_backfiller import CATALOG_SCHEMA_VERSION

    assert _CFG["reward_semantics_version"] == 27
    assert CATALOG_SCHEMA_VERSION == 2


def test_purge_recognises_the_bump(tmp_path):
    from automation.optimizer import purge_stale_studies as psq

    assert psq._current_simulation_semantics_version(base_cfg=_REPO / "automation" / "config") == 9


def test_agents_md_carries_pitfalls_483_to_493_once_each():
    for n in range(483, 494):
        assert len(re.findall(rf"^### .*Pitfall #{n} — ", _AGENTS, flags=re.M)) == 1, n
    assert "## Neue Pitfalls #483–#493" in _AGENTS


def test_agents_md_change_log_entry_lists_every_issue_of_the_catalog():
    section = _AGENTS[_AGENTS.index("## Issue-Katalog #1354–#1371"):_AGENTS.index("## Neue Pitfalls #483–#493")]
    assert "Änderungsprotokoll" in section
    for issue in (1354, 1356, 1357, 1358, 1359, 1360, 1361, 1362, 1363, 1364, 1365, 1366, 1367, 1368,
                  1369, 1370, 1371):
        assert f"**#{issue}**" in section, issue
    assert "Duplikat" in section and "#1262" in section
