"""GH #1272 (Katalog #1352–#1353) — AGENTS.md trägt die zuvor nur reservierten Pitfalls #481–#482 und
einen Änderungsprotokoll-Eintrag zum Katalog.
"""
from __future__ import annotations

import re
from pathlib import Path

_AGENTS = (Path(__file__).resolve().parents[2] / "automation" / "AGENTS.md").read_text("utf-8")


def test_pitfalls_481_and_482_exist_once_each_between_480_and_483():
    positions = {}
    for n in (480, 481, 482, 483):
        hits = [m.start() for m in re.finditer(rf"^### .*Pitfall #{n} — ", _AGENTS, flags=re.M)]
        assert len(hits) == 1, n
        positions[n] = hits[0]
    assert positions[480] < positions[481] < positions[482] < positions[483]
    assert "#481–#482 sind durch #1352–#1353 reserviert" not in _AGENTS


def test_pitfall_bodies_are_verbatim_from_the_catalog():
    assert ("Eine Validierung, die an einer wiederverwendbaren Stelle existiert, gehört direkt hinter jede "
            "Schreiboperation, nicht nur an den bequemsten Lesepunkt.") in _AGENTS
    assert ("muss explizit nach bewusst entkoppelten Zweitimplementierungen desselben Pfads suchen, nicht nur "
            "nach den über `catalog_paths.py` verdrahteten Konsumenten.") in _AGENTS


def test_change_log_lists_both_catalog_entries():
    section = _AGENTS[_AGENTS.index("## Issue-Katalog #1352–#1353"):_AGENTS.index("## Neue Pitfalls #481–#482")]
    assert "Änderungsprotokoll" in section
    for entry in ("**#1352** (#1270", "**#1353** (#1271"):
        assert entry in section, entry
    assert "InstrumentTypeID" in section and "kein Versions-Bump" in section
