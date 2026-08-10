"""Issue #887 (P0) — ``PROMOTE_GLOBAL_DEFAULT`` wird mit der Such-Multiplizität deflationiert.

Der globale Default (Route ``global_default_on_symbol``, #682/#783 — kein einziger symbol-eligibler
Trial) nahm bislang mit der VOLLEN Stufe-1-Familiengrösse (``deflation_n_family``, z. B. 99 oder
117) an der Deflation teil, obwohl er an dieser Selektion nie teilgenommen hat — die
Deflationsschwelle wurde damit strukturell unerreichbar (E[max_N] wächst mit ``sqrt(2 ln N)``).

Fix: ``confirm.resolve_promotion_multiplicity`` ist die EINE Quelle für ``N`` — für die
``global_default``-Route immer ``N=1``. Eine neue Schranke (``global_default_min_holdout_sortino``)
verhindert, dass die Lockerung selbst zur Hintertür wird. ``check_promotion_multiplicity_route``
ist der Regressionswächter dagegen, dass ein anderer Codepfad die volle Familiengrösse erneut
einschleust.
"""
import json

import optuna
import pytest

from automation.optimizer import confirm as cmod
from automation.optimizer import invariants as inv
from automation.optimizer.parsing import TournamentMetrics


def _mk(*, sortino, ret, dd, sortino_period=None, n_periods=0, period_returns=()):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
        oos_sortino=sortino, oos_max_drawdown=dd, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret,
        oos_sortino_period=sortino_period, oos_n_periods=n_periods,
        oos_period_returns=tuple(period_returns),
    )


def _empty_study():
    return optuna.create_study(direction="maximize")


def _patch(tmp_path, monkeypatch, *, deflated_selection=False,
          global_default_min_holdout_sortino=None):
    cfg = {"max_drawdown": 0.30, "holdout_top_k": 5, "deflated_selection": deflated_selection,
           "deflation_confidence": 0.95, "deflation_min_cohort": 10}
    if global_default_min_holdout_sortino is not None:
        cfg["global_default_min_holdout_sortino"] = global_default_min_holdout_sortino
    (tmp_path / "tournament.json").write_text(json.dumps(cfg))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps(
        {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)


def _patch_budget_execution(monkeypatch, *, fraction: float):
    monkeypatch.setattr(
        "automation.optimizer.run_optimization.compute_budget_execution",
        lambda *a, **k: {"budget_executed_fraction": fraction, "n_trials_budgeted": 100,
                         "n_trials_completed": int(fraction * 100), "stop_reason": "BUDGET_EXHAUSTED",
                         "n_modelled_trials_completed": 0},
    )


# ── resolve_promotion_multiplicity: reine Funktion ───────────────────────────────────────────────
def test_global_default_route_always_returns_n_equal_one():
    n, rationale = cmod.resolve_promotion_multiplicity("global_default", deflation_n_family=99)
    assert n == 1
    assert rationale["route"] == "global_default"
    assert rationale["n_family_available"] == 99
    assert rationale["n_used"] == 1


def test_symbol_eligible_route_is_bit_identical_to_deflation_n_family():
    n, rationale = cmod.resolve_promotion_multiplicity("symbol_eligible", deflation_n_family=99)
    assert n == 99
    assert rationale["route"] == "symbol_eligible"
    assert rationale["n_used"] == 99


def test_unknown_route_raises_fail_loud():
    with pytest.raises(ValueError, match="resolve_promotion_multiplicity"):
        cmod.resolve_promotion_multiplicity("bogus_route", deflation_n_family=10)


def test_missing_deflation_n_family_defaults_to_zero():
    n, rationale = cmod.resolve_promotion_multiplicity("global_default", deflation_n_family=None)
    assert n == 1
    assert rationale["n_family_available"] == 0


# ── confirm_per_symbol_promotion: die #887-Regression selbst ────────────────────────────────────
def test_global_default_with_deflation_uses_n_family_one_not_the_full_cohort(tmp_path, monkeypatch):
    """Der Kern-Regressionsbeleg: ein globaler Default mit DSR=0.0797 bei N=99 (das reale
    Issue-Symptom) waere unter der alten Multiplizitaet abgelehnt worden; mit N=1 ist SR0 nahe 0
    und ein klar positiver Holdout-Sortino besteht die Deflation."""
    _patch(tmp_path, monkeypatch, deflated_selection=True)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(
                            sortino=4.64, ret=0.20, dd=0.05, sortino_period=0.9, n_periods=250))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.71)

    study = _empty_study()
    res = cmod.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "NKE.ETORO", {"p": 0},
        deflation_n_family=99)  # die reale Stufe-1-Familiengroesse aus dem Issue-Symptom

    assert res["promote"] is True
    assert res["status"] == "PROMOTE_GLOBAL_DEFAULT"
    metrics = res["metrics_symbol"]
    assert metrics["deflation_n_family"] == 1
    assert metrics["deflation_multiplicity_rationale"]["route"] == "global_default"
    assert metrics["deflation_multiplicity_rationale"]["n_family_available"] == 99


def test_global_default_rejected_below_min_holdout_sortino_even_without_deflation(tmp_path, monkeypatch):
    """Issue #887 Fix Punkt 3 — die Lockerung auf N=1 darf keine Hintertuer werden: ein globaler
    Default mit einem Holdout-Sortino unter der Schranke wird abgelehnt, UNABHAENGIG davon, ob
    deflated_selection ueberhaupt aktiv ist."""
    _patch(tmp_path, monkeypatch, deflated_selection=False,
          global_default_min_holdout_sortino=0.5)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    # Ein positiver, aber schwacher Holdout-Sortino (0.2 < 0.5) besteht das Holdout-Gate
    # (>0.0) und haette den globalen Default vor #887 ohne Zusatzschranke promotet.
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=0.2, ret=0.05, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 0.5)

    study = _empty_study()
    res = cmod.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "NKE.ETORO", {"p": 0})

    assert res["promote"] is False
    assert res["is_rejection_detail_override"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"


def test_global_default_promoted_above_min_holdout_sortino(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch, deflated_selection=False,
          global_default_min_holdout_sortino=0.5)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=4.64, ret=0.20, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.71)

    study = _empty_study()
    res = cmod.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "NKE.ETORO", {"p": 0})
    assert res["promote"] is True
    assert res["status"] == "PROMOTE_GLOBAL_DEFAULT"


# ── invariants.check_promotion_multiplicity_route ────────────────────────────────────────────────
def test_multiplicity_check_not_applicable_to_regular_route():
    result = inv.check_promotion_multiplicity_route(
        {"promotion_route": "per_symbol_holdout_validated"})
    assert result.passed is True


def test_multiplicity_check_passes_at_n_equal_one():
    proposal = {"promotion_route": "global_default_on_symbol",
                "holdout": {"symbol": {"deflation_n_family": 1}}}
    result = inv.check_promotion_multiplicity_route(proposal)
    assert result.passed is True


def test_multiplicity_check_fails_when_full_family_leaks_into_global_default_route():
    """Regressionswächter: taucht die volle Stufe-1-Familiengroesse (> 1) erneut auf der
    global_default-Route auf, wurde resolve_promotion_multiplicity umgangen."""
    proposal = {"promotion_route": "global_default_on_symbol",
                "holdout": {"symbol": {"deflation_n_family": 99}}}
    result = inv.check_promotion_multiplicity_route(proposal)
    assert result.passed is False
    assert result.actual == 99


def test_multiplicity_check_passes_without_any_deflation_telemetry():
    proposal = {"promotion_route": "global_default_on_symbol", "holdout": {"symbol": {}}}
    result = inv.check_promotion_multiplicity_route(proposal)
    assert result.passed is True
