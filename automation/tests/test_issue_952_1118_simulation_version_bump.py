"""Issue #952/#1118 (Katalog #960) — ``simulation_semantics_version`` stand auf 4, obwohl drei
simulationsverändernde Fixes (#1092/#1094/#1096, Issue-Katalog #1086-#1104) bereits in AGENTS.md als
umgesetzt dokumentiert waren.

Symptom: AGENTS.md dokumentiert #1092 (Anker/Auslöser-Auflösung), #1094 (monotone Ratsche) und #1096
(kostengekoppelter ATR-Floor) unter der Überschrift "Stufe 1 (Risikoschicht —
simulation_semantics_version 4 → 5, Pflicht-Purge am Ende)" — der tatsächliche Config-Wert in
optimizer.json blieb dabei unverändert auf 4 stehen.

Root-Cause: nach der Definition im _schema selbst ändert jede der drei Änderungen "WAS gemessen
wird" (Haltedauern/Equity-Kurve/simulierte Fill-/Stop-Preise) und ist damit je einzeln hinreichend
für einen Bump. Ohne Bump bleiben Alt-Trials/Champion-Einträge mit der alten Trailing-Stop-Semantik
gültig und mischen sich in dieselbe Deflationsfamilie wie neue — ``champions.champion_simulation_
stale`` greift nicht, ``check_semantics_version_coherence`` findet nichts.

Fix: ``simulation_semantics_version`` 4 → 5 (automation/config/optimizer.json), Schema-Eintrag
(``_schema.fields.simulation_semantics_version``) um einen v5-Abschnitt mit den drei Auslösern
ergänzt (analog zum bestehenden v2/v3/v4-Changelog-Muster, Pitfall #299).

Pitfall #299 verlangt, dass die _schema-Beschreibung ausschliesslich implementiertes Verhalten
nennt und gegen den Code getestet wird, nicht gegen die Absicht.
"""
import json

from automation.optimizer.trial_config import config_dir


def _load_optimizer_cfg() -> dict:
    with open(config_dir() / "optimizer.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_simulation_semantics_version_is_5():
    cfg = _load_optimizer_cfg()
    assert cfg["simulation_semantics_version"] == 5


def test_simulation_schema_v5_names_its_actual_triggers():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v5_segment = doc[doc.index("v5 ="):]
    for ref in ("#1092", "#1094", "#1096"):
        assert ref in v5_segment, f"simulation_semantics_version v5-Changelog muss {ref} nennen"


def test_simulation_schema_v5_excludes_non_trigger_issues():
    """#1093 (neue blockierende Invariante, liest nur bereits simulierte Trades) und #1095
    (Stop-Exit-Lag-Telemetrie, veraendert selbst keinen Fill) sind explizit KEIN Bump-Ausloeser fuer
    die SIMULATIONS-Semantik."""
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v5_segment = doc[doc.index("v5 ="):]
    assert "NICHT Ausloeser" in v5_segment
    for ref in ("#1093", "#1095"):
        assert ref in v5_segment


def test_simulation_schema_v5_references_the_session_catalog():
    """Der v5-Eintrag muss auf den Ursprungs-Katalog verweisen, damit ein spaeterer Purge-Audit
    den Bump nachvollziehen kann (analog test_reward_and_simulation_bumps_reference_the_same_
    session_catalog in test_issue_961_version_bumps.py)."""
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v5_segment = doc[doc.index("v5 ="):]
    assert "#1086-#1104" in v5_segment
    assert "#919-#937" in v5_segment


def test_simulation_schema_v5_documents_mandatory_purge():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v5_segment = doc[doc.index("v5 ="):]
    assert "purge_stale_studies" in v5_segment


def test_simulation_schema_v5_describes_only_implemented_behaviour():
    """Pitfall #299 — HourlyStrategyConfig.trailing_stop_anchor_resolution (der #1092-Fix) muss
    real im Strategie-Code existieren, nicht nur im Changelog-Text behauptet werden. Textbasiert
    (statt Import) geprueft, weil hourly_strategy_base.py transitiv nautilus_trader (>=1.226, <2.0,
    Python >=3.12) voraussetzt — in dieser Sandbox (Python 3.11) nicht importierbar, siehe
    test_issue_946_1112_dust_round_trip_source_filter.py fuer dieselbe Einschraenkung."""
    from pathlib import Path
    src_path = (Path(__file__).resolve().parents[2]
                / "automation" / "strategies" / "hourly_strategy_base.py")
    src = src_path.read_text(encoding="utf-8")
    assert 'trailing_stop_anchor_resolution: str = "close"' in src
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    assert "trailing_stop_anchor_resolution" in doc


def test_champion_admissibility_rejects_stale_v4_entries():
    """Akzeptanzkriterium #952 — kein Champion-Eintrag mit simulation_semantics_version=4 (der
    alten Trailing-Stop-Semantik) darf nach dem Bump noch als Seed verwendet werden."""
    from automation.optimizer import champions

    opt_data = _load_optimizer_cfg()
    entry_stale = {"integrity": {"simulation_semantics_version": 4}}
    entry_fresh = {"integrity": {"simulation_semantics_version": 5}}
    assert champions.champion_simulation_stale(entry_stale, opt_data) is True
    assert champions.champion_simulation_stale(entry_fresh, opt_data) is False
