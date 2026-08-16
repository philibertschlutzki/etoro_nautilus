"""Issue #941/#1107 (Katalog #960) — Fail-Fast-Pfad und Report-Pfad derselben Invariante laufen auf
zwei Populationen.

Root-Cause: ``check_effective_stop_distance`` lieferte im selben Lauf zwei Ergebnisse: die
Abbruch-Nutzlast (In-Process, nur das eigene Symbol) nannte 12/13/13 Offender, das Report-Artefakt
(aus ``WORK`` eingesammelte ``proposal_*.json``) nannte 25/38/38 Offender ueber alle drei Symbole
(B-2) — zwei Grundgesamtheiten, ein Name, keine Deklaration der Kohorte im Artefakt.

Fix: ``InvariantResult`` traegt ein ``cohort``-Feld (``{run_id, n_studies, symbols, source}``);
``report._build_report`` stempelt es auf JEDEN Eintrag von ``invariant_checks``, der noch keins
traegt (``build_cohort_descriptor``). Eine Nachlauf-Pruefung
(``check_cohort_declaration_consistency``) vergleicht die Kohorten-Deklarationen identischer
Check-Namen zwischen einer frueheren In-Process-Probe und dem finalen Report DESSELBEN Laufs.

Deckt ab:
  - ``build_cohort_descriptor`` liefert ``source='in_process'`` fuer jeden ``report_source`` ausser
    ``'final'`` und ``source='report_scan'`` fuer ``'final'``.
  - Akzeptanzkriterium #941: jeder ``invariant_checks``-Eintrag traegt ``cohort``.
  - Ein synthetischer Lauf mit divergierenden Kohorten (ein Symbol, das die Probe bereits kannte,
    fehlt im finalen Report) FAILt ueber ``check_cohort_declaration_consistency``.
  - Auf einem sauberen Einzellauf (Probe-Kohorte ⊆ finale Kohorte) sind beide Deklarationen
    konsistent -- PASS.
"""
from automation.optimizer import invariants as inv


def test_build_cohort_descriptor_source_mapping():
    records = [{"symbol": "TSLA.ETORO"}, {"symbol": "NVDA.ETORO"}, {"symbol": "TSLA.ETORO"}]
    probe = inv.build_cohort_descriptor(records, run_id="run_a", report_source="fail_fast_probe")
    assert probe == {
        "run_id": "run_a", "n_studies": 3, "symbols": ["NVDA.ETORO", "TSLA.ETORO"],
        "source": "in_process",
    }
    final = inv.build_cohort_descriptor(records, run_id="run_a", report_source="final")
    assert final["source"] == "report_scan"


def test_declaration_consistency_not_applicable_without_prior_probe():
    result = inv.check_cohort_declaration_consistency(
        [{"name": "check_effective_stop_distance", "cohort": {"run_id": "a"}}],
        prior_probe_checks=None,
    )
    assert result.passed is True


def test_declaration_consistency_passes_when_final_is_a_superset():
    """Der Normalfall: die Probe sah 1 Symbol (fruehe Auswertung), der finale Report 3 (der Lauf
    ist inzwischen weitergelaufen) -- das ist erwartete Progression, keine Divergenz."""
    probe_checks = [{
        "name": "check_effective_stop_distance",
        "cohort": {"run_id": "run_a", "n_studies": 5, "symbols": ["TSLA.ETORO"], "source": "in_process"},
    }]
    final_checks = [{
        "name": "check_effective_stop_distance",
        "cohort": {"run_id": "run_a", "n_studies": 14,
                   "symbols": ["NVDA.ETORO", "PLTR.ETORO", "TSLA.ETORO"], "source": "report_scan"},
    }]
    result = inv.check_cohort_declaration_consistency(final_checks, prior_probe_checks=probe_checks)
    assert result.passed is True


def test_declaration_consistency_fails_when_final_loses_a_symbol_the_probe_already_saw():
    """Akzeptanzkriterium #941 (B-2-Analog): die Probe kannte TSLA.ETORO bereits -- ein finaler
    Report DESSELBEN Laufs, der TSLA.ETORO nicht mehr enthaelt, ist ein struktureller Widerspruch."""
    probe_checks = [{
        "name": "check_effective_stop_distance",
        "cohort": {"run_id": "run_a", "n_studies": 13, "symbols": ["TSLA.ETORO"], "source": "in_process"},
    }]
    final_checks = [{
        "name": "check_effective_stop_distance",
        "cohort": {"run_id": "run_a", "n_studies": 12, "symbols": ["PLTR.ETORO"], "source": "report_scan"},
    }]
    result = inv.check_cohort_declaration_consistency(final_checks, prior_probe_checks=probe_checks)
    assert result.passed is False
    assert result.severity == "blocking"
    assert "check_effective_stop_distance" in result.actual["violations"][0]


def test_declaration_consistency_fails_when_n_studies_shrinks():
    probe_checks = [{
        "name": "check_holding_time_cap",
        "cohort": {"run_id": "run_a", "n_studies": 20, "symbols": ["TSLA.ETORO"], "source": "in_process"},
    }]
    final_checks = [{
        "name": "check_holding_time_cap",
        "cohort": {"run_id": "run_a", "n_studies": 5, "symbols": ["TSLA.ETORO"], "source": "report_scan"},
    }]
    result = inv.check_cohort_declaration_consistency(final_checks, prior_probe_checks=probe_checks)
    assert result.passed is False


def test_declaration_consistency_fails_on_run_id_mismatch():
    probe_checks = [{
        "name": "check_holding_time_cap",
        "cohort": {"run_id": "run_a", "n_studies": 5, "symbols": ["TSLA.ETORO"], "source": "in_process"},
    }]
    final_checks = [{
        "name": "check_holding_time_cap",
        "cohort": {"run_id": "run_b", "n_studies": 5, "symbols": ["TSLA.ETORO"], "source": "report_scan"},
    }]
    result = inv.check_cohort_declaration_consistency(final_checks, prior_probe_checks=probe_checks)
    assert result.passed is False


def test_declaration_consistency_ignores_check_names_only_in_one_wave():
    """Ein Check, der nur in der Probe ODER nur im finalen Report auftaucht (z. B. eine Meta-
    Invariante, die erst nach allen anderen Checks ausgewertet wird), wird nicht verglichen."""
    probe_checks = [{"name": "only_in_probe", "cohort": {"run_id": "run_a", "n_studies": 1,
                                                          "symbols": ["TSLA.ETORO"], "source": "in_process"}}]
    final_checks = [{"name": "only_in_final", "cohort": {"run_id": "run_a", "n_studies": 3,
                                                          "symbols": ["TSLA.ETORO"], "source": "report_scan"}}]
    result = inv.check_cohort_declaration_consistency(final_checks, prior_probe_checks=probe_checks)
    assert result.passed is True
    assert result.actual["n_check_names_compared"] == 0
