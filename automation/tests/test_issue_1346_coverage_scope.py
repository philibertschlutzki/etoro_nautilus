"""Issue #1346 (GH #1240, P2) — ``check_invariant_coverage`` kannte keinen Lauf-Skope und FAILte
auf jedem Abbruchlauf mit einer langen Liste von Checks, die eine Study-/Trial-/Promotion-
Grundgesamtheit voraussetzen und daher STRUKTURELL nicht erscheinen konnten (Symptom: n_defined=151,
n_in_stream=113, n_allowlisted=9, missing=29 — obwohl der Lauf 0 Studies hatte).

Root-Cause. ``automation/optimizer/invariants.py`` war rein mengenbasiert: ``missing = defined -
stream - allowlisted``. Sie kannte weder n_studies noch eine Erreichbarkeitsklasse.

Fix.
1. Jede ``check_*``-Funktion traegt eine deklarierte Skope-Anforderung (``@invariant_scope(...)``,
   Attribut am Funktionsobjekt, nicht in einer zweiten Liste): ``run`` (immer erreichbar), ``study``
   (>= 1 Study), ``trial`` (>= 1 Trial), ``promotion`` (>= 1 Promotion).
2. ``check_invariant_coverage`` erhaelt die Lauf-Zaehlstaende (n_studies/n_trials/n_promotions) und
   wertet nur Checks aus, deren Skope-Anforderung erfuellt ist. Nicht erreichbare Checks werden als
   ``not_reachable_in_run_scope`` (``actual['not_reachable']``/``['n_not_reachable']``) ausgewiesen
   — sichtbar, aber kein FAIL.
3. Ein Check mit erfuellter Skope-Anforderung, der trotzdem aus dem Strom bleibt, ist weiterhin FAIL.

Akzeptanzkriterien:
- Ein Lauf mit 0 Studies liefert passed=true mit n_not_reachable > 0.
- Ein Lauf mit Studies, in dem ein study-skopierter Check entfernt wurde, FAILt weiterhin mit
  genau diesem Namen.
- Jede der check_*-Funktionen traegt eine Skope-Deklaration; ein Meta-Test schlaegt fehl, sobald
  eine neue ohne Deklaration hinzukommt.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report as r


# ── invariants.invariant_scope / _reachable_invariant_scopes (reine Funktionen) ──────────────────

def test_invariant_scope_decorator_stamps_the_function_attribute():
    @inv.invariant_scope("study")
    def check_dummy():
        pass

    assert check_dummy._invariant_scope == "study"


def test_invariant_scope_rejects_an_unknown_scope_name():
    import pytest
    with pytest.raises(ValueError):
        inv.invariant_scope("symbol")


def test_reachable_scopes_at_zero_studies_is_only_run():
    assert inv._reachable_invariant_scopes(n_studies=0, n_trials=0, n_promotions=0) == {"run"}


def test_reachable_scopes_ladder_is_cumulative():
    assert inv._reachable_invariant_scopes(n_studies=1, n_trials=0, n_promotions=0) == {
        "run", "study"}
    assert inv._reachable_invariant_scopes(n_studies=1, n_trials=5, n_promotions=0) == {
        "run", "study", "trial"}
    assert inv._reachable_invariant_scopes(n_studies=1, n_trials=5, n_promotions=1) == {
        "run", "study", "trial", "promotion"}


# ── invariants.check_invariant_coverage — Skope-Filterung ─────────────────────────────────────────

def test_zero_study_run_passes_with_a_positive_not_reachable_count():
    """Akzeptanzkriterium 1 — der #1346-Referenzbefund: 0 Studies, ein study-skopierter Check
    fehlt im Strom (er konnte nie erscheinen) -> PASS, nicht FAIL."""
    defined = ["check_run_thing", "check_study_thing"]
    stream = ["check_run_thing"]
    scopes = {"check_run_thing": "run", "check_study_thing": "study"}
    result = inv.check_invariant_coverage(
        defined, stream, check_scopes=scopes, n_studies=0, n_trials=0, n_promotions=0)
    assert result.passed is True
    assert result.actual["n_not_reachable"] == 1
    assert result.actual["not_reachable"] == ["check_study_thing"]
    assert result.actual["missing"] == []


def test_study_scoped_check_missing_on_a_run_with_studies_still_fails_by_name():
    """Akzeptanzkriterium 2 — ein study-skopierter Check, der auf einem Lauf MIT Studies trotzdem
    aus dem Strom bleibt, ist weiterhin FAIL, mit genau diesem Namen."""
    defined = ["check_run_thing", "check_study_thing"]
    stream = ["check_run_thing"]
    scopes = {"check_run_thing": "run", "check_study_thing": "study"}
    result = inv.check_invariant_coverage(
        defined, stream, check_scopes=scopes, n_studies=3, n_trials=0, n_promotions=0)
    assert result.passed is False
    assert result.actual["missing"] == ["check_study_thing"]
    assert result.actual["n_not_reachable"] == 0


def test_trial_scoped_check_is_not_reachable_with_studies_but_zero_trials():
    defined = ["check_trial_thing"]
    scopes = {"check_trial_thing": "trial"}
    result = inv.check_invariant_coverage(
        defined, [], check_scopes=scopes, n_studies=3, n_trials=0, n_promotions=0)
    assert result.passed is True
    assert result.actual["n_not_reachable"] == 1


def test_promotion_scoped_check_is_not_reachable_without_a_promotion():
    defined = ["check_promotion_thing"]
    scopes = {"check_promotion_thing": "promotion"}
    result = inv.check_invariant_coverage(
        defined, [], check_scopes=scopes, n_studies=3, n_trials=10, n_promotions=0)
    assert result.passed is True
    assert result.actual["n_not_reachable"] == 1

    result_reachable = inv.check_invariant_coverage(
        defined, [], check_scopes=scopes, n_studies=3, n_trials=10, n_promotions=1)
    assert result_reachable.passed is False
    assert result_reachable.actual["missing"] == ["check_promotion_thing"]


def test_a_defined_name_without_a_scope_declaration_defaults_conservatively_to_run():
    """Eine fehlende Deklaration macht einen Check NIE erreichbarkeits-befreit — sie faellt auf
    'run' (immer erreichbar) zurueck, nicht auf eine staerkere Anforderung."""
    defined = ["check_undeclared"]
    result = inv.check_invariant_coverage(
        defined, [], check_scopes={}, n_studies=0, n_trials=0, n_promotions=0)
    assert result.passed is False
    assert result.actual["missing"] == ["check_undeclared"]


def test_allowlisted_names_are_excluded_regardless_of_reachability():
    defined = ["check_study_thing"]
    scopes = {"check_study_thing": "study"}
    result = inv.check_invariant_coverage(
        defined, [], allowlisted_check_names=["check_study_thing"],
        check_scopes=scopes, n_studies=5, n_trials=5, n_promotions=0)
    assert result.passed is True


# ── report._all_defined_check_scopes — jede definierte Funktion traegt eine Deklaration ──────────

def test_every_defined_check_function_across_the_package_carries_a_scope_declaration():
    """Akzeptanzkriterium 3 — Meta-Test: jede der ueber das Paket verteilten check_*-Funktionen
    (siehe report._all_defined_check_names, dieselbe Dateimenge) muss in
    report._all_defined_check_scopes() erscheinen. Schlaegt fehl, sobald eine neue check_*-Funktion
    ohne @invariant_scope(...)-Dekorator hinzukommt."""
    names = set(r._all_defined_check_names())
    scopes = r._all_defined_check_scopes()
    undeclared = sorted(names - set(scopes))
    assert undeclared == [], (
        f"{len(undeclared)} check_*-Funktion(en) ohne @invariant_scope(...)-Deklaration: "
        f"{undeclared}")


def test_every_declared_scope_is_one_of_the_four_valid_tiers():
    scopes = r._all_defined_check_scopes()
    assert set(scopes.values()) <= {"run", "study", "trial", "promotion"}


def test_real_report_module_check_invariant_coverage_call_passes_check_scopes_and_counts():
    """Verdrahtungs-Test (Text-Scan, analog test_check_invariant_coverage_is_wired_into_build_
    report in test_issue_1015_1167): die Aufrufstelle in _build_report muss die neuen Parameter
    tatsaechlich fuettern, nicht nur die Funktion selbst anbieten."""
    import inspect
    from automation.optimizer import report as report_mod
    src = inspect.getsource(report_mod._build_report)
    assert "check_scopes=" in src
    assert "n_studies=" in src
    assert "n_promotions=" in src
