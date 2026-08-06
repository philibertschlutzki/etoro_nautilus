"""Issue #761 (P1) — Die Closed-Loop-Diagnose schreibt nur in den Cache; Denylist und Overrides
sind seit vier Katalogen leer.

Root-Cause: `binding_cause = "signal_sparse"` (vor #769: `"signal_frequency"`) empfiehlt `search_space_override`, aber kein
Mechanismus erzeugt einen kalibrierten Bounds-Vorschlag — ein Override kann nur ein Mensch
eintragen. AdxAtrMomentumStrategy/TrendPullbackStrategy verbrannten wiederholt ihr volles
Trial-Budget (1984/1920 Trials über den Referenzlauf) ohne Ertrag.

Fix: `propose_bounds_widening` leitet aus der Trial-Kohorte einen konkreten, verengten/geweiteten
Bounds-Vorschlag ab (Spearman-Korrelation Parameter↔is_total_trades); `spaces._bounds_for` wendet
ihn automatisch als Fallback unterhalb der kuratierten `search_space_overrides.json` an
(`SEARCH_SPACE_AUTO_OVERRIDE`-Telemetrie); Cache-Denylist-Einträge verfallen nach
`expires_after_runs` (Default 10) und werden dann einmalig re-getestet; der Cache liegt jetzt unter
`data/optimizer/diagnostics/` (übersteht `purge_stale_studies.py`, das nur `sweep/*.db` +
`<study_name>/trial_*/` anfasst).
"""
import json

import pytest

from automation.optimizer.sweep_diagnostics import (
    propose_bounds_widening, record_diagnosed_pair, load_diagnosed_pairs_cache,
    age_diagnosed_pairs_cache, is_diagnosed_pair_expired, recommend_diagnosis_action,
    _diagnosed_pairs_cache_path,
)


# ── propose_bounds_widening: Richtung aus der Trial-Kohorte ────────────────────────────────────────
def test_negative_correlation_proposes_lowering_the_lower_bound():
    """Laengerer cooldown_bars ⇒ WENIGER Trades (negative Korrelation) ⇒ Vorschlag: Untergrenze
    absenken (Suchraum nach unten weiten), Obergrenze unveraendert."""
    rows = [{"params": {"cooldown_bars": float(cd)}, "is_total_trades": 100 - 2 * cd}
            for cd in range(2, 30)]
    proposals = propose_bounds_widening(
        rows, "TrendPullbackStrategy", current_bounds={"cooldown_bars": (2.0, 36.0)},
        min_samples=5)
    assert "cooldown_bars" in proposals
    new_lo, new_hi = proposals["cooldown_bars"]
    assert new_lo < 2.0
    assert new_hi == 36.0


def test_positive_correlation_proposes_raising_the_upper_bound():
    rows = [{"params": {"ema_period": float(p)}, "is_total_trades": p}
            for p in range(50, 300, 5)]
    proposals = propose_bounds_widening(
        rows, "AdxAtrMomentumStrategy", current_bounds={"ema_period": (50.0, 300.0)},
        min_samples=5)
    assert "ema_period" in proposals
    new_lo, new_hi = proposals["ema_period"]
    assert new_lo == 50.0
    assert new_hi > 300.0


def test_weak_correlation_proposes_nothing():
    import random
    rng = random.Random(7)
    rows = [{"params": {"cooldown_bars": float(cd)}, "is_total_trades": rng.randint(0, 200)}
            for cd in range(2, 30)]
    proposals = propose_bounds_widening(
        rows, "TrendPullbackStrategy", current_bounds={"cooldown_bars": (2.0, 36.0)},
        min_samples=5, correlation_threshold=0.5)
    assert proposals == {}


def test_insufficient_samples_proposes_nothing():
    rows = [{"params": {"cooldown_bars": 5.0}, "is_total_trades": 10}] * 3
    proposals = propose_bounds_widening(
        rows, "TrendPullbackStrategy", current_bounds={"cooldown_bars": (2.0, 36.0)},
        min_samples=8)
    assert proposals == {}


def test_param_not_in_current_bounds_is_skipped():
    rows = [{"params": {"unrelated_param": float(i)}, "is_total_trades": i} for i in range(20)]
    proposals = propose_bounds_widening(rows, "TrendPullbackStrategy", current_bounds={})
    assert proposals == {}


# ── recommend_diagnosis_action includes proposed_bounds only for search_space_override ────────────
def test_proposed_bounds_only_attached_for_search_space_override_action():
    diagnosis = {"binding_cause": "signal_sparse", "median_oos_trades": 5, "median_is_trades": 3}
    rec = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO", diagnosis,
        has_existing_override=False, proposed_bounds={"cooldown_bars": [1.0, 36.0]})
    assert rec["action"] == "search_space_override"
    assert rec["proposed_bounds"] == {"cooldown_bars": [1.0, 36.0]}


def test_proposed_bounds_absent_for_quarantine_action():
    """Issue #830 — 'signal_quality' braucht Evidenz (n_runs_confirmed>=2 UND
    budget_executed_fraction>=0.9) fuer die volle Eskalation; ohne diese Kwargs waere die
    Empfehlung 'none'. Issue #911 — die volle Eskalation ist seither 'quarantined_pending_
    simulation_review', NICHT 'denylist' (der Closed-Loop-Rueckschrieb fuer 'signal_quality' ist
    bis nach dem #897-Kalibrierlauf ausgesetzt, siehe test_issue_830_signal_quality_
    deprioritized.py). Hier explizit mit ausreichender Evidenz aufgerufen, um weiterhin GENAU
    diesen Zweig zu testen."""
    diagnosis = {"binding_cause": "signal_quality"}
    rec = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO", diagnosis,
        proposed_bounds={"cooldown_bars": [1.0, 36.0]},
        n_runs_confirmed=2, budget_executed_fraction=1.0)
    assert rec["action"] == "quarantined_pending_simulation_review"
    assert "proposed_bounds" not in rec


# ── Cache-Ablauf (expires_after_runs) ──────────────────────────────────────────────────────────────
def test_fresh_entry_is_not_expired(tmp_path):
    rec = {"strategy": "S", "symbol": "SYM", "action": "denylist", "binding_cause": "signal_quality"}
    record_diagnosed_pair(rec, work_dir=tmp_path)
    entry = load_diagnosed_pairs_cache(tmp_path)[("S", "SYM")]
    assert entry["runs_since_recorded"] == 0
    assert entry["expires_after_runs"] == 10
    assert is_diagnosed_pair_expired(entry) is False


def test_aging_increments_runs_since_recorded_and_expires_after_threshold(tmp_path):
    rec = {"strategy": "S", "symbol": "SYM", "action": "denylist", "binding_cause": "signal_quality"}
    record_diagnosed_pair(rec, work_dir=tmp_path)
    for _ in range(9):
        cache = age_diagnosed_pairs_cache(work_dir=tmp_path)
    entry = cache[("S", "SYM")]
    assert entry["runs_since_recorded"] == 9
    assert is_diagnosed_pair_expired(entry) is False

    cache = age_diagnosed_pairs_cache(work_dir=tmp_path)
    entry = cache[("S", "SYM")]
    assert entry["runs_since_recorded"] == 10
    assert is_diagnosed_pair_expired(entry) is True


def test_re_recording_resets_the_age(tmp_path):
    rec = {"strategy": "S", "symbol": "SYM", "action": "denylist", "binding_cause": "signal_quality"}
    record_diagnosed_pair(rec, work_dir=tmp_path)
    for _ in range(10):
        age_diagnosed_pairs_cache(work_dir=tmp_path)
    assert is_diagnosed_pair_expired(load_diagnosed_pairs_cache(tmp_path)[("S", "SYM")]) is True

    # Ein frischer Diagnose-Treffer (z. B. nach dem Re-Test) setzt die Frist zurueck.
    record_diagnosed_pair(rec, work_dir=tmp_path)
    entry = load_diagnosed_pairs_cache(tmp_path)[("S", "SYM")]
    assert entry["runs_since_recorded"] == 0
    assert is_diagnosed_pair_expired(entry) is False


def test_recommendation_can_override_expires_after_runs(tmp_path):
    rec = {"strategy": "S", "symbol": "SYM", "action": "denylist",
          "binding_cause": "signal_quality", "expires_after_runs": 3}
    record_diagnosed_pair(rec, work_dir=tmp_path)
    for _ in range(3):
        cache = age_diagnosed_pairs_cache(work_dir=tmp_path)
    assert is_diagnosed_pair_expired(cache[("S", "SYM")]) is True


# ── Cache-Pfad überlebt den Purge (liegt unter diagnostics/, nicht sweep/) ─────────────────────────
def test_cache_path_lives_under_diagnostics_subdir(tmp_path):
    path = _diagnosed_pairs_cache_path(tmp_path)
    assert path.parent.name == "diagnostics"
    assert path.parent.parent == tmp_path


def test_purge_stale_studies_does_not_touch_diagnostics_cache(tmp_path, monkeypatch):
    from automation.optimizer import purge_stale_studies as psm

    rec = {"strategy": "S", "symbol": "SYM", "action": "denylist", "binding_cause": "signal_quality"}
    cache_path = record_diagnosed_pair(rec, work_dir=tmp_path)
    assert cache_path.exists()

    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    (sweep_dir / "study_Foo_BAR.db").write_text("not a real db", "utf-8")

    # find_stale_study_dbs liest echte Optuna-Storages — hier reicht der No-Op-Nachweis, dass
    # purge_stale_studies() den diagnostics/-Baum nicht anfasst, unabhaengig vom Purge-Ergebnis.
    monkeypatch.setattr(psm, "find_stale_study_dbs", lambda **k: [])
    psm.purge_stale_studies(sweep_dir=sweep_dir, current_version=99)
    assert cache_path.exists()


# ── spaces._bounds_for: Auto-Override als Fallback unterhalb der kuratierten Config ────────────────
def test_bounds_for_applies_auto_proposed_bounds_when_no_curated_override(monkeypatch, tmp_path):
    import automation.optimizer.spaces as spaces_mod

    monkeypatch.setattr(spaces_mod, "_search_space_overrides_cache", {})
    monkeypatch.setattr(spaces_mod, "_auto_proposed_bounds_cache", None)
    monkeypatch.setattr(
        "automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
        lambda: {
            ("TrendPullbackStrategy", "TSLA.ETORO"): {
                "action": "search_space_override",
                "proposed_bounds": {"cooldown_bars": [1.0, 36.0]},
            },
        },
    )
    lo, hi = spaces_mod._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "cooldown_bars", 2, 36)
    assert (lo, hi) == (1.0, 36.0)


def test_bounds_for_curated_override_takes_precedence_over_auto(monkeypatch):
    import automation.optimizer.spaces as spaces_mod

    monkeypatch.setattr(spaces_mod, "_search_space_overrides_cache", {
        "TrendPullbackStrategy": {"TSLA.ETORO": {"cooldown_bars": [5.0, 40.0]}},
    })
    monkeypatch.setattr(spaces_mod, "_auto_proposed_bounds_cache", {
        "TrendPullbackStrategy": {"TSLA.ETORO": {"cooldown_bars": [1.0, 36.0]}},
    })
    lo, hi = spaces_mod._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "cooldown_bars", 2, 36)
    assert (lo, hi) == (5.0, 40.0)


def test_bounds_for_no_override_at_all_falls_back_to_defaults(monkeypatch):
    import automation.optimizer.spaces as spaces_mod

    monkeypatch.setattr(spaces_mod, "_search_space_overrides_cache", {})
    monkeypatch.setattr(spaces_mod, "_auto_proposed_bounds_cache", {})
    lo, hi = spaces_mod._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "cooldown_bars", 2, 36)
    assert (lo, hi) == (2, 36)
