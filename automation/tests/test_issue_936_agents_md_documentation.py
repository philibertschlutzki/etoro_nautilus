"""Issue #936 (P0, letzte Aktion des Katalogs) — AGENTS.md-Dokumentation fuer den Katalog
#913-#936 (GitHub-Issues #774/#775/#776/#777). Traegt die 14 in den vier Issue-Texten vollstaendig
ausformulierten neuen Pitfalls (#293-#306) nach, analog der etablierten Pitfall-Kompendium-
Konvention (siehe test_issue_912_agents_md_documentation.py fuer den unmittelbaren Vorgaenger-
Katalog #897-#912).
"""
import json
from pathlib import Path

AGENTS_MD = Path("automation/AGENTS.md").read_text("utf-8")
CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))

_PITFALL_ISSUE_MAP = {
    293: "#913",
    294: "#914",
    295: "#915",
    296: "#913",
    297: "#920",
    298: "#920",
    299: "#924",
    300: "#925",
    301: "#926",
    302: "#927",
    303: "#930",
    304: "#931",
    305: "#932",
    306: "#933",
}


def test_pitfall_index_hint_updated_to_306():
    assert "Pitfall #306" in AGENTS_MD


def test_all_fourteen_pitfalls_are_documented_with_headers():
    for n in range(293, 307):
        assert f"Pitfall #{n} —" in AGENTS_MD, f"Pitfall #{n} fehlt als Header in AGENTS.md"


def test_each_pitfall_references_its_triggering_issue():
    for n, issue_ref in _PITFALL_ISSUE_MAP.items():
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        segment = AGENTS_MD[header_idx:header_idx + 2500]
        assert issue_ref in segment, f"Pitfall #{n} referenziert {issue_ref} nicht im Nahbereich"


def test_each_pitfall_has_symptom_root_cause_and_fix_sections():
    for n in range(293, 307):
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        next_header_idx = AGENTS_MD.find("### 🟢 Pitfall #", header_idx + 1)
        if next_header_idx == -1:
            next_header_idx = AGENTS_MD.find("### 📋", header_idx + 1)
        segment = AGENTS_MD[header_idx:next_header_idx]
        assert "**Symptom:**" in segment, f"Pitfall #{n} hat keinen Symptom-Abschnitt"
        assert "**Root-Cause:**" in segment, f"Pitfall #{n} hat keinen Root-Cause-Abschnitt"
        assert "**Fix/Regel:**" in segment, f"Pitfall #{n} hat keinen Fix/Regel-Abschnitt"


# ── Katalog-Index #913-#936 ──────────────────────────────────────────────────────────────────────
def test_catalog_913_936_section_header_exists():
    assert "## Issue-Katalog #913–#936" in AGENTS_MD


def test_catalog_913_936_references_all_four_github_issues():
    idx = AGENTS_MD.index("## Issue-Katalog #913–#936")
    segment = AGENTS_MD[idx:idx + 500]
    for issue in ("#774", "#775", "#776", "#777"):
        assert issue in segment, f"Katalog-Header muss GitHub-Issue {issue} referenzieren"


def test_toc_links_to_the_new_catalog_section():
    assert "Issue-Katalog #913–#936" in AGENTS_MD.split("## 1. Produktübersicht")[0]


# ── Neue/geänderte Config-Keys + Watertight Invariants Subsections ──────────────────────────────
def test_config_keys_and_invariants_subsections_exist_for_new_catalog():
    idx = AGENTS_MD.index("## Issue-Katalog #913–#936")
    segment = AGENTS_MD[idx:]
    assert "### 📋 Neue/geänderte Config-Keys (Issue-Katalog #913–#936)" in segment
    assert "### 🔒 Watertight Invariants (Issue-Katalog #913–#936)" in segment


def test_changelog_table_has_a_row_for_the_new_catalog():
    assert "Implementierung Issue-Katalog #913–#936" in AGENTS_MD


def test_pre_existing_269_284_backlog_note_is_preserved_not_lost():
    """Der aus #897-#912 dokumentierte Rueckstand (Pitfalls #269-#284) ist NICHT Teil dieses
    Katalogs -- der Hinweis darauf muss unveraendert erhalten bleiben, nicht stillschweigend
    verschwinden, weil ein neuer Katalog dazukam."""
    idx = AGENTS_MD.index("Dokumentationsrückstand")
    segment = AGENTS_MD[idx:idx + 1500]
    for ref in ("#269", "#284", "#856", "#896"):
        assert ref in segment, f"Rueckstands-Hinweis muss {ref} referenzieren"
