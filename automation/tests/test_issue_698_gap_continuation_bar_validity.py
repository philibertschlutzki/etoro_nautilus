"""Issue #698 (P2) — `GapContinuation`: 24/7-Continuous-Bars haben keinen echten Overnight-Gap
(Messvalidität).

Root-Cause: `GapContinuationStrategy` (Variante A) feuert auf einem Vortagsschluss-vs-
Tagesbeginn-Gap. Auf den synthetischen, kontinuierlichen 24/7-1h-Bars existiert kein echter
Overnight-Gap — die Strategie misst strukturell die Differenz zweier aufeinanderfolgender Bars,
keine Continuation-Edge. Kein Bounds-Weiten (`gap_threshold_pct`) behebt eine ungültige Messung.

Fix (Stand #698): `strategies.json` deklariert `invalid_on_continuous_bars: true` für
GapContinuation; `sweep.enumerate_tunable_pairs` überspringt eine so markierte Strategie
VOLLSTÄNDIG (kein Budget-Verbrauch je Symbol) und weist sie im `sweep_completed`-Event als
`strategies_skipped` mit Grund `SKIPPED_INVALID_ON_CONTINUOUS_BARS` aus. Der GENERISCHE
Mechanismus (`load_continuous_bar_invalid_strategies`/`enumerate_tunable_pairs`) bleibt für
künftige Strategien mit derselben Bar-Semantik-Degeneration bestehen und wird unten weiter
getestet (mit synthetischen Fixtures, nicht mehr über die REALE `strategies.json`).

Issue #809 — für GapContinuation SELBST ist der Laufzeit-Skip inzwischen SUPERSEDED: ein
still übersprungenes Budget ist eine "Stilllegung, keine Lösung" (der Roster war faktisch 14
statt 15 Strategien, ohne dass die Config das aussagt). GapContinuation ist jetzt EXPLIZIT
`strategies.json['active'] = false` (mit vollständiger #809-Begründung im `_note`-Feld) statt
weiterhin über `invalid_on_continuous_bars` markiert zu sein — siehe
`test_issue_809_gap_continuation_deactivated.py`.
"""
import json
import subprocess
from pathlib import Path

from automation.optimizer import sweep
from automation.optimizer.sweep_diagnostics import load_continuous_bar_invalid_strategies

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


# ── strategies.json: GapContinuation ist seit #809 explizit deaktiviert, nicht mehr geflaggt ────
def test_real_strategies_json_no_longer_flags_gap_continuation():
    """Issue #809 — invalid_on_continuous_bars wurde von GapContinuation entfernt (redundant: eine
    inaktive Strategie wird nie enumeriert, siehe test_issue_809_gap_continuation_deactivated.py
    für den Ersatz-Nachweis über active=false)."""
    flagged = load_continuous_bar_invalid_strategies()
    assert "GapContinuationStrategy" not in flagged


def test_other_active_strategies_are_not_flagged():
    flagged = load_continuous_bar_invalid_strategies()
    assert "OpeningRangeBreakoutStrategy" not in flagged
    assert "SqueezeBreakoutStrategy" not in flagged
    assert "DonchianRegimeBreakoutStrategy" not in flagged


def test_missing_strategies_json_yields_empty_set(tmp_path):
    assert load_continuous_bar_invalid_strategies(base_cfg=tmp_path) == frozenset()


# ── enumerate_tunable_pairs: eine markierte Strategie erzeugt NULL Paare ─────────────────────────
def test_flagged_strategy_produces_no_pairs(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_continuous_bar_invalid_strategies",
                        lambda *a, **k: frozenset({"GapContinuationStrategy"}))
    bars = {"A.ETORO": 10_000, "B.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["GapContinuationStrategy", "SmaCrossoverStrategy"], ["A.ETORO", "B.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    strategies_in_pairs = {s for s, _, _ in pairs}
    assert "GapContinuationStrategy" not in strategies_in_pairs
    assert "SmaCrossoverStrategy" in strategies_in_pairs


def test_unflagged_run_stays_bit_identical(monkeypatch):
    """Kein invalid_on_continuous_bars-Eintrag (leere Menge) ⇒ bit-identisch zum Pre-#698-
    Verhalten — GapContinuation würde normal enumeriert (hier via SmaCrossover als Stand-in
    getestet, um keine echte Backtest-Infrastruktur zu benötigen)."""
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_continuous_bar_invalid_strategies", lambda *a, **k: frozenset())
    bars = {"A.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(["SmaCrossoverStrategy"], ["A.ETORO"],
                                          tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("SmaCrossoverStrategy", "A.ETORO", "OK")]


# ── run_per_symbol_sweep: strategies_skipped trägt den benannten Grund ───────────────────────────
def test_sweep_completed_lists_flagged_strategy_with_named_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Phase 5 verboten")))
    monkeypatch.setattr(sweep, "count_available_bars", lambda *a, **k: {})
    monkeypatch.setattr(sweep, "_load_gate_config", lambda: _GATE_CFG)
    monkeypatch.setattr(sweep, "load_continuous_bar_invalid_strategies",
                        lambda *a, **k: frozenset({"GapContinuationStrategy"}))
    # enumerate_tunable_pairs bleibt REAL (nicht gemockt) — sie muss selbst die Strategie
    # überspringen und darf keine Paare für sie erzeugen (kein n_params_for-Aufruf nötig, da der
    # #698-Skip VOR n_params_for greift, siehe enumerate_tunable_pairs-Reihenfolge).
    events = []
    monkeypatch.setattr(sweep, "emit_execution_event", lambda logger, name, payload: events.append((name, payload)))

    result = sweep.run_per_symbol_sweep(
        ["GapContinuationStrategy"], [], tier="deployable")
    assert result == []
    skip_events = [p for n, p in events if n == "STRATEGY_INVALID_ON_CONTINUOUS_BARS"]
    assert len(skip_events) == 1
    assert skip_events[0]["strategy"] == "GapContinuationStrategy"
    assert skip_events[0]["reason"] == "SKIPPED_INVALID_ON_CONTINUOUS_BARS"

    completed_events = [p for n, p in events if n == "sweep_completed"]
    assert len(completed_events) == 1
    skipped = completed_events[0]["strategies_skipped"]
    assert {"strategy": "GapContinuationStrategy", "reason": "SKIPPED_INVALID_ON_CONTINUOUS_BARS"} in skipped
