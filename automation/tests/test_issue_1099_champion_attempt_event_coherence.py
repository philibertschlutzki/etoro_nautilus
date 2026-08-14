"""Issue #1099 (Katalog #932) — ``cross_study.champions.attempts`` zählte bislang jedes
(strategy, symbol)-Paar der ``studies_out``-Kohorte als "Versuch" (Issue #1084). Symptom:
``attempts`` = 14 / 52 / 53 / 56 in vier Referenzläufen, obwohl der Ereignisstrom in JEDEM Lauf
genau 14 ``CHAMPION_WRITEBACK``-Ereignisse enthielt — die Study-Liste (die bei einem
``--report-only``-Report über eine gealterte ``proposal_*.json``-Menge wachsen kann) ist die
falsche Grundgesamtheit für "wie oft wurde ``maybe_write_back`` tatsächlich versucht".

Fix: ``report._champions_summary`` leitet ``attempts``/``skipped_by_reason`` jetzt bevorzugt aus
den tatsächlich emittierten ``CHAMPION_WRITEBACK``-Ereignissen ab (nur auflösbar im SELBEN
Prozess, der den Lauf ausgeführt hat — ``jsonl_sidecar_path``); die #1084-``studies_out``-
Rekonstruktion bleibt der Fallback für einen frischen ``--report-only``-Prozess.
``invariants.check_champion_attempt_coherence`` bewacht die Übereinstimmung dauerhaft.
"""
import json

from automation import log_manager
from automation.optimizer import champions, invariants as inv, report, trial_config


CFG_DIR = __import__("pathlib").Path("automation/config")
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
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)


def _register_events_jsonl(monkeypatch, tmp_path, events):
    """Registriert eine synthetische ``events.jsonl`` als den ``optimizer``-Sidecar-Pfad DIESES
    Tests, ohne die reale ``setup_bot_logging``-Maschinerie zu durchlaufen — ``monkeypatch.setitem``
    macht die Registrierung automatisch rückgängig (kein Leck in spätere Tests derselben
    pytest-Session, siehe die #1099-Kontaminationslehre in test_issue_1084/test_issue_818)."""
    path = tmp_path / "optimizer.events.jsonl"
    lines = "\n".join(json.dumps(ev) for ev in events)
    path.write_text(lines + ("\n" if events else ""), "utf-8")
    monkeypatch.setitem(log_manager._JSONL_SIDECAR_PATHS, "optimizer", path)
    return path


def _writeback_event(strategy, symbol, *, applied, skipped_reason=None):
    return {"event_type": "CHAMPION_WRITEBACK", "strategy": strategy, "symbol": symbol,
            "corroboration_count": None, "advance_days": None,
            "applied": applied, "skipped_reason": skipped_reason}


# ── report._champions_summary — bevorzugt den Ereignisstrom über studies_out ────────────────────
def test_attempts_derived_from_events_not_study_count(tmp_path, monkeypatch):
    """Reproduziert das #932-Referenzsymptom direkt: 3 CHAMPION_WRITEBACK-Ereignisse, aber eine
    studies_out-Kohorte mit 5 Eintraegen (z. B. ein --report-only-Report ueber gealterte
    Proposals) — attempts muss der EREIGNISZAHL folgen, nicht der Study-Zahl."""
    _isolate(monkeypatch, tmp_path)
    _register_events_jsonl(monkeypatch, tmp_path, [
        _writeback_event("A", "X.ETORO", applied=True),
        _writeback_event("B", "X.ETORO", applied=False, skipped_reason="STORE_EMPTY"),
        _writeback_event("C", "X.ETORO", applied=False, skipped_reason="QUALITY_STALE"),
    ])
    studies_out = [
        {"strategy": "A", "symbol": "X.ETORO"}, {"strategy": "B", "symbol": "X.ETORO"},
        {"strategy": "C", "symbol": "X.ETORO"}, {"strategy": "D", "symbol": "X.ETORO"},
        {"strategy": "E", "symbol": "X.ETORO"},
    ]
    summary = report._champions_summary(OPT_DATA, studies_out=studies_out)
    assert summary["attempts"] == 3  # NICHT 5 (len(studies_out)).
    assert summary["skipped_by_reason"] == {"STORE_EMPTY": 1, "QUALITY_STALE": 1}


def test_attempts_from_events_ignores_applied_true_in_skipped_by_reason(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _register_events_jsonl(monkeypatch, tmp_path, [
        _writeback_event("A", "X.ETORO", applied=True),
        _writeback_event("B", "X.ETORO", applied=True),
    ])
    summary = report._champions_summary(OPT_DATA, studies_out=[{"strategy": "A", "symbol": "X.ETORO"}])
    assert summary["attempts"] == 2
    assert summary["skipped_by_reason"] == {}


def test_attempts_from_events_defaults_missing_skipped_reason_to_unknown(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _register_events_jsonl(monkeypatch, tmp_path, [
        {"event_type": "CHAMPION_WRITEBACK", "strategy": "A", "symbol": "X.ETORO",
         "applied": False},  # kein skipped_reason-Feld
    ])
    summary = report._champions_summary(OPT_DATA, studies_out=[{"strategy": "A", "symbol": "X.ETORO"}])
    assert summary["skipped_by_reason"] == {"UNKNOWN": 1}


def test_zero_events_yields_zero_attempts_not_none(tmp_path, monkeypatch):
    """Ein registrierter, aber leerer Ereignisstrom ist ein ECHTES Nullresultat (0 Versuche dieses
    Laufs), nicht "unbekannt" — anders als ``studies_out=None`` (dort bleibt attempts None)."""
    _isolate(monkeypatch, tmp_path)
    _register_events_jsonl(monkeypatch, tmp_path, [])
    summary = report._champions_summary(OPT_DATA, studies_out=[{"strategy": "A", "symbol": "X.ETORO"}])
    assert summary["attempts"] == 0
    assert summary["skipped_by_reason"] == {}


def test_studies_out_none_stays_on_legacy_path_even_with_live_events(tmp_path, monkeypatch):
    """Vertrag aus #1084 unveraendert: ``studies_out=None`` (Legacy-/Report-only-Aufrufer) bleibt
    IMMER auf der reinen Verzeichnis-Iteration, selbst wenn (untypisch) ein Ereignisstrom
    registriert ist — in der Produktion ruft ausschliesslich _build_report auf, IMMER mit
    studies_out gesetzt; dieser Test bewacht den expliziten Vertrag fuer den Fall, dass er es
    nicht tut."""
    _isolate(monkeypatch, tmp_path)
    _register_events_jsonl(monkeypatch, tmp_path, [_writeback_event("A", "X.ETORO", applied=True)])
    summary = report._champions_summary(OPT_DATA)
    assert summary["attempts"] is None


def test_no_registered_event_stream_falls_back_to_studies_out_reconstruction(tmp_path, monkeypatch):
    """Kein registrierter Sidecar-Pfad (frischer --report-only-Prozess) — die #1084-Rekonstruktion
    aus studies_out bleibt der Fallback."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delitem(log_manager._JSONL_SIDECAR_PATHS, "optimizer", raising=False)
    studies_out = [{"strategy": "A", "symbol": "X.ETORO"}, {"strategy": "B", "symbol": "X.ETORO"}]
    summary = report._champions_summary(OPT_DATA, studies_out=studies_out)
    assert summary["attempts"] == 2


# ── invariants.check_champion_attempt_coherence ──────────────────────────────────────────────────
def test_attempt_coherence_passes_when_counts_match():
    result = inv.check_champion_attempt_coherence(14, 14)
    assert result.passed is True
    assert result.inconclusive is False


def test_attempt_coherence_fails_matching_932_reference_symptom():
    result = inv.check_champion_attempt_coherence(52, 14)
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual == {"reported_attempts": 52, "actual_writeback_events": 14}


def test_attempt_coherence_inconclusive_without_event_stream():
    result = inv.check_champion_attempt_coherence(None, None)
    assert result.passed is True
    assert result.inconclusive is True
    assert result.actual is None
