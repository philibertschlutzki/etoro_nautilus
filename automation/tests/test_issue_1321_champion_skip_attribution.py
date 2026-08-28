"""Issue #1321 (GH #1198, P2) — Champion-Skip-Attribution nennt eine Lese-Ursache für ein
Schreibproblem.

Symptom. ``CHAMPION_STORE_ATTEMPT`` meldet ``skip_detail.reason = "EMPTY_PARAMS"``, der Report
aggregiert ``skipped_by_reason = {"STORE_EMPTY": 14}``, und ``check_champion_writeback_
reachability`` übernimmt das wörtlich mit dem Zusatz "die Ursache ist gemessen, nicht geraten"
(B-14).

Root-Cause. ``report._champions_summary`` aggregierte ausschliesslich die ``CHAMPION_WRITEBACK``-
Ereignisse (Ebene 2, WARUM ein admissibler Store-Eintrag nicht zurückgeschrieben wurde). Bei einem
leeren Store ist Ebene 2 trivialerweise ``STORE_EMPTY``; die eigentliche Ursache (WARUM nie ein
admissibler Kandidat entstand) liegt eine Ebene davor (``CHAMPION_STORE_ATTEMPT``, Ebene 1).

Fix. ``champions.skipped_by_reason`` wird zweistufig ausgewiesen (``store_attempt_skipped_by_
reason``, ``writeback_skipped_by_reason``). ``check_champion_writeback_reachability`` nennt die
ERSTE nicht-triviale Ursache in der Kette und fällt nur bei fehlender Stufe-1-Telemetrie auf
``STORE_EMPTY``/``writeback_skipped_by_reason`` zurück.
"""
import logging
from pathlib import Path

import pytest

from automation.optimizer import champions, invariants as inv, report as rpt, sweep, trial_config


# ── Akzeptanzkriterium 1 — bei 0 eligiblen Trials nennt der Check EMPTY_PARAMS, nicht STORE_EMPTY ─

def test_check_names_the_stage_one_cause_not_the_trivial_store_empty():
    """Direkte B-14-Reproduktion: 14 Ebene-1-Versuche scheitern an EMPTY_PARAMS (0 eligible
    Trials), waehrend Ebene 2 nur das triviale STORE_EMPTY traegt."""
    result = inv.check_champion_writeback_reachability({
        "stored": 0, "written_back": 0, "attempts": 14,
        "skipped_by_reason": {"STORE_EMPTY": 14},
        "writeback_skipped_by_reason": {"STORE_EMPTY": 14},
        "store_attempt_skipped_by_reason": {"EMPTY_PARAMS": 14},
        "champion_store_attempts": [
            {"strategy": "S", "symbol": f"SYM{i}.ETORO", "stored": False,
             "skip_detail": {"reason": "EMPTY_PARAMS"}}
            for i in range(14)
        ],
    })
    assert "EMPTY_PARAMS" in result.detail
    assert "STORE_EMPTY" not in result.detail
    assert result.actual["store_attempt_skipped_by_reason"] == {"EMPTY_PARAMS": 14}


def test_falls_back_to_writeback_reason_when_stage_one_telemetry_is_absent():
    """Ein aelterer Report ohne store_attempt_skipped_by_reason-Feld faellt auf die Ebene-2-
    Verteilung zurueck (unveraendertes Pre-#1321-Verhalten, kein Feld -> kein Fehler)."""
    result = inv.check_champion_writeback_reachability({
        "stored": 0, "written_back": 0, "attempts": 14,
        "skipped_by_reason": {"STORE_EMPTY": 14},
    })
    assert "STORE_EMPTY" in result.detail


def test_falls_back_when_stage_one_dict_is_explicitly_empty():
    """Ebene-1-Feld ist PRAESENT, aber leer (kein einziger Ebene-1-Versuch beobachtet, z. B. ein
    Report-only-Prozess ohne eigenen Ereignisstrom) -- faellt ebenfalls auf Ebene 2 zurueck."""
    result = inv.check_champion_writeback_reachability({
        "stored": 0, "written_back": 0, "attempts": 14,
        "writeback_skipped_by_reason": {"STORE_EMPTY": 14},
        "store_attempt_skipped_by_reason": {},
    })
    assert "STORE_EMPTY" in result.detail


def test_stage_one_reason_wins_even_when_it_differs_from_stage_two():
    result = inv.check_champion_writeback_reachability({
        "stored": 0, "written_back": 0, "attempts": 5,
        "writeback_skipped_by_reason": {"STORE_PATH_MISSING": 5},
        "store_attempt_skipped_by_reason": {"REJECT_HOLDOUT_GATE": 5},
    })
    assert "REJECT_HOLDOUT_GATE" in result.detail
    assert "STORE_PATH_MISSING" not in result.detail


def test_writeback_success_reports_ok_regardless_of_stage_one_reasons():
    """written_back > 0 bleibt PASS, unabhaengig davon, ob Ebene-1-Ablehnungen fuer ANDERE
    Kandidaten desselben Laufs beobachtet wurden."""
    result = inv.check_champion_writeback_reachability({
        "stored": 2, "written_back": 1, "attempts": 2,
        "writeback_skipped_by_reason": {},
        "store_attempt_skipped_by_reason": {"EMPTY_PARAMS": 1},
    })
    assert result.detail == "OK"
    assert result.passed is True


# ── Akzeptanzkriterium 2 — beide Zaehler stehen getrennt in cross_study.champions ────────────────

class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.events = []

    def emit(self, record):
        msg = record.getMessage()
        if "[JSON_EVENT]" in msg:
            import json as _json
            try:
                self.events.append(_json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))
            except Exception:
                pass


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(champions, "CHAMPION_ROOT", tmp_path / "champions")
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)


def _register_events_jsonl(monkeypatch, tmp_path, events):
    """Dieselbe Registrierungs-Konvention wie test_issue_1099_champion_attempt_event_coherence.py
    (die etablierte, kontaminationsfreie Art, dem 'optimizer'-Sidecar-Pfad eine synthetische
    events.jsonl unterzuschieben, ohne die reale setup_bot_logging-Maschinerie zu durchlaufen)."""
    import json as _json
    from automation import log_manager

    path = tmp_path / "optimizer.events.jsonl"
    lines = "\n".join(_json.dumps(ev) for ev in events)
    path.write_text(lines + ("\n" if events else ""), "utf-8")
    monkeypatch.setitem(log_manager._JSONL_SIDECAR_PATHS, "optimizer", path)
    return path


def _store_attempt_event(strategy, symbol, *, stored, reason=None):
    return {"event_type": "CHAMPION_STORE_ATTEMPT", "strategy": strategy, "symbol": symbol,
            "stored": stored, "skip_detail": {"reason": reason} if reason else None}


def _writeback_event(strategy, symbol, *, applied, skipped_reason=None):
    return {"event_type": "CHAMPION_WRITEBACK", "strategy": strategy, "symbol": symbol,
            "corroboration_count": None, "advance_days": None,
            "applied": applied, "skipped_reason": skipped_reason}


def test_champions_summary_exposes_both_counters_separately(monkeypatch, tmp_path):
    """End-to-End durch report._champions_summary: ein Lauf mit ausschliesslich Ebene-1-
    Ablehnungen (0 eligible Trials, EMPTY_PARAMS) UND keinem einzigen Store-Eintrag muss
    store_attempt_skipped_by_reason UND writeback_skipped_by_reason getrennt tragen."""
    _isolate(monkeypatch, tmp_path)
    events = []
    for i in range(3):
        events.append(_store_attempt_event(
            "TrendPullbackStrategy", f"SYM{i}.ETORO", stored=False, reason="EMPTY_PARAMS"))
        events.append(_writeback_event(
            "TrendPullbackStrategy", f"SYM{i}.ETORO", applied=False, skipped_reason="STORE_EMPTY"))
    _register_events_jsonl(monkeypatch, tmp_path, events)

    summary = rpt._champions_summary({"champion_promote_after_runs": 2}, studies_out=[])
    assert summary["store_attempt_skipped_by_reason"] == {"EMPTY_PARAMS": 3}
    assert summary["writeback_skipped_by_reason"] == {"STORE_EMPTY": 3}
    # Rueckwaertskompatibilitaet: der alte Feldname bleibt ein Alias fuer Ebene 2.
    assert summary["skipped_by_reason"] == summary["writeback_skipped_by_reason"]


def test_champions_summary_stage_one_counter_is_empty_when_no_rejections_observed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    summary = rpt._champions_summary({"champion_promote_after_runs": 2}, studies_out=[])
    assert summary["store_attempt_skipped_by_reason"] == {}


# ── Akzeptanzkriterium 3 — Unit-Test fuer die Kette Stufe 1 -> Stufe 2 ────────────────────────────

def test_stage_one_to_stage_two_chain_end_to_end(monkeypatch, tmp_path):
    """Simuliert die volle Kette: store_champion() emittiert CHAMPION_STORE_ATTEMPT (Ebene 1,
    EMPTY_PARAMS, da candidate_params leer ist) -> report._champions_summary aggregiert BEIDE
    Ebenen getrennt -> check_champion_writeback_reachability nennt die Ebene-1-Ursache."""
    _isolate(monkeypatch, tmp_path)
    # Sidecar-Pfad VOR dem store_champion()-Aufruf registrieren, damit emit_execution_event
    # (log_manager._JSONL_SIDECAR_PATHS-gesteuert) das reale CHAMPION_STORE_ATTEMPT-Ereignis
    # tatsaechlich physisch dorthin schreibt (dieselbe Registrierung wie _register_events_jsonl,
    # hier aber leer gestartet -- store_champion() selbst ist die Ereignisquelle).
    _register_events_jsonl(monkeypatch, tmp_path, [])
    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)

    class _FakeStudy:
        best_value = 1.0
        directions = ["maximize"]

    try:
        opt_data = {
            "reward_semantics_version": 1, "champion_min_R_symbol": 0.5,
            "champion_min_tuning_edge": 0.1, "champion_promote_after_runs": 2,
            "champion_demote_after_runs": 2, "champion_min_advance_days": 30,
            "champion_region_eps": 0.10, "champion_enabled": True,
        }
        # Ein Kandidat mit leeren params -> champion_is_admissible lehnt mit 'EMPTY_PARAMS' ab
        # (spaces.py-Domaenenregister-unabhaengig, siehe champions.py:497).
        promotion = {
            "params": {}, "r_symbol": 0.0, "r_global": 0.0, "status": "READY_FOR_PR",
            "tuning_edge": 0.0,
        }
        champions.store_champion(
            _FakeStudy(), "TrendPullbackStrategy", "TSLA.ETORO", promotion,
            catalog_newest_ns=1_700_000_000_000_000_000, opt_data=opt_data, tier="tier1",
            run_id="test-1321-chain",
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    store_attempt_events = [e for e in handler.events if e.get("event_type") == "CHAMPION_STORE_ATTEMPT"]
    assert len(store_attempt_events) == 1
    assert store_attempt_events[0]["skip_detail"]["reason"] == "EMPTY_PARAMS"

    # store_champion() selbst emittiert NUR das Ebene-1-Ereignis (die Kette bricht dort ab, kein
    # Store-Eintrag entsteht). Der Ebene-2-Schreibversuch (sweep._attempt_champion_writeback) ist
    # ein SEPARATER, spaeterer Produktionscodepfad -- fuer denselben Kandidaten wuerde er trivial
    # 'kein Eintrag gefunden' (STORE_EMPTY) melden, exakt das B-14-Symptom. Hier synthetisch
    # angehaengt, um die VOLLE Kette (Ebene 1 -> Ebene 2 -> Check) ohne die gesamte Sweep-
    # Orchestrierung nachzubilden.
    import json as _json
    from automation.log_manager import jsonl_sidecar_path
    events_path = jsonl_sidecar_path("optimizer")
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(_writeback_event(
            "TrendPullbackStrategy", "TSLA.ETORO", applied=False, skipped_reason="STORE_EMPTY",
        )) + "\n")

    summary = rpt._champions_summary(opt_data, studies_out=[])
    assert summary["store_attempt_skipped_by_reason"].get("EMPTY_PARAMS") == 1
    assert summary["stored"] == 0
    assert summary["written_back"] == 0

    result = inv.check_champion_writeback_reachability(summary)
    assert "EMPTY_PARAMS" in result.detail
