"""Issue #1044/#1193 (P2) — Der Champion-Kreis läuft in drei aufeinanderfolgenden Läufen nicht an.

Symptom. ``champions = {stored: 0, admissible: 0, corroborated: 0, written_back: 0, attempts: 14,
skipped_by_reason: {STORE_EMPTY: 14}}`` — identisch in DREI aufeinanderfolgenden Läufen. Root-Cause
laut Issue-Text NICHT entscheidbar: entweder wird der Store zwischen Läufen tatsächlich geleert,
oder ein Folgelauf liest schlicht aus einem anderen ``WORK``, das der schreibende Lauf nie sah —
"Beides ist aus den Artefakten allein nicht entscheidbar".

Fix (rein diagnostisch, siehe Issue-Text — die Root-Cause selbst bleibt offen).
1. ``CHAMPION_STORE_SCAN``-Ereignis beim Lauf-Start (``run_per_symbol_sweep``s allererste
   Champion-Store-Berührung, VOR jedem mkdir-Seiteneffekt): absoluter Pfad, mtime, Anzahl
   Einträge, Schlüsselliste (``champions.store_status``).
2. ``champions.store_path``/``store_found``/... in ``cross_study.champions`` ausgewiesen
   (``report._champions_summary``), analog ``symbol_bar_quality_cache.cache_path``.
3. ``skipped_by_reason`` unterscheidet seither ``STORE_PATH_MISSING`` (Verzeichnis existierte zu
   Laufbeginn NICHT, aus dem CHAMPION_STORE_SCAN-Ereignis) von ``STORE_EMPTY`` (Verzeichnis
   existierte, war aber leer).
"""
import json
from pathlib import Path

from automation import log_manager
from automation.optimizer import champions, invariants as inv, report, sweep, trial_config


CFG_DIR = Path("automation/config")
_CURRENT_REWARD_SEMANTICS_VERSION = json.loads(
    (CFG_DIR / "optimizer.json").read_text("utf-8")
)["reward_semantics_version"]
OPT_DATA = {
    "reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
    "champion_min_R_symbol": 0.0,
    "champion_min_tuning_edge": 0.0,
    "champion_promote_after_runs": 2,
    "champion_demote_after_runs": 2,
    "champion_min_advance_days": 30,
    "champion_region_eps": 0.10,
    "champion_enabled": True,
}


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)


def _register_events_jsonl(monkeypatch, tmp_path, events, name="events2.jsonl"):
    path = tmp_path / name
    lines = "\n".join(json.dumps(ev) for ev in events)
    path.write_text(lines + ("\n" if events else ""), "utf-8")
    monkeypatch.setitem(log_manager._JSONL_SIDECAR_PATHS, "optimizer", path)
    return path


def _scan_event(*, store_found, entry_count=0, keys=None, store_path="/x/champions"):
    return {"event_type": "CHAMPION_STORE_SCAN", "store_path": store_path,
            "store_found": store_found, "mtime_utc": None, "entry_count": entry_count,
            "keys": keys or []}


def _writeback_event(strategy, symbol, *, applied, skipped_reason=None):
    return {"event_type": "CHAMPION_WRITEBACK", "strategy": strategy, "symbol": symbol,
            "corroboration_count": None, "advance_days": None,
            "applied": applied, "skipped_reason": skipped_reason}


# ── champions.store_status ────────────────────────────────────────────────────────────────────
def test_store_status_reports_path_missing_for_fresh_work(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    status = champions.store_status()
    assert status["store_found"] is False
    assert status["entry_count"] == 0
    assert status["keys"] == []
    assert status["store_path"] == str(tmp_path / "champions")


def test_store_status_does_not_create_the_directory(tmp_path, monkeypatch):
    """Der zentrale Beweis fuer den #1193-Fix: store_status() darf KEINEN mkdir-Seiteneffekt
    haben -- sonst waere STORE_PATH_MISSING nach dem ERSTEN Aufruf fuer immer unbeobachtbar."""
    monkeypatch.setattr(champions, "WORK", tmp_path)
    champions.store_status()
    assert not (tmp_path / "champions").exists()


def test_store_status_counts_entries_and_lists_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    d = tmp_path / "champions"
    d.mkdir()
    (d / "champion_A_X_ETORO.json").write_text("{}", "utf-8")
    (d / "champion_B_Y_ETORO.json").write_text("{}", "utf-8")
    (d / "_stale").mkdir()
    status = champions.store_status()
    assert status["store_found"] is True
    assert status["entry_count"] == 2
    assert status["keys"] == ["champion_A_X_ETORO.json", "champion_B_Y_ETORO.json"]
    assert status["mtime_utc"] is not None


# ── sweep.py emits CHAMPION_STORE_SCAN as the run's first champion-store touch ──────────────────
def test_sweep_py_emits_champion_store_scan_as_the_very_first_statement(tmp_path, monkeypatch):
    """Statischer Nachweis (kein Katalog/keine Config-Isolation noetig, analog #1046s
    Source-Scan-Test): die Emission steht UNMITTELBAR nach ``sweep_t0`` -- VOR der
    ``using_real_optimize``-Bestimmung UND VOR JEDEM anderen Store-Zugriff (#794-Purge,
    Preflight-Bloecke) --, sodass ``champions.store_status()`` garantiert die ALLERERSTE
    Champion-Store-Beruehrung dieses Laufs misst."""
    source = Path("automation/optimizer/sweep.py").read_text("utf-8")
    scan_idx = source.index('"CHAMPION_STORE_SCAN"')
    t0_idx = source.index("sweep_t0 = time.perf_counter()")
    real_optimize_idx = source.index("using_real_optimize = optimize_symbol is None")
    purge_idx = source.index('# Issue #794 — Lauf-Start-Purge')
    assert t0_idx < scan_idx < real_optimize_idx < purge_idx
    assert "champions.store_status()" in source


# ── report._champions_summary — store_path/store_found im Report ────────────────────────────────
def test_champions_summary_exposes_store_path_and_found_for_empty_store(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    summary = report._champions_summary(OPT_DATA)
    assert summary["store_path"] == str(tmp_path / "champions")
    assert summary["store_found"] is False
    assert summary["entry_count"] == 0


def test_champions_summary_exposes_store_found_true_when_entries_exist(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "champions").mkdir()
    (tmp_path / "champions" / "champion_A_X_ETORO.json").write_text(
        json.dumps({"lifecycle": {}}), "utf-8")
    summary = report._champions_summary(OPT_DATA)
    assert summary["store_found"] is True
    assert summary["entry_count"] == 1


# ── STORE_PATH_MISSING vs. STORE_EMPTY relabeling ────────────────────────────────────────────────
def test_skipped_by_reason_relabels_store_empty_to_store_path_missing_when_scan_says_missing(
    tmp_path, monkeypatch,
):
    """Der Kernfall aus dem Issue: das CHAMPION_STORE_SCAN-Ereignis (vom Lauf-Start) sagt, das
    Verzeichnis existierte NICHT -- jeder STORE_EMPTY-Eintrag aus dem legacy studies_out-Fallback
    wird zu STORE_PATH_MISSING umbenannt (eine praezisere, staerkere Aussage)."""
    _isolate(monkeypatch, tmp_path)
    _register_events_jsonl(monkeypatch, tmp_path, [
        _scan_event(store_found=False),
        _writeback_event("A", "X.ETORO", applied=False, skipped_reason="STORE_EMPTY"),
    ])
    summary = report._champions_summary(
        OPT_DATA, studies_out=[{"strategy": "A", "symbol": "X.ETORO"}])
    assert summary["skipped_by_reason"] == {"STORE_PATH_MISSING": 1}
    assert "STORE_EMPTY" not in summary["skipped_by_reason"]


def test_skipped_by_reason_keeps_store_empty_when_scan_says_directory_existed(tmp_path, monkeypatch):
    """Gegenprobe: das Verzeichnis existierte laut Scan bereits (nur leer) -- STORE_EMPTY bleibt
    STORE_EMPTY, keine Ueberkorrektur."""
    _isolate(monkeypatch, tmp_path)
    _register_events_jsonl(monkeypatch, tmp_path, [
        _scan_event(store_found=True),
        _writeback_event("A", "X.ETORO", applied=False, skipped_reason="STORE_EMPTY"),
    ])
    summary = report._champions_summary(
        OPT_DATA, studies_out=[{"strategy": "A", "symbol": "X.ETORO"}])
    assert summary["skipped_by_reason"] == {"STORE_EMPTY": 1}


def test_no_scan_event_falls_back_to_fresh_store_status_scan(tmp_path, monkeypatch):
    """Kein registrierter CHAMPION_STORE_SCAN-Ereignisstrom (frischer --report-only-Prozess) --
    faellt auf den aktuellen champions.store_status()-Scan zurueck (degradiert, aber bester
    verfuegbarer Ersatz, analog dem 'attempts'-Fallback aus #1099)."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delitem(log_manager._JSONL_SIDECAR_PATHS, "optimizer", raising=False)
    summary = report._champions_summary(
        OPT_DATA, studies_out=[{"strategy": "A", "symbol": "X.ETORO"}])
    # Fresh tmp_path: kein Verzeichnis existiert -> auch der Fallback-Scan sagt store_found=False.
    assert summary["skipped_by_reason"] == {"STORE_PATH_MISSING": 1}


# ── Akzeptanzkriterium 2: stored(Lauf 1) == available_keys_total(Lauf 2) ────────────────────────
def test_two_sequential_runs_stored_equals_next_available_keys_total(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    promo = {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 33}, "R_symbol": 0.9, "R_global": 0.0,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "t1",
        "metrics_symbol": {}, "metrics_global": {},
    }

    class _FakeStudy:
        best_value = 1.0
        directions = ["maximize"]

    champions.store_champion(_FakeStudy(), "A", "X.ETORO", promo,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    summary_run1 = report._champions_summary(OPT_DATA)
    assert summary_run1["stored"] == 1

    _, _reason, provenance = champions.load_champion_entry_with_reason(
        "B", "Y.ETORO", opt_data=OPT_DATA)  # ein ANDERES Paar im "zweiten Lauf" nachgeschlagen.
    assert provenance["available_keys_total"] == summary_run1["stored"]


# ── invariants.check_champion_store_visibility ───────────────────────────────────────────────────
def test_invariant_passes_when_store_path_is_set():
    result = inv.check_champion_store_visibility({"store_path": "/x/champions", "store_found": True})
    assert result.passed is True


def test_invariant_fails_when_store_path_is_missing():
    result = inv.check_champion_store_visibility({"store_path": None, "store_found": False})
    assert result.passed is False
    assert result.severity == "medium"
