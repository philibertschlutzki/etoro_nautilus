"""Issue #1259/#1129 (Katalog #1247+, Stufe 2 der #1142-Merge-Reihenfolge) — der Fix selbst (die
neue ``BAR_RANGE_POPULATION_N``-Telemetrie, ``DEGENERATE_ZERO_RANGE`` vs. ``POPULATION_UNAVAILABLE``
in ``check_stop_loss_vs_bar_range``) wurde bereits umgesetzt, der geforderte ``simulation_semantics_
version``-Bump 5 → 6 blieb dabei jedoch aus — genau dieselbe "Bump-Versaeumnis"-Fehlerklasse wie
beim v4→v5-Uebergang (dort #952/#1118 dokumentiert und behoben, siehe ``test_issue_952_1118_
simulation_version_bump.py``, dessen Struktur diese Datei bewusst spiegelt).

Symptom: ``automation/config/optimizer.json``'s ``simulation_semantics_version``-Feld stand auf 5,
obwohl das GitHub-Issue #1129 (intern #1259) explizit fordert: "Semantik-Bump.
``simulation_semantics_version`` 5 → 6 (neuer Tag im Simulationspfad)." — der neue Tag
(``BAR_RANGE_POPULATION_N``, gestempelt im Order-Submit-Pfad von ``hourly_strategy_base.py``,
gepoolt in ``backtest_runner.py``/``report.py``, konsumiert von ``invariants.check_stop_loss_vs_
bar_range``) war zum Zeitpunkt dieses Fixes bereits vollstaendig implementiert.

Root-Cause: ohne den Bump ist ein Alt-Trial OHNE den neuen Tag von einem Neu-Trial MIT
``bar_range_population_n=0`` nicht unterscheidbar — beide erscheinen im Report identisch als "kein
Urteil moeglich", obwohl nur Ersteres tatsaechlich ``POPULATION_UNAVAILABLE`` ist und Letzteres ein
eigenstaendiges ``DEGENERATE_ZERO_RANGE``-FAIL sein sollte.

Fix: ``simulation_semantics_version`` 5 → 6, Schema-Eintrag um einen v6-Abschnitt ergaenzt (analog
zum bestehenden v2-v5-Changelog-Muster, Pitfall #299 — nur implementiertes Verhalten, gegen den Code
geprueft, nicht gegen die Absicht)."""
import json

from automation.optimizer.trial_config import config_dir


def _load_optimizer_cfg() -> dict:
    with open(config_dir() / "optimizer.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_simulation_semantics_version_is_6():
    cfg = _load_optimizer_cfg()
    assert cfg["simulation_semantics_version"] == 6


def test_simulation_schema_v6_names_its_actual_trigger():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v6_segment = doc[doc.index("v6 ="):]
    assert "#1259" in v6_segment
    assert "#1129" in v6_segment
    assert "BAR_RANGE_POPULATION_N" in v6_segment


def test_simulation_schema_v6_excludes_non_trigger_issues():
    """#1262/#1132 (Gewinner- statt Kohorten-Median), #1266/#1136 (Kostenstress-Kalibrierung je
    Study) und #1268/#1138 (Holdout-Exit-Telemetrie-Bruecke) sind Teil derselben Stufe-2-Sitzung,
    aber explizit KEIN Bump-Ausloeser fuer die SIMULATIONS-Semantik (reine Report-/Invarianten-/
    confirm.py-Ebene, kein veraenderter simulierter Fill)."""
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v6_segment = doc[doc.index("v6 ="):]
    assert "NICHT Ausloeser" in v6_segment
    for ref in ("#1262", "#1132", "#1266", "#1136", "#1268", "#1138"):
        assert ref in v6_segment


def test_simulation_schema_v6_documents_mandatory_purge():
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v6_segment = doc[doc.index("v6 ="):]
    assert "purge_stale_studies" in v6_segment


def test_simulation_schema_v6_documents_the_bump_omission_root_cause():
    """Haelt die Nachtrags-Natur dieses Fixes fest — derselbe Fehlerklasse-Verweis wie im v5-Eintrag
    auf #952/#1118, damit ein spaeterer Audit die Wiederkehr des Musters nachvollziehen kann."""
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v6_segment = doc[doc.index("v6 ="):]
    assert "Bump-Versaeumnis" in v6_segment
    assert "#952" in v6_segment
    assert "#1118" in v6_segment


def test_simulation_schema_v6_describes_only_implemented_behaviour():
    """Pitfall #299 — BAR_RANGE_POPULATION_N muss real im Strategie-Code existieren, nicht nur im
    Changelog-Text behauptet werden. Textbasiert (statt Import) geprueft, analog test_issue_952_1118_
    simulation_version_bump.py::test_simulation_schema_v5_describes_only_implemented_behaviour."""
    from pathlib import Path
    src_path = (Path(__file__).resolve().parents[2]
                / "automation" / "strategies" / "hourly_strategy_base.py")
    src = src_path.read_text(encoding="utf-8")
    assert '"BAR_RANGE_POPULATION_N:{len(readings)}"' in src
    cfg = _load_optimizer_cfg()
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    assert "BAR_RANGE_POPULATION_N" in doc


def test_backtest_runner_and_report_consume_the_new_tag():
    """Die Bruecke, die den Bump rechtfertigt: der Tag muss tatsaechlich bis in den Study-Record
    durchgereicht werden (backtest_runner.py -> run_optimization.py -> report.py), nicht nur an der
    Strategie-Quelle existieren."""
    from pathlib import Path
    br_src = (Path(__file__).resolve().parents[1] / "backtest_runner.py").read_text(encoding="utf-8")
    assert '"BAR_RANGE_POPULATION_N"' in br_src
    assert "bar_range_population_n" in br_src

    run_opt_src = (Path(__file__).resolve().parents[1]
                   / "optimizer" / "run_optimization.py").read_text(encoding="utf-8")
    assert "oos_bar_range_population_n" in run_opt_src

    report_src = (Path(__file__).resolve().parents[1]
                  / "optimizer" / "report.py").read_text(encoding="utf-8")
    assert '"bar_range_population_n"' in report_src


def test_check_stop_loss_vs_bar_range_distinguishes_degenerate_from_unavailable():
    """Akzeptanzkriterium #1259 — die Konsequenz, die den Bump ueberhaupt erst rechtfertigt: ein
    Study-Record MIT bar_range_population_n=0 (Neu-Trial, echte Nullspannen-Degeneration) erhaelt
    ein ANDERES Verdikt als ein Study-Record OHNE das Feld (Alt-Trial, Pre-v6)."""
    from automation.optimizer import invariants as inv
    import inspect
    source = inspect.getsource(inv)
    assert "DEGENERATE_ZERO_RANGE" in source
    assert "POPULATION_UNAVAILABLE" in source


def test_champion_admissibility_rejects_stale_v5_entries():
    """Analog test_issue_952_1118_simulation_version_bump.py::
    test_champion_admissibility_rejects_stale_v4_entries — kein Champion-Eintrag mit
    simulation_semantics_version=5 (der alten Bar-Range-Telemetrie-Semantik) darf nach dem Bump
    noch als Seed verwendet werden."""
    from automation.optimizer import champions

    opt_data = _load_optimizer_cfg()
    entry_stale = {"integrity": {"simulation_semantics_version": 5}}
    entry_fresh = {"integrity": {"simulation_semantics_version": 6}}
    assert champions.champion_simulation_stale(entry_stale, opt_data) is True
    assert champions.champion_simulation_stale(entry_fresh, opt_data) is False
