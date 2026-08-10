"""Issue #655 — `R_global = -20.0`-Sentinel verunreinigt Cross-Strategy-Vergleichbarkeit.

AdxAtr und TrendPullback (0 eligible Trials) exportierten `R_global = -20.0`, `R_symbol = 0.0`,
`reward = null`. Die Magic-Zahl -20 (= `penalty_unevaluable_oos`) leakte in jede
Cross-Strategy-`R_global`-Aggregation und in den globalen Baseline-Bezug
(`R_symbol > R_global + margin`), weil sie ununterscheidbar von einem echten, sehr schlechten
Reward war.

Fix: `compute_reward(..., holdout=True)` liefert `None` (statt der numerischen
Unevaluable-Shaping-Formel) für einen Holdout-Backtest, der NIE OOS-evaluierbar war — die
IS-Eligibility-Shaping-Logik ist im Holdout-Kontext (ein abgeschlossener Einzellauf, kein
Optimierungsschritt) ohnehin kategorial fehl am Platz. `confirm.py` behandelt `R_global`/`R_symbol`
seither explizit `None`-safe statt sie als Zahl zu vergleichen.
"""
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config

_WEIGHTS = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "sortino_clip_abs": 5.0, "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0,
    "bonus_coverage_weight": 1.0,
}


def _unevaluated_metrics() -> "TournamentMetrics":
    return TournamentMetrics(
        oos_evaluated=False, oos_eligible=False, is_sortino_median=0.0,
        oos_sortino=None, oos_max_drawdown=0.0, oos_total_trades=0, win_count=0,
        fully_eligible_pairs=0, is_total_trades=0, oos_total_return=0.0,
    )


# ── Kernkriterium (#655): Holdout + nie evaluiert ⇒ None, NICHT die alte -20-Zahl ────────────────
def test_holdout_unevaluated_returns_none_not_numeric_sentinel():
    m = _unevaluated_metrics()
    reward = compute_reward(m, universe_size=1, weights=_WEIGHTS, holdout=True)
    assert reward is None


def test_holdout_unevaluated_returns_none_with_return_terms():
    m = _unevaluated_metrics()
    reward, terms = compute_reward(m, universe_size=1, weights=_WEIGHTS, holdout=True,
                                   return_terms=True)
    assert reward is None
    assert terms["branch"] == "unevaluated_holdout"


def test_non_holdout_unevaluated_path_unchanged():
    """Der IS/Study-Pfad (holdout=False) bleibt UNVERÄNDERT numerisch geshaped — dort braucht der
    TPE-Sampler den Gradienten Richtung Eligibility (Pitfall #75), das #655-Fix betrifft
    AUSSCHLIESSLICH den Holdout-Pfad."""
    m = _unevaluated_metrics()
    reward = compute_reward(m, universe_size=1, weights=_WEIGHTS, holdout=False)
    assert reward is not None
    assert reward == pytest.approx(-20.0, abs=0.3)  # penalty_unevaluable_oos + kleines Shaping


def test_evaluated_holdout_still_returns_numeric_reward():
    """Ein ECHT evaluierter Holdout-Lauf liefert weiterhin eine Zahl (kein Über-Fix, der jeden
    Holdout-Reward auf None kollabieren lässt)."""
    m = TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
        oos_sortino=1.5, oos_max_drawdown=0.05, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=30, oos_total_return=0.02,
    )
    reward = compute_reward(m, universe_size=1, weights=_WEIGHTS, holdout=True)
    assert reward is not None
    assert isinstance(reward, float)


# ── Integration: confirm.py behandelt R_global/R_symbol None-sicher ──────────────────────────────
def _cfg_dir(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    with open(cfg_dir / "optimizer.json", "w", encoding="utf-8") as f:
        json.dump({
            "promotion_margin": 0.10, "penalty_unevaluable_oos": -20.0,
            "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0,
            "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
            "bonus_coverage_weight": 1.0, "evaluable_floor_epsilon": 0.001,
            "lambda_reg": 0.25,
        }, f)
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump({"max_drawdown": 0.30}, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)


def _factory_global_never_trades(tuned_keyvals, sortino_tuned, dd=0.05):
    """Der symbol-getunte Vektor evaluiert normal; der GLOBALE Vektor produziert auf diesem
    Symbol/Holdout NIE OOS-Trades (AdxAtr/TrendPullback-Signatur, #656) — R_global muss None sein,
    nicht -20.0."""
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        is_tuned = all(params.get(k) == v for k, v in tuned_keyvals.items())
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        if is_tuned:
            payload = {"fully_eligible_pairs": 1, "aggregate_winner": {
                "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
                "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_tuned],
                "oos_metrics": {"sortino_ratio": sortino_tuned, "max_drawdown": dd,
                                "total_return": 0.02, "total_trades": 30,
                                # Issue #958 — oos_n_periods > 0 macht diesen Trial admissible.
                                "n_periods": 50, "psr": 0.9}}}
        else:
            # Globaler Vektor: 0 OOS-Trades ⇒ nie evaluierbar.
            payload = {"fully_eligible_pairs": 0, "aggregate_winner": None,
                       "full_results": [{"metrics": {"total_trades": 0, "total_return": 0.0,
                                                      "win_rate": 0.0}}]}
        out.write_text(json.dumps(payload), "utf-8")
        return out
    return _fake


def test_confirm_promotes_when_global_baseline_is_undefined(tmp_path, monkeypatch):
    """Kein numerischer -20-Sentinel mehr im Proposal — R_global ist None, und die Promotion
    behandelt eine undefinierte Baseline als 'keine Baseline zu schlagen' (trivial erfüllt)."""
    tuned = {"sma_period": 60}
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _factory_global_never_trades(tuned, 2.0))
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: dict(tuned))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params={"sma_period": 20},
        run_backtest=_factory_global_never_trades(tuned, 2.0),
    )
    assert res["R_global"] is None
    assert res["R_symbol"] is not None
    assert res["promote"] is True
    assert res["status"] == "READY_FOR_PR"

    proposal_path = confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "AAA.ETORO", res)
    payload = json.loads(Path(proposal_path).read_text("utf-8"))
    assert payload["R_global"] is None
    assert payload["R_global"] != -20.0
