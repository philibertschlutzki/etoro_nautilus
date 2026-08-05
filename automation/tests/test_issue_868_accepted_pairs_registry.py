"""Issue #868 (P2, Katalog #866-#869, GitHub-Issue #760, Pitfall #280) — der Gate-Kollinearitäts-
Alarm meldete in 87 Studies eine Redundanz, die seit sechs Katalogen niemand konsumiert.

Root-Cause: ``reward.assert_gate_collinearity_guard`` (der ``[#667]``-Warnungs-Emitter) und
``reward.gate_collinearity_redundancy_alarm`` (die Jaccard-Kandidatenmatrix, die
``invariants.check_gate_collinearity_consolidation`` speist) trafen NIE eine Entscheidung — jedes
gemeldete Paar blieb ein folgenloser Alarm. Zusätzlich respektierte NUR die Jaccard-Matrix
``gate_consolidation_protected`` (#811); der rohe Spearman-Alarm warnte für ``min_trades`` ×
``max_drawdown`` trotz Protected-Status weiter (9 von 87 Warnungen).

Fix:
1. ``tournament.json['gate_collinearity_accepted_pairs']`` — maschinenlesbare, begründete
   Ausnahmeliste. Ein Paar hier erzeugt keine ``[#667]``-Warnung mehr und zählt nicht als
   ``redundant_candidate``, erscheint aber in ``accepted_redundancies`` mit seiner Begründung.
2. ``assert_gate_collinearity_guard`` misst bewusst UNVERÄNDERT ALLE aktiven Paare (auch
   ``gate_consolidation_protected``-Gates, #811 — die Jaccard-Matrix schliesst sie strukturell aus
   der Kandidatenmatrix aus, die Spearman-Matrix ist reine forensische Telemetrie und tut das
   NICHT); ein protected Gate wie ``max_drawdown`` braucht deshalb einen EXPLIZITEN
   ``gate_collinearity_accepted_pairs``-Eintrag, um nicht mehr ``[#667]``-Warnungen auszulösen.
3. Ein Paar, das nicht auf der Ausnahmeliste steht, bleibt in ``unaccepted_redundancies`` — genau
   diese Fälle speisen ``invariants.check_gate_collinearity_consolidation``, jetzt mit
   ``severity='blocking'``.
"""
import logging

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer.reward import (
    _gate_collinearity_accepted_pairs,
    assert_gate_collinearity_guard,
    gate_collinearity_redundancy_alarm,
)

# Spiegelt die reale tournament.json-Form (Issue #866/#867-konsolidiert).
_TCFG = {
    "eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr", "min_profit_factor"],
    "eligible_requires_any": [],
    "gate_consolidation_priority": [
        "min_trades", "max_drawdown", "oos_min_psr", "oos_min_profit_factor", "any_condition"],
    "gate_consolidation_protected": ["min_trades", "max_drawdown"],
    "gate_collinearity_accepted_pairs": [
        {"gates": ["oos_max_drawdown", "oos_min_psr"], "rationale": "harte Risiko-Obergrenze, #776"},
        {"gates": ["oos_min_trades", "oos_max_drawdown"], "rationale": "strukturelle Stichproben-Vorbedingung, #811"},
    ],
}


def _cohort(n=30):
    """Alle vier aktiven Gate-Deltas leiten sich aus DERSELBEN latenten Achse ab (Nulldurchgang
    bei latent=0 fuer ALLE Spalten, damit auch die PASS-Mengen (delta>=0), nicht nur die
    Rangkorrelation, uebereinstimmen) — perfekt kollinear paarweise (dieselbe Fixture-Konstruktion
    wie test_issue_667, ohne Offset)."""
    cohort = []
    for i in range(n):
        latent = (i - n / 2) / 10.0
        cohort.append({
            "oos_min_trades": latent,
            "oos_max_drawdown": latent * 2.0,
            "oos_min_psr": latent,
            "oos_min_profit_factor": latent * 0.5,
        })
    return cohort


# ── _gate_collinearity_accepted_pairs: Parsing + Fail-Loud ──────────────────────────────────────
def test_accepted_pairs_parses_normalized_frozenset_keys():
    accepted = _gate_collinearity_accepted_pairs(_TCFG)
    assert accepted[frozenset({"oos_max_drawdown", "oos_min_psr"})] == "harte Risiko-Obergrenze, #776"


def test_accepted_pairs_empty_when_key_missing():
    assert _gate_collinearity_accepted_pairs({}) == {}
    assert _gate_collinearity_accepted_pairs(None) == {}


def test_accepted_pairs_fail_loud_on_missing_rationale():
    with pytest.raises(ValueError, match="rationale"):
        _gate_collinearity_accepted_pairs(
            {"gate_collinearity_accepted_pairs": [{"gates": ["a", "b"]}]})


def test_accepted_pairs_fail_loud_on_wrong_gate_count():
    with pytest.raises(ValueError, match="gates"):
        _gate_collinearity_accepted_pairs(
            {"gate_collinearity_accepted_pairs": [{"gates": ["a"], "rationale": "x"}]})


def test_accepted_pairs_fail_loud_on_empty_rationale():
    with pytest.raises(ValueError, match="rationale"):
        _gate_collinearity_accepted_pairs(
            {"gate_collinearity_accepted_pairs": [{"gates": ["a", "b"], "rationale": "  "}]})


# ── assert_gate_collinearity_guard: accepted (inkl. protected-Gates!) vs. unaccepted ─────────────
def test_protected_pair_still_needs_an_explicit_accepted_pairs_entry(caplog):
    """min_trades x max_drawdown sind BEIDE gate_consolidation_protected, aber die Spearman-Matrix
    misst sie TROTZDEM (#811: sie ist bewusst unveraendert gegenueber der Jaccard-Umstellung) — nur
    der EXPLIZITE accepted_pairs-Eintrag (in _TCFG bereits gesetzt) verhindert die Warnung."""
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        result = assert_gate_collinearity_guard(_cohort(), _TCFG, threshold=0.95)
    accepted_pairs_seen = {frozenset(r["gates"]) for r in result["accepted_redundancies"]}
    assert frozenset({"oos_min_trades", "oos_max_drawdown"}) in accepted_pairs_seen
    assert not any("oos_min_trades" in r.message and "oos_max_drawdown" in r.message
                  for r in caplog.records)


def test_accepted_pair_suppresses_warning_and_is_telemetered(caplog):
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        result = assert_gate_collinearity_guard(_cohort(), _TCFG, threshold=0.95)
    accepted = result["accepted_redundancies"]
    assert len(accepted) == 2
    accepted_pairs_seen = {frozenset(r["gates"]): r["rationale"] for r in accepted}
    assert accepted_pairs_seen[frozenset({"oos_max_drawdown", "oos_min_psr"})] == "harte Risiko-Obergrenze, #776"
    assert not any("oos_max_drawdown" in r.message and "oos_min_psr" in r.message
                  and "oos_min_trades" not in r.message
                  for r in caplog.records)


def test_unaccepted_pair_still_warns_and_is_telemetered(caplog):
    """oos_min_psr x oos_min_profit_factor ist weder protected noch accepted -- bleibt eine
    UNENTSCHIEDENE Redundanz mit weiterhin aktiver [#667]-Warnung."""
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        result = assert_gate_collinearity_guard(_cohort(), _TCFG, threshold=0.95)
    unaccepted = result["unaccepted_redundancies"]
    unaccepted_pairs = {frozenset(r["gates"]) for r in unaccepted}
    assert frozenset({"oos_min_psr", "oos_min_profit_factor"}) in unaccepted_pairs
    assert any("UNENTSCHIEDEN" in r.message for r in caplog.records)


def test_no_pairs_over_threshold_yields_empty_lists_and_silence(caplog):
    import random
    rng = random.Random(11)
    cohort = [{
        "oos_min_trades": rng.uniform(-1, 1), "oos_max_drawdown": rng.uniform(-1, 1),
        "oos_min_psr": rng.uniform(-1, 1), "oos_min_profit_factor": rng.uniform(-1, 1),
    } for _ in range(40)]
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        result = assert_gate_collinearity_guard(cohort, _TCFG, threshold=0.95)
    assert result["accepted_redundancies"] == []
    assert result["unaccepted_redundancies"] == []
    assert not any("Gate-Kollinearität" in r.message for r in caplog.records)


# ── gate_collinearity_redundancy_alarm: akzeptierte Paare zählen nicht als redundant_candidate ──
def test_accepted_pair_does_not_appear_as_redundant_candidate():
    result = gate_collinearity_redundancy_alarm(_cohort(), _TCFG)
    candidate_pairs = {frozenset({a["keep"], a["redundant_candidate"]}) for a in result["alarms"]}
    assert frozenset({"oos_max_drawdown", "oos_min_psr"}) not in candidate_pairs


def test_unaccepted_pair_can_still_appear_as_redundant_candidate():
    result = gate_collinearity_redundancy_alarm(_cohort(), _TCFG)
    assert "oos_min_profit_factor" in result["redundant_candidates"] or any(
        a["redundant_candidate"] == "oos_min_profit_factor" for a in result["alarms"])


# ── invariants.check_gate_collinearity_consolidation: severity='blocking' ───────────────────────
def test_consolidation_invariant_is_blocking_severity_on_pass():
    result = inv.check_gate_collinearity_consolidation([])
    assert result.severity == "blocking"
    assert result.passed is True


def test_consolidation_invariant_is_blocking_severity_on_fail():
    records = [{"gate_collinearity_unconsolidated": ["oos_min_profit_factor"]}] * 5
    result = inv.check_gate_collinearity_consolidation(records, max_affected_fraction=0.20)
    assert result.severity == "blocking"
    assert result.passed is False


# ── Realer Config-Zustand (Smoke) ─────────────────────────────────────────────────────────────
def test_real_config_accepted_pairs_parses_without_error():
    import json
    from pathlib import Path
    tcfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    accepted = _gate_collinearity_accepted_pairs(tcfg)
    assert frozenset({"oos_max_drawdown", "oos_min_psr"}) in accepted
    assert accepted[frozenset({"oos_max_drawdown", "oos_min_psr"})]
