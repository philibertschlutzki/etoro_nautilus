"""Issue #943/#1109 (Katalog #960) — ``check_champion_corroboration_reachable``: Abnahmemessung des
#1089-Fixes.

Symptom (B-5): Report A FAILt ehrlich (``total_runs_started=1``), Reports B/C bestehen FAELSCHLICH
(``total_runs_started=2``, durch Nebenprozesse hochgezaehlt) bei identischem
``max_corroboration_count=1`` — derselbe Faktenstand (die Korroboration ist auf der EIGENEN Kohorte
struktuell noch unerreichbar), aber ein Nebenprozess-Zaehler entschied ueber PASS/FAIL. Der Zaehler
selbst ist zusaetzlich rennbehaftet (2 bei DREI gestarteten Laeufen).

Der #1089-Fix (bereits im HEAD, siehe AGENTS.md Pitfall #384) entfernte den ODER-Ast
(``max_corroboration_count >= threshold ODER total_runs_started > 1``) ERSATZLOS — dieses Issue
fuehrt AUSSCHLIESSLICH die Abnahme: nach dem Fix muss der Check auf ALLEN DREI B-5-Konstellationen
FAILen, nicht nur auf einer.

Zusaetzlich: ``check_coverage_ledger_continuity``s Bootstrap-Phase-Zweig trug im Detailtext eine
hart kodierte "1" ("total_runs_started==1 ist hier der erwartete Wert"), waehrend ``actual`` in den
realen Reports B/C ``2`` meldete — ein Widerspruch zwischen Text und Messwert im selben Ergebnis.
"""
from automation.optimizer import invariants as inv


# ── check_champion_corroboration_reachable: total_runs_started hat KEINEN Einfluss mehr ─────────

def test_fails_symmetrically_regardless_of_total_runs_started():
    """Akzeptanzkriterium #943: identisches max_corroboration_count=1 (< threshold=2) FAILt fuer
    ALLE drei B-5-Konstellationen (total_runs_started 1, 2, 2), nicht nur fuer die erste."""
    champions_summary = {"stored": 1, "max_corroboration_count": 1}
    results = [
        inv.check_champion_corroboration_reachable(
            champions_summary, total_runs_started=total_runs_started, corroboration_threshold=2)
        for total_runs_started in (1, 2, 2)
    ]
    assert all(r.passed is False for r in results), (
        f"Erwartet: alle drei FAILen unabhaengig von total_runs_started, erhalten: "
        f"{[(r.passed, r.provenance) for r in results]}"
    )
    # total_runs_started bleibt reine Diagnose-Provenienz, nie ein Entscheidungsfaktor.
    assert [r.provenance["total_runs_started"] for r in results] == [1, 2, 2]


def test_passes_when_max_corroboration_count_actually_reaches_threshold():
    champions_summary = {"stored": 1, "max_corroboration_count": 2}
    result = inv.check_champion_corroboration_reachable(
        champions_summary, total_runs_started=1, corroboration_threshold=2)
    assert result.passed is True


def test_not_applicable_without_a_champion_store_entry():
    result = inv.check_champion_corroboration_reachable({"stored": 0}, total_runs_started=5)
    assert result.passed is True
    assert result.actual is None


# ── check_coverage_ledger_continuity: Detailtext aus dem Messwert, nicht hart kodiert ────────────

def test_bootstrap_phase_detail_text_reflects_the_actual_measured_value():
    """Reproduziert B-5 Reports B/C: total_runs_started=2 waehrend der Bootstrap-Phase -- der Text
    darf NICHT "==1" behaupten."""
    result = inv.check_coverage_ledger_continuity(2, True, coverage_bootstrap_phase=True)
    assert result.passed is True
    assert result.actual == 2
    assert "==1" not in result.detail
    assert "==2" in result.detail


def test_bootstrap_phase_detail_text_with_the_literal_value_one():
    result = inv.check_coverage_ledger_continuity(1, False, coverage_bootstrap_phase=True)
    assert result.actual == 1
    assert "==1" in result.detail
