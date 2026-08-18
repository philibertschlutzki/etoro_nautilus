"""Issue #1009/#1161 (Katalog #1170, P3) — ``SWEEP_ABORTED`` bei erfolgreichem Lauf.

Symptom: beide Laeufe emittieren ``SWEEP_ABORTED`` mit ``run_status = 'complete_with_blocking_
invariants'``, ``failed_symbols = []``, ``symbols_completed = symbols_planned = 1`` — ein Lauf, der
tatsaechlich vollstaendig durchlief, sah im Log wie ein Abbruch aus.

Root-Cause: die alte Bedingung ("jeder ``run_status`` ausser dem exakten String ``'complete'`` ist
``SWEEP_ABORTED``") behandelte JEDEN Nicht-``'complete'``-Ausgang als Abbruch — auch
``completed_with_quarantine``/``completed_with_failures``/``resumed_complete``/``completed_
invalid``/``complete_with_blocking_invariants`` (allesamt Laeufe, die ALLE geplanten Symbole
abgeschlossen haben, nur mit einer Randnotiz im ``run_status``).

Fix: ``sweep._sweep_completion_event(run_status)`` unterscheidet jetzt DREI Faelle:
``'complete'`` -> ``SWEEP_COMPLETED``; jeder ``run_status``, der mit ``'aborted_'`` beginnt (die
volle Menge: invariant/wallclock/disk/signal/error) -> ``SWEEP_ABORTED`` (WARNING, unveraendert);
jeder andere Ausgang -> das NEUE ``SWEEP_FINISHED`` (INFO), mit einem ``event_type_alias``-Feld
als Uebergangs-Alias (Konvention #1081) fuer Log-Parser, die noch nach dem String "SWEEP_ABORTED"
irgendwo in der JSON-Zeile suchen.
"""
import logging

from automation.optimizer import sweep


# --- sweep._sweep_completion_event: die reine Entscheidungsfunktion -------------------------------

def test_reference_symptom_complete_with_blocking_invariants_is_no_longer_aborted():
    """Das exakte #1161-Symptom: ein Lauf, der alle Symbole abschloss, aber mindestens eine
    blockierende Invariante FAILte (siehe sweep._downgrade_run_status_for_blocking_invariants)."""
    event_type, level = sweep._sweep_completion_event("complete_with_blocking_invariants")
    assert event_type == "SWEEP_FINISHED"
    assert event_type != "SWEEP_ABORTED"
    assert level == logging.INFO


def test_clean_completion_stays_sweep_completed():
    assert sweep._sweep_completion_event("complete") == ("SWEEP_COMPLETED", logging.INFO)


def test_every_genuine_abort_status_still_emits_sweep_aborted_at_warning():
    """Regressionswaechter: die #1161-Praezisierung darf bestehende Ressourcen-Guard-Alarme nicht
    stillschweigend abschalten -- ALLE fuenf bekannten aborted_*-Ausgaenge bleiben SWEEP_ABORTED."""
    for status in ("aborted_invariant", "aborted_wallclock", "aborted_disk",
                   "aborted_signal", "aborted_error"):
        assert sweep._sweep_completion_event(status) == ("SWEEP_ABORTED", logging.WARNING)


def test_the_other_non_complete_non_aborted_outcomes_are_sweep_finished_not_aborted():
    for status in ("completed_with_quarantine", "completed_with_failures",
                   "resumed_complete", "completed_invalid"):
        event_type, level = sweep._sweep_completion_event(status)
        assert event_type == "SWEEP_FINISHED", status
        assert level == logging.INFO, status


def test_a_future_aborted_prefixed_status_is_covered_without_a_code_change():
    """Der Praefix-Test (statt einer festen 2-elementigen Menge, wie im Issue-Text illustrativ
    genannt) deckt jeden KUENFTIGEN aborted_*-Grund automatisch ab."""
    assert sweep._sweep_completion_event("aborted_some_future_guard") == (
        "SWEEP_ABORTED", logging.WARNING)


# --- Verdrahtung: main() nutzt die neue Entscheidungsfunktion, inkl. Uebergangs-Alias --------------

def test_main_is_wired_to_the_new_decision_function_with_a_transition_alias():
    """Regressionswaechter (Quelltext-Ebene, analog #984/#1138-Konvention): main() ruft
    _sweep_completion_event auf UND setzt den Uebergangs-Alias fuer den SWEEP_FINISHED-Zweig."""
    import inspect
    source = inspect.getsource(sweep.main)
    assert "_sweep_completion_event(run_status)" in source
    assert '"event_type_alias"] = "SWEEP_ABORTED"' in source
