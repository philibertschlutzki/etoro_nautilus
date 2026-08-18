"""Issue #1018/#1170 (letzte Aktion des Katalogs) — AGENTS.md-Nachtrag #1146–#1170 (GitHub-Issues
#994–#1018) und Pitfalls #413–#420, analog der etablierten Pitfall-Kompendium-Konvention (siehe
test_issue_936_agents_md_documentation.py fuer den unmittelbaren Vorgaenger-Katalog #913–#936).
"""
from pathlib import Path

AGENTS_MD = Path("automation/AGENTS.md").read_text("utf-8")

_PITFALL_ISSUE_MAP = {
    413: "#995",
    414: "#996",
    415: "#1000",
    416: "#1001",
    417: "#1003",
    418: "#1004",
    419: "#1010",
    420: "#1011",
}

_CATALOG_GITHUB_ISSUES = [f"#{n}" for n in range(994, 1019)]  # #994 .. #1018 inclusive


def test_pitfall_420_is_documented():
    assert "Pitfall #420" in AGENTS_MD


def test_all_eight_pitfalls_are_documented_with_headers():
    for n in range(413, 421):
        assert f"Pitfall #{n} —" in AGENTS_MD, f"Pitfall #{n} fehlt als Header in AGENTS.md"


def test_pitfall_numbers_are_globally_unique():
    """§16-Konvention: Pitfall-Nummern sind global eindeutig ueber die gesamte Datei -- jede der
    acht neuen Nummern darf als Header-Praefix ('### ... Pitfall #N —') nur EIN Mal vorkommen."""
    import re
    for n in range(413, 421):
        headers = re.findall(rf"### [^\n]*Pitfall #{n} —", AGENTS_MD)
        assert len(headers) == 1, f"Pitfall #{n} erscheint {len(headers)}x als Header (muss 1x sein)"


def test_each_pitfall_references_its_triggering_issue():
    for n, issue_ref in _PITFALL_ISSUE_MAP.items():
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        segment = AGENTS_MD[header_idx:header_idx + 500]
        assert issue_ref in segment, f"Pitfall #{n} referenziert {issue_ref} nicht im Header"


def test_each_pitfall_has_symptom_root_cause_and_fix_sections():
    for n in range(413, 421):
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        next_header_idx = AGENTS_MD.find("### 🔴 Pitfall #", header_idx + 1)
        alt = AGENTS_MD.find("### 🟡 Pitfall #", header_idx + 1)
        alt2 = AGENTS_MD.find("### 🟢 Pitfall #", header_idx + 1)
        candidates = [i for i in (next_header_idx, alt, alt2) if i != -1]
        end_idx = min(candidates) if candidates else len(AGENTS_MD)
        segment = AGENTS_MD[header_idx:end_idx]
        assert "**Symptom:**" in segment, f"Pitfall #{n} hat keinen Symptom-Abschnitt"
        assert "**Root-Cause:**" in segment, f"Pitfall #{n} hat keinen Root-Cause-Abschnitt"
        assert "**Fix/Regel:**" in segment, f"Pitfall #{n} hat keinen Fix/Regel-Abschnitt"


def test_pitfall_414_names_itself_an_extension_of_406():
    """Der Issue-Text verlangt explizit 'Erweiterung von #406' fuer #414."""
    header_idx = AGENTS_MD.index("Pitfall #414 —")
    segment = AGENTS_MD[header_idx:header_idx + 300]
    assert "#406" in segment


# ── Katalog-Index #1146-#1170 ─────────────────────────────────────────────────────────────────────

def test_catalog_1146_1170_section_header_exists():
    assert "## Issue-Katalog #1146–#1170" in AGENTS_MD


def test_catalog_references_the_github_issue_range():
    idx = AGENTS_MD.index("## Issue-Katalog #1146–#1170")
    segment = AGENTS_MD[idx:idx + 500]
    assert "#994" in segment and "#1018" in segment


def test_toc_links_to_the_new_catalog_section():
    assert "Issue-Katalog #1146–#1170" in AGENTS_MD.split("## 1. Produktübersicht")[0]


def test_all_twenty_four_github_issues_are_referenced_somewhere_in_the_catalog_section():
    idx = AGENTS_MD.index("## Issue-Katalog #1146–#1170")
    end_idx = AGENTS_MD.index("## Neue Pitfalls #413", idx)
    segment = AGENTS_MD[idx:end_idx]
    missing = [gh for gh in _CATALOG_GITHUB_ISSUES if gh not in segment]
    assert not missing, f"GitHub-Issues fehlen im Katalog-Abschnitt: {missing}"


def test_config_keys_and_invariants_subsections_exist_for_new_catalog():
    idx = AGENTS_MD.index("## Issue-Katalog #1146–#1170")
    segment = AGENTS_MD[idx:]
    assert "### 📋 Neue/geänderte Config-Keys (Issue-Katalog #1146–#1170)" in segment
    assert "### 🔒 Watertight Invariants (Issue-Katalog #1146–#1170)" in segment


def test_changelog_table_has_a_row_for_the_new_catalog():
    assert "Implementierung Issue-Katalog #1146–#1170" in AGENTS_MD


def test_pre_existing_backlog_notes_are_preserved_not_lost():
    """Der aus #897-#912 dokumentierte Rueckstand (Pitfalls #269-#284) ist NICHT Teil dieses
    Katalogs -- der Hinweis darauf muss unveraendert erhalten bleiben."""
    idx = AGENTS_MD.index("Dokumentationsrückstand")
    segment = AGENTS_MD[idx:idx + 1500]
    for ref in ("#269", "#284", "#856", "#896"):
        assert ref in segment, f"Rueckstands-Hinweis muss {ref} referenzieren"
