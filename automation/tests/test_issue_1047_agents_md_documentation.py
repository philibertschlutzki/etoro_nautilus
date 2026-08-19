"""Issue #1047 (letzte Aktion des Katalogs, "Stufe 7") — AGENTS.md-Nachtrag #1171–#1195
(GitHub-Issues #1021–#1046) und Pitfalls #421–#428, analog der etablierten
Pitfall-Kompendium-Konvention (siehe test_issue_1018_1170_agents_md_documentation.py fuer den
unmittelbaren Vorgaenger-Katalog #1146–#1170). Zusaetzlich: §18.6/§18.7, die aus Issue #1047s
Merge-Reihenfolge (Abschnitt 3) und Ertrags-/Risiko-Empfehlungen (Abschnitt 5) verallgemeinerte,
dauerhafte Methodik.
"""
from pathlib import Path

AGENTS_MD = Path("automation/AGENTS.md").read_text("utf-8")

_PITFALL_ISSUE_MAP = {
    421: "#1022",
    422: "#1029",  # zweite Instanz derselben Klasse: #1033
    423: "#1024",
    424: "#1025",
    425: "#1031",
    426: "#1027",  # zweite Instanz derselben Klasse: #1030
    427: "#1028",
    428: "#1042",
}

_CATALOG_GITHUB_ISSUES = [f"#{n}" for n in range(1021, 1047)]  # #1021 .. #1046 inclusive


def test_pitfall_428_is_documented():
    assert "Pitfall #428" in AGENTS_MD


def test_all_eight_pitfalls_are_documented_with_headers():
    for n in range(421, 429):
        assert f"Pitfall #{n} —" in AGENTS_MD, f"Pitfall #{n} fehlt als Header in AGENTS.md"


def test_pitfall_numbers_are_globally_unique():
    """§16-Konvention: Pitfall-Nummern sind global eindeutig ueber die gesamte Datei -- jede der
    acht neuen Nummern darf als Header-Praefix ('### ... Pitfall #N —') nur EIN Mal vorkommen."""
    import re
    for n in range(421, 429):
        headers = re.findall(rf"### [^\n]*Pitfall #{n} —", AGENTS_MD)
        assert len(headers) == 1, f"Pitfall #{n} erscheint {len(headers)}x als Header (muss 1x sein)"


def test_each_pitfall_references_its_triggering_issue():
    for n, issue_ref in _PITFALL_ISSUE_MAP.items():
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        segment = AGENTS_MD[header_idx:header_idx + 500]
        assert issue_ref in segment, f"Pitfall #{n} referenziert {issue_ref} nicht im Header"


def test_each_pitfall_has_symptom_root_cause_and_fix_sections():
    for n in range(421, 429):
        header_idx = AGENTS_MD.index(f"Pitfall #{n} —")
        candidates = [
            idx for idx in (
                AGENTS_MD.find("### 🔴 Pitfall #", header_idx + 1),
                AGENTS_MD.find("### 🟡 Pitfall #", header_idx + 1),
                AGENTS_MD.find("### 🟢 Pitfall #", header_idx + 1),
                AGENTS_MD.find("### 🟠 Pitfall #", header_idx + 1),
            ) if idx != -1
        ]
        end_idx = min(candidates) if candidates else len(AGENTS_MD)
        segment = AGENTS_MD[header_idx:end_idx]
        assert "**Symptom:**" in segment, f"Pitfall #{n} hat keinen Symptom-Abschnitt"
        assert "**Root-Cause:**" in segment, f"Pitfall #{n} hat keinen Root-Cause-Abschnitt"
        assert "**Fix/Regel:**" in segment, f"Pitfall #{n} hat keinen Fix/Regel-Abschnitt"


def test_pitfall_426_names_itself_an_extension_of_420():
    """#426 (Bar-Achse als Querschnittsgroesse) baut explizit auf Pitfall #420
    (24/7-vs-RTH-Kalenderannahme) auf."""
    header_idx = AGENTS_MD.index("Pitfall #426 —")
    segment = AGENTS_MD[header_idx:header_idx + 300]
    assert "#420" in segment


# ── Katalog-Index #1171-#1195 ─────────────────────────────────────────────────────────────────────

def test_catalog_1171_1195_section_header_exists():
    assert "## Issue-Katalog #1171–#1195" in AGENTS_MD


def test_catalog_references_the_github_issue_range():
    idx = AGENTS_MD.index("## Issue-Katalog #1171–#1195")
    segment = AGENTS_MD[idx:idx + 500]
    assert "#1021" in segment and "#1046" in segment


def test_toc_links_to_the_new_catalog_section():
    assert "Issue-Katalog #1171–#1195" in AGENTS_MD.split("## 1. Produktübersicht")[0]


def test_all_twenty_six_github_issues_are_referenced_somewhere_in_the_catalog_section():
    idx = AGENTS_MD.index("## Issue-Katalog #1171–#1195")
    end_idx = AGENTS_MD.index("## Neue Pitfalls #421", idx)
    segment = AGENTS_MD[idx:end_idx]
    missing = [gh for gh in _CATALOG_GITHUB_ISSUES if gh not in segment]
    assert not missing, f"GitHub-Issues fehlen im Katalog-Abschnitt: {missing}"


def test_config_keys_and_invariants_subsections_exist_for_new_catalog():
    idx = AGENTS_MD.index("## Issue-Katalog #1171–#1195")
    segment = AGENTS_MD[idx:]
    assert "### 📋 Neue/geänderte Config-Keys (Issue-Katalog #1171–#1195)" in segment
    assert "### 🔒 Watertight Invariants (Issue-Katalog #1171–#1195)" in segment


def test_sperrvermerke_subsection_exists_and_addresses_all_nine_from_1047():
    idx = AGENTS_MD.index("## Issue-Katalog #1171–#1195")
    end_idx = AGENTS_MD.index("## Neue Pitfalls #421", idx)
    segment = AGENTS_MD[idx:end_idx]
    sperr_idx = segment.index("**Sperrvermerke")
    sperr_segment = segment[sperr_idx:]
    for n in range(1, 10):
        assert f"{n}. **" in sperr_segment, f"Sperrvermerk {n} fehlt im Stand-Update"


def test_changelog_table_has_a_row_for_the_new_catalog():
    assert "Implementierung Issue-Katalog #1171–#1195" in AGENTS_MD


def test_pre_existing_backlog_notes_are_preserved_not_lost():
    """Der aus #897-#912 dokumentierte Rueckstand (Pitfalls #269-#284) bleibt unveraendert
    erhalten -- dieser Katalog ist nicht sein Nachtrag."""
    idx = AGENTS_MD.index("Dokumentationsrückstand")
    segment = AGENTS_MD[idx:idx + 1500]
    for ref in ("#269", "#284", "#856", "#896"):
        assert ref in segment, f"Rueckstands-Hinweis muss {ref} referenzieren"


def test_prior_catalog_1146_1170_section_still_present():
    """Diese Sitzung haengt an, statt zu ersetzen -- der unmittelbare Vorgaenger-Katalog bleibt
    vollstaendig erhalten."""
    assert "## Issue-Katalog #1146–#1170" in AGENTS_MD
    assert "## Neue Pitfalls #413–420" in AGENTS_MD


# ── §18.6/§18.7 — verallgemeinerte Merge-Reihenfolge-/Bewertungsdisziplin-Methodik ───────────────

def test_section_18_6_and_18_7_exist_with_toc_links():
    assert "### 18.6 Reihenfolge bei abhängigen Issue-Batches & Sperrvermerke" in AGENTS_MD
    assert "### 18.7 Bewertungsdisziplin: Ertrag und Risiko" in AGENTS_MD
    toc = AGENTS_MD.split("## 1. Produktübersicht")[0]
    assert "18.6." in toc and "18.7." in toc


def test_section_18_6_references_issue_1047_and_the_seven_stufen():
    idx = AGENTS_MD.index("### 18.6 Reihenfolge bei abhängigen Issue-Batches")
    end_idx = AGENTS_MD.index("### 18.7 Bewertungsdisziplin")
    segment = AGENTS_MD[idx:end_idx]
    assert "#1047" in segment
    for keyword in ("Messketten", "Risikoschicht", "Sperrvermerk"):
        assert keyword in segment


def test_section_18_7_covers_the_ten_e_recommendations_generalized():
    idx = AGENTS_MD.index("### 18.7 Bewertungsdisziplin")
    end_idx = AGENTS_MD.index("## ARCHITECTURAL INVARIANT", idx)
    segment = AGENTS_MD[idx:end_idx]
    # Zehn Bullet-Punkte (E-1 bis E-10 verallgemeinert) -- gezaehlt ueber die Markdown-Bullets.
    bullets = [line for line in segment.splitlines() if line.strip().startswith("- **")]
    assert len(bullets) == 10, f"erwarte 10 verallgemeinerte Ertrags-/Risiko-Regeln, gefunden {len(bullets)}"
