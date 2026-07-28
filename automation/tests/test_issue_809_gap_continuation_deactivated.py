"""Issue #809 — `GapContinuationStrategy` war seit #698 vollständig zur LAUFZEIT übersprungen
(`invalid_on_continuous_bars`/`SKIPPED_INVALID_ON_CONTINUOUS_BARS`): der Roster war damit faktisch
14 statt 15 Strategien, ohne dass `strategies.json` selbst dies aussagt — die Anforderung „jede
Strategie maximal optimiert" war unerfüllbar, ohne dass irgendwo ein Beleg dafür stand.

Entscheidung (siehe strategies.json's `_note` für die volle Begründung): Variante B (der im Issue
vorgeschlagene Default — ein session_open_hour-basierter EQUITY-Overnight-Gap + expliziter
CRYPTO-Denylist-Eintrag) ist NICHT umsetzbar, weil dieses System keinen RTH-Session-Kalender
besitzt (`sweep_diagnostics.py`: "die einzige in diesem System verfügbare Bar-Semantik, kein
RTH-Session-Katalog") — jede `session_open_hour`-Berechnung wäre auf denselben kontinuierlichen
24/7-Bars erfunden, kein echtes Signal. Stattdessen Variante A (vom Issue selbst als "ehrlicher"
benannte Alternative): `strategies.json['active'] = false`, explizit und begründet deklariert statt
einer stillen Laufzeit-Ausnahme. Der Strategie-Code bleibt für eine künftige Re-Aktivierung
erhalten, sobald ein echter Session-Kalender verfügbar ist.
"""
import json
from pathlib import Path

from automation.optimizer import sweep
from automation.optimizer.sweep_diagnostics import load_continuous_bar_invalid_strategies


def _strategies_json():
    return json.loads((Path("automation/config") / "strategies.json").read_text("utf-8"))


def _gap_continuation_entry():
    data = _strategies_json()
    entries = [s for s in data["strategies"] if s.get("strategy_class") == "GapContinuationStrategy"]
    assert len(entries) == 1
    return entries[0]


# ── strategies.json: explizit und begründet deaktiviert (Variante A) ──────────────────────────────
def test_gap_continuation_is_explicitly_deactivated():
    entry = _gap_continuation_entry()
    assert entry["active"] is False


def test_deactivation_note_references_issue_809_and_the_root_cause():
    entry = _gap_continuation_entry()
    note = entry.get("_note", "")
    assert "#809" in note
    assert "RTH-Session-Kalender" in note or "Session-Kalender" in note


def test_invalid_on_continuous_bars_flag_removed_as_redundant():
    """Der #698-Laufzeit-Skip-Mechanismus ist fuer GapContinuation nicht mehr noetig — eine
    inaktive Strategie wird nie enumeriert, das Flag waere doppelt gemoppelt."""
    entry = _gap_continuation_entry()
    assert "invalid_on_continuous_bars" not in entry


def test_gap_continuation_not_flagged_via_the_698_mechanism_anymore():
    flagged = load_continuous_bar_invalid_strategies()
    assert "GapContinuationStrategy" not in flagged


# ── sweep._resolve_strategies: nicht mehr im aktiven Set ─────────────────────────────────────────
def test_gap_continuation_absent_from_active_strategy_resolution():
    strategies = sweep._resolve_strategies("all")
    assert "GapContinuationStrategy" not in strategies


def test_other_previously_flagged_strategies_are_unaffected():
    """Regression: die #809-Deaktivierung betrifft NUR GapContinuation — andere aktive Strategien
    bleiben unveraendert im Roster."""
    strategies = sweep._resolve_strategies("all")
    assert "SmaCrossoverStrategy" in strategies
    assert "OpeningRangeBreakoutStrategy" in strategies


# ── Konsistenz mit #595: deaktivierte Strategien brauchen keinen Suchraum ─────────────────────────
def test_gap_continuation_disabled_but_not_contradicting_active_set():
    """Dieselbe Invariante wie test_issue_595_strategy_space_parity.py: eine deaktivierte Strategie
    ist NICHT im aktiven Set, der Parity-Guard (jede aktive Strategie braucht einen Suchraum in
    spaces.py) betrifft sie folglich nicht — unabhaengig davon, ob GapContinuation ueberhaupt einen
    Suchraum hat."""
    data = _strategies_json()
    disabled = [s["strategy_class"] for s in data["strategies"] if s.get("active") is False]
    assert "GapContinuationStrategy" in disabled
    active = [s["strategy_class"] for s in data["strategies"]
              if s.get("active", True) is not False and s.get("strategy_class")]
    assert "GapContinuationStrategy" not in active
