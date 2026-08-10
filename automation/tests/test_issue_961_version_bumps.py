"""Issue #961 (Katalog E) — Semantik-Bumps nach Katalog #937-#964 (GitHub-Issues #779-#783):
reward_semantics_version 21->22 (Ausloeser #958/#960), simulation_semantics_version 3->4
(Ausloeser #944/#947/#956). Pitfall #299 verlangt, dass die _schema-Beschreibung ausschliesslich
implementiertes Verhalten nennt und gegen den Code getestet wird, nicht gegen die Absicht.
"""
import json

from automation.optimizer.trial_config import config_dir


def _load_optimizer_cfg() -> dict:
    with open(config_dir() / "optimizer.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_reward_semantics_version_is_23():
    """Issue #991 (Katalog E, GitHub-Issue #789) — bumped 22 -> 23 (Ausloeser #977/#966, siehe
    test_issue_991_version_bumps.py fuer die vollstaendige v23-Verifikation)."""
    cfg = _load_optimizer_cfg()
    assert cfg["reward_semantics_version"] == 23


def test_simulation_semantics_version_is_4():
    cfg = _load_optimizer_cfg()
    assert cfg["simulation_semantics_version"] == 4


def test_reward_schema_v22_names_its_actual_triggers():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["reward_semantics_version"]
    v22_segment = doc[doc.index("v22 ="):]
    for ref in ("#958", "#960"):
        assert ref in v22_segment, f"reward_semantics_version v22-Changelog muss {ref} nennen"


def test_reward_schema_v22_excludes_non_trigger_issues():
    """#949/#950/#953/#957 sind explizit KEIN Bump-Ausloeser (reine Diagnostik, nicht-produktiver
    Pfad oder opt-in mit sicherem Default) -- der v22-Abschnitt muss das auch so dokumentieren,
    nicht nur den Bump selbst begruenden."""
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["reward_semantics_version"]
    v22_segment = doc[doc.index("v22 ="):]
    assert "NICHT Ausloeser" in v22_segment
    for ref in ("#949", "#950", "#953", "#957"):
        assert ref in v22_segment


def test_simulation_schema_v4_names_its_actual_triggers():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v4_segment = doc[doc.index("v4 ="):]
    for ref in ("#944", "#947", "#956"):
        assert ref in v4_segment, f"simulation_semantics_version v4-Changelog muss {ref} nennen"


def test_simulation_schema_v4_excludes_non_trigger_issues():
    """#943/#948/#958 sind explizit KEIN Bump-Ausloeser fuer die SIMULATIONS-Semantik (ungebundene
    Infrastruktur, Meldungstext-Korrektur bzw. eine Promotion- statt Simulations-Entscheidung)."""
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v4_segment = doc[doc.index("v4 ="):]
    assert "NICHT Ausloeser" in v4_segment
    for ref in ("#943", "#948", "#958"):
        assert ref in v4_segment


def test_reward_and_simulation_bumps_reference_the_same_session_catalog():
    """Beide Changelog-Eintraege muessen auf denselben Ursprung (#937-#964 / #779-#783) verweisen,
    damit ein spaeterer Purge-Audit die Bumps als zusammengehoerig nachvollziehen kann."""
    cfg = _load_optimizer_cfg()
    reward_doc = cfg["_schema"]["fields"]["reward_semantics_version"]
    sim_doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    reward_v22 = reward_doc[reward_doc.index("v22 ="):]
    sim_v4 = sim_doc[sim_doc.index("v4 ="):]
    for segment in (reward_v22, sim_v4):
        assert "#937-#964" in segment
        assert "#779-#783" in segment
