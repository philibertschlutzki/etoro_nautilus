"""Issue #1313 (GH #1190, P2) — Abbruchereignis mit leerer Offender-Menge nennt seinen Grund nicht.

Symptom. ``SWEEP_ABORTED_ON_FAIL_FAST_INVARIANT`` trägt ``offending_studies: {}`` und
``offending_symbols: []`` (B-10) — das Ereignis behauptet einen Abbruch, ohne einen Verursacher zu
nennen.

Root-Cause. ``check_bar_quality`` ist global-skopiert und kann die Pair-Konvention nicht erfüllen;
``sweep._offending_pairs_for_fail_fast_check`` fällt auf den Konservativ-Zweig zurück, der aber
nichts stempelt.

Fix. Das Ereignis erhält ein Pflichtfeld ``abort_scope`` mit den Werten ``pairs`` (Offender
genannt) oder ``global`` (mit ``global_reason`` = ``detail`` des auslösenden Checks). Ein Abbruch
ohne beides ist ein ``AssertionError`` im Emissionspfad (``sweep._fail_fast_abort_scope_and_
reason``, aufgerufen unmittelbar vor der ``SWEEP_ABORTED_ON_FAIL_FAST_INVARIANT``-Emission).
"""
import inspect

from automation.optimizer import sweep


# ── sweep._fail_fast_abort_scope_and_reason — reine Entscheidungsfunktion ───────────────────────

def test_pairs_scope_when_offending_pairs_is_non_empty():
    scope, reason = sweep._fail_fast_abort_scope_and_reason(
        [{"name": "check_holding_time_cap", "detail": "irrelevant hier"}],
        "check_holding_time_cap",
        {"S/X.ETORO": 3.7},
    )
    assert scope == "pairs"
    assert reason is None


def test_global_scope_with_the_triggering_checks_detail_as_reason():
    """Der Referenzfall aus dem Symptom: check_bar_quality ist global-skopiert, offending_pairs
    ist leer — global_reason ist das detail DIESES Checks, nicht irgendein generischer Text."""
    checks = [
        {"name": "check_holding_time_cap", "detail": "OK"},
        {"name": "check_bar_quality", "detail": "frac_zero_true_range=1.000 > 0.25 — Bar-Achse "
                                                 "traegt keine Intrabar-Information."},
    ]
    scope, reason = sweep._fail_fast_abort_scope_and_reason(checks, "check_bar_quality", {})
    assert scope == "global"
    assert reason == "frac_zero_true_range=1.000 > 0.25 — Bar-Achse traegt keine Intrabar-Information."


def test_global_scope_falls_back_to_a_non_empty_generic_reason_when_detail_is_missing():
    """Selbst wenn der ausloesende Check (aus irgendeinem Grund) kein detail traegt oder gar nicht
    im Strom auftaucht, bleibt global_reason NIE leer — das ist genau die urspruengliche
    Symptom-Signatur (Grund fehlt), die dieser Fix ausschliessen soll."""
    scope, reason = sweep._fail_fast_abort_scope_and_reason([], "check_bar_quality", {})
    assert scope == "global"
    assert reason
    assert "check_bar_quality" in reason

    scope2, reason2 = sweep._fail_fast_abort_scope_and_reason(
        [{"name": "check_bar_quality", "detail": ""}], "check_bar_quality", {})
    assert scope2 == "global"
    assert reason2


def test_global_scope_ignores_other_checks_details():
    """Der Grund muss vom AUSLOESENDEN Check stammen, nicht vom ersten/letzten Eintrag im Strom."""
    checks = [
        {"name": "check_a", "detail": "unrelated A"},
        {"name": "check_bar_quality", "detail": "the real reason"},
        {"name": "check_c", "detail": "unrelated C"},
    ]
    scope, reason = sweep._fail_fast_abort_scope_and_reason(checks, "check_bar_quality", {})
    assert reason == "the real reason"


# ── Verdrahtung an der Emissionsstelle (Textsicherung, siehe test_issue_1269-Konvention) ────────

def test_emission_call_site_computes_and_asserts_both_fields():
    src = inspect.getsource(sweep.run_per_symbol_sweep)
    assert "_fail_fast_abort_scope_and_reason(" in src
    idx = src.index("_fail_fast_abort_scope_and_reason(")
    window = src[idx:idx + 1050]
    assert 'assert _abort_scope in ("pairs", "global")' in window
    assert "assert _abort_scope != \"global\" or _global_reason" in window
    assert '"abort_scope": _abort_scope' in window
    assert '"global_reason": _global_reason' in window
