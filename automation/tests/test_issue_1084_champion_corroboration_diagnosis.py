"""Issue #1084 (Katalog #866-2, Kohorte "Stufe 3b") — ``check_champion_writeback_reachability``
nennt eine widerlegte Ursache; der Closed Loop hängt am Coverage-Ledger, nicht an einer fehlenden
Call-Site.

Symptom (Referenzlauf): 14 ``CHAMPION_WRITEBACK``-Events, 12× ``STORE_EMPTY``, 2×
``NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED`` bei ``corroboration_count = 1``.

Root-Cause: (a) der Check schloss aus ``written_back == 0`` auf eine fehlende Call-Site, statt die
beobachtete ``skipped_by_reason``-Verteilung zu melden. (b) der tatsächliche Blocker ist
``corroboration_count = 1`` gegen eine Schwelle >= 2 — Korroboration erfordert eine NEUE run_id,
die ein auf ``total_runs_started = 1`` zurückgesetztes Ledger (#1064) nie ausweist. (c)
``STORE_EMPTY`` war pair-skopiert, aber store-weit benannt: ``_champion_path`` ist eine Datei je
(strategy, symbol) — ``entry is None`` heisst "kein Eintrag für DIESES Paar", nicht "der Store ist
leer".

Fix: ``champions.load_champion_entry_with_reason`` trennt ``STORE_EMPTY`` (Store insgesamt leer)
von ``NO_ENTRY_FOR_PAIR`` (Store gefüllt, kein Eintrag für dieses Paar).
``report._champions_summary(opt_data, studies_out=...)`` zählt ``attempts`` (Versuche, nicht
Store-Einträge) und ersetzt das tautologische ``NOT_WRITTEN_BACK`` durch den granularen Grund.
``invariants.check_champion_writeback_reachability`` meldet die beobachtete Verteilung statt einer
geratenen Ursache. Neue Invariante ``check_champion_corroboration_reachable`` benennt den
Korroborations-Deadlock direkt.
"""
import json
from pathlib import Path

from automation import log_manager
from automation.optimizer import champions, invariants as inv, report, trial_config


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
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)
    # Issue #1099 (Katalog #932) — report._champions_summary bevorzugt seit #1099 ein live
    # registriertes events.jsonl (log_manager._JSONL_SIDECAR_PATHS["optimizer"]) über die
    # studies_out-Rekonstruktion; dieses Modul-Dict ist GLOBAL und überlebt zwischen Testdateien in
    # DERSELBEN pytest-Session (jede Testdatei, die zuvor setup_bot_logging("optimizer", ...)
    # aufgerufen hat, würde diese Tests sonst je nach Laufreihenfolge kontaminieren). Diese Tests
    # prüfen explizit die #1084-Fallback-Rekonstruktion (kein live Ereignisstrom) — deshalb hier
    # deterministisch zurückgesetzt statt sich auf die Testreihenfolge zu verlassen.
    monkeypatch.delitem(log_manager._JSONL_SIDECAR_PATHS, "optimizer", raising=False)


def _entry(strategy, symbol, *, corroboration_count=1, writeback_applied=False,
          status_at_store="EVALUATED"):
    return {
        "strategy": strategy, "symbol": symbol, "tier": "all",
        "params": {"p": 1}, "quality": {"R_symbol": 0.9, "R_global": 0.0, "reward": 1.0},
        "provenance": {"run_id": "run1", "holdout_reject_detail": None,
                       "status_at_store": status_at_store,
                       "holdout_binding_gate": None, "holdout_gate_deltas": {}},
        "integrity": {"reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
                      "data_snapshot_sha256": "abc", "params_schema_version": None,
                      "catalog_newest_ns": 1000},
        "lifecycle": {"first_seen_run": "run1", "first_seen_catalog_newest_ns": 1000,
                      "corroboration_count": corroboration_count, "last_seen_run": "run1",
                      "last_R_symbol": 0.9, "degrade_streak": 0,
                      "writeback_applied": writeback_applied,
                      "snapshot_rollover_count": 0, "semantics_migrated_from": None},
    }


# ── champions.load_champion_entry_with_reason — STORE_EMPTY vs NO_ENTRY_FOR_PAIR (Fix Punkt 2) ──
def test_store_empty_when_no_pair_anywhere_has_an_entry(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    entry, reason, provenance = champions.load_champion_entry_with_reason(
        "DonchianRegimeBreakoutStrategy", "TSLA.ETORO", opt_data=OPT_DATA)
    assert entry is None
    assert reason == "STORE_EMPTY"
    # Issue #1103 (Katalog #936) — Provenienz auch fuer STORE_EMPTY gesetzt.
    assert provenance["looked_up_key"] == "champion_DonchianRegimeBreakoutStrategy_TSLA_ETORO.json"
    assert provenance["available_keys"] == []
    assert provenance["available_keys_total"] == 0


def test_no_entry_for_pair_when_other_pairs_have_entries(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    champions._write_entry(
        tmp_path / "champions" / "champion_Rsi2ReversionStrategy_TSLA_ETORO.json",
        _entry("Rsi2ReversionStrategy", "TSLA.ETORO"))
    entry, reason, provenance = champions.load_champion_entry_with_reason(
        "DonchianRegimeBreakoutStrategy", "TSLA.ETORO", opt_data=OPT_DATA)
    assert entry is None
    assert reason == "NO_ENTRY_FOR_PAIR"
    # Issue #1103 (Katalog #936) — der gesuchte Schluessel UND die tatsaechlich existierenden.
    assert provenance["looked_up_key"] == "champion_DonchianRegimeBreakoutStrategy_TSLA_ETORO.json"
    assert provenance["available_keys"] == ["champion_Rsi2ReversionStrategy_TSLA_ETORO.json"]
    assert provenance["available_keys_total"] == 1


# ── report._champions_summary(studies_out=...) — attempts statt Store-Eintraege (Fix Punkt 1/3) ──
def test_champions_summary_without_studies_out_stays_bit_identical(tmp_path, monkeypatch):
    """Legacy-Aufrufer (kein studies_out): attempts bleibt None, altes Verhalten unveraendert."""
    _isolate(monkeypatch, tmp_path)
    summary = report._champions_summary(OPT_DATA)
    assert summary["attempts"] is None
    assert summary["stored"] == 0
    assert summary["max_corroboration_count"] is None


def test_champions_summary_with_studies_out_counts_attempts_not_store_entries(tmp_path, monkeypatch):
    """Referenzlauf-Analogon: 14 Paare (hier 3, aus Aufwandsgruenden), nur 2 mit Store-Eintrag; die
    uebrigen zeigen NO_ENTRY_FOR_PAIR statt unsichtbar zu bleiben."""
    _isolate(monkeypatch, tmp_path)
    champions._write_entry(
        tmp_path / "champions" / "champion_DonchianRegimeBreakoutStrategy_TSLA_ETORO.json",
        _entry("DonchianRegimeBreakoutStrategy", "TSLA.ETORO", corroboration_count=1))
    champions._write_entry(
        tmp_path / "champions" / "champion_Rsi2ReversionStrategy_TSLA_ETORO.json",
        _entry("Rsi2ReversionStrategy", "TSLA.ETORO", corroboration_count=1))

    studies_out = [
        {"strategy": "DonchianRegimeBreakoutStrategy", "symbol": "TSLA.ETORO"},
        {"strategy": "Rsi2ReversionStrategy", "symbol": "TSLA.ETORO"},
        {"strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO"},
    ]
    summary = report._champions_summary(OPT_DATA, studies_out=studies_out)
    assert summary["attempts"] == 3
    assert summary["skipped_by_reason"].get("NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED") == 2
    assert summary["skipped_by_reason"].get("NO_ENTRY_FOR_PAIR") == 1
    assert "NOT_WRITTEN_BACK" not in summary["skipped_by_reason"]
    assert summary["max_corroboration_count"] == 1


def test_champions_summary_deduplicates_repeated_pairs_in_studies_out(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    studies_out = [
        {"strategy": "A", "symbol": "X.ETORO"}, {"strategy": "A", "symbol": "X.ETORO"},
    ]
    summary = report._champions_summary(OPT_DATA, studies_out=studies_out)
    assert summary["attempts"] == 1


# ── invariants.check_champion_writeback_reachability — Fix Punkt 1 ──────────────────────────────
def test_writeback_reachability_reports_observed_reasons_not_a_guessed_call_site():
    result = inv.check_champion_writeback_reachability({
        "stored": 2, "admissible": 2, "corroborated": 0, "written_back": 0,
        "skipped_by_reason": {"NO_ENTRY_FOR_PAIR": 12, "NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED": 2},
        "attempts": 14, "max_corroboration_count": 1,
    })
    assert result.passed is False
    assert "12x NO_ENTRY_FOR_PAIR" in result.detail
    assert "2x NOT_CORROBORATED_OR_WINDOW_NOT_ADVANCED" in result.detail
    assert "Call-Site" not in result.detail
    assert "14 Versuche" in result.detail


def test_writeback_reachability_names_missing_call_site_only_when_zero_attempts_observed():
    result = inv.check_champion_writeback_reachability({
        "stored": 3, "admissible": 3, "corroborated": 0, "written_back": 0,
        "skipped_by_reason": {}, "attempts": 0, "max_corroboration_count": 0,
    })
    assert result.passed is False
    assert "Call-Site" in result.detail


def test_writeback_reachability_legacy_dict_without_attempts_key_stays_backward_compatible():
    """Genau die drei bestehenden #818-Fixtures (kein 'attempts'-Key) — passed bleibt bit-identisch."""
    assert inv.check_champion_writeback_reachability(
        {"stored": 76, "admissible": 76, "corroborated": 0, "written_back": 0,
         "skipped_by_reason": {}}).passed is False
    assert inv.check_champion_writeback_reachability(
        {"stored": 5, "admissible": 5, "corroborated": 3, "written_back": 2,
         "skipped_by_reason": {}}).passed is True
    assert inv.check_champion_writeback_reachability(
        {"stored": 0, "admissible": 0, "corroborated": 0, "written_back": 0,
         "skipped_by_reason": {}}).passed is True


# ── invariants.check_champion_corroboration_reachable — Fix Punkt 4, gehärtet #1089 (Katalog #922)
# Issue #1089 — der frühere ODER-Ast (``... ODER total_runs_started > 1``) machte den Check
# fälschlich gruen, sobald IRGENDEIN gleichzeitiger Sweep-Prozess das globale, prozessübergreifende
# Ledger anfasste — unabhängig vom eigenen Symbol/Paar. Der ODER-Ast ist ERSATZLOS entfallen; der
# Check prüft seither ausschliesslich ``max_corroboration_count >= corroboration_threshold``.
# ``total_runs_started`` wird nur noch als ``provenance`` mitgeführt (siehe unten), nicht mehr in
# ``actual`` und OHNE jeden Einfluss auf PASS/FAIL.
def test_corroboration_reachable_fails_on_the_866_2_reference_deadlock():
    result = inv.check_champion_corroboration_reachable(
        {"stored": 2, "max_corroboration_count": 1}, total_runs_started=1,
        corroboration_threshold=2)
    assert result.passed is False
    assert result.actual["max_corroboration_count"] == 1
    assert result.provenance["total_runs_started"] == 1


def test_corroboration_reachable_still_fails_when_a_sibling_process_bumps_total_runs_started():
    """Issue #1089 Akzeptanzkriterium — der Check kann durch KEINEN Nebenprozess mehr gruen
    werden: ein gestiegenes (aber falsch-skopiertes) ``total_runs_started`` darf ein FAIL nicht
    mehr in ein PASS kippen, solange ``max_corroboration_count`` selbst unter der Schwelle bleibt."""
    result = inv.check_champion_corroboration_reachable(
        {"stored": 2, "max_corroboration_count": 1}, total_runs_started=4,
        corroboration_threshold=2)
    assert result.passed is False


def test_corroboration_reachable_passes_when_threshold_already_met():
    result = inv.check_champion_corroboration_reachable(
        {"stored": 2, "max_corroboration_count": 2}, total_runs_started=1,
        corroboration_threshold=2)
    assert result.passed is True


def test_corroboration_reachable_not_applicable_without_store():
    assert inv.check_champion_corroboration_reachable(
        {"stored": 0, "max_corroboration_count": None}, total_runs_started=1).passed is True


def test_corroboration_reachable_evaluates_even_without_a_readable_ledger():
    """Issue #1089 — ``total_runs_started=None`` (Ledger nicht lesbar) ist seit dem Fix KEIN
    Grund mehr, die Entscheidung auszusetzen: das Ledger ist nicht mehr Teil der Entscheidung."""
    result = inv.check_champion_corroboration_reachable(
        {"stored": 2, "max_corroboration_count": 1}, total_runs_started=None)
    assert result.passed is False
