"""Issue #681 — Die #669-Diagnose feuert, aber die Deaktivierungsliste ist nicht closed-loop.

1. ``recommend_diagnosis_action`` schliesst die Diagnose (binding_cause) zu einer konkreten Aktion:
   'signal_quality' ⇒ immer 'denylist'; 'signal_sparse'/'hold_duration' ⇒ 'search_space_override'
   NUR für verdrahtete Strategien OHNE existierenden Override, sonst 'denylist'.
2. ``record_diagnosed_pair``/``load_diagnosed_pairs_cache`` — der automatisch gepflegte Cache
   (GETRENNT von der menschlich-kuratierten symbol_strategy_denylist.json).
3. ``enumerate_tunable_pairs`` überspringt ein 'denylist'-empfohlenes Auto-Cache-Paar automatisch ab
   dem nächsten Lauf; 'search_space_override'-Empfehlungen werden NICHT automatisch übersprungen.
"""
import json

from automation.optimizer import sweep
from automation.optimizer.sweep_diagnostics import (
    recommend_diagnosis_action,
    record_diagnosed_pair,
    load_diagnosed_pairs_cache,
    has_existing_search_space_override,
    WIRED_OVERRIDE_STRATEGIES,
)

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


# ── recommend_diagnosis_action: Fallunterscheidung nach binding_cause ───────────────────────────
def test_signal_quality_always_recommends_denylist():
    rec = recommend_diagnosis_action(
        "HourlyMeanReversionStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_quality", "median_oos_trades": 214, "median_is_trades": None},
    )
    assert rec["action"] == "denylist"
    assert rec["binding_cause"] == "signal_quality"


def test_signal_sparse_wired_strategy_without_override_recommends_bounds_override():
    assert "TrendPullbackStrategy" in WIRED_OVERRIDE_STRATEGIES
    rec = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_sparse", "median_oos_trades": 0, "median_is_trades": 3},
        has_existing_override=False,
    )
    assert rec["action"] == "search_space_override"


def test_signal_sparse_wired_strategy_with_existing_override_escalates_to_denylist():
    """Bounds-Kalibrierung wurde bereits versucht (Override existiert) und das Paar ist TROTZDEM
    tot ⇒ Eskalation auf 'denylist' statt endloser Wiederholung derselben Empfehlung."""
    rec = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_sparse", "median_oos_trades": 0, "median_is_trades": 3},
        has_existing_override=True,
    )
    assert rec["action"] == "denylist"


def test_signal_sparse_non_wired_strategy_recommends_denylist_directly():
    """Ein Override haette fuer eine nicht verdrahtete Strategie ohnehin keine Wirkung
    (spaces._bounds_for ist fail-open No-Op) ⇒ direkt Denylist, kein nutzloser Override-Vorschlag."""
    rec = recommend_diagnosis_action(
        "ComboTrendVwapStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_sparse", "median_oos_trades": 0, "median_is_trades": 1},
    )
    assert rec["action"] == "denylist"


def test_no_collapse_recommends_none():
    rec = recommend_diagnosis_action(
        "SmaCrossoverStrategy", "TSLA.ETORO", {"binding_cause": "none"},
    )
    assert rec["action"] == "none"


# ── Auto-Cache: record/load round-trip ──────────────────────────────────────────────────────────
def test_record_and_load_diagnosed_pairs_cache_roundtrip(tmp_path):
    rec = recommend_diagnosis_action(
        "HourlyMeanReversionStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_quality", "median_oos_trades": 214},
    )
    record_diagnosed_pair(rec, work_dir=tmp_path)
    cache = load_diagnosed_pairs_cache(tmp_path)
    assert cache[("HourlyMeanReversionStrategy", "TSLA.ETORO")]["action"] == "denylist"


def test_none_action_is_not_persisted(tmp_path):
    rec = recommend_diagnosis_action("SmaCrossoverStrategy", "TSLA.ETORO", {"binding_cause": "none"})
    record_diagnosed_pair(rec, work_dir=tmp_path)
    assert load_diagnosed_pairs_cache(tmp_path) == {}


def test_cache_entry_is_idempotently_overwritten(tmp_path):
    rec1 = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_sparse", "median_oos_trades": 0}, has_existing_override=False)
    record_diagnosed_pair(rec1, work_dir=tmp_path)
    rec2 = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_sparse", "median_oos_trades": 0}, has_existing_override=True)
    record_diagnosed_pair(rec2, work_dir=tmp_path)
    cache = load_diagnosed_pairs_cache(tmp_path)
    assert len(cache) == 1
    assert cache[("TrendPullbackStrategy", "TSLA.ETORO")]["action"] == "denylist"


def test_has_existing_search_space_override_reads_real_schema(tmp_path):
    (tmp_path / "search_space_overrides.json").write_text(json.dumps({
        "overrides": {"TrendPullbackStrategy": {"TSLA.ETORO": {"ema_period": [50, 100]}}}
    }), encoding="utf-8")
    assert has_existing_search_space_override("TrendPullbackStrategy", "TSLA.ETORO", tmp_path) is True
    assert has_existing_search_space_override("TrendPullbackStrategy", "AAPL.ETORO", tmp_path) is False
    assert has_existing_search_space_override("AdxAtrMomentumStrategy", "TSLA.ETORO", tmp_path) is False


def test_has_existing_search_space_override_absent_file_is_false(tmp_path):
    assert has_existing_search_space_override("TrendPullbackStrategy", "TSLA.ETORO", tmp_path) is False


# ── enumerate_tunable_pairs: closed-loop skip ────────────────────────────────────────────────────
def test_enumerate_tunable_pairs_skips_auto_diagnosed_denylist_pair(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist", lambda: {})
    monkeypatch.setattr(sweep, "age_diagnosed_pairs_cache", lambda: {
        ("HourlyMeanReversionStrategy", "A.ETORO"): {
            "strategy": "HourlyMeanReversionStrategy", "symbol": "A.ETORO",
            "action": "denylist", "binding_cause": "signal_quality",
        },
    })
    bars = {"A.ETORO": 10_000, "B.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["HourlyMeanReversionStrategy"], ["A.ETORO", "B.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("HourlyMeanReversionStrategy", "B.ETORO", "OK")]


def test_enumerate_tunable_pairs_does_not_skip_search_space_override_recommendation(monkeypatch):
    """Eine 'search_space_override'-Empfehlung ist KEIN automatischer Skip-Grund — Bounds-
    Kalibrierung bleibt eine bewusste Kalibrierlauf-/PR-Entscheidung."""
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist", lambda: {})
    monkeypatch.setattr(sweep, "age_diagnosed_pairs_cache", lambda: {
        ("TrendPullbackStrategy", "A.ETORO"): {
            "strategy": "TrendPullbackStrategy", "symbol": "A.ETORO",
            "action": "search_space_override", "binding_cause": "signal_sparse",
        },
    })
    bars = {"A.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["TrendPullbackStrategy"], ["A.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("TrendPullbackStrategy", "A.ETORO", "OK")]


def test_enumerate_tunable_pairs_absent_cache_is_bit_identical(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist", lambda: {})
    monkeypatch.setattr(sweep, "age_diagnosed_pairs_cache", lambda: {})
    bars = {"A.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["SmaCrossoverStrategy"], ["A.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("SmaCrossoverStrategy", "A.ETORO", "OK")]
