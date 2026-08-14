"""Issue #1088 (Katalog #921) — ``assert_invariant_scope_uncontaminated`` als zusaetzliche
Sicherung GEGEN dieselbe Fehlerklasse wie #1086: die Invarianten-Suite darf niemals auf
``study_records`` urteilen, die aus mehr als einem Lauf (``run_id``) stammen.
"""
import pytest

from automation.optimizer import invariants as inv


def test_single_run_id_passes():
    records = [
        {"strategy": "A", "symbol": "TSLA.ETORO", "run_id": "run_a"},
        {"strategy": "B", "symbol": "TSLA.ETORO", "run_id": "run_a"},
    ]
    inv.assert_invariant_scope_uncontaminated(records)  # keine Exception


def test_missing_run_id_is_fail_open_not_contamination():
    """Legacy-Studies ohne run_id-Nachweis (#1086-Zeitfenster-Fallback) sind kein Kontaminations-
    Signal — fehlende Evidenz != widerspruechliche Evidenz."""
    records = [
        {"strategy": "A", "symbol": "TSLA.ETORO", "run_id": None},
        {"strategy": "B", "symbol": "TSLA.ETORO"},
        {"strategy": "C", "symbol": "TSLA.ETORO", "run_id": "run_a"},
    ]
    inv.assert_invariant_scope_uncontaminated(records)  # keine Exception


def test_two_distinct_run_ids_raise_scope_contaminated():
    records = [
        {"strategy": "Combo", "symbol": "ASML.ETORO", "run_id": "run_nvda"},
        {"strategy": "DynBreakout", "symbol": "TSLA.ETORO", "run_id": "run_nvda"},
        {"strategy": "AdxAtrMomentum", "symbol": "NVDA.ETORO", "run_id": "run_own"},
    ]
    with pytest.raises(RuntimeError, match=r"INVARIANT_SCOPE_CONTAMINATED"):
        inv.assert_invariant_scope_uncontaminated(records)


def test_empty_records_pass():
    inv.assert_invariant_scope_uncontaminated([])
