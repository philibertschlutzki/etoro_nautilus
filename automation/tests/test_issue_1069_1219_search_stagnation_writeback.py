"""Issue #1069/#1219 (P2, Katalog #1196-1221) — Suchstagnation wird gemeldet, aber nicht
zurückgeschrieben.

Symptom: ``invariants.check_search_made_progress``-FAILs und ``check_structural_zero_eligible_
has_diagnosis``-FAILs (#1045/#1194) erscheinen Lauf für Lauf im Report, ohne je den #681/#761-
Diagnose-Rückschrieb (``sweep_diagnostics.recommend_diagnosis_action``/``record_diagnosed_pair``)
zu erreichen — dieselben stagnierenden (Strategie, Symbol)-Paare werden bei JEDEM Lauf neu
enumeriert, ohne dass der wiederholte Befund je eine Konsequenz hat ("Diagnose ohne Konsequenz").

Fix: ``sweep_diagnostics.recommend_diagnosis_action`` erhält einen neuen ``binding_cause``-Zweig
(``'search_stagnation'``); ``report._writeback_search_stagnation_diagnoses`` vereinigt die FAIL-
Mengen beider Checks zu (Strategie, Symbol)-Paaren und schreibt sie über die bestehende Pipeline
zurück.

Akzeptanzkriterien (aus dem Issue): nach zwei aufeinanderfolgenden Läufen mit demselben Befund für
dasselbe Paar automatisch ``'deprioritized'`` (halbes Trial-Budget), nach vier ``'denylist'``.
"""
import json

import pytest

from automation.optimizer import sweep_diagnostics
from automation.optimizer.sweep_diagnostics import (
    recommend_diagnosis_action, record_diagnosed_pair, load_diagnosed_pairs_cache,
)
from automation.optimizer.report import _writeback_search_stagnation_diagnoses


@pytest.fixture(autouse=True)
def _enable_diagnostic_writeback(monkeypatch):
    monkeypatch.setattr(sweep_diagnostics, "_read_diagnostic_writeback_enabled", lambda: True)


# --- recommend_diagnosis_action: der neue 'search_stagnation'-Zweig -----------------------------

def test_zero_confirmations_is_none():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "search_stagnation"},
        n_runs_confirmed=0)
    assert rec["action"] == "none"
    assert rec["binding_cause"] == "search_stagnation"


def test_one_confirmation_is_deprioritized():
    """'n_runs_confirmed=1' als Eingabe bedeutet 'dies ist bereits der ZWEITE aufeinanderfolgende
    Lauf mit demselben Befund' (die Zählung selbst zählt Bestätigungen VOR diesem Lauf) — erfüllt
    das Akzeptanzkriterium 'nach zwei Läufen deprioritized'."""
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "search_stagnation"},
        n_runs_confirmed=1)
    assert rec["action"] == "deprioritized"


def test_three_confirmations_still_deprioritized_not_yet_denylisted():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "search_stagnation"},
        n_runs_confirmed=3)
    assert rec["action"] == "deprioritized"


def test_four_confirmations_is_denylist():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "search_stagnation"},
        n_runs_confirmed=4)
    assert rec["action"] == "denylist"


def test_five_confirmations_stays_denylist():
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "search_stagnation"},
        n_runs_confirmed=5)
    assert rec["action"] == "denylist"


def test_other_causes_are_unaffected_by_the_new_branch():
    """Regressionsschutz: der neue Zweig darf keine bestehende binding_cause-Behandlung
    verändern — stichprobenartig gegen 'signal_absent' geprüft (dieselbe Schwellen-Form)."""
    rec_low = recommend_diagnosis_action(
        "AdxAtrMomentum", "TSLA.ETORO", {"binding_cause": "signal_absent"},
        n_runs_confirmed=0, stop_reason="STRUCTURAL_ALL_UNEVALUABLE")
    assert rec_low["action"] == "none"
    rec_high = recommend_diagnosis_action(
        "AdxAtrMomentum", "TSLA.ETORO", {"binding_cause": "signal_absent"},
        n_runs_confirmed=2, stop_reason="STRUCTURAL_ALL_UNEVALUABLE")
    assert rec_high["action"] == "denylist"


# --- report._writeback_search_stagnation_diagnoses -----------------------------------------------

def test_no_offenders_is_a_pure_no_op(tmp_path):
    out = _writeback_search_stagnation_diagnoses(None, None, run_id="run_1", work_dir=tmp_path)
    assert out == []
    assert load_diagnosed_pairs_cache(tmp_path) == {}


def test_search_made_progress_offender_and_structural_zero_eligible_missing_are_unioned(tmp_path):
    """Beide Befundmengen (unterschiedliche Key-Formen, aber beide 'strategy/symbol') werden zu
    EINER Paarmenge vereinigt — ein Paar, das in BEIDEN Mengen vorkommt, erzeugt nur EINEN
    Rückschrieb-Eintrag."""
    out = _writeback_search_stagnation_diagnoses(
        {"SqueezeBreakout/ASML.ETORO": -0.01},
        ["SqueezeBreakout/ASML.ETORO", "AdxAtrMomentum/TSLA.ETORO"],
        run_id="run_1", work_dir=tmp_path,
    )
    pairs_written = {(r["strategy"], r["symbol"]) for r in out}
    assert pairs_written == {("SqueezeBreakout", "ASML.ETORO"), ("AdxAtrMomentum", "TSLA.ETORO")}
    cache = load_diagnosed_pairs_cache(tmp_path)
    assert set(cache.keys()) == pairs_written
    for entry in cache.values():
        assert entry["binding_cause"] == "search_stagnation"
        assert entry["action"] == "none"  # erste Beobachtung: noch keine Bestaetigung


def test_escalation_across_repeated_runs_reaches_denylist_without_ever_regressing(tmp_path):
    """Der zentrale Regressionstest: über viele aufeinanderfolgende Läufe mit demselben Befund für
    dasselbe Paar MUSS die Aktion irgendwann 'denylist' erreichen — und darf dabei NIE von
    'deprioritized' zurück auf 'none' fallen (record_diagnosed_pair startet die Bestätigungs-Serie
    bei JEDEM Aktionswechsel neu, siehe dortiger Docstring; eine Schwelle, die genau auf die
    Reset-Landestelle trifft, würde in einer none/deprioritized-Oszillation enden, die 'denylist'
    nie erreicht — das ist der Fehler, den diese Schwellenwahl (>= 1 statt >= 2) verhindert)."""
    key = ("SqueezeBreakout", "ASML.ETORO")
    seen_actions = []
    reached_denylist = False
    for i in range(12):
        if reached_denylist:
            break
        out = _writeback_search_stagnation_diagnoses(
            {"SqueezeBreakout/ASML.ETORO": -0.01}, None,
            run_id=f"run_{i}", work_dir=tmp_path,
        )
        action = out[0]["action"]
        seen_actions.append(action)
        if action == "denylist":
            reached_denylist = True
    assert reached_denylist, f"nie denylisted, Verlauf: {seen_actions}"
    # Keine Rückstufung: sobald 'deprioritized' zum ersten Mal erreicht ist, darf 'none' nie
    # wieder auftreten (monotone Eskalation).
    first_deprioritized = seen_actions.index("deprioritized")
    assert "none" not in seen_actions[first_deprioritized:]


def test_expires_after_runs_falls_back_to_default_ten():
    """'search_stagnation' hat keinen eigenen Eintrag in _EXPIRES_AFTER_RUNS_BY_CAUSE — bewusst
    (kein im Issue benannter abweichender Wert) — der globale Default (10) gilt."""
    rec = recommend_diagnosis_action(
        "SqueezeBreakout", "ASML.ETORO", {"binding_cause": "search_stagnation"},
        n_runs_confirmed=0)
    assert rec["expires_after_runs"] == 10


def test_prior_entry_with_a_different_binding_cause_does_not_leak_its_n_runs_confirmed(tmp_path):
    """Ein Paar, das VORHER wegen 'signal_sparse' im Cache stand, darf dessen n_runs_confirmed
    nicht für 'search_stagnation' übernehmen (unterschiedliche Ursache ⇒ Zählung beginnt bei 0)."""
    record_diagnosed_pair(
        {"strategy": "SqueezeBreakout", "symbol": "ASML.ETORO",
         "action": "search_space_override", "binding_cause": "signal_sparse",
         "n_runs_confirmed": 5},
        work_dir=tmp_path, run_id="run_prior",
    )
    out = _writeback_search_stagnation_diagnoses(
        {"SqueezeBreakout/ASML.ETORO": -0.01}, None, run_id="run_next", work_dir=tmp_path)
    assert out[0]["action"] == "none"  # nicht sofort 'deprioritized' wegen fremder Vorgeschichte


def test_writeback_respects_the_diagnostic_writeback_enabled_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep_diagnostics, "_read_diagnostic_writeback_enabled", lambda: False)
    _writeback_search_stagnation_diagnoses(
        {"SqueezeBreakout/ASML.ETORO": -0.01}, None, run_id="run_1", work_dir=tmp_path)
    assert load_diagnosed_pairs_cache(tmp_path) == {}


def test_malformed_offender_key_is_skipped_not_fatal(tmp_path):
    out = _writeback_search_stagnation_diagnoses(
        {"no_slash_here": -0.01}, ["also_no_slash"], run_id="run_1", work_dir=tmp_path)
    assert out == []
