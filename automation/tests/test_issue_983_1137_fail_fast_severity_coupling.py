"""Issue #983/#1137 (Katalog #986, Pitfall #410 in AGENTS.md) — ``severity`` und
``fail_fast_invariants`` waren zwei entkoppelte Taxonomien: ``check_effective_stop_distance`` stand
in ``optimizer.json['fail_fast_invariants']``, trug aber ``severity='high'`` statt ``'blocking'``.

Fix: ``check_effective_stop_distance`` ist seither ``severity='blocking'``; neue
``check_fail_fast_invariants_are_blocking`` ist der dauerhafte Regressionswächter gegen ein
erneutes Auseinanderlaufen.
"""
from automation.optimizer import invariants as inv


def test_check_effective_stop_distance_is_now_blocking_severity():
    """Akzeptanzkriterium: die reale Funktion, nicht nur die neue Meta-Pruefung."""
    result = inv.check_effective_stop_distance([])
    assert result.severity == "blocking"


def test_fail_fast_invariants_are_blocking_passes_when_all_configured_are_blocking():
    checks = [
        {"name": "check_holding_time_cap", "severity": "blocking"},
        {"name": "check_guard_reference_coherence", "severity": "blocking"},
    ]
    result = inv.check_fail_fast_invariants_are_blocking(
        checks, fail_fast_invariants=["check_holding_time_cap", "check_guard_reference_coherence"])
    assert result.passed is True
    assert result.actual is None


def test_fail_fast_invariants_are_blocking_fails_on_a_non_blocking_configured_check():
    checks = [
        {"name": "check_holding_time_cap", "severity": "blocking"},
        {"name": "check_effective_stop_distance", "severity": "high"},
    ]
    result = inv.check_fail_fast_invariants_are_blocking(
        checks, fail_fast_invariants=["check_holding_time_cap", "check_effective_stop_distance"])
    assert result.passed is False
    assert result.actual == {"check_effective_stop_distance": "high"}
    assert result.severity == "blocking"


def test_fail_fast_invariants_are_blocking_not_applicable_without_config():
    result = inv.check_fail_fast_invariants_are_blocking([], fail_fast_invariants=None)
    assert result.passed is True


def test_fail_fast_invariants_are_blocking_ignores_unevaluated_names():
    """Ein konfigurierter Name ohne Ergebnis in diesem Lauf ist check_fail_fast_invariants_wired's
    Zustaendigkeit, keine doppelte Meldung hier."""
    result = inv.check_fail_fast_invariants_are_blocking(
        [], fail_fast_invariants=["check_never_evaluated"])
    assert result.passed is True
