"""Issue #1295 (GH #1168, Katalog #1272-1297, P1) — ``atr_trailing_multiplier`` einfrieren, wo der
Floor bindet.

Symptom. ``check_atr_floor_dimension_freeze_candidates`` meldet in 4/4 Läufen 8 qualifizierende
Studies mit dem expliziten Zusatz "NICHT umgesetzt". ``atr_floor_binding_trial_fraction`` erreicht
0,9837-1,0000; der Median über alle Studies liegt bei 80,2 %. ``check_search_made_progress`` FAILt
konsequent 4/4 (9 Studies je Lauf ohne Gradienten).

Root-Cause. Der Check (#1263/GH #1133) ist reine Diagnose ohne Konsequenz.

Scope-Entscheidung (dieselbe Sandbox-Beschraenkung wie #1263 selbst — kein ``nautilus_trader``
verfuegbar, um eine "bit-identisch ohne Bindung"-Live-Intervention End-to-End zu verifizieren):
implementiert ist die EXPLIZITE Policy-Steuerung (Fix Punkt 1 aus dem Issue-Text) —
``optimizer.json['atr_floor_dimension_freeze_policy'] ∈ {'diagnose', 'freeze'}`` —, NICHT die
Live-Sampling-Intervention selbst (Fix Punkt 2). Unter der (bewusst NICHT als Default gesetzten)
Policy ``'freeze'`` FAILt severity='high' fuer jeden Kandidaten, der nicht tatsaechlich eingefroren
wurde (Fix Punkt 3) — da die Intervention fehlt, ist das JEDER Kandidat: kein stiller No-Op unter
einer explizit gewaehlten Policy, sondern ein sichtbarer, ehrlicher Befund.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv


def _study_record(strategy, symbol, *, fraction, n_trials=100, frozen_dimensions=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_floor_binding_trial_fraction": fraction,
        "n_trials_completed": n_trials,
        "frozen_dimensions": frozen_dimensions,
    }


# ---------------------------------------------------------------------------------------------
# policy validation
# ---------------------------------------------------------------------------------------------

def test_unknown_policy_fails_loud():
    with pytest.raises(ValueError, match="atr_floor_dimension_freeze_policy"):
        inv.check_atr_floor_dimension_freeze_candidates([], policy="bogus")


def test_default_policy_is_diagnose_bit_identical_to_pre_1295():
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.768)]
    r = inv.check_atr_floor_dimension_freeze_candidates(records)
    assert r.passed is False
    assert r.severity == "medium"


# ---------------------------------------------------------------------------------------------
# policy='freeze'
# ---------------------------------------------------------------------------------------------

def test_freeze_policy_fails_high_for_a_candidate_since_the_intervention_is_unimplemented():
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.9837)]
    r = inv.check_atr_floor_dimension_freeze_candidates(records, policy="freeze")
    assert r.passed is False
    assert r.severity == "high"
    assert "AdxAtrStrategy/TSLA.ETORO" in r.actual


def test_freeze_policy_passes_if_no_candidates_qualify():
    records = [_study_record("S", "X.ETORO", fraction=0.30)]
    r = inv.check_atr_floor_dimension_freeze_candidates(records, policy="freeze")
    assert r.passed is True


def test_freeze_policy_would_pass_if_frozen_dimensions_were_actually_populated():
    """Zukunftssicherung: sobald eine Live-Intervention frozen_dimensions tatsaechlich befuellt,
    PASSt der Check unter 'freeze' fuer diesen Kandidaten -- die Pruefung selbst ist bereits
    korrekt spezifiziert, nur die Quelle fehlt noch."""
    records = [_study_record("AdxAtrStrategy", "TSLA.ETORO", fraction=0.9837,
                             frozen_dimensions=["atr_trailing_multiplier"])]
    r = inv.check_atr_floor_dimension_freeze_candidates(records, policy="freeze")
    assert r.passed is True


# ---------------------------------------------------------------------------------------------
# config schema
# ---------------------------------------------------------------------------------------------

def test_production_config_default_policy_is_diagnose_not_freeze():
    """Bewusst NICHT 'freeze' (Issue-Text-Ziel-Default): 'freeze' waere ohne die Live-Intervention
    ein stiller No-Op-Anspruch. 'diagnose' ist der ehrliche, unveraenderte Default."""
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("atr_floor_dimension_freeze_policy") == "diagnose"


def test_production_config_passes_under_its_own_default_policy():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    r = inv.check_atr_floor_dimension_freeze_candidates(
        [], policy=cfg.get("atr_floor_dimension_freeze_policy", "diagnose"))
    assert r.passed is True


# ---------------------------------------------------------------------------------------------
# report.py wiring
# ---------------------------------------------------------------------------------------------

def test_wired_with_policy_from_config():
    import inspect
    from automation.optimizer import report as rpt
    source = inspect.getsource(rpt._build_report)
    assert "atr_floor_dimension_freeze_policy" in source
