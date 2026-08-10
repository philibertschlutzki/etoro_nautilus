"""Issue #830 (P1, Katalog #828-#835, GitHub-Issue #751) — Gegenstück zu #829: 'signal_quality'
deaktiviert nach einem EINZIGEN Lauf ohne jede Evidenzschwelle.

Root-Cause: ``recommend_diagnosis_action`` resolvte ``binding_cause == 'signal_quality'``
UNBEDINGT auf ``'denylist'`` — kein ``budget_executed_fraction``, kein ``n_runs_confirmed``. 298
Zero-Eligible-Plateaus in einem einzigen Lauf (31 % der 948 protokollierten Studies), überwiegend
mit VOLLEM Budget (99/99, 119/119, ...) — diese Studies haben gesucht und nichts gefunden, das
genau die Art Ergebnis ist, die ein gutes Verfahren in einem ungünstigen Regime produziert. Auf
n=1 daraus eine 10-läufige Deaktivierung abzuleiten, ist ein Typ-II-Verstärker: das Verfahren
entfernt bevorzugt die Paare, deren Nicht-Ergebnis regimebedingt war.

Fix: 'signal_quality' unterliegt jetzt demselben grundsätzlichen Evidenzregime wie 'signal_absent'
(#829/#778) — volle Deaktivierung ('denylist') nur nach ``n_runs_confirmed >= 2`` UND
``budget_executed_fraction >= 0.9``. Neu (Fix Punkt 2): STATT direkt bei fehlender Evidenz auf
'none' zurückzufallen, erhält ein Paar mit mindestens EINER Bestätigung die Zwischenklasse
'deprioritized' — der Suchraum bleibt erreichbar (reduziertes statt volles Budget,
``run_optimization._apply_deprioritized_budget``, ``optimizer.json['deprioritized_budget_factor']``,
Default 0,5), statt entweder jeden Lauf voll zu suchen oder nach einer einzigen Beobachtung
komplett zu verschwinden. Fix Punkt 3 (``expires_after_runs`` getrennt je Ursache) und Fix Punkt 4
(Report-Sektion ``diagnosed_pairs``) runden den Closed Loop ab.
"""
from unittest.mock import patch

import pytest

from automation.optimizer.sweep_diagnostics import recommend_diagnosis_action
from automation.optimizer import report as _report


def _diag(cause="signal_quality"):
    return {"binding_cause": cause, "median_oos_trades": 214, "median_is_trades": None}


# ── Akzeptanzkriterium #830 (Unit-Test aus dem Issue-Text) ──────────────────────────────────────────
def test_max_consecutive_structural_runs_threshold_is_configurable():
    """Issue #911 Fix 1 — die Konsekutiv-Laeufe-Schwelle ist jetzt ein Parameter statt eines
    eingefrorenen Literals. n_runs_confirmed=1 reicht nicht unter dem Default (2), aber genuegt
    unter einer abgesenkten Schwelle von 1."""
    rec_default = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(cause="signal_absent"),
        n_runs_confirmed=1, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
    )
    assert rec_default["action"] == "none"

    rec_lowered = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(cause="signal_absent"),
        n_runs_confirmed=1, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
        max_consecutive_structural_runs=1,
    )
    assert rec_lowered["action"] == "denylist"


def test_signal_quality_with_zero_confirmations_yields_none():
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=0,
    )
    assert rec["action"] == "none"


def test_signal_quality_with_two_confirmations_and_full_budget_escalates_to_quarantine():
    """Issue #911 (wichtigste Aussage des Katalogs) — 'signal_quality' eskaliert NICHT MEHR auf
    'denylist': der Closed-Loop-Rueckschrieb ist bis nach dem #897-Kalibrierlauf ausgesetzt (eine
    durch die #897-Sperrklinke zerstoerte Trade-Oekonomie kann keinen eligiblen Trial erzeugen,
    unabhaengig vom Suchraum — ein Denylist-Rueckschrieb auf dieser Basis wuerde funktionierende
    Strategien dauerhaft ausschliessen). Volle Evidenz eskaliert stattdessen auf
    'quarantined_pending_simulation_review' (protokolliert, aber NICHT auf der Denylist)."""
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=2, budget_executed_fraction=1.0,
        simulation_semantics_version=2,
    )
    assert rec["action"] == "quarantined_pending_simulation_review"
    assert rec["diagnosed_simulation_semantics_version"] == 2


# ── Fix Punkt 2: Zwischenklasse 'deprioritized' ──────────────────────────────────────────────────
def test_single_confirmation_yields_deprioritized_not_denylist():
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=1, budget_executed_fraction=1.0,
    )
    assert rec["action"] == "deprioritized"


def test_two_confirmations_but_insufficient_budget_stays_deprioritized():
    """n_runs_confirmed>=1, aber budget < 0.9 -- noch NICHT die volle Evidenz fuer 'denylist',
    faellt aber NICHT auf 'none' zurueck (das Paar ist ja bereits mindestens einmal bestaetigt)."""
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=2, budget_executed_fraction=0.5,
    )
    assert rec["action"] == "deprioritized"


def test_confirmed_with_missing_budget_fraction_stays_deprioritized():
    rec = recommend_diagnosis_action(
        "OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag(),
        n_runs_confirmed=3, budget_executed_fraction=None,
    )
    assert rec["action"] == "deprioritized"


def test_signal_absent_unaffected_by_the_830_deprioritized_branch():
    """'signal_absent' bleibt binaer (denylist/none) -- 'deprioritized' ist SPEZIFISCH fuer
    'signal_quality' (#830-Root-Cause: hier ist ein Regime-Urteil wahrscheinlicher als bei
    parameterunabhaengiger struktureller Abwesenheit)."""
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
        n_runs_confirmed=1, budget_executed_fraction=0.28, stop_reason="STRUCTURAL_ALL_UNEVALUABLE",
    )
    assert rec["action"] != "deprioritized"


# ── Fix Punkt 3: expires_after_runs getrennt je Ursache ────────────────────────────────────────────
def test_expires_after_runs_is_ten_for_signal_absent():
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_absent", "median_oos_trades": 0, "median_is_trades": 0},
    )
    assert rec["expires_after_runs"] == 10


def test_expires_after_runs_is_three_for_signal_quality():
    rec = recommend_diagnosis_action("OpeningRangeBreakoutStrategy", "TSLA.ETORO", _diag())
    assert rec["expires_after_runs"] == 3


def test_expires_after_runs_falls_back_to_default_for_other_causes():
    rec = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_sparse", "median_oos_trades": 0, "median_is_trades": 3},
    )
    assert rec["expires_after_runs"] == 10


# ── run_optimization._apply_deprioritized_budget ─────────────────────────────────────────────────
def _cache(entry_action):
    return {("OpeningRangeBreakoutStrategy", "TSLA.ETORO"): {"action": entry_action}}


def test_deprioritized_pair_gets_scaled_budget():
    from automation.optimizer.run_optimization import _apply_deprioritized_budget
    with patch("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
              return_value=_cache("deprioritized")):
        n = _apply_deprioritized_budget("OpeningRangeBreakoutStrategy", "TSLA.ETORO", 100, {})
    assert n == 50  # Default-Faktor 0.5


def test_deprioritized_pair_respects_custom_factor():
    from automation.optimizer.run_optimization import _apply_deprioritized_budget
    with patch("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
              return_value=_cache("deprioritized")):
        n = _apply_deprioritized_budget(
            "OpeningRangeBreakoutStrategy", "TSLA.ETORO", 100, {"deprioritized_budget_factor": 0.25})
    assert n == 25


def test_deprioritized_budget_never_reaches_zero():
    from automation.optimizer.run_optimization import _apply_deprioritized_budget
    with patch("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
              return_value=_cache("deprioritized")):
        n = _apply_deprioritized_budget(
            "OpeningRangeBreakoutStrategy", "TSLA.ETORO", 1, {"deprioritized_budget_factor": 0.1})
    assert n == 1


def test_non_deprioritized_pair_budget_is_unchanged():
    from automation.optimizer.run_optimization import _apply_deprioritized_budget
    for action in ("denylist", "none", "search_space_override"):
        with patch("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
                  return_value=_cache(action)):
            assert _apply_deprioritized_budget(
                "OpeningRangeBreakoutStrategy", "TSLA.ETORO", 100, {}) == 100


def test_pair_absent_from_cache_budget_is_unchanged():
    from automation.optimizer.run_optimization import _apply_deprioritized_budget
    with patch("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache", return_value={}):
        assert _apply_deprioritized_budget("OpeningRangeBreakoutStrategy", "TSLA.ETORO", 100, {}) == 100


def test_cache_read_failure_is_fail_open():
    from automation.optimizer.run_optimization import _apply_deprioritized_budget
    with patch("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
              side_effect=OSError("boom")):
        assert _apply_deprioritized_budget("OpeningRangeBreakoutStrategy", "TSLA.ETORO", 100, {}) == 100


# ── Fix Punkt 4: Report-Sektion diagnosed_pairs ─────────────────────────────────────────────────────
def test_report_diagnosed_pairs_section_lists_all_entries_with_required_fields():
    fake_cache = {
        ("OpeningRangeBreakoutStrategy", "TSLA.ETORO"): {
            "strategy": "OpeningRangeBreakoutStrategy", "symbol": "TSLA.ETORO",
            "action": "deprioritized", "binding_cause": "signal_quality",
            "n_runs_confirmed": 1, "expires_after_runs": 3, "budget_executed_fraction": 1.0,
        },
        ("AdxAtrMomentumStrategy", "TSLA.ETORO"): {
            "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
            "action": "denylist", "binding_cause": "signal_absent",
            "n_runs_confirmed": 2, "expires_after_runs": 10, "budget_executed_fraction": 0.28,
        },
    }
    with patch("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
              return_value=fake_cache):
        section = _report._diagnosed_pairs_section()
    assert len(section) == 2
    by_strategy = {e["strategy"]: e for e in section}
    assert by_strategy["OpeningRangeBreakoutStrategy"]["action"] == "deprioritized"
    assert by_strategy["OpeningRangeBreakoutStrategy"]["expires_after_runs"] == 3
    assert by_strategy["AdxAtrMomentumStrategy"]["n_runs_confirmed"] == 2


def test_report_diagnosed_pairs_is_wired_into_cross_study(monkeypatch):
    monkeypatch.setattr("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
                        lambda *a, **k: {})
    report_dict = _report._build_report(
        [], run_id="testrun", started_at_utc=None, wallclock_s=None, cli_args=None)
    assert "diagnosed_pairs" in report_dict["cross_study"]
    assert report_dict["cross_study"]["diagnosed_pairs"] == []
