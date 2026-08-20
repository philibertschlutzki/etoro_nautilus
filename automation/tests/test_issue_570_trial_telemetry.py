"""Issue #569 — Per-Trial-Metrik-Telemetrie: Roh-Kennzahlen ins optimizer_trial_completed-Event.

Das Event trug oos_total_return/oos_total_trades/oos_gate_deltas, aber NICHT die Roh-Kennzahlen
oos_sortino/oos_expectancy/oos_win_rate/oos_profit_factor/is_sortino_median — die gesamte
Ursachenanalyse musste sie aus den Deltas + Reward rekonstruieren. Der Fix hebt die fünf Felder
(plus per_fold_oos_sortino) additiv ins Event (rein observational, kein Entscheidungseinfluss).

Akzeptanzkriterien (Issue #569):
- Jedes Trial-Event trägt die fünf Felder (null bei mathematisch undefiniertem Sortino/PF).
- Bestehende Consumer brechen nicht (additiv).
- Reward aus den emittierten Rohwerten rekonstruierbar ± 1e−6 (End-to-End-Konsistenz).
"""

import json
import logging
import math
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

RAW_KEYS = (
    "oos_sortino",
    "oos_expectancy",
    "oos_win_rate",
    "oos_profit_factor",
    "is_sortino_median",
    "per_fold_oos_sortino",
)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_backtest(payload):
    def _fake(trial_dir, manifest_path, **_kwargs):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), "utf-8")
        return out

    return _fake


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        msg = record.getMessage()
        if "[JSON_EVENT]" in msg:
            self.events.append(json.loads(msg.split("[JSON_EVENT]", 1)[1].strip()))


@pytest.fixture
def trial_events():
    handler = _Capture()
    logger = logging.getLogger("optimizer")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield handler.events
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)


def _completed(events):
    return [e for e in events if e.get("event_type") == "optimizer_trial_completed"]


# ── Die fünf Roh-Felder erscheinen im Event ──────────────────────────────────────────────────
def test_raw_metrics_present_in_event(tmp_path, monkeypatch, trial_events):
    _isolate(monkeypatch, tmp_path)
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True,
            "oos_eligible": True,
            "win_count": 1,
            "median_is_sortino": 1.4,
            "oos_fold_sortinos": [1.1, 1.3, 1.5],
            "oos_metrics": {
                "sortino_ratio": 1.3,
                "profit_factor": 1.6,
                "win_rate": 0.55,
                "expectancy": 0.004,
                "max_drawdown": 0.05,
                "total_trades": 22,
                "total_return": 0.09,
            },
        },
    }
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)

    e = _completed(trial_events)[-1]
    for k in RAW_KEYS:
        assert k in e, f"Roh-Feld '{k}' fehlt im Event (Issue #569)."
    assert e["oos_sortino"] == pytest.approx(1.3)
    assert e["oos_expectancy"] == pytest.approx(0.004)
    assert e["oos_win_rate"] == pytest.approx(0.55)
    assert e["oos_profit_factor"] == pytest.approx(1.6)
    assert e["is_sortino_median"] == pytest.approx(1.4)
    assert e["per_fold_oos_sortino"] == [1.1, 1.3, 1.5]


def test_undefined_sortino_and_pf_are_null(tmp_path, monkeypatch, trial_events):
    """Mathematisch undefinierter Sortino/PF (Zero-Loss) ⇒ null im Event (nicht 0.0)."""
    _isolate(monkeypatch, tmp_path)
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True,
            "oos_eligible": True,
            "win_count": 1,
            "median_is_sortino": 1.0,
            "oos_fold_sortinos": [],
            "oos_metrics": {
                "sortino_ratio": None,
                "profit_factor": None,
                "win_rate": 1.0,
                "expectancy": 0.01,
                "max_drawdown": 0.02,
                "total_trades": 5,
                "total_return": 0.05,
            },
        },
    }
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)

    e = _completed(trial_events)[-1]
    assert e["oos_sortino"] is None
    assert e["oos_profit_factor"] is None


# ── Reward aus den Rohwerten rekonstruierbar (±1e-6) ─────────────────────────────────────────
def test_reward_reconstructable_from_raw_metrics():
    """End-to-End-Konsistenz Reward↔Telemetrie: mit dd/param/turnover=0 ergibt sich der Reward
    exakt aus den emittierten Rohwerten (base, Divergenz, Fold-Dispersion, return-Tie-Breaker).
    """
    weights = {
        "penalty_unevaluable_oos": -10.0,
        "unevaluable_shaping_span": 0.25,
        "evaluable_floor_epsilon": 0.001,
        "sortino_clip_abs": 5.0,
        "sortino_soft_scale": 5.0,
        "penalty_overfit_weight": 0.5,
        "penalty_dd_weight": 8.0,
        "bonus_coverage_weight": 1.0,
        "overfit_divergence_mode": "symmetric",
        "overfit_oos_luck_weight": 0.25,
        "fold_dispersion_weight": 0.5,
        "w_ret": 2.0,
    }
    cfg = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}
    raw = dict(
        oos_sortino=3.0,
        is_sortino_median=2.0,
        oos_total_return=0.02,
        per_fold=[1.0, 3.0, 2.5, 3.5],
    )
    m = TournamentMetrics(
        oos_evaluated=True,
        oos_eligible=True,
        is_sortino_median=raw["is_sortino_median"],
        oos_sortino=raw["oos_sortino"],
        oos_max_drawdown=0.02,
        oos_total_trades=30,
        win_count=1,
        fully_eligible_pairs=1,
        is_total_trades=100,
        oos_total_return=raw["oos_total_return"],
        oos_expectancy=0.004,
        oos_win_rate=0.55,
        oos_profit_factor=1.6,
        oos_fold_sortinos=tuple(raw["per_fold"]),
        # Issue #589 — die Fold-Dispersion läuft über die per-Fold-RETURNS (+ oos_folds_total).
        oos_fold_returns=tuple(raw["per_fold"]),
        oos_folds_total=len(raw["per_fold"]),
    )
    actual = compute_reward(
        m, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=cfg
    )

    # Rekonstruktion NUR aus den emittierten Rohwerten:
    c = weights["sortino_soft_scale"]
    base = c * math.asinh(raw["oos_sortino"] / c)
    is_sortino_val = c * math.asinh(raw["is_sortino_median"] / c)
    diff = is_sortino_val - base
    divergence = (
        weights["penalty_overfit_weight"] * diff
        if diff >= 0
        else weights["overfit_oos_luck_weight"] * (-diff)
    )
    # Issue #1068/#1218 (Katalog #1196-1221, supersedes #589s Fold-Dispersions-Rekonstruktion) —
    # fold_dispersion ist seither code-seitig auf 0.0 gezwungen (reward.RETIRED_REWARD_TERMS),
    # unabhaengig vom uebergebenen fold_dispersion_weight -- kein Term mehr in der Rekonstruktion.
    dd_penalty = weights["penalty_dd_weight"] * ((0.02 / 0.3) ** 2)
    reconstructed = (
        base
        - divergence
        + weights["w_ret"] * raw["oos_total_return"]
        - dd_penalty
    )

    assert actual == pytest.approx(reconstructed, abs=1e-6)
