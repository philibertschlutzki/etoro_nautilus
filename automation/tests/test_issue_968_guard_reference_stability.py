"""Issue #968 (Katalog A, P0 HEADLINE) — der Sortino-Numerik-Guard ist geschlossen rekonstruiert
(``guard(n) = min(25, 25·sqrt(n_periods/n_ref))``, bitgenau über 42/42 SORTINO_GUARD_TRIPPED-
Ereignisse), aber seine Referenz ``n_ref`` wandert INNERHALB EINES LAUFS zwischen acht verschiedenen
Werten und wechselt sogar MITTEN in einer Study die Quelle (``absolute_bootstrap`` →
``family_median``) — derselbe Parametervektor kann dadurch, abhängig allein von seiner
Ankunftsreihenfolge im Scheduler, ein umgekehrtes Guard-Urteil erhalten. Der Lauf ist trotz festem
Seed nicht bitweise reproduzierbar.

``invariants.check_guard_reference_stability`` ist der Regressionswächter: für jede Study müssen
``guard_reference_value`` und ``guard_reference_source`` über alle getrippten/unbewertbaren Trials
KONSTANT bleiben.
"""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, *, values, sources):
    return {
        "strategy": strategy, "symbol": symbol,
        "guard_reference_values": values, "guard_reference_sources": sources,
    }


def test_passes_when_reference_is_constant_across_the_study():
    result = inv.check_guard_reference_stability([
        _study("MeanReversion", "GSAT.ETORO", values=[320.0, 320.0, 320.0],
               sources=["absolute_bootstrap", "absolute_bootstrap", "absolute_bootstrap"]),
    ])
    assert result.passed is True


def test_fails_when_value_drifts_within_a_study():
    """Reproduziert exakt den #968-Befund: FlashCrashReversal/GSAT wechselt von 320,0
    (absolute_bootstrap) zu 189,5/193,0/194,5 (family_median) mitten in der Study."""
    result = inv.check_guard_reference_stability([
        _study("FlashCrashReversal", "GSAT.ETORO",
               values=[320.0, 320.0, 189.5, 193.0, 194.5],
               sources=["absolute_bootstrap", "absolute_bootstrap", "family_median",
                        "family_median", "family_median"]),
    ])
    assert result.passed is False
    assert result.severity == "blocking"
    offender = result.actual["FlashCrashReversal/GSAT.ETORO"]
    assert offender["guard_reference_values"] == [189.5, 193.0, 194.5, 320.0]
    assert offender["guard_reference_sources"] == ["absolute_bootstrap", "family_median"]


def test_fails_when_only_the_source_switches_but_value_happens_to_match():
    result = inv.check_guard_reference_stability([
        _study("X", "Y", values=[320.0, 320.0], sources=["absolute_bootstrap", "family_median"]),
    ])
    assert result.passed is False


def test_multiple_studies_only_offending_ones_are_reported():
    result = inv.check_guard_reference_stability([
        _study("A", "S1", values=[320.0], sources=["absolute_bootstrap"]),
        _study("B", "S1", values=[320.0, 216.0], sources=["absolute_bootstrap", "family_median"]),
    ])
    assert result.passed is False
    assert list(result.actual.keys()) == ["B/S1"]


def test_noop_without_any_guard_telemetry():
    result = inv.check_guard_reference_stability([{"strategy": "A", "symbol": "S1"}])
    assert result.passed is True
    assert result.actual is None
