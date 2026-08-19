"""Issue #1033/#1182 (P1) — ``check_gate_marginal_contribution`` erhebt ``null`` zu ``0.0``.

Root-Cause: eine Study-Zeile mit ``marginal_delta=None`` (das Gate wurde in dieser Study nie
ausgewertet, ``n_rejections == 0``) wurde über ``float(entry.get('marginal_delta') or 0)`` als
GEMESSENE ``0.0`` behandelt, und ihr ``n_evaluated`` floss trotzdem in den Nenner — "nicht
gemessen" wurde so zu "gemessen und null" (dieselbe Fehlerklasse wie #995/#1147, hier auf der
Aggregationsebene).
"""
from automation.optimizer import invariants as inv


def test_all_none_marginal_delta_is_inconclusive_not_zero_contribution():
    study_records = [
        {"gate_inventory": [
            {"gate": "min_trades", "n_rejections": 0, "n_solo_rejections": 0,
             "marginal_delta": None, "n_evaluated": 600},
        ]},
    ]
    result = inv.check_gate_marginal_contribution(study_records, min_evaluated=500)
    assert result.passed is True  # kein "offender" -- es ist INCONCLUSIVE, keine 0-Beitrag-Messung.
    assert result.actual is None  # nicht in offenders
    assert "min_trades" in result.provenance["inconclusive_gates"]
    assert "min_trades" in result.detail
    assert "INCONCLUSIVE" in result.detail


def test_mixed_none_and_zero_entries_excludes_none_from_both_numerator_and_denominator():
    """Zwei Studies: eine misst marginal_delta=0.0 (echt gemessen, 600 Beobachtungen), eine hat
    None (nie gemessen, 10000 Beobachtungen). Der None-Eintrag darf weder den Zaehler noch den
    Nenner beeinflussen -- die Gate-Bewertung basiert ausschliesslich auf der echten Messung."""
    study_records = [
        {"gate_inventory": [
            {"gate": "oos_min_psr", "marginal_delta": 0.0, "n_evaluated": 600},
        ]},
        {"gate_inventory": [
            {"gate": "oos_min_psr", "marginal_delta": None, "n_evaluated": 10000},
        ]},
    ]
    result = inv.check_gate_marginal_contribution(study_records, min_evaluated=500)
    assert result.passed is False
    assert result.actual["oos_min_psr"]["n_evaluated"] == 600  # NICHT 10600
    assert "oos_min_psr" not in (result.provenance or {}).get("inconclusive_gates", {})


def test_genuine_zero_contribution_over_enough_measured_observations_still_fails():
    study_records = [
        {"gate_inventory": [
            {"gate": "oos_min_trades", "marginal_delta": 0.0, "n_evaluated": 600},
        ]},
    ]
    result = inv.check_gate_marginal_contribution(study_records, min_evaluated=500)
    assert result.passed is False
    assert "oos_min_trades" in result.actual


def test_provenance_makes_n_evaluated_reproducible_from_entry_counts():
    study_records = [
        {"gate_inventory": [{"gate": "min_trades", "marginal_delta": None, "n_evaluated": 100}]},
        {"gate_inventory": [{"gate": "min_trades", "marginal_delta": None, "n_evaluated": 100}]},
    ]
    result = inv.check_gate_marginal_contribution(study_records, min_evaluated=500)
    prov = result.provenance["inconclusive_gates"]["min_trades"]
    assert prov["n_entries_total"] == 2
    assert prov["n_entries_measured"] == 0
    assert prov["n_evaluated_measured"] == 0
