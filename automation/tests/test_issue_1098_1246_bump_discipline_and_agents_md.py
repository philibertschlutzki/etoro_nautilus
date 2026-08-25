"""Issue #1098/#1246 (P3, Katalog #1247+) — Semantik-Bump-Disziplin für diesen Katalog.

Symptom: zwei Issues dieses Katalogs ändern Semantik — #1226 (Selektions-Kostenbasis ⇒
reward_semantics_version 24 → 25) und #1228 (Exit-Telemetrie an der Quelle ⇒
simulation_semantics_version-Bump, bereits via #1080 gemergt). Beide erfordern einen SQLite-Purge;
ein Purge zur falschen Zeit vernichtet die Vergleichsbasis für die Abnahme.

Fix:
1. Kein Purge vor Abschluss von Stufe 5 der #1099-Merge-Reihenfolge (dokumentiert in beiden
   betroffenen Schema-Feldern von optimizer.json).
2. #1228-Bump (SIM v5) und #1226-Bump (REWARD v25) werden in EINER gemeinsamen Purge-Aktion
   dokumentiert, als letzte Handlung vor dem Re-Run.
3. AGENTS.md um die Pitfalls #437–446 (Issue #1099 Abschnitt 8) ergänzt.
"""
import json
import re
from pathlib import Path

AGENTS_MD = Path("automation/AGENTS.md").read_text("utf-8")


# ── AGENTS.md: Pitfalls #437-446 ──────────────────────────────────────────────────────────────

def test_all_ten_pitfalls_are_documented_with_headers():
    for n in range(437, 447):
        assert f"Pitfall #{n} —" in AGENTS_MD, f"Pitfall #{n} fehlt als Header in AGENTS.md"


def test_pitfall_numbers_are_globally_unique():
    for n in range(437, 447):
        headers = re.findall(rf"### [^\n]*Pitfall #{n} —", AGENTS_MD)
        assert len(headers) == 1, f"Pitfall #{n} erscheint {len(headers)}x als Header (muss 1x sein)"


def test_each_pitfall_references_the_triggering_issues():
    for n in range(437, 447):
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        line_end = AGENTS_MD.index("\n", header_idx)
        header_line = AGENTS_MD[header_idx:line_end]
        assert "#1098" in header_line or "#1099" in header_line, (
            f"Pitfall #{n}-Header referenziert weder #1098 noch #1099: {header_line}")


def test_each_pitfall_carries_verbatim_text_from_issue_1099():
    for n in range(437, 447):
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        segment_end = min(
            x for x in (
                AGENTS_MD.find("### 🔴 Pitfall #", header_idx + 1),
                AGENTS_MD.find("### 🟡 Pitfall #", header_idx + 1),
                AGENTS_MD.find("### 🟠 Pitfall #", header_idx + 1),
                len(AGENTS_MD),
            ) if x != -1
        )
        segment = AGENTS_MD[header_idx:segment_end]
        assert "**Verbatim (Issue #1099 Abschnitt 8):**" in segment, (
            f"Pitfall #{n} traegt keinen Verbatim-Block aus Issue #1099.")


def test_pitfall_441_names_itself_the_eighth_instance():
    """Stichprobe: die Median-von-Summe/Produkt-Pitfall zaehlt sich selbst explizit als achte
    Instanz derselben Fehlerklasse (#304, #1126, #1173, #1230, #1231, #1229, #1233)."""
    header_idx = AGENTS_MD.index("Pitfall #441 —")
    segment = AGENTS_MD[header_idx:header_idx + 700]
    assert "Achte Instanz" in segment
    assert "#1230" in segment and "#1233" in segment


def test_pitfall_442_names_itself_the_sixth_instance():
    header_idx = AGENTS_MD.index("Pitfall #442 —")
    segment = AGENTS_MD[header_idx:header_idx + 700]
    assert "sechste Instanz" in segment
    assert "#1095" in segment and "#1171" in segment


def test_new_pitfalls_section_header_exists():
    assert "## Neue Pitfalls #437–446" in AGENTS_MD


# ── optimizer.json: gebündelte Purge-Disziplin ────────────────────────────────────────────────

def _optimizer_cfg() -> dict:
    with open("automation/config/optimizer.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_reward_semantics_version_v25_doc_references_the_bundled_purge():
    cfg = _optimizer_cfg()
    doc = cfg["_schema"]["fields"]["reward_semantics_version"]
    assert "#1098/#1246" in doc
    assert "simulation_semantics_version" in doc
    assert "EIN gemeinsamer SQLite-Purge" in doc
    assert "Stufe 5" in doc


def test_simulation_semantics_version_v5_doc_references_the_bundled_purge():
    cfg = _optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    assert "#1098/#1246" in doc
    assert "reward_semantics_version" in doc
    assert "EIN gemeinsamer SQLite-Purge" in doc


def test_both_bumps_are_at_their_expected_final_versions():
    """Sanity: die beiden Bumps, deren Purge-Buendelung hier dokumentiert wird, sind tatsaechlich
    vollzogen (#1078/#1226 -> v25, #1080/#1228 -> v5, bereits vor dieser Session gemergt).

    ``>=`` statt exakter Gleichheit — spaetere Bumps in DERSELBEN #1099-Merge-Reihenfolge (#1248
    reward_semantics_version -> 26/27, #1259/#1129 simulation_semantics_version -> 6) duerfen diesen
    Sanity-Check nicht rueckwirkend brechen (derselbe Musterwechsel wie test_issue_936_version_
    bumps.py bei jedem spaeteren Bump)."""
    cfg = _optimizer_cfg()
    assert cfg["reward_semantics_version"] >= 25
    assert cfg["simulation_semantics_version"] >= 5


def test_optimizer_json_still_parses_as_valid_json():
    cfg = _optimizer_cfg()
    assert isinstance(cfg, dict)
