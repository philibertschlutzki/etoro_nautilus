"""Issue #1094/#1242 (P1, Fix Punkt 1) — STORE_PATH_MISSING zuerst: der Zähler wird an dieselbe
Pfadauflösung gebunden wie ``store_found``.

Symptom. ``check_champion_writeback_reachability`` meldete in Frischläufen 2× STORE_PATH_MISSING,
obwohl ``champions.store_path`` mit ``store_found = true`` gemeldet wurde.

Root-Cause. ``sweep._attempt_champion_writeback`` stempelte ``skipped_reason='STORE_EMPTY'`` über
``champions.load_champion_entry_with_reason``s ``_champion_store_has_any_entry()`` — eine LIVE
Glob-Prüfung zum Zeitpunkt JEDES einzelnen Paar-Versuchs. Die Unterscheidung STORE_EMPTY (Store
existierte, war aber leer) vs. STORE_PATH_MISSING (Verzeichnis existierte beim Lauf-Start NICHT)
geschah erst NACHTRÄGLICH in ``report._champions_summary`` — durch Wiederabspielen des ERSTEN
``CHAMPION_STORE_SCAN``-Ereignisses aus einem JSONL-Sidecar. Bei mehreren Symbolen in getrennten
Worker-Prozessen (n_jobs > 1) kann der Report-Prozess ein ANDERES Scan-Ereignis lesen als das, das
den tatsächlichen Lauf-Start-Zustand FÜR DIESES Symbols Paare trägt — der ``store_path``, den der
Report zeigt, ist ausserdem der AKTUELLE (längst existierende) Pfad, nicht der Zustand zum
Zeitpunkt des jeweiligen Skip-Ereignisses.

Fix. Dieselbe In-Prozess-Variable, die ``run_per_symbol_sweep`` als ``CHAMPION_STORE_SCAN``-
Ereignis emittiert (``champions.store_status()['store_found']``), wird jetzt UNMITTELBAR (kein
Cross-Prozess-Replay) an ``_attempt_champion_writeback`` durchgereicht und reklassifiziert
STORE_EMPTY dort direkt zu STORE_PATH_MISSING, AN DER QUELLE.
"""
import json
import logging
from pathlib import Path

from automation.optimizer import champions, sweep, trial_config
from automation.tests.test_issue_818_champion_writeback_wiring import (
    OPT_DATA, _capture_champion_writeback_events, _isolate, _FakeStudy,
)


def test_store_found_at_run_start_true_keeps_store_empty_classification(tmp_path, monkeypatch):
    """Regressionsschutz: ein Lauf, der auf einem BEREITS EXISTIERENDEN (aber leeren) Store-
    Verzeichnis startet (store_found=True), reklassifiziert STORE_EMPTY NICHT — die Unterscheidung
    bleibt intakt."""
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "champions").mkdir(parents=True, exist_ok=True)  # Verzeichnis existiert, ist leer.
    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback(
            "SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA, store_found_at_run_start=True))
    assert len(events) == 1
    assert events[0]["applied"] is False
    assert events[0]["skipped_reason"] == "STORE_EMPTY"


def test_store_found_at_run_start_false_reclassifies_to_store_path_missing(tmp_path, monkeypatch):
    """Kernreproduktion des Fixes: store_found_at_run_start=False (das Verzeichnis existierte beim
    Lauf-Start nachweislich NICHT) reklassifiziert ein STORE_EMPTY-Ergebnis direkt an der Quelle zu
    STORE_PATH_MISSING — ohne auf eine spätere Report-seitige Rekonstruktion angewiesen zu sein."""
    _isolate(monkeypatch, tmp_path)
    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback(
            "SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA, store_found_at_run_start=False))
    assert len(events) == 1
    assert events[0]["applied"] is False
    assert events[0]["skipped_reason"] == "STORE_PATH_MISSING"


def test_reclassification_only_applies_to_store_empty_not_other_skip_reasons(tmp_path, monkeypatch):
    """store_found_at_run_start=False darf NUR ein STORE_EMPTY-Ergebnis reklassifizieren -- ein
    existierender, aber inadmissibler/nicht-korroborierter Eintrag bleibt bei seinem eigenen,
    granularen Reason-Code (hier: QUALITY_STALE)."""
    _isolate(monkeypatch, tmp_path)
    old_opt = {**OPT_DATA, "reward_semantics_version": 1}
    promotion = {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 33}, "R_symbol": 0.9, "R_global": 0.0,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=old_opt, run_id="run1")
    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback(
            "SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA, store_found_at_run_start=False))
    assert len(events) == 1
    assert events[0]["skipped_reason"] == "QUALITY_STALE"


def test_legacy_caller_without_parameter_is_bit_identical(tmp_path, monkeypatch):
    """Ein Aufrufer ohne den neuen Parameter (Default None) bleibt bit-identisch zum Pre-#1094-
    Verhalten -- keine Reklassifikation."""
    _isolate(monkeypatch, tmp_path)
    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))
    assert events[0]["skipped_reason"] == "STORE_EMPTY"


def test_run_per_symbol_sweep_threads_store_found_at_run_start_from_the_same_scan(tmp_path, monkeypatch):
    """End-to-End: die Variable, die run_per_symbol_sweep als CHAMPION_STORE_SCAN-Ereignis
    emittiert, ist dieselbe, die an _attempt_champion_writeback durchgereicht wird (kein separater
    Cross-Prozess-Replay-Pfad mehr nötig, um STORE_PATH_MISSING korrekt zu erkennen)."""
    import inspect
    src = inspect.getsource(sweep.run_per_symbol_sweep)
    assert "_champion_store_found_at_run_start" in src
    assert "store_found_at_run_start=_champion_store_found_at_run_start" in src
