"""Issue #912 (P0, letzte Aktion des Katalogs) — Semantik-Bumps und der AGENTS.md-Rückstand.

Letzter Fix des Katalogs #897-#912 (GitHub-Issues #771/#769/#770/#772) — trägt die acht im
Issue-Text vollständig ausformulierten neuen Pitfalls (#285-#292) nach, analog der etablierten
Pitfall-Kompendium-Konvention (siehe test_issue_855_agents_md_documentation.py für den unmittelbaren
Vorgänger-Katalog #836-#855).

Akzeptanzkriterien:
- AGENTS.md enthaelt Pitfalls #285-#292 mit Datei:Zeile-Beleg (Funktions-/Modulnamen) und
  Issue-Referenz.
- Katalog-Index #897-#912 mit den vier GitHub-Issue-Nummern (#771/#769/#770/#772) ergaenzt.
- reward_semantics_version=20 / simulation_semantics_version=2 sind in optimizer.json gesetzt und
  im jeweiligen _schema.fields-Eintrag vollstaendig begruendet.
- Der Pitfalls-#269-#284-Rueckstand (Kataloge #856-#896) ist explizit als offen dokumentiert, NICHT
  stillschweigend uebersprungen oder erfunden.
"""
import json
from pathlib import Path

AGENTS_MD = Path("automation/AGENTS.md").read_text("utf-8")
CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))

_PITFALL_ISSUE_MAP = {
    285: "#897",
    286: "#897",
    287: "#898",
    288: "#908",
    289: "#904",
    290: "#904",
    291: "#909",
    292: "#911",
}


def test_pitfall_index_hint_updated_to_292():
    assert "Pitfall #292" in AGENTS_MD


def test_all_eight_pitfalls_are_documented_with_headers():
    for n in range(285, 293):
        assert f"Pitfall #{n} —" in AGENTS_MD, f"Pitfall #{n} fehlt als Header in AGENTS.md"


def test_each_pitfall_references_its_triggering_issue():
    for n, issue_ref in _PITFALL_ISSUE_MAP.items():
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        segment = AGENTS_MD[header_idx:header_idx + 2500]
        assert issue_ref in segment, f"Pitfall #{n} referenziert {issue_ref} nicht im Nahbereich"


def test_each_pitfall_has_symptom_root_cause_and_fix_sections():
    for n in range(285, 293):
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        next_header_idx = AGENTS_MD.find("### 🟢 Pitfall #", header_idx + 1)
        if next_header_idx == -1:
            next_header_idx = AGENTS_MD.find("### ⚠️", header_idx + 1)
        segment = AGENTS_MD[header_idx:next_header_idx]
        assert "**Symptom:**" in segment, f"Pitfall #{n} hat keinen Symptom-Abschnitt"
        assert "**Root-Cause:**" in segment, f"Pitfall #{n} hat keinen Root-Cause-Abschnitt"
        assert "**Fix/Regel:**" in segment, f"Pitfall #{n} hat keinen Fix/Regel-Abschnitt"


# ── Katalog-Index #897-#912 ──────────────────────────────────────────────────────────────────────
def test_catalog_897_912_section_header_exists():
    assert "## Issue-Katalog #897–#912" in AGENTS_MD


def test_catalog_897_912_references_all_four_github_issues():
    idx = AGENTS_MD.index("## Issue-Katalog #897–#912")
    segment = AGENTS_MD[idx:idx + 500]
    for issue in ("#771", "#769", "#770", "#772"):
        assert issue in segment, f"Katalog-Header muss GitHub-Issue {issue} referenzieren"


def test_toc_links_to_the_new_catalog_section():
    assert "Issue-Katalog #897–#912" in AGENTS_MD.split("## 1. Produktübersicht")[0]


# ── #269-#284-Rückstand bleibt ehrlich dokumentiert, nicht erfunden ─────────────────────────────
def test_pitfall_269_to_284_backlog_gap_is_explicitly_documented():
    assert "#269" in AGENTS_MD and "#284" in AGENTS_MD and "#856" in AGENTS_MD and "#896" in AGENTS_MD
    idx = AGENTS_MD.index("Dokumentationsrückstand")
    segment = AGENTS_MD[idx:idx + 1500]
    for ref in ("#269", "#284", "#856", "#896"):
        assert ref in segment, f"Rueckstands-Hinweis muss {ref} referenzieren"


def test_pitfall_269_to_284_headers_do_not_exist_yet():
    """Diese Nummern sind bewusst NICHT nachtragen worden (kein Quellmaterial in dieser Sitzung) —
    ein Header fuer sie waere erfundener statt belegter Inhalt."""
    for n in range(269, 285):
        assert f"Pitfall #{n} —" not in AGENTS_MD, (
            f"Pitfall #{n} hat einen Header, obwohl der #912-Rueckstands-Hinweis ihn als offen "
            f"fuehrt — entweder den Header mit echtem Beleg vervollstaendigen oder den Hinweis "
            f"anpassen."
        )


# ── Neue/geänderte Config-Keys + Watertight Invariants Subsections ──────────────────────────────
def test_config_keys_and_invariants_subsections_exist_for_new_catalog():
    idx = AGENTS_MD.index("## Issue-Katalog #897–#912")
    segment = AGENTS_MD[idx:]
    assert "### 📋 Neue/geänderte Config-Keys (Issue-Katalog #897–#912)" in segment
    assert "### 🔒 Watertight Invariants (Issue-Katalog #897–#912)" in segment


def test_changelog_table_has_a_row_for_the_new_catalog():
    assert "Implementierung Issue-Katalog #897–#912" in AGENTS_MD


# ── Semantik-Versionen (Akzeptanzkriterium #912) ─────────────────────────────────────────────────
def test_reward_semantics_version_is_20():
    assert CFG["reward_semantics_version"] == 20


def test_simulation_semantics_version_is_2():
    assert CFG["simulation_semantics_version"] == 2


def test_simulation_schema_documents_v2_trigger_issues():
    doc = CFG["_schema"]["fields"]["simulation_semantics_version"]
    v2_segment = doc[doc.index("v2 ="):]
    for ref in ("#897", "#898"):
        assert ref in v2_segment, f"simulation_semantics_version v2-Changelog muss {ref} referenzieren"


def test_reward_schema_documents_v20_trigger_issue():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    v20_segment = doc[doc.index("v20 ="):]
    assert "#901" in v20_segment
