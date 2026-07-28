"""Issue #811 (P1, hängt von #810 ab) — die Kollinearitätsmessung lief auf rohen ``oos_gate_deltas``-
WERTEN (Spearman-Rangkorrelation, ``gate_rank_correlation_matrix``) und erkannte damit
Aktivitäts-KOVARIANZ statt Entscheidungs-REDUNDANZ: 105 Studies zeigten ρ(oos_min_trades,
oos_max_drawdown) > 0.9 — beide Größen kovariieren mechanisch mit der Trade-Frequenz (mehr Trades ⇒
engere Drawdown-Margen), OHNE dieselbe Zulassungs-Entscheidung zu treffen. Die eigentliche Frage ist:
"Ändert sich die Menge der eligible Trials, wenn ich Gate B entferne?"

Fix: ``gate_collinearity_redundancy_alarm`` misst jetzt (1) die Jaccard-Ähnlichkeit der PASS-Mengen
(``delta >= 0``, ``_jaccard_of_pass_sets``) statt der Spearman-Rangkorrelation der Delta-WERTE, UND
(2) den marginalen Eigenbeitrag Δ_B = P(eligible ohne B) − P(eligible mit B)
(``gate_marginal_pass_rate_delta``) des niedriger priorisierten Gates — ein Gate gilt erst als
redundant, wenn BEIDE Kriterien zutreffen (Jaccard > ``gate_redundancy_jaccard``, Default 0.95, UND
Δ_B < ``gate_redundancy_marginal``, Default 0.02). Strukturelle Vorbedingungen/harte Risikogrenzen
(``gate_consolidation_protected``, #810) werden VOR der Messung VOLLSTÄNDIG aus der Kandidatenmatrix
entfernt (stärker als die #810-Garantie "wird nie Kandidat"). Die Spearman-Matrix
(``gate_rank_correlation_matrix``/``assert_gate_collinearity_guard``) bleibt UNVERÄNDERT als reine
#742-Report-Telemetrie erhalten — sie verliert nur ihre Alarm-Funktion.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.reward import (
    gate_collinearity_redundancy_alarm,
    gate_rank_correlation_matrix,
    assert_gate_collinearity_guard,
    gate_marginal_pass_rate_delta,
    _gate_pass_flags,
    _jaccard_of_pass_sets,
    _gate_redundancy_jaccard_threshold,
    _gate_redundancy_marginal_threshold,
)

CFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))

_TCFG_AB = {
    "eligible_requires_all": ["oos_gate_a", "oos_gate_b"],
    "gate_consolidation_priority": ["oos_gate_a", "oos_gate_b"],
}


# ── _jaccard_of_pass_sets: reine Mengen-Arithmetik ─────────────────────────────────────────────────
def test_jaccard_identical_sets_is_one():
    assert _jaccard_of_pass_sets([True, True, False, True], [True, True, False, True]) == 1.0


def test_jaccard_disjoint_sets_is_zero():
    assert _jaccard_of_pass_sets([True, True, False, False], [False, False, True, True]) == 0.0


def test_jaccard_partial_overlap():
    # A={0,1,2}, B={1,2,3} über 4 Elemente -> Schnitt {1,2}=2, Vereinigung {0,1,2,3}=4.
    assert _jaccard_of_pass_sets([True, True, True, False], [False, True, True, True]) == 0.5


def test_jaccard_none_values_are_skipped_not_treated_as_false():
    # Reihe 1 fehlt bei A -> wird uebersprungen, nicht als False gezaehlt (sonst kuenstlich
    # divergierende Passrate).
    assert _jaccard_of_pass_sets([True, None, False], [True, False, False]) == 1.0


def test_jaccard_empty_union_is_none_not_zero():
    # Beide Mengen durchgehend False -> Jaccard bei leerer Vereinigung ist UNDEFINIERT, nicht 0
    # (0 wuerde faelschlich "voellig verschieden" statt "nicht messbar" bedeuten).
    assert _jaccard_of_pass_sets([False, False], [False, False]) is None


def test_jaccard_no_common_defined_trials_is_none():
    assert _jaccard_of_pass_sets([True, None], [None, True]) is None


# ── Akzeptanzkriterium #811 (1): identisches Pass-Muster -> Jaccard=1.0, Alarm feuert ──────────────
def test_identical_pass_sets_trigger_alarm_even_with_wildly_different_magnitudes_and_ranks():
    """Zwei Gates mit exakt demselben PASS/FAIL-Muster (Jaccard=1.0), aber unterschiedlichen
    Groessenordnungen UND ohne nennenswerte Rangkorrelation der rohen Werte — beweist, dass die
    Jaccard-Messung tatsaechlich die ENTSCHEIDUNG misst, nicht (wie Spearman) die Werte."""
    a = [0.01, 0.5, -0.01, 0.3, -0.2, -0.05, 0.001, -0.9]
    b = [50.0, 2.0, -80.0, 1.0, -0.001, -300.0, 400.0, -0.0001]
    cohort = [{"oos_gate_a": x, "oos_gate_b": y} for x, y in zip(a, b)]

    flags = _gate_pass_flags(cohort, ["oos_gate_a", "oos_gate_b"])
    assert _jaccard_of_pass_sets(flags["oos_gate_a"], flags["oos_gate_b"]) == 1.0

    spearman = gate_rank_correlation_matrix(cohort, _TCFG_AB)["correlations"][("oos_gate_a", "oos_gate_b")]
    assert abs(spearman) < 0.90  # deutlich unter der ALTEN Alarm-Schwelle -> Spearman haette NICHT gefeuert

    alarm = gate_collinearity_redundancy_alarm(cohort, _TCFG_AB)
    assert alarm["alarms"] == [{"keep": "oos_gate_a", "redundant_candidate": "oos_gate_b",
                                "jaccard": 1.0, "marginal_delta": 0.0}]
    assert "oos_gate_b" in alarm["redundant_candidates"]


# ── Akzeptanzkriterium #811 (2): disjunkte Pass-Mengen trotz perfekt kovariierender Deltas ─────────
# -> KEIN Alarm (das ist der HEAD-Bug: die Spearman-Version feuert hier faelschlich).
def test_disjoint_pass_sets_with_perfectly_covarying_deltas_do_not_trigger_alarm():
    """Der literale HEAD-Bug: A und B sind PERFEKT (rangkorreliert, |ρ|=1.0) — beide sind
    monotone Funktionen desselben Index i (die 'Aktivitaets-Achse') — aber ihre PASS-Mengen
    (delta >= 0) sind komplett disjunkt (A passt fuer die ersten 5 Trials, B fuer die letzten 5).
    Die alte Spearman-Schwelle (>0.9) haette hier fälschlich einen Redundanz-Alarm ausgeloest."""
    n = 10
    cohort = [{"oos_gate_a": float(4 - i), "oos_gate_b": float(i - 5)} for i in range(n)]

    spearman = gate_rank_correlation_matrix(cohort, _TCFG_AB)["correlations"][("oos_gate_a", "oos_gate_b")]
    assert abs(spearman) == pytest.approx(1.0)  # die rohen Deltas kovariieren PERFEKT

    flags = _gate_pass_flags(cohort, ["oos_gate_a", "oos_gate_b"])
    assert _jaccard_of_pass_sets(flags["oos_gate_a"], flags["oos_gate_b"]) == 0.0  # disjunkte Entscheidung

    alarm = gate_collinearity_redundancy_alarm(cohort, _TCFG_AB)
    assert alarm["alarms"] == []
    assert alarm["redundant_candidates"] == {}

    # Die Spearman-Telemetrie (assert_gate_collinearity_guard, #742-Report) bleibt UNVERAENDERT und
    # warnt weiterhin (reine Diagnose, kein Alarm) -- sie verliert nur ihre Alarm-Funktion.
    guard = assert_gate_collinearity_guard(cohort, _TCFG_AB)
    assert abs(guard["correlations"][("oos_gate_a", "oos_gate_b")]) == pytest.approx(1.0)


# ── Marginaler Beitrag als zweites, unabhaengiges Kriterium (Sicherheitsnetz gegen Kleinstichproben)
def test_high_jaccard_but_nonneglible_marginal_contribution_suppresses_the_alarm():
    """B stimmt in 39 von 40 Trials mit A ueberein (Jaccard=0.975 > 0.95-Schwelle) -- ABER B
    schliesst in der verbleibenden 1 Trial (2.5% der Kohorte, >= 0.02-Marginal-Schwelle)
    eigenstaendig einen Trial aus, den die uebrigen Gates durchgelassen haetten. B ist damit KEIN
    reiner Karteileichen-Kandidat -> kein Alarm, trotz Jaccard > 0.95."""
    n = 40
    cohort = [{"oos_gate_a": 1.0, "oos_gate_b": 1.0 if i != n - 1 else -1.0} for i in range(n)]

    flags = _gate_pass_flags(cohort, ["oos_gate_a", "oos_gate_b"])
    jaccard = _jaccard_of_pass_sets(flags["oos_gate_a"], flags["oos_gate_b"])
    assert jaccard == pytest.approx(0.975)
    assert jaccard > _gate_redundancy_jaccard_threshold(_TCFG_AB)

    marginal = gate_marginal_pass_rate_delta(cohort, _TCFG_AB, "oos_gate_b")
    assert marginal == pytest.approx(1.0 / 40.0)
    assert marginal >= _gate_redundancy_marginal_threshold(_TCFG_AB)

    alarm = gate_collinearity_redundancy_alarm(cohort, _TCFG_AB)
    assert alarm["alarms"] == []
    assert alarm["redundant_candidates"] == {}


# ── gate_marginal_pass_rate_delta: Randfaelle ──────────────────────────────────────────────────────
def test_marginal_pass_rate_delta_is_none_for_inactive_gate():
    cohort = [{"oos_gate_a": 1.0, "oos_gate_b": 1.0}]
    assert gate_marginal_pass_rate_delta(cohort, _TCFG_AB, "oos_gate_c") is None


def test_marginal_pass_rate_delta_is_none_when_no_trial_has_all_active_deltas():
    cohort = [{"oos_gate_a": 1.0}, {"oos_gate_b": 1.0}]  # nie beide gemeinsam definiert
    assert gate_marginal_pass_rate_delta(cohort, _TCFG_AB, "oos_gate_b") is None


def test_marginal_pass_rate_delta_zero_when_gate_never_changes_the_outcome():
    # B ist IMMER identisch zu A -> Entfernen von B aendert nichts.
    cohort = [{"oos_gate_a": e, "oos_gate_b": e} for e in [0.1, -0.1, 0.2, -0.2, 0.3]]
    assert gate_marginal_pass_rate_delta(cohort, _TCFG_AB, "oos_gate_b") == 0.0


# ── Protected Gates: VOLLSTAENDIG aus der Matrix entfernt (staerker als #810 "nie Kandidat") ───────
def test_protected_gate_is_removed_from_the_matrix_entirely_not_just_never_a_candidate():
    """Ein protected Gate (min_trades) mit PERFEKT identischem Pass-Muster zu einem aktiven
    Quality-Gate darf unter #811 nicht einmal als 'keep'-Partner in einem Alarm auftauchen -- es
    wird VOR der Messung entfernt, nicht nur von der redundant_candidate-Rolle ausgeschlossen."""
    tcfg = {
        "eligible_requires_all": ["min_trades", "oos_min_psr"],
        "gate_consolidation_priority": ["min_trades", "oos_min_psr"],
        "gate_consolidation_protected": ["min_trades"],
    }
    cohort = [{"oos_min_trades": e, "oos_min_psr": e} for e in [0.1, -0.1, 0.2, -0.2, 0.3]]
    result = gate_collinearity_redundancy_alarm(cohort, tcfg)
    assert result["alarms"] == []
    assert result["redundant_candidates"] == {}
    for alarm in result["alarms"]:
        assert "oos_min_trades" not in (alarm["keep"], alarm["redundant_candidate"])


# ── Produktions-Config: neue Keys deklariert + dokumentiert, real config bleibt alarm-frei ────────
def test_real_config_declares_the_new_redundancy_keys():
    assert CFG["gate_redundancy_jaccard"] == 0.95
    assert CFG["gate_redundancy_marginal"] == 0.02


def test_schema_documents_811_for_both_new_keys():
    doc_jaccard = CFG["_schema"]["fields"]["gate_redundancy_jaccard"]
    doc_marginal = CFG["_schema"]["fields"]["gate_redundancy_marginal"]
    assert "#811" in doc_jaccard
    assert "#811" in doc_marginal


def test_missing_keys_fall_back_to_issue_text_defaults():
    assert _gate_redundancy_jaccard_threshold({}) == 0.95
    assert _gate_redundancy_jaccard_threshold(None) == 0.95
    assert _gate_redundancy_marginal_threshold({}) == 0.02
    assert _gate_redundancy_marginal_threshold(None) == 0.02


def test_real_config_yields_no_alarm_for_a_clean_cohort():
    cohort = [{"oos_min_trades": 50.0 + i, "oos_max_drawdown": 0.05 + i * 0.001,
              "oos_min_psr": 0.7 + i * 0.01} for i in range(8)]
    result = gate_collinearity_redundancy_alarm(cohort, CFG)
    assert result["alarms"] == []
    assert result["redundant_candidates"] == {}
