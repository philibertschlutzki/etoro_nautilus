"""Issue #682 — ein global-eligibler Default ohne symbol-eligiblen Trial darf nicht lautlos als
HOLDOUT_NO_ELIGIBLE_TRIALS enden.

Symptom: VolatilityBreakoutPumpStrategy bestand auf TSLA das Symbol-Holdout-Gate mit dem UNGETUNTEN
globalen Default (Sortino 4.64, R_global=+1.71), aber die Per-Symbol-Study fand 0 eligible Trials
⇒ HOLDOUT_NO_ELIGIBLE_TRIALS — ein global-viabler Kandidat verschwand lautlos.

Fix (#682): besteht der globale Vektor das Symbol-Holdout-Gate SELBST und ist sein Reward positiv,
wird er als Symbol-Kandidat promotet (R_symbol := R_global).

Issue #783 — DREI Härtungen, nachdem der #742-Report zeigte, dass ALLE 37 promoteten Studies dieser
Route aus einem vorzeitigen #768-Plateau-Abbruch stammten und im Report von einer echten,
holdout-validierten Promotion nicht unterscheidbar waren:
  1. EIGENER Status ``PROMOTE_GLOBAL_DEFAULT`` (nicht ``READY_FOR_PR``).
  2. Budget-Vorbedingung: die Route ist nur zulässig, wenn ``budget_executed_fraction`` über der
     deklarativen Schwelle (Default 0.9) liegt.
  3. Deflation auf R_global mit familienweiter Multiplizität (theoretische Referenzvarianz).
"""
import json

import optuna

from automation.optimizer import confirm as cmod
from automation.optimizer.parsing import TournamentMetrics


def _mk(*, sortino, ret, dd, evaluated=True, eligible=True):
    m = TournamentMetrics(
        oos_evaluated=evaluated, oos_eligible=eligible, is_sortino_median=None,
        oos_sortino=sortino, oos_max_drawdown=dd, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret)
    return m


def _empty_study():
    return optuna.create_study(direction="maximize")


def _patch(tmp_path, monkeypatch, *, top_k=5):
    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "holdout_top_k": top_k, "deflated_selection": False}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps(
        {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)


def _patch_budget_execution(monkeypatch, *, fraction: float):
    """Issue #783 — ``compute_budget_execution`` wird von ``confirm.py`` LAZY aus
    ``run_optimization`` importiert; hier direkt auf der Quelle gepatcht (einfacher/robuster als
    echte Optuna-Trials in die leere Test-Study einzufügen)."""
    monkeypatch.setattr(
        "automation.optimizer.run_optimization.compute_budget_execution",
        lambda *a, **k: {"budget_executed_fraction": fraction, "n_trials_budgeted": 100,
                         "n_trials_completed": int(fraction * 100), "stop_reason": "BUDGET_EXHAUSTED",
                         "n_modelled_trials_completed": 0},
    )


def test_global_default_promoted_when_no_symbol_eligible_trials(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    events = []
    monkeypatch.setattr(cmod, "emit_execution_event",
                        lambda logger, name, payload: events.append((name, payload)))

    def fake_holdout(strategy, symbol, params, **kw):
        # Der globale Vektor (params == global_params, hier {}) besteht das Symbol-Holdout klar.
        return _mk(sortino=4.64, ret=0.20, dd=0.05)

    def fake_reward(m, **kw):
        return 1.71  # R_global aus dem Issue-Symptom

    monkeypatch.setattr(cmod, "_holdout_metrics_for_params", fake_holdout)
    monkeypatch.setattr(cmod, "compute_reward", fake_reward)

    study = _empty_study()  # 0 Trials ⇒ 0 eligible
    global_params = {"bb_period": 20, "bb_std_dev": 2.0}
    res = cmod.confirm_per_symbol_promotion(study, "VolatilityBreakoutPumpStrategy", "TSLA.ETORO",
                                            global_params)

    assert res["promote"] is True
    # Issue #783 — EIGENER Status, NICHT READY_FOR_PR (das bleibt fuer holdout-validierte
    # Symbol-Kandidaten reserviert).
    assert res["status"] == "PROMOTE_GLOBAL_DEFAULT"
    assert res["is_rejection_detail_override"] is None
    assert res["promotion_route"] == "global_default_on_symbol"
    assert res["symbol_params"] == global_params
    assert res["R_symbol"] == res["R_global"] == 1.71
    assert res["holdout_passed"] is True
    # Explizite Entscheidung geloggt, KEIN stilles HOLDOUT_NO_ELIGIBLE_TRIALS.
    assert any(name == "PROMOTE_GLOBAL_DEFAULT_ON_SYMBOL" for name, _ in events)
    assert not any(name == "HOLDOUT_NO_ELIGIBLE_TRIALS" for name, _ in events)


def test_global_default_not_promoted_when_budget_execution_insufficient(tmp_path, monkeypatch):
    """Akzeptanzkriterium #783/2: budget_executed_fraction=0.45 ⇒ keine #682-Promotion — ein
    Plateau-Abbruch (#768/#769) darf keine Deployment-Entscheidung auslösen."""
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=0.45)
    events = []
    monkeypatch.setattr(cmod, "emit_execution_event",
                        lambda logger, name, payload: events.append((name, payload)))
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=4.64, ret=0.20, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.71)

    study = _empty_study()
    res = cmod.confirm_per_symbol_promotion(study, "VolatilityBreakoutPumpStrategy", "TSLA.ETORO", {})

    assert res["promote"] is False
    # Issue #1002/#1154 (Katalog #1170) — "kein eligibler Trial" erreicht die Holdout-Stufe nie.
    assert res["status"] == "REJECTED_BEFORE_HOLDOUT"
    assert res["is_rejection_detail_override"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"
    assert not any(name == "PROMOTE_GLOBAL_DEFAULT_ON_SYMBOL" for name, _ in events)


def test_global_default_not_promoted_when_holdout_gate_fails(tmp_path, monkeypatch):
    """Scheitert der globale Vektor SELBST am Symbol-Holdout-Gate (z. B. negativer Sortino), bleibt
    es beim regulären fail-loud HOLDOUT_NO_ELIGIBLE_TRIALS — keine unbegründete Promotion."""
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    events = []
    monkeypatch.setattr(cmod, "emit_execution_event",
                        lambda logger, name, payload: events.append((name, payload)))

    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=-0.5, ret=-0.02, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: -0.3)

    study = _empty_study()
    res = cmod.confirm_per_symbol_promotion(study, "VolatilityBreakoutPumpStrategy", "TSLA.ETORO", {})

    assert res["promote"] is False
    # Issue #1002/#1154 (Katalog #1170) — "kein eligibler Trial" erreicht die Holdout-Stufe nie.
    assert res["status"] == "REJECTED_BEFORE_HOLDOUT"
    assert res["is_rejection_detail_override"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"
    assert "promotion_route" not in res or res.get("promotion_route") is None
    assert any(name == "HOLDOUT_NO_ELIGIBLE_TRIALS" for name, _ in events)
    assert not any(name == "PROMOTE_GLOBAL_DEFAULT_ON_SYMBOL" for name, _ in events)


def test_global_default_not_promoted_when_reward_non_positive(tmp_path, monkeypatch):
    """Der globale Vektor besteht das Punkt-Gate (Sortino > 0, DD im Rahmen), aber sein Reward ist
    NICHT positiv (z. B. eine grosse Divergenz-/DD-Strafe) ⇒ keine Promotion ohne echten Edge."""
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=0.2, ret=0.01, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: -0.1)

    study = _empty_study()
    res = cmod.confirm_per_symbol_promotion(study, "VolatilityBreakoutPumpStrategy", "TSLA.ETORO", {})
    assert res["promote"] is False
    assert res["is_rejection_detail_override"] == "HOLDOUT_NO_ELIGIBLE_TRIALS"


def test_exported_proposal_carries_promotion_route(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    _patch_budget_execution(monkeypatch, fraction=1.0)
    monkeypatch.setattr(cmod, "WORK", tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=4.64, ret=0.20, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.71)

    study = _empty_study()
    global_params = {"bb_period": 20}
    res = cmod.confirm_per_symbol_promotion(study, "VolatilityBreakoutPumpStrategy", "TSLA.ETORO",
                                            global_params)
    from pathlib import Path
    p = cmod.export_symbol_proposal(study, "VolatilityBreakoutPumpStrategy", "TSLA.ETORO", res)
    written = json.loads(Path(p).read_text("utf-8"))
    assert written["status"] == "PROMOTE_GLOBAL_DEFAULT"
    assert written["promotion_route"] == "global_default_on_symbol"
    assert written["proposed_instrument_override"] == global_params
