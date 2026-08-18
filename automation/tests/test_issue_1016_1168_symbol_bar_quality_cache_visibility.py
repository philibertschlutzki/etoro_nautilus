"""Issue #1016/#1168 (Katalog #1170) — ``symbol_bar_quality`` null in 28/28 Studies.

Symptom: Das Feld ist in beiden Läufen für alle Studies ``None``; ``sweep.write_symbol_bar_quality_
cache``/``read_symbol_bar_quality_cache`` existieren, ``check_bar_quality`` ist in ``sweep.py``
verdrahtet.

Root-Cause (laut Issue): der Cache (``{WORK}/symbol_bar_quality.json``) wird entweder nicht
geschrieben oder liegt nicht in dem ``WORK``, aus dem der Report liest — beides in dieser Sandbox
(kein ``nautilus_trader``, kein echter Produktionslauf) nicht end-to-end nachvollziehbar.

Fix (Beobachtbarkeit statt stiller Diagnose, dieselbe Fehlerklasse wie #973/#1127, Pitfall #406 in
AGENTS.md): ``sweep.symbol_bar_quality_cache_status`` liefert ``{cache_path, cache_found}``;
``report.py`` traegt das in ``cross_study['symbol_bar_quality_cache']`` und wertet es ueber die neue
Invariante ``check_symbol_bar_quality_cache_availability`` aus — ein fehlendes Feld erscheint als
eigene Zeile im Invarianten-Strom (Abschnitt 5), benennt den erwarteten Pfad UND ob die Datei
gefunden wurde, statt als stilles ``None`` zu verschwinden. Akzeptanzkriterium: ``symbol_bar_
quality`` ist fuer jedes Symbol mit >= 1 Study nicht-null, ODER der Report nennt den erwarteten Pfad
und dass er fehlt.
"""
import json

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import sweep


# ── sweep.symbol_bar_quality_cache_status — {cache_path, cache_found} ────────────────────────────

def test_cache_not_found_when_file_is_absent(tmp_path):
    status = sweep.symbol_bar_quality_cache_status(tmp_path)
    assert status["cache_found"] is False
    assert status["cache_path"] == str(tmp_path / "symbol_bar_quality.json")


def test_cache_found_after_write_symbol_bar_quality_cache(tmp_path):
    sweep.write_symbol_bar_quality_cache(tmp_path, {"A.ETORO": {"atr_median_bps": 12.0}})
    status = sweep.symbol_bar_quality_cache_status(tmp_path)
    assert status["cache_found"] is True
    assert status["cache_path"] == str(tmp_path / "symbol_bar_quality.json")


def test_status_path_matches_the_actual_read_write_path(tmp_path):
    """Regressionswächter gegen genau das #1168-Symptom (Schreib-/Lesepfad divergieren): derselbe
    ``work_dir`` muss über ``symbol_bar_quality_cache_status`` UND ``read_symbol_bar_quality_
    cache`` denselben Datenstand sehen."""
    sweep.write_symbol_bar_quality_cache(tmp_path, {"A.ETORO": {"atr_median_bps": 12.0}})
    status = sweep.symbol_bar_quality_cache_status(tmp_path)
    cache = sweep.read_symbol_bar_quality_cache(tmp_path)
    assert status["cache_found"] is True
    assert "A.ETORO" in cache


# ── invariants.check_symbol_bar_quality_cache_availability — reine Funktion ──────────────────────

def test_passes_with_no_studies():
    result = inv.check_symbol_bar_quality_cache_availability([])
    assert result.passed is True
    assert result.actual is None


def test_passes_when_every_study_has_symbol_bar_quality():
    records = [
        {"symbol": "A.ETORO", "symbol_bar_quality": {"atr_median_bps": 10.0}},
        {"symbol": "B.ETORO", "symbol_bar_quality": {"atr_median_bps": 5.0}},
    ]
    result = inv.check_symbol_bar_quality_cache_availability(
        records, cache_path="/x/symbol_bar_quality.json", cache_found=True)
    assert result.passed is True


def test_reproduces_the_1168_reference_symptom_28_of_28_null():
    records = [{"symbol": f"S{i}.ETORO", "symbol_bar_quality": None} for i in range(28)]
    result = inv.check_symbol_bar_quality_cache_availability(
        records, cache_path="/work/symbol_bar_quality.json", cache_found=False)
    assert result.passed is False
    assert len(result.actual["symbols_without_symbol_bar_quality"]) == 28
    assert result.actual["cache_found"] is False
    assert "/work/symbol_bar_quality.json" in result.detail


def test_fail_detail_distinguishes_missing_file_from_missing_symbol_in_a_found_file():
    records = [{"symbol": "A.ETORO", "symbol_bar_quality": None}]
    missing_file = inv.check_symbol_bar_quality_cache_availability(
        records, cache_path="/work/symbol_bar_quality.json", cache_found=False)
    found_but_absent = inv.check_symbol_bar_quality_cache_availability(
        records, cache_path="/work/symbol_bar_quality.json", cache_found=True)
    assert missing_file.passed is False and found_but_absent.passed is False
    assert "NICHT gefunden" in missing_file.detail
    assert "gefunden" in found_but_absent.detail and "NICHT gefunden" not in found_but_absent.detail


def test_only_symbols_actually_missing_the_field_are_named():
    records = [
        {"symbol": "A.ETORO", "symbol_bar_quality": {"atr_median_bps": 10.0}},
        {"symbol": "B.ETORO", "symbol_bar_quality": None},
    ]
    result = inv.check_symbol_bar_quality_cache_availability(records, cache_found=True)
    assert result.actual["symbols_without_symbol_bar_quality"] == ["B.ETORO"]


def test_severity_is_medium_an_observability_not_correctness_finding():
    result = inv.check_symbol_bar_quality_cache_availability(
        [{"symbol": "A.ETORO", "symbol_bar_quality": None}])
    assert result.severity == "medium"


# ── report.py wiring ───────────────────────────────────────────────────────────────────────────

def test_check_is_wired_into_build_report():
    source = __import__("pathlib").Path(rpt.__file__).read_text("utf-8")
    assert "_inv.check_symbol_bar_quality_cache_availability(" in source
    assert "symbol_bar_quality_cache_status(WORK)" in source


def test_invariant_registry_wiring_check_still_passes():
    """Regressionswächter (#984/#1138): die neue Aufrufstelle in report.py macht die neue
    invariants.py-Funktion automatisch 'wired' (Text-Scan, siehe dortiger Docstring)."""
    result = rpt._invariant_registry_wiring_check()
    assert result.passed is True


# ── end-to-end via generate_sweep_report (dieselbe wired_storage-Infrastruktur wie #742) ─────────

def _proposal():
    return {
        "strategy": "TestStrat", "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": {"deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
                               "deflation_n_eligible": 3, "deflation_n_family_effective": 3,
                               "deflation_n_effective": 3}},
    }


def _make_study(storage_url, study_name):
    import optuna
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize")
    for i in range(5):
        t = study.ask()
        t.set_user_attr("oos_evaluated", True)
        t.set_user_attr("oos_eligible", i < 3)
        study.tell(t, 1.0)
    return study


def test_missing_cache_appears_as_a_named_report_row_not_a_silent_none(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    study_name = "study_TestStrat_A_ETORO"
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, study_name)
    monkeypatch.setattr(rpt, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    monkeypatch.setattr(rpt, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "WORK", tmp_path)

    out_path = rpt.generate_sweep_report(
        [_proposal()], run_id="testrun_1168", started_at_utc="2026-07-19T00:00:00.000+00:00",
        wallclock_s=1, cli_args={}, reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))

    cache_info = data["cross_study"]["symbol_bar_quality_cache"]
    assert cache_info["cache_found"] is False
    assert cache_info["cache_path"] == str(tmp_path / "symbol_bar_quality.json")

    assert data["studies"][0]["symbol_bar_quality"] is None
    availability_check = next(
        c for c in data["invariant_checks"]
        if c["name"] == "check_symbol_bar_quality_cache_availability")
    assert availability_check["passed"] is False
    assert availability_check["actual"]["cache_found"] is False
    assert "A.ETORO" in availability_check["actual"]["symbols_without_symbol_bar_quality"]


def test_populated_cache_makes_the_field_and_the_check_pass(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    study_name = "study_TestStrat_A_ETORO"
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, study_name)
    monkeypatch.setattr(rpt, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    monkeypatch.setattr(rpt, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    sweep.write_symbol_bar_quality_cache(tmp_path, {
        "A.ETORO": {"frac_zero_true_range": 0.01, "atr_median_bps": 12.0,
                   "bar_coverage_ratio": 0.95, "median_delta_t_s": 3600},
    })

    out_path = rpt.generate_sweep_report(
        [_proposal()], run_id="testrun_1168b", started_at_utc="2026-07-19T00:00:00.000+00:00",
        wallclock_s=1, cli_args={}, reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))

    assert data["cross_study"]["symbol_bar_quality_cache"]["cache_found"] is True
    assert data["studies"][0]["symbol_bar_quality"] is not None
    availability_check = next(
        c for c in data["invariant_checks"]
        if c["name"] == "check_symbol_bar_quality_cache_availability")
    assert availability_check["passed"] is True
