"""Issue #936 — Semantik-Bumps nach Katalog #913-#924: reward_semantics_version 20->21
(Ausloeser #913/#914/#917), simulation_semantics_version 2->3 (Ausloeser #920/#924). Pitfall #299
verlangt, dass die _schema-Beschreibung ausschliesslich implementiertes Verhalten nennt und gegen
den Code getestet wird, nicht gegen die Absicht.
"""
import json

from automation.optimizer.trial_config import config_dir


def _load_optimizer_cfg() -> dict:
    with open(config_dir() / "optimizer.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_reward_semantics_version_is_21():
    """Issue #961 (Katalog E) bumpte weiter auf 22 (siehe test_issue_961_version_bumps.py) —
    dieser Test prueft weiterhin, dass v21 NICHT UNTERSCHRITTEN wird (die #936-Bump-Historie
    bleibt gueltig), nicht mehr die exakte Gleichheit."""
    cfg = _load_optimizer_cfg()
    assert cfg["reward_semantics_version"] >= 21


def test_simulation_semantics_version_is_3():
    """Issue #961 (Katalog E) bumpte weiter auf 4 (siehe test_issue_961_version_bumps.py)."""
    cfg = _load_optimizer_cfg()
    assert cfg["simulation_semantics_version"] >= 3


def test_reward_schema_v21_names_its_actual_triggers():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["reward_semantics_version"]
    v21_segment = doc[doc.index("v21 ="):]
    for ref in ("#913", "#914", "#917"):
        assert ref in v21_segment, f"reward_semantics_version v21-Changelog muss {ref} nennen"


def test_reward_schema_v21_excludes_non_trigger_issues():
    """Issue #915/#916/#918 sind explizit KEIN Bump-Ausloeser (reine Wirkungs-/Registry-Ebene) --
    der v21-Abschnitt muss das auch so dokumentieren, nicht nur den Bump selbst begruenden."""
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["reward_semantics_version"]
    v21_segment = doc[doc.index("v21 ="):]
    assert "NICHT Ausloeser" in v21_segment
    for ref in ("#915", "#916", "#918"):
        assert ref in v21_segment


def test_simulation_schema_v3_names_its_actual_triggers():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v3_segment = doc[doc.index("v3 ="):]
    for ref in ("#920", "#924"):
        assert ref in v3_segment, f"simulation_semantics_version v3-Changelog muss {ref} nennen"


def test_simulation_schema_v3_excludes_non_trigger_issues():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v3_segment = doc[doc.index("v3 ="):]
    assert "NICHT Ausloeser" in v3_segment
    for ref in ("#919", "#921", "#922", "#923"):
        assert ref in v3_segment


def test_simulation_schema_v3_describes_only_implemented_behaviour():
    """Pitfall #299 (die #924-Root-Cause selbst): eine Versions-Beschreibung, die Verhalten nennt,
    das im Code nicht existiert, ist schlimmer als keine Dokumentation. atr_floor_bps_by_asset_class
    muss real in backtest.json existieren, nicht nur im Changelog-Text behauptet werden."""
    with open(config_dir() / "backtest.json", "r", encoding="utf-8") as f:
        backtest_cfg = json.load(f)
    assert "atr_floor_bps_by_asset_class" in backtest_cfg
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    assert "atr_floor_bps_by_asset_class" in doc
