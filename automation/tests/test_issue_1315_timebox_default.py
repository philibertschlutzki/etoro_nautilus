"""Issue #1315 (GH #1192, P1) — Code-Default und Schema-Default für ``time_box_bars``
widersprechen sich.

Symptom. ``_time_box_penalty`` fiel bei fehlendem ``weights["time_box_bars"]`` auf den
hartkodierten Wert ``24.0`` zurück — die ALTE, Kalender-Bars-Achse. ``optimizer.json`` selbst
dokumentiert seit der #1275-RTH-Rekalibrierung (24 Kalender-Bars → 5,76 RTH-Bars, Faktor 0.24)
jedoch ``"Fehlt der Key ⇒ 5.76."`` — Code-Default und Schema-Default widersprachen sich um den
Faktor 4,17x.

Root-Cause. ``_time_box_penalty`` (reward.py) hatte einen EIGENEN, von ``_contracts.py`` (der seit
#1298/GH #1175 kanonischen Quelle für ``TIME_BOX_BARS``) unabhängigen Literal-Fallback.

Fix.
1. ``reward.py`` importiert ``_contracts.TIME_BOX_BARS`` und verwendet ihn als Fallback:
   ``weights.get("time_box_bars", _TIME_BOX_BARS)``.
2. Kein eigener ``24.0``-Fallback für ``time_box_bars`` mehr in ``reward.py``.
"""
import inspect
import json
from pathlib import Path

import pytest

from automation.optimizer import _contracts
from automation.optimizer import reward
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

OPT_CFG_PATH = Path("automation/config/optimizer.json")
OPT_CFG = json.loads(OPT_CFG_PATH.read_text("utf-8"))

_TCFG = {
    "oos_min_trades": 10, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
    "oos_min_win_rate": 0.0, "max_drawdown": 0.3,
}


def _make_metrics(bars_held: float | None, **overrides) -> TournamentMetrics:
    kwargs = dict(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
        oos_sortino=1.0, oos_max_drawdown=0.01, oos_total_trades=50, win_count=1,
        fully_eligible_pairs=1, is_total_trades=50, oos_total_return=0.02,
        oos_win_rate=0.4, oos_profit_factor=1.3, oos_psr_z=0.8,
        oos_fold_returns=(0.01, 0.012, 0.009, 0.011), oos_folds_total=4,
        oos_median_bars_held=bars_held,
    )
    kwargs.update(overrides)
    return TournamentMetrics(**kwargs)


# ── Akzeptanzkriterium 1 — kein 24.0-Fallback fuer time_box_bars mehr im Quelltext ───────────────

def test_no_stray_24_0_time_box_bars_fallback_remains_in_source():
    src = inspect.getsource(reward._time_box_penalty)
    assert '"time_box_bars", 24.0' not in src
    assert "'time_box_bars', 24.0" not in src


def test_time_box_penalty_source_uses_contracts_time_box_bars_as_fallback():
    src = inspect.getsource(reward._time_box_penalty)
    assert "_TIME_BOX_BARS" in src
    assert "time_box_bars" in src


def test_reward_module_imports_time_box_bars_from_contracts():
    assert reward._TIME_BOX_BARS == _contracts.TIME_BOX_BARS


# ── Akzeptanzkriterium 2 — compute_reward mit leerem weights-Dict nutzt _contracts.TIME_BOX_BARS ──

def test_compute_reward_with_empty_weights_uses_contracts_time_box_bars():
    """Ein leeres (aber nicht-None) weights-Dict hat keinen 'time_box_bars'-Schluessel — der
    Penalty-Term muss dieselbe Normierung verwenden, als waere er explizit mit
    _contracts.TIME_BOX_BARS aufgerufen worden (identischer t_norm, identischer Reward-Beitrag)."""
    weights_empty = {"penalty_time_box_weight": 0.05}
    weights_explicit = {
        "penalty_time_box_weight": 0.05, "time_box_bars": _contracts.TIME_BOX_BARS,
    }
    m = _make_metrics(bars_held=3.0)
    penalty_empty = reward._time_box_penalty(m, weights_empty)
    penalty_explicit = reward._time_box_penalty(m, weights_explicit)
    assert penalty_empty == pytest.approx(penalty_explicit)
    assert penalty_empty > 0.0


def test_compute_reward_with_empty_weights_does_not_use_the_old_24_0_axis():
    """Regressionsschutz: die ALTE 24.0-Kalender-Bars-Achse haette bei identischen Metriken einen
    (4,17x = (24/5.76)^2) KLEINEREN t_norm^2-Term erzeugt — der reparierte Fallback muss klar
    unterscheidbar groesser sein."""
    weights_empty = {"penalty_time_box_weight": 0.05}
    weights_old_axis = {"penalty_time_box_weight": 0.05, "time_box_bars": 24.0}
    m = _make_metrics(bars_held=3.0)
    penalty_empty = reward._time_box_penalty(m, weights_empty)
    penalty_old_axis = reward._time_box_penalty(m, weights_old_axis)
    assert penalty_empty > penalty_old_axis * 3.0


def test_compute_reward_full_call_with_minimal_weights_dict_does_not_raise():
    weights = dict(OPT_CFG)
    weights.pop("time_box_bars", None)
    weights["penalty_time_box_weight"] = 0.05
    m = _make_metrics(bars_held=3.0)
    _, terms = compute_reward(
        m, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=_TCFG,
        return_terms=True,
    )
    assert terms["time_box_penalty"] > 0.0


# ── Akzeptanzkriterium 3 — Schema-Text und Code-Default nennen denselben Zahlenwert ──────────────

def test_optimizer_json_schema_text_names_the_same_value_as_contracts_time_box_bars():
    doc = OPT_CFG["_schema"]["fields"]["time_box_bars"]
    assert f"⇒ {_contracts.TIME_BOX_BARS}" in doc


def test_optimizer_json_default_value_matches_contracts_time_box_bars():
    assert OPT_CFG["time_box_bars"] == _contracts.TIME_BOX_BARS


def test_contracts_time_box_bars_is_7_0():
    # Issue #1343 (GH #1237) — 5.76 (geschaetzter RTH_AXIS_FACTOR=0.24 * 24.0) → 7.0
    # (BARS_PER_TRADING_DAY=7 * max_handelstage=1.0, mechanisch aus der Session-Ueberlappung
    # gezaehlt statt geschaetzt).
    assert _contracts.TIME_BOX_BARS == pytest.approx(7.0)
