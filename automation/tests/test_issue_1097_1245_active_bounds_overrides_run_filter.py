"""Issue #1097/#1245 (P3, Katalog #1247+) — active_bounds_overrides im Artefakt auf Laufsymbole
filtern.

Symptom: ``cross_study.active_bounds_overrides`` enthält in allen 11 Läufen dieselben 18 Einträge —
sämtlich für TSLA.ETORO, auch in den GOOGL-, NVDA- und NATGAS-Läufen. Die Zusammenfassung ist mit
#1214 bereits korrekt gefiltert ("Keine aktiven Overrides für die Symbole dieses Laufs"), das JSON
nicht.

Fix: dieselbe Filterung wie in summary_de.py §5.4 wird jetzt auch auf ``cross_study.active_bounds_
overrides`` angewendet (report._build_report, unmittelbar bei der ``bounds.active_bounds_overrides``-
Auflösung); die vollständige, ungefilterte Liste bleibt unter ``active_bounds_overrides_all``
erhalten.

Akzeptanzkriterium: im GOOGL-Lauf ist active_bounds_overrides leer und active_bounds_overrides_all
enthält 18 Einträge.
"""
from automation.optimizer import bounds, report


def test_report_json_carries_both_keys(tmp_path):
    out_path = report.generate_sweep_report([], run_id="run-1097", reports_dir=tmp_path / "reports")
    import json
    data = json.loads(out_path.read_text("utf-8"))
    assert "active_bounds_overrides" in data["cross_study"]
    assert "active_bounds_overrides_all" in data["cross_study"]


def test_full_inventory_is_unaffected_by_the_run_filter(tmp_path):
    """active_bounds_overrides_all traegt dieselbe Menge wie bounds.active_bounds_overrides()
    direkt aufgerufen — unabhaengig davon, welche (oder wie wenige) Studies dieser Lauf hatte."""
    out_path = report.generate_sweep_report([], run_id="run-1097b", reports_dir=tmp_path / "reports")
    import json
    data = json.loads(out_path.read_text("utf-8"))
    all_names = {
        (o["strategy"], o["symbol"], o["parameter"])
        for o in data["cross_study"]["active_bounds_overrides_all"]
    }
    direct_names = {
        (o["strategy"], o["symbol"], o["parameter"]) for o in bounds.active_bounds_overrides()
    }
    assert all_names == direct_names
    assert len(all_names) > 0  # Sanity: die kuratierte search_space_overrides.json ist nicht leer.


def test_run_filtered_key_is_empty_when_zero_studies_present(tmp_path):
    """0 Studies ⇒ 0 Laufsymbole ⇒ active_bounds_overrides ist leer, obwohl
    active_bounds_overrides_all nicht leer ist (das konkrete GOOGL-Lauf-Symptom, hier mit 0
    Studies als Grenzfall reproduziert)."""
    out_path = report.generate_sweep_report([], run_id="run-1097c", reports_dir=tmp_path / "reports")
    import json
    data = json.loads(out_path.read_text("utf-8"))
    assert data["cross_study"]["active_bounds_overrides"] == []
    assert len(data["cross_study"]["active_bounds_overrides_all"]) > 0
