"""Issue #783 (P0) — Alle 37 Promotionen im `#742`-Report stammten aus der
`global_default_on_symbol`-Route; der Report konnte sie nicht von einer echten Promotion
unterscheiden (dieselbe Statuszeichenkette `READY_FOR_PR`, `n_eligible=0`, `best_reward=null`,
`inference_method.promotion=null`, `rejection_chain` fehlte die `confirm_or_selection`-Stufe).

Siehe auch: `test_issue_682_global_default_promotion.py` (AC 1/2/4), `test_issue_785_decision_chain.py`
(AC 5 — decision_chain), `test_issue_786_holdout_attribution.py`. Dieser Test deckt spezifisch
Akzeptanzkriterium #3: jeder Report-Record mit `promote=True` trägt eine nicht-leere
`promotion_route`.
"""
import json

import optuna

from automation.optimizer import confirm as cmod
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.report import _study_record


def _mk(*, sortino, ret, dd):
    # Issue #958 — oos_n_periods > 0 macht diesen Fixture-Trial admissible (ein real evaluierter
    # Trial mit 30 Trades haette in der Praxis stets > 0 OOS-Perioden).
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
        oos_sortino=sortino, oos_max_drawdown=dd, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=ret,
        oos_n_periods=50)


def _patch(tmp_path, monkeypatch):
    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "holdout_top_k": 5, "deflated_selection": False}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps(
        {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)


def test_regular_per_symbol_promotion_has_a_non_empty_promotion_route(tmp_path, monkeypatch):
    """Akzeptanzkriterium #783/3 — auch der GEWOEHNLICHE (holdout-validierte) Promotions-Pfad
    traegt jetzt explizit eine promotion_route, nicht nur die #682-Default-Route."""
    _patch(tmp_path, monkeypatch)

    def fake_holdout(strategy, symbol, params, **kw):
        # Symbol-getunter Vektor ({"p": 1}) schlaegt den globalen Default ({"p": 0}) klar.
        if params == {"p": 1}:
            return _mk(sortino=4.0, ret=0.30, dd=0.05)
        return _mk(sortino=0.2, ret=0.01, dd=0.05)

    def fake_reward(m, **kw):
        return 3.0 if m.oos_sortino == 4.0 else 0.1

    monkeypatch.setattr(cmod, "_holdout_metrics_for_params", fake_holdout)
    monkeypatch.setattr(cmod, "compute_reward", fake_reward)

    study = optuna.create_study(direction="maximize")
    t = study.ask()
    t.set_user_attr("oos_eligible", True)
    t.set_user_attr("sampled_params", {"p": 1})
    study.tell(t, 3.0)

    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {"p": 0})
    assert res["promote"] is True
    assert res["status"] == "READY_FOR_PR"
    assert res.get("promotion_route")
    assert res["promotion_route"] != "global_default_on_symbol"


def test_rejected_candidate_has_no_promotion_route(tmp_path, monkeypatch):
    _patch(tmp_path, monkeypatch)
    monkeypatch.setattr(cmod, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **kw: _mk(sortino=-0.5, ret=-0.02, dd=0.05))
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: -0.5)

    study = optuna.create_study(direction="maximize")
    t = study.ask()
    t.set_user_attr("oos_eligible", True)
    t.set_user_attr("sampled_params", {"p": 1})
    study.tell(t, -0.5)

    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {"p": 0})
    assert res["promote"] is False
    assert not res.get("promotion_route")


def test_report_record_promotion_route_field_propagates_from_proposal():
    """report._study_record muss promotion_route aus dem Proposal in JEDES Artefakt uebernehmen
    (Root-Cause #783: report._study_record uebernahm promotion_outcome, aber nicht
    promotion_route)."""
    class _S:
        trials = []
        best_value = None
        user_attrs = {}

    proposal = {
        "symbol": "TSLA.ETORO", "strategy": "TestStrategy", "status": "PROMOTE_GLOBAL_DEFAULT",
        "promotion_route": "global_default_on_symbol", "holdout_reject_detail": None,
    }
    record, _checks = _study_record(proposal, _S())
    assert record["promotion_route"] == "global_default_on_symbol"
    assert record["promotion_outcome"] == "PROMOTE_GLOBAL_DEFAULT"
