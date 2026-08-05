"""Issue #866 (P0, Katalog #866-#869, GitHub-Issue #760, Pitfall #278) — PROMOTE_GLOBAL_DEFAULT wird
mit der Such-Multiplizität der STUDY deflationiert, obwohl der Kandidat nicht aus der Suche stammt.

Symptom: drei archivierte Fälle (NKE/Sma DSR=0.0797, LLY/Vwap DSR=0.0526, LULU/FlashCrash DSR=0.0004)
scheiterten alle an der Deflation mit N=99/100/159 (der Trial-Zahl der jeweiligen Study) — 0
Promotionen im gesamten Lauf.

Root-Cause: der globale Default-Vektor (``confirm.py``, ``PROMOTE_GLOBAL_DEFAULT``-Route, #682/#783)
wird EINMALIG geprüft, ohne an der Trial-Selektion irgendeiner Study teilzunehmen. Eine Deflation mit
``N=deflation_n_family`` (der Study-Trial-Zahl) ist damit strukturell unerreichbar (``E[max₁]=0``
gegen ``E[max₁₅₉]≈2.8σ``), unabhängig von der tatsächlichen Kandidaten-Qualität.

Fix: ``deflation.resolve_promotion_multiplicity(route, ...)`` bindet die Multiplizität an die
``promotion_route`` — ``'global_default'`` verwendet ``n_global_default_candidates`` (den
Roster-Umfang: Anzahl der für dieses Symbol als Notfallkandidat geprüften Strategien), NICHT
``deflation_n_family``. Ein zusätzliches, absolutes Gate (``global_default_min_holdout_sortino``,
Default 0.5) verhindert, dass die gelockerte Multiplizität zu einer Hintertür für schwache
Kandidaten wird (der globale Default hat keinen Micro-Tuning-Anspruch).
"""
import json

import optuna
import pytest

from automation.optimizer import confirm as cmod
from automation.optimizer import deflation as dmod
from automation.optimizer import sweep as smod
from automation.optimizer.parsing import TournamentMetrics


# ── Unit-Ebene: deflation.resolve_promotion_multiplicity ────────────────────────────────────────
def test_resolve_multiplicity_per_symbol_tuned_uses_deflation_n_family():
    assert dmod.resolve_promotion_multiplicity(
        "per_symbol_tuned", deflation_n_family=99, n_global_default_candidates=3) == 99


def test_resolve_multiplicity_global_default_uses_roster_count_not_family():
    """AK — die globale Route ignoriert deflation_n_family komplett, selbst wenn übergeben."""
    assert dmod.resolve_promotion_multiplicity(
        "global_default", deflation_n_family=159, n_global_default_candidates=3) == 3


def test_resolve_multiplicity_none_inputs_default_to_zero():
    assert dmod.resolve_promotion_multiplicity("per_symbol_tuned") == 0
    assert dmod.resolve_promotion_multiplicity("global_default") == 0


def test_resolve_multiplicity_unknown_route_is_fail_loud():
    with pytest.raises(ValueError, match="promotion_route"):
        dmod.resolve_promotion_multiplicity("bogus_route")


# ── promotion_family_scope: 'route_dependent' ist ein gültiger dritter Wert ─────────────────────
def test_resolve_scope_accepts_route_dependent():
    assert smod._resolve_promotion_family_scope(
        {"promotion_family_scope": "route_dependent"}) == "route_dependent"


def test_run_confirm_and_export_accepts_route_dependent_scope():
    """AK #5 — 'route_dependent' darf NICHT an der defensiven family_scope-Prüfung in
    _run_confirm_and_export scheitern (dieselbe Prüfung, die frueher NUR 'per_strategy' zuliess)."""
    import inspect
    source = inspect.getsource(smod)
    assert '"per_strategy", "route_dependent"' in source


# ── End-to-End über confirm.confirm_per_symbol_promotion (0 eligible Trials ⇒ globale Route) ────
def _mk(*, sortino, ret, dd, sortino_period=0.2, n_periods=200, period_returns=()):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
        oos_sortino=sortino, oos_max_drawdown=dd, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret,
        oos_sortino_period=sortino_period, oos_n_periods=n_periods,
        oos_ret_skew=0.0, oos_ret_kurtosis=3.0, oos_period_returns=period_returns)


def _patch(tmp_path, monkeypatch, *, tournament_extra=None, optimizer_extra=None):
    tcfg = {"max_drawdown": 0.30, "holdout_top_k": 5, "deflated_selection": True,
            "deflation_confidence": 0.95, "deflation_min_cohort": 10}
    if tournament_extra:
        tcfg.update(tournament_extra)
    (tmp_path / "tournament.json").write_text(json.dumps(tcfg))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    ocfg = {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}
    if optimizer_extra:
        ocfg.update(optimizer_extra)
    (tmp_path / "optimizer.json").write_text(json.dumps(ocfg))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)


def _patch_budget_execution(monkeypatch, *, fraction: float = 1.0):
    monkeypatch.setattr(
        "automation.optimizer.run_optimization.compute_budget_execution",
        lambda *a, **k: {"budget_executed_fraction": fraction, "n_trials_budgeted": 100,
                         "n_trials_completed": int(fraction * 100), "stop_reason": "BUDGET_EXHAUSTED",
                         "n_modelled_trials_completed": 0},
    )


def _empty_study():
    return optuna.create_study(direction="maximize")


def test_global_default_promoted_when_roster_count_used_instead_of_study_trials(tmp_path, monkeypatch):
    """AK #1 — derselbe Kandidat (sortino_period=0.2, n_periods=200), der unter der ALTEN,
    Study-Trial-Zahl-basierten Deflation (N=159) an der DSR gescheitert wäre (DSR=0.5545 < 0.95),
    wird PROMOTET, sobald die korrekte route-abhängige Multiplizität (N=3, der Roster-Umfang)
    verwendet wird — deflation_n_family=159 wird übergeben, aber IGNORIERT."""
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=4.0, ret=0.20, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.71)

    res = cmod.confirm_per_symbol_promotion(
        _empty_study(), "FlashCrashReversalStrategy", "LULU.ETORO", {"p": 0},
        deflation_n_family=159, n_global_default_candidates=3)

    assert res["promote"] is True
    assert res["status"] == "PROMOTE_GLOBAL_DEFAULT"
    assert res["metrics_symbol"]["deflation_n_family"] == 3
    assert res["metrics_symbol"]["deflated_dsr"] >= 0.95


def test_global_default_rejected_when_deflated_with_study_trial_count(tmp_path, monkeypatch):
    """Gegenprobe — mit der (jetzt verworfenen) Study-Trial-Zahl als Multiplizität (N=159) scheitert
    exakt derselbe Kandidat an der DSR (das archivierte #866-Symptom, reproduziert)."""
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=4.0, ret=0.20, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.71)

    res = cmod.confirm_per_symbol_promotion(
        _empty_study(), "FlashCrashReversalStrategy", "LULU.ETORO", {"p": 0},
        deflation_n_family=159, n_global_default_candidates=159)

    assert res["promote"] is False
    assert res["status"] == "REJECTED_ON_HOLDOUT"
    assert res["is_rejection_detail_override"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"


def test_global_default_telemetry_carries_multiplicity_rationale(tmp_path, monkeypatch):
    """AK #4/#6 — deflation_n_family_raw/deflation_n_effective/deflation_multiplicity_rationale sind
    im Holdout-Record vorhanden und benennen die route='global_default'-Herkunft."""
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=4.0, ret=0.20, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.71)

    res = cmod.confirm_per_symbol_promotion(
        _empty_study(), "FlashCrashReversalStrategy", "LULU.ETORO", {"p": 0},
        deflation_n_family=159, n_global_default_candidates=3)

    ms = res["metrics_symbol"]
    assert ms["deflation_n_family_raw"] == 3
    assert ms["deflation_n_effective"] == 3
    assert "global_default" in ms["deflation_multiplicity_rationale"]
    assert "3" in ms["deflation_multiplicity_rationale"]


def test_missing_n_global_default_candidates_disables_deflation_check_not_crash(tmp_path, monkeypatch):
    """Legacy-Aufrufer (kein n_global_default_candidates übergeben) — die Deflation der globalen
    Route bleibt inaktiv (N=0 ⇒ Gate übersprungen), bit-identisch zum Pre-#866-Verhalten für einen
    Aufrufer, der den neuen Parameter nicht kennt. Kein Crash, kein KeyError."""
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=4.0, ret=0.20, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.71)

    res = cmod.confirm_per_symbol_promotion(
        _empty_study(), "FlashCrashReversalStrategy", "LULU.ETORO", {"p": 0})

    assert res["promote"] is True
    assert res["metrics_symbol"].get("deflated_sr0") is None
    assert res["metrics_symbol"]["deflation_n_family_raw"] == 0


# ── global_default_min_holdout_sortino: strengeres absolutes Gate für die globale Route ─────────
def test_global_default_rejected_below_min_holdout_sortino_default(tmp_path, monkeypatch):
    """AK #4 — ein Default-Kandidat mit Holdout-Sortino 0.2 wird abgelehnt (Default-Schwelle 0.5),
    obwohl das reguläre Gate (nur oos_sortino > 0) selbst bestehen würde."""
    _patch(tmp_path, monkeypatch, tournament_extra={"deflated_selection": False})
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=0.2, ret=0.01, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 0.5)

    res = cmod.confirm_per_symbol_promotion(
        _empty_study(), "SmaCrossoverStrategy", "NKE.ETORO", {"p": 0},
        n_global_default_candidates=3)

    assert res["promote"] is False
    assert res["is_rejection_detail_override"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"


def test_global_default_promoted_above_min_holdout_sortino_default(tmp_path, monkeypatch):
    """Gegenprobe — derselbe Aufbau mit Sortino 0.6 (über der 0.5-Default-Schwelle) wird promotet."""
    _patch(tmp_path, monkeypatch, tournament_extra={"deflated_selection": False})
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=0.6, ret=0.01, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 0.5)

    res = cmod.confirm_per_symbol_promotion(
        _empty_study(), "SmaCrossoverStrategy", "NKE.ETORO", {"p": 0},
        n_global_default_candidates=3)

    assert res["promote"] is True
    assert res["status"] == "PROMOTE_GLOBAL_DEFAULT"


def test_global_default_min_holdout_sortino_is_configurable(tmp_path, monkeypatch):
    """Zero-Hardcoding — ein Betreiber kann die Schwelle über optimizer.json senken; derselbe
    Sortino-0.2-Kandidat, der unter dem Default (0.5) abgelehnt wird, wird unter 0.1 promotet."""
    _patch(tmp_path, monkeypatch, tournament_extra={"deflated_selection": False},
          optimizer_extra={"global_default_min_holdout_sortino": 0.1})
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=0.2, ret=0.01, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 0.5)

    res = cmod.confirm_per_symbol_promotion(
        _empty_study(), "SmaCrossoverStrategy", "NKE.ETORO", {"p": 0},
        n_global_default_candidates=3)

    assert res["promote"] is True


# ── Regressionswächter: die getunte Route bleibt UNVERÄNDERT bei deflation_n_family (AK #3) ─────
def test_tuned_route_still_uses_deflation_n_family_unchanged(tmp_path, monkeypatch):
    """AK #3 — ein Kandidat mit einem symbol-eligiblen Trial (die getunte Route) trägt weiterhin
    deflation_n_family als Multiplizität, UNBEEINFLUSST von n_global_default_candidates (das nur die
    GLOBALE Route betrifft) — Regressionstest gegen #826."""
    _patch(tmp_path, monkeypatch, tournament_extra={"deflated_selection": False})

    def fake_holdout(strategy, symbol, params, **kw):
        if params == {"p": 1}:
            return _mk(sortino=4.0, ret=0.30, dd=0.05)
        return _mk(sortino=0.2, ret=0.01, dd=0.05)

    def fake_reward(m, **kw):
        return 3.0 if m.oos_sortino == 4.0 else 0.1

    monkeypatch.setattr(cmod, "_holdout_metrics_for_params", fake_holdout)
    monkeypatch.setattr(cmod, "compute_reward", fake_reward)

    study = _empty_study()
    t = study.ask()
    t.set_user_attr("oos_eligible", True)
    t.set_user_attr("sampled_params", {"p": 1})
    study.tell(t, 3.0)

    res = cmod.confirm_per_symbol_promotion(
        study, "TestStrategy", "TSLA.ETORO", {"p": 0},
        deflation_n_family=42, n_global_default_candidates=3)

    assert res["promote"] is True
    assert res["status"] == "READY_FOR_PR"
    assert res["promotion_route"] != "global_default_on_symbol"
