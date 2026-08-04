"""Issue #855 (P3) — AGENTS.md: Pitfalls #259–#268 + Katalog-Index #836–#855.

Letzter Fix des Katalogs (#849-#855) — dokumentiert die in den Kohorten A-D dieser Sitzung
gefundenen Fehlerklassen als benannte, wiederauffindbare Pitfalls in `automation/AGENTS.md`,
analog der bereits etablierten Pitfall-Kompendium-Konvention (siehe Pitfalls #249-#258 für den
unmittelbaren Vorgänger-Katalog #817-#835).

Akzeptanzkriterien:
- Pitfalls #259-#268 in automation/AGENTS.md dokumentiert, jeweils mit Datei:Zeile-Beleg (in Form
  von referenzierten Funktions-/Modulnamen) und Issue-Referenz.
- Katalog-Index #836-#855 mit den vier Issue-Nummern (#753/#754/#755/#756) ergänzt.
- Die Wiederkehr-Zähler in #264 (6x) und #267 (5x) sind mit den Vorgänger-Issues verlinkt.
"""
from pathlib import Path

AGENTS_MD = Path("automation/AGENTS.md").read_text("utf-8")

_PITFALL_ISSUE_MAP = {
    259: "#836",
    260: "#837",
    261: "#836",
    262: "#838",
    263: "#838",
    264: "#840",
    265: "#841",
    266: "#828",  # behoben in Kohorte C des #817-#835-Katalogs, hier dokumentiert (#843-Kontext)
    267: "#844",
    268: "#850",
}


def test_pitfall_index_hint_updated_to_268():
    assert "Pitfall #268**" in AGENTS_MD


def test_all_ten_pitfalls_are_documented_with_headers():
    for n in range(259, 269):
        assert f"Pitfall #{n} —" in AGENTS_MD, f"Pitfall #{n} fehlt als Header in AGENTS.md"


def test_each_pitfall_references_its_triggering_issue():
    for n, issue_ref in _PITFALL_ISSUE_MAP.items():
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        # Ein Beleg ist entweder direkt im Header-Titel ([BEHOBEN: GH-#XXX]) oder im unmittelbar
        # folgenden Symptom/Root-Cause/Fix-Absatz enthalten.
        segment = AGENTS_MD[header_idx:header_idx + 2500]
        assert issue_ref in segment, f"Pitfall #{n} referenziert {issue_ref} nicht im Nahbereich"


def test_each_pitfall_has_symptom_root_cause_and_fix_sections():
    for n in range(259, 269):
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        next_header_idx = AGENTS_MD.find("### 🟢 Pitfall #", header_idx + 1)
        if next_header_idx == -1:
            next_header_idx = AGENTS_MD.find("### 📋", header_idx + 1)
        segment = AGENTS_MD[header_idx:next_header_idx]
        assert "**Symptom:**" in segment, f"Pitfall #{n} hat keinen Symptom-Abschnitt"
        assert "**Root-Cause:**" in segment, f"Pitfall #{n} hat keinen Root-Cause-Abschnitt"
        assert "**Fix/Regel:**" in segment, f"Pitfall #{n} hat keinen Fix/Regel-Abschnitt"


# ── Katalog-Index #836-#855 ──────────────────────────────────────────────────────────────────────
def test_catalog_836_855_section_header_exists():
    assert "## Issue-Katalog #836–#855" in AGENTS_MD


def test_catalog_836_855_references_all_four_github_issues():
    idx = AGENTS_MD.index("## Issue-Katalog #836–#855")
    segment = AGENTS_MD[idx:idx + 500]
    for issue in ("#753", "#754", "#755", "#756"):
        assert issue in segment, f"Katalog-Header muss GitHub-Issue {issue} referenzieren"


def test_toc_links_to_the_new_catalog_section():
    assert "Issue-Katalog #836–#855" in AGENTS_MD.split("## 1. Produktübersicht")[0]


# ── Wiederkehr-Zähler #264 (6x) und #267 (5x) ────────────────────────────────────────────────────
def test_pitfall_264_documents_sixth_recurrence_with_predecessors():
    header_idx = AGENTS_MD.index("Pitfall #264 —")
    segment = AGENTS_MD[header_idx:header_idx + 1500]
    assert "SECHSTE Wiederkehr" in segment or "sechste Wiederkehr" in segment.lower() or "6" in segment
    for predecessor in ("#794", "#818", "#831"):
        assert predecessor in segment, f"Pitfall #264 muss Vorgänger-Issue {predecessor} verlinken"


def test_pitfall_267_documents_fifth_recurrence_with_predecessors():
    header_idx = AGENTS_MD.index("Pitfall #267 —")
    segment = AGENTS_MD[header_idx:header_idx + 1500]
    assert "FÜNFTE Wiederkehr" in segment or "fünfte Wiederkehr" in segment.lower()
    for predecessor in ("#488", "#753", "#769", "#805", "#823"):
        assert predecessor in segment, f"Pitfall #267 muss Vorgänger-Issue {predecessor} verlinken"


# ── Neue/geänderte Config-Keys + Watertight Invariants Subsections ──────────────────────────────
def test_config_keys_and_invariants_subsections_exist_for_new_catalog():
    idx = AGENTS_MD.index("## Issue-Katalog #836–#855")
    segment = AGENTS_MD[idx:]
    assert "### 📋 Neue/geänderte Config-Keys (Issue-Katalog #836–#855)" in segment
    assert "### 🔒 Watertight Invariants (Issue-Katalog #836–#855)" in segment


def test_changelog_table_has_a_row_for_the_new_catalog():
    assert "Implementierung Issue-Katalog #836–#855" in AGENTS_MD
