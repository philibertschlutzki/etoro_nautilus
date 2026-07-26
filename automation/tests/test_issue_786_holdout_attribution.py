"""Issue #786 (P1) — `REJECT_HOLDOUT_GATE` ist die dominante Ablehnungsursache (239 von 321 Studies
mit eligiblen Trials) und wird nicht aufgeschlüsselt.

Root-Cause: `near_miss_deltas` im Study-Record stammt aus `best_trial.user_attrs['oos_gate_deltas']`
(den OOS-FOLDS), nicht aus dem HOLDOUT-Fenster, auf dem die Promotion tatsächlich abgelehnt wird.

Fix: `confirm._holdout_binding_gate` + `_metrics_dict` liefern `holdout_gate_deltas` (dieselbe
Struktur wie `oos_gate_deltas`, auf dem Holdout-Backtest geparst) und `holdout_binding_gate` (das
Gate mit dem negativsten normierten Delta) für JEDE Study mit einem erreichten Holdout-Lauf.
"""
from automation.optimizer.confirm import _holdout_binding_gate, _metrics_dict
from automation.optimizer.parsing import TournamentMetrics


def _mk(**kw):
    base = dict(oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
                oos_sortino=0.1, oos_max_drawdown=0.05, oos_total_trades=30, win_count=1,
                fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.01)
    base.update(kw)
    return TournamentMetrics(**base)


def test_binding_gate_is_the_most_negative_numeric_delta():
    deltas = {"oos_min_psr": -0.02, "oos_max_drawdown": 0.10, "oos_min_excess_return": -0.15}
    assert _holdout_binding_gate(deltas) == "oos_min_excess_return"


def test_binding_gate_none_for_empty_or_missing_deltas():
    assert _holdout_binding_gate({}) is None
    assert _holdout_binding_gate(None) is None


def test_binding_gate_ignores_non_numeric_entries():
    deltas = {"oos_min_psr": -0.02, "some_flag": True, "oos_min_trades": None}
    assert _holdout_binding_gate(deltas) == "oos_min_psr"


def test_synthetic_drawdown_only_binding_scenario():
    """Akzeptanzkriterium #786/3: synthetischer Holdout, der nur am Drawdown scheitert ⇒
    holdout_binding_gate == 'max_drawdown'-Aequivalent (hier oos_max_drawdown, die
    oos_-praefigierte Spaltenkonvention von oos_gate_deltas)."""
    deltas = {"oos_min_psr": 0.05, "oos_min_excess_return": 0.10, "oos_max_drawdown": -0.20,
              "oos_min_trades": 0.30}
    assert _holdout_binding_gate(deltas) == "oos_max_drawdown"


def test_metrics_dict_carries_holdout_gate_deltas():
    m = _mk(oos_gate_deltas={"oos_min_psr": -0.03})
    d = _metrics_dict(m)
    assert d["holdout_gate_deltas"] == {"oos_min_psr": -0.03}


def test_metrics_dict_defaults_to_empty_dict_when_absent():
    m = _mk()
    d = _metrics_dict(m)
    assert d["holdout_gate_deltas"] == {}


def test_confirm_per_symbol_promotion_attaches_holdout_binding_gate(tmp_path, monkeypatch):
    import json
    import optuna
    from automation.optimizer import confirm as cmod

    (tmp_path / "tournament.json").write_text(json.dumps(
        {"max_drawdown": 0.30, "holdout_top_k": 5, "deflated_selection": False}))
    (tmp_path / "backtest.json").write_text(json.dumps(
        {"walk_forward": {"is_window_days": 100, "holdout_days": 10, "oos_window_days": 10,
                          "splits": 1, "embargo_period_days": 0}}))
    (tmp_path / "optimizer.json").write_text(json.dumps(
        {"promotion_margin": 0.10, "oos_sortino_fallback": "total_return"}))
    monkeypatch.setattr(cmod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cmod, "emit_execution_event", lambda *a, **k: None)

    def fake_holdout(strategy, symbol, params, **kw):
        return _mk(oos_sortino=-0.5, oos_total_return=-0.02,
                   oos_gate_deltas={"oos_min_psr": -0.30, "oos_max_drawdown": 0.05})

    monkeypatch.setattr(cmod, "_holdout_metrics_for_params", fake_holdout)
    monkeypatch.setattr(cmod, "compute_reward", lambda m, **kw: 1.0)

    study = optuna.create_study(direction="maximize")
    t = study.ask()
    t.set_user_attr("oos_eligible", True)
    t.set_user_attr("sampled_params", {})
    study.tell(t, 1.0)

    res = cmod.confirm_per_symbol_promotion(study, "TestStrategy", "TSLA.ETORO", {})
    assert res["metrics_symbol"]["holdout_binding_gate"] == "oos_min_psr"
    assert res["metrics_symbol"]["holdout_gate_deltas"]["oos_min_psr"] == -0.30
