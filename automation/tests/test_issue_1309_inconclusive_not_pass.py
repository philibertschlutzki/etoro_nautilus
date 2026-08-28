"""Issue #1309 (GH #1186, P1) — Fünf Checks melden Nicht-Auswertbarkeit nur im Text, nicht im
Wahrheitswert.

Symptom. ``check_stop_loss_decomposition_identity`` (blocking), ``check_atr_floor_enforcement``,
``check_cost_drag_decomposition``, ``check_slippage_calibration_not_circular``, ``check_slippage_
scope_agreement`` stehen in §5.1b als "nicht auswertbar" und im JSON mit ``passed = true`` (B-6).

Root-Cause. Die fünf Funktionen gaben bei leerer Grundgesamtheit ``InvariantResult(passed=True,
detail="… nicht auswertbar/nicht anwendbar")`` zurück — ein Widerspruch zwischen Text und
Wahrheitswert (siehe ``InvariantResult.passed``-Feld-Docstring, #995/#1147: ``None`` ist reserviert
fuer "kein Urteil moeglich").

Fix.
1. Die fünf Funktionen setzen im Leer-Zweig ``passed=None`` statt ``passed=True``. Bei der
   Implementierung des generischen Wächters (Punkt 2) wurden zwei WEITERE, bislang unbekannte
   Instanzen derselben Fehlerklasse aufgedeckt und ebenfalls repariert: ``check_stop_trigger_axis_
   coherence`` und ``check_mandatory_gate_reachability_global``.
2. Meta-Invariante ``invariants.check_inconclusive_not_reported_as_pass`` (severity ``high``) fängt
   JEDE künftige Instanz derselben Fehlerklasse generisch ab — geprüft gegen das STRUKTURIERTE
   ``evaluable``-Feld (nicht gegen einen Text-Musterabgleich auf ``detail``: ein reiner Textabgleich
   auf "nicht auswertbar"/"nicht anwendbar" erzeugte gegen einen realen Report > 40 Falsch-Treffer,
   weil die uebergrosse Mehrheit der bestehenden ``inconclusive=True``-Checks ABSICHTLICH
   ``passed=True`` fuer eine vacuose "nichts zu pruefen"-Situation traegt, siehe
   ``InvariantResult.evaluable``-Feld-Docstring).
"""
from automation.optimizer import invariants as inv


# ── Die fuenf betroffenen Checks: passed=None (nicht True) auf leerer Kohorte ───────────────────

def test_check_stop_loss_decomposition_identity_is_none_on_empty_cohort():
    result = inv.check_stop_loss_decomposition_identity([])
    assert result.passed is None
    assert result.evaluable is False


def test_check_atr_floor_enforcement_is_none_on_empty_cohort():
    result = inv.check_atr_floor_enforcement([])
    assert result.passed is None
    assert result.evaluable is False


def test_check_cost_drag_decomposition_is_none_on_empty_cohort():
    result = inv.check_cost_drag_decomposition([])
    assert result.passed is None
    assert result.evaluable is False


def test_check_slippage_calibration_not_circular_is_none_on_empty_cohort():
    result = inv.check_slippage_calibration_not_circular([])
    assert result.passed is None
    assert result.evaluable is False


def test_check_slippage_scope_agreement_is_none_on_empty_cohort():
    result = inv.check_slippage_scope_agreement([])
    assert result.passed is None
    assert result.evaluable is False


# ── Regressionsschutz: eine informative Kohorte bleibt unveraendert bool-wertig ──────────────────

def test_check_slippage_scope_agreement_still_passes_a_matching_cohort():
    result = inv.check_slippage_scope_agreement([
        {"strategy": "S", "symbol": "A.ETORO",
         "slippage_p50_calibration_scope": "symbol", "slippage_calibration_scope": "symbol"},
    ])
    assert result.passed is True


def test_check_slippage_scope_agreement_still_fails_a_diverging_cohort():
    result = inv.check_slippage_scope_agreement([
        {"strategy": "S", "symbol": "A.ETORO",
         "slippage_p50_calibration_scope": "symbol", "slippage_calibration_scope": "asset_class"},
    ])
    assert result.passed is False


# ── invariants.check_inconclusive_not_reported_as_pass — generischer Meta-Waechter ──────────────

def test_inconclusive_not_pass_fails_on_a_constructed_evaluable_false_with_passed_true():
    checks = [{
        "name": "check_some_future_check", "passed": True, "evaluable": False,
        "detail": "Keine Study mit vollstaendiger Telemetrie — nicht auswertbar (INCONCLUSIVE).",
    }]
    result = inv.check_inconclusive_not_reported_as_pass(checks)
    assert result.passed is False
    assert "check_some_future_check" in result.actual


def test_inconclusive_not_pass_ignores_the_widespread_legitimate_vacuous_pass_pattern():
    """Regressionsschutz GEGEN die urspruengliche (zu breite) Text-Musterabgleich-Implementierung:
    die uebergrosse Mehrheit der bestehenden inconclusive=True-Checks traegt ABSICHTLICH
    passed=True bei "nichts zu pruefen" (z. B. check_fail_fast_invariants_wired bei leerer
    fail_fast_invariants-Config) — OHNE evaluable=False zu setzen. Ein reiner Textabgleich auf
    "nicht anwendbar" wuerde das faelschlich als Verstoss melden (empirisch > 40 Falsch-Treffer
    gegen einen realen Report); das strukturierte evaluable-Feld unterscheidet beide Faelle korrekt."""
    checks = [{
        "name": "check_fail_fast_invariants_wired", "passed": True,
        "detail": "fail_fast_invariants leer/fehlt — nicht anwendbar.",
    }]
    result = inv.check_inconclusive_not_reported_as_pass(checks)
    assert result.passed is True


def test_inconclusive_not_pass_passes_when_the_same_detail_carries_passed_none():
    """Der reparierte Zustand (dieser Fix, Teil 1): passed=None + evaluable=False ist KEIN Verstoss
    (die Tri-State-Konvention wurde korrekt eingehalten)."""
    checks = [{
        "name": "check_atr_floor_enforcement", "passed": None, "evaluable": False,
        "detail": "Keine floor-gebundene Study mit vollstaendiger Kostenbasis — nicht auswertbar "
                  "(INCONCLUSIVE).",
    }]
    result = inv.check_inconclusive_not_reported_as_pass(checks)
    assert result.passed is True


def test_check_stop_trigger_axis_coherence_is_none_on_missing_axis_declaration():
    """Zweite, bei der Implementierung dieses Wächters aufgedeckte Instanz derselben Fehlerklasse
    (ausserhalb der urspruenglich benannten fuenf Checks, aber identisches Muster: evaluable=False
    + passed=True)."""
    result = inv.check_stop_trigger_axis_coherence(None, [])
    assert result.passed is None
    assert result.evaluable is False


def test_check_mandatory_gate_reachability_global_is_none_without_any_live_result():
    """Dritte aufgedeckte Instanz derselben Fehlerklasse."""
    result = inv.check_mandatory_gate_reachability_global([])
    assert result.passed is None
    assert result.evaluable is False


def test_inconclusive_not_pass_passes_on_a_genuine_pass_true_with_ok_detail():
    checks = [{"name": "check_holding_time_cap", "passed": True, "detail": "OK"}]
    result = inv.check_inconclusive_not_reported_as_pass(checks)
    assert result.passed is True


def test_inconclusive_not_pass_ignores_a_genuine_fail():
    """Ein echtes FAIL (passed=False) ist kein Fall dieses Waechters — dafuer ist §5.1 zustaendig."""
    checks = [{"name": "check_x", "passed": False, "detail": "nicht auswertbar — irrelevant hier"}]
    result = inv.check_inconclusive_not_reported_as_pass(checks)
    assert result.passed is True


def test_inconclusive_not_pass_passes_on_a_reference_run_derived_from_the_fixed_five_checks():
    """Akzeptanzkriterium: PASSt auf einem Referenzlauf — hier: die fuenf reparierten Checks,
    tatsaechlich mit leerer Kohorte aufgerufen und ihre echten to_dict()-Ergebnisse durchgereicht."""
    checks = [
        inv.check_stop_loss_decomposition_identity([]).to_dict(),
        inv.check_atr_floor_enforcement([]).to_dict(),
        inv.check_cost_drag_decomposition([]).to_dict(),
        inv.check_slippage_calibration_not_circular([]).to_dict(),
        inv.check_slippage_scope_agreement([]).to_dict(),
    ]
    result = inv.check_inconclusive_not_reported_as_pass(checks)
    assert result.passed is True


def test_inconclusive_not_pass_empty_stream_is_trivially_true():
    result = inv.check_inconclusive_not_reported_as_pass([])
    assert result.passed is True


# ── §5.1/§5.1b Disjunktheit — bestehende summary_de.py-Klassifikation ────────────────────────────

def test_section_5_1_and_5_1b_are_disjoint_and_cover_every_non_pass_entry():
    """Akzeptanzkriterium: §5.1 (failing_checks, passed is False) und §5.1b (inconclusive_checks,
    passed is None oder evaluable is False) sind disjunkt und decken zusammen jeden Nicht-PASS-
    Eintrag ab — inklusive der jetzt korrekt passed=None tragenden fuenf Checks."""
    checks = [
        inv.check_stop_loss_decomposition_identity([]).to_dict(),
        inv.check_atr_floor_enforcement([]).to_dict(),
        {"name": "check_holding_time_cap", "passed": False, "evaluable": True},
        {"name": "check_ok", "passed": True, "evaluable": True},
    ]
    failing = [c for c in checks if c.get("passed") is False]
    inconclusive = [c for c in checks if c.get("passed") is None or c.get("evaluable") is False]
    non_pass = [c for c in checks if not (c.get("passed") is True and c.get("evaluable") is not False)]

    failing_names = {c.get("name") for c in failing}
    inconclusive_names = {c.get("name") for c in inconclusive}
    assert failing_names.isdisjoint(inconclusive_names)
    assert failing_names | inconclusive_names == {c.get("name") for c in non_pass}
