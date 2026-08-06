"""Issue #818 (P0, Katalog #817-#821, GitHub-Issue #749) — ``champions.maybe_write_back`` hatte
KEINE Produktions-Call-Site.

Root-Cause: eine vollständige Call-Site-Analyse (``grep -rn "maybe_write_back(" --include=*.py .``)
fand die Funktion nur in ihrer eigenen Definition und in Tests — kein einziger Aufruf im
Produktionspfad. ``lifecycle.writeback_applied`` blieb in 76/76 Store-Einträgen ``false``. Exakt
die Fehlerklasse aus Pitfall #237 (#794/#796/#797): ein Fix gilt als "gemergt", sobald der Code
existiert, statt sobald ein gemessenes Akzeptanzkriterium in einem realen Lauf erfüllt ist.

Fix: ``sweep._attempt_champion_writeback`` — aufgerufen unmittelbar nach einem erfolgreichen
``champions.store_champion`` in ``_run_confirm_and_export`` (siehe ``sweep.py``) — liest den
frisch geschriebenen Store-Stand (``load_champion_entry``) und versucht ``maybe_write_back``.
Emittiert IMMER ein ``CHAMPION_WRITEBACK``-Event, unabhängig vom Ausgang. Ergänzt um den achten
Invarianten-Check (``invariants.check_champion_writeback_reachability``) und die
``report.cross_study.champions``-Zählung.
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import champions, sweep, invariants, report, trial_config


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


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.events = []

    def emit(self, record):
        msg = record.getMessage()
        if "[JSON_EVENT]" in msg:
            try:
                self.events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))
            except Exception:
                pass


def _capture_champion_writeback_events(fn):
    handler = _CaptureHandler()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        fn()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    return [e for e in handler.events if e.get("event_type") == "CHAMPION_WRITEBACK"]


def _isolate(monkeypatch, tmp_path):
    # WICHTIG: config_dir() wird auf tmp_path isoliert (NICHT das reale automation/config) --
    # maybe_write_back() schreibt strategy_symbol_seeds.json relativ zu config_dir(), wenn kein
    # base_cfg injiziert wird (Produktionsverhalten, sweep._attempt_champion_writeback ruft ohne
    # base_cfg auf). Ohne diese Isolation wuerde ein Test das REALE Repo-Config-Verzeichnis
    # verschmutzen.
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(champions, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)


class _FakeStudy:
    best_value = 1.0
    directions = ["maximize"]


def test_attempt_champion_writeback_applies_for_ready_for_pr(tmp_path, monkeypatch):
    """Integrationstest ueber ZWEI Sweep-'Laeufe': store_champion(READY_FOR_PR) gefolgt von
    _attempt_champion_writeback muss sofort schreiben (kein Korroborations-Wartefenster fuer eine
    echte Promotion) und strategy_symbol_seeds.json enthaelt danach einen Eintrag."""
    _isolate(monkeypatch, tmp_path)
    promotion = {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 33}, "R_symbol": 0.9, "R_global": 0.0,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")

    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))

    assert len(events) == 1
    assert events[0]["applied"] is True
    assert events[0]["skipped_reason"] is None
    seeds = json.loads((tmp_path / "strategy_symbol_seeds.json").read_text("utf-8"))
    assert seeds["seeds"]["SmaCrossoverStrategy"]["TSLA.ETORO"] == {"sma_period": 33}
    stored = json.loads(
        (tmp_path / "champions" / "champion_SmaCrossoverStrategy_TSLA_ETORO.json").read_text("utf-8"))
    assert stored["lifecycle"]["writeback_applied"] is True


def test_attempt_champion_writeback_reports_quality_stale_skip_reason(tmp_path, monkeypatch):
    """Issue #819-Kopplung: ein quality_stale Eintrag (reward_semantics_version-Mismatch) wird
    NICHT zurueckgeschrieben; das Event traegt skipped_reason == 'QUALITY_STALE'."""
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
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))

    assert len(events) == 1
    assert events[0]["applied"] is False
    assert events[0]["skipped_reason"] == "QUALITY_STALE"


def test_attempt_champion_writeback_no_entry_is_non_fatal(tmp_path, monkeypatch):
    """Kein Champion-Store-Eintrag vorhanden -- die Funktion crasht nicht und meldet STORE_EMPTY
    (Issue #910 Fix 2 — vorher NO_ADMISSIBLE_ENTRY, ununterscheidbar von einem existierenden, aber
    inadmissiblen Eintrag)."""
    _isolate(monkeypatch, tmp_path)
    events = _capture_champion_writeback_events(
        lambda: sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA))
    assert len(events) == 1
    assert events[0]["applied"] is False
    assert events[0]["skipped_reason"] == "STORE_EMPTY"


def test_attempt_champion_writeback_swallows_exceptions(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    def _boom(*a, **k):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(champions, "load_champion_entry", _boom)
    sweep._attempt_champion_writeback("S", "SYM.ETORO", OPT_DATA)  # darf nicht raisen


# ── Invarianten-Check (achter Check, #742) ──────────────────────────────────────────────────────
def test_invariant_fails_when_stored_but_never_written_back():
    result = invariants.check_champion_writeback_reachability(
        {"stored": 76, "admissible": 76, "corroborated": 0, "written_back": 0, "skipped_by_reason": {}})
    assert result.passed is False


def test_invariant_passes_when_written_back_gt_zero():
    result = invariants.check_champion_writeback_reachability(
        {"stored": 5, "admissible": 5, "corroborated": 3, "written_back": 2, "skipped_by_reason": {}})
    assert result.passed is True


def test_invariant_not_applicable_when_store_empty():
    result = invariants.check_champion_writeback_reachability(
        {"stored": 0, "admissible": 0, "corroborated": 0, "written_back": 0, "skipped_by_reason": {}})
    assert result.passed is True


# ── report._champions_summary — der #742-Zaehlerpaar-Konsument ─────────────────────────────────
def test_report_champions_summary_counts_store_state(tmp_path, monkeypatch):
    """store_champion selbst persistiert nie einen inadmissiblen NEUEN Kandidaten (kein File ohne
    Vorgaenger) -- 'skipped_by_reason' im Report deckt daher vor allem Eintraege ab, die auf der
    Platte liegen, aber unter der AKTUELLEN Config nicht mehr admissible sind (z. B. ein
    versionsfremder/degenerierter Alt-Eintrag). Dieser Test schreibt einen solchen Eintrag direkt
    (bypass store_champion), um genau diesen Report-Pfad zu pruefen."""
    _isolate(monkeypatch, tmp_path)
    promo_ready = {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 33}, "R_symbol": 0.9, "R_global": 0.0,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "t1",
        "metrics_symbol": {}, "metrics_global": {},
    }
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", promo_ready,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    sweep._attempt_champion_writeback("SmaCrossoverStrategy", "TSLA.ETORO", OPT_DATA)

    bad_entry = {
        "strategy": "SmaCrossoverStrategy", "symbol": "AAPL.ETORO", "tier": "all",
        "params": {"sma_period": 12}, "quality": {"R_symbol": 0.9, "R_global": 0.0, "reward": 1.0},
        "provenance": {"run_id": "run1", "holdout_reject_detail": "REJECT_SELECTION_PBO",
                       "status_at_store": "REJECTED_SELECTION_OVERFIT",
                       "holdout_binding_gate": None, "holdout_gate_deltas": {}},
        "integrity": {"reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
                      "data_snapshot_sha256": "abc", "params_schema_version": None,
                      "catalog_newest_ns": 1000},
        "lifecycle": {"first_seen_run": "run1", "first_seen_catalog_newest_ns": 1000,
                      "corroboration_count": 1, "last_seen_run": "run1", "last_R_symbol": 0.9,
                      "degrade_streak": 0, "writeback_applied": False,
                      "snapshot_rollover_count": 0, "semantics_migrated_from": None},
    }
    champions._write_entry(tmp_path / "champions" / "champion_SmaCrossoverStrategy_AAPL_ETORO.json",
                           bad_entry)

    summary = report._champions_summary(OPT_DATA)
    assert summary["stored"] == 2
    assert summary["admissible"] == 1
    assert summary["written_back"] == 1
    assert summary["skipped_by_reason"].get("REJECTION_NOT_ALLOWLISTED") == 1
