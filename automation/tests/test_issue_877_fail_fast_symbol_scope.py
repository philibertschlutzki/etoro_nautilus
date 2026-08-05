"""Issue #877 (P0) — Der Fail-Fast eskaliert eine symbol-lokale Anomalie global.

`check_holding_time_cap` (scope='global', severity='blocking') FAILt, sobald EINE Study die
Zeitbox-Toleranz reisst, und `sweep.py` brach den gesamten Sweep sofort ab (`break`) — auch wenn
die Verletzungen auf einem einzigen Symbol konzentriert waren, während alle übrigen Symbole sauber
blieben (Pitfall #282: die Streuung der Evidenz muss die Reichweite der Konsequenz bestimmen).

`sweep._offending_pairs_for_fail_fast_check` ist die reine Entscheidungsfunktion, die aus einem
FAILenden Check dessen (Strategie, Symbol)-Offender extrahiert (geparst aus der
`{"<strategy>/<symbol>": wert}`-``actual``-Konvention, die `check_holding_time_cap` verwendet) —
der Aufrufer in `run_per_symbol_sweep` bricht nur noch global ab, wenn die Offender über
`>= fail_fast_min_offending_symbols` verschiedene Symbole streuen; sonst werden nur die
betroffenen Paare quarantänisiert (`record_diagnosed_pair`, `action='denylist'`) und der Sweep
läuft weiter.
"""
from automation.optimizer import sweep


def _check(name, actual, passed=False):
    return {"name": name, "passed": passed, "actual": actual}


def test_single_symbol_offender_is_not_global_evidence():
    checks = [_check("check_holding_time_cap", {
        "Rsi2ReversionStrategy/VLO.ETORO": 0.52,
        "ComboTrendVwapStrategy/VLO.ETORO": 0.38,
    })]
    pairs, symbols = sweep._offending_pairs_for_fail_fast_check(checks, "check_holding_time_cap")
    assert symbols == {"VLO.ETORO"}
    assert pairs == {
        ("Rsi2ReversionStrategy", "VLO.ETORO"): 0.52,
        ("ComboTrendVwapStrategy", "VLO.ETORO"): 0.38,
    }


def test_offenders_spread_across_multiple_symbols():
    checks = [_check("check_holding_time_cap", {
        "Rsi2ReversionStrategy/VLO.ETORO": 0.52,
        "Rsi2ReversionStrategy/NKE.ETORO": 0.30,
    })]
    pairs, symbols = sweep._offending_pairs_for_fail_fast_check(checks, "check_holding_time_cap")
    assert symbols == {"VLO.ETORO", "NKE.ETORO"}
    assert len(pairs) == 2


def test_unparseable_actual_conservatively_reports_no_offenders():
    """Ein global-scope-Check (z. B. check_guard_reference_coherence) traegt keine
    "<strategy>/<symbol>"-actual-Konvention — der Aufrufer muss das als 'Streuung nicht
    ermittelbar' behandeln und konservativ (Pre-#877-Verhalten) abbrechen, statt eine unbekannte
    Struktur stillschweigend als quarantänisierbar zu werten."""
    checks = [_check("check_guard_reference_coherence", 5.0955)]
    pairs, symbols = sweep._offending_pairs_for_fail_fast_check(
        checks, "check_guard_reference_coherence")
    assert pairs == {}
    assert symbols == set()


def test_missing_check_name_returns_empty():
    pairs, symbols = sweep._offending_pairs_for_fail_fast_check([], "check_holding_time_cap")
    assert pairs == {}
    assert symbols == set()


def test_first_failing_fail_fast_invariant_ignores_passing_checks():
    checks = [
        _check("check_holding_time_cap", None, passed=True),
        _check("check_guard_reference_coherence", 5.0, passed=False),
    ]
    assert sweep._first_failing_fail_fast_invariant(
        checks, ["check_holding_time_cap", "check_guard_reference_coherence"]
    ) == "check_guard_reference_coherence"
