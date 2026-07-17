"""Issue #666 — Excess-Return-Gate (≥0) im Bärenmarkt trivial erfüllt: null Diskriminierung, wenn
Benchmark negativ.

Root-Cause: ``oos_min_excess_return = oos_total_return − oos_buyhold_return`` mit Schwelle 0.0 ist
im fallenden Markt blind — jede Strategie, die nicht schlimmer als der Markt verliert, "schlägt"
ihn trivial (misst negatives Beta, kein Alpha). Fix: im Bär-Markt (``oos_buyhold_return < 0``)
verlangt das Gate einen ECHTEN positiven risikoadjustierten Return (``sortino_period > 0``) statt
des absoluten Excess-Deltas; im Bull-/Flat-Markt bleibt das Gate bit-identisch zum Status quo.
"""
from automation.backtest_runner import _evaluate_oos_eligibility
from automation.optimizer.reward import _normalized_gate_distances
from automation.optimizer.parsing import TournamentMetrics


_TCFG = {
    "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
    "oos_min_win_rate": 0.0, "max_drawdown": 0.3, "oos_min_excess_return": 0.0,
    "eligible_requires_all": ["min_excess_return"],
}


def _oos_metrics(*, total_return, buyhold_return, sortino_period, total_trades=50):
    excess = total_return - buyhold_return
    return {
        "total_trades": total_trades, "max_drawdown": 0.02, "win_rate": 0.4,
        "total_return": total_return, "expectancy": 0.001, "sortino_ratio": 1.0,
        "profit_factor": 1.2, "median_position_notional": 1000.0,
        "oos_buyhold_return": buyhold_return, "oos_excess_return": excess,
        "sortino_period": sortino_period,
    }


# ── Akzeptanzkriterium: flache Strategie, Benchmark −20 % scheitert am neuen Alpha-Gate ─────────
def test_flat_strategy_fails_bear_market_gate_status_quo_would_pass():
    """Symptom-Reproduktion: Buy&Hold fiel 20 %, die Strategie verlor nur 5 % (Excess=+0.15,
    trivial positiv) — aber KEIN echter risikoadjustierter Edge (sortino_period <= 0). Status quo
    (Pre-#666) hätte dies BESTANDEN; das neue Gate lehnt korrekt ab."""
    m = _oos_metrics(total_return=-0.05, buyhold_return=-0.20, sortino_period=-0.02)
    assert (m["total_return"] - m["oos_buyhold_return"]) >= 0.0  # Excess ist trivial positiv
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is False
    assert any("bear-regime" in r for r in ev["oos_rejection_reasons"])


def test_zero_sortino_period_also_fails_bear_gate():
    m = _oos_metrics(total_return=-0.05, buyhold_return=-0.20, sortino_period=0.0)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is False


def test_undefined_sortino_period_fails_bear_gate_no_implicit_pass():
    m = _oos_metrics(total_return=-0.05, buyhold_return=-0.20, sortino_period=None)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is False
    assert any("insufficient/guard" in r for r in ev["oos_rejection_reasons"])


def test_real_alpha_strategy_passes_bear_market_gate():
    """Ein echter Edge (positiver risikoadjustierter Return TROTZ fallendem Markt) besteht weiterhin."""
    m = _oos_metrics(total_return=0.02, buyhold_return=-0.20, sortino_period=0.05)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is True


# ── Bull-/Flat-Markt: bit-identisch zum Status quo (absolutes Excess-Gate) ───────────────────────
def test_bull_market_positive_alpha_passes():
    m = _oos_metrics(total_return=0.10, buyhold_return=0.05, sortino_period=0.5)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is True


def test_bull_market_negative_excess_still_fails_regardless_of_sortino():
    """Bull-Markt: das ABSOLUTE Excess-Gate bleibt massgeblich (unverändert) — ein positiver
    Sortino rettet einen negativen Excess im Bull-Markt NICHT (kein Regime-Bonus für Bull)."""
    m = _oos_metrics(total_return=0.02, buyhold_return=0.05, sortino_period=0.8)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is False
    assert not any("bear-regime" in r for r in ev["oos_rejection_reasons"])


def test_flat_market_zero_buyhold_uses_absolute_branch():
    """oos_buyhold_return == 0.0 ist NICHT < 0 ⇒ der Bull-/Flat-Zweig (absolutes Gate) greift."""
    m = _oos_metrics(total_return=0.0, buyhold_return=0.0, sortino_period=None)
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is True  # excess=0.0 >= req 0.0


# ── Fail-open: fehlende Benchmark-Telemetrie bleibt rückwärtskompatibel trivial erfüllt ──────────
def test_missing_benchmark_telemetry_is_fail_open():
    m = {
        "total_trades": 50, "max_drawdown": 0.02, "win_rate": 0.4, "total_return": -0.5,
        "expectancy": 0.001, "sortino_ratio": 1.0, "profit_factor": 1.2,
        "median_position_notional": 1000.0,
        # kein oos_buyhold_return / oos_excess_return
    }
    ev = _evaluate_oos_eligibility(m, _TCFG)
    assert ev["oos_eligible"] is True


# ── Symmetrie-Test (Akzeptanzkriterium #666): diskriminiert in Bull- UND Bear-Fixtures gleich ────
def test_issue_666_excess_return_regime_symmetry():
    no_edge_bear = _oos_metrics(total_return=-0.03, buyhold_return=-0.15, sortino_period=-0.01)
    no_edge_bull = _oos_metrics(total_return=0.01, buyhold_return=0.05, sortino_period=0.02)
    real_edge_bear = _oos_metrics(total_return=0.03, buyhold_return=-0.15, sortino_period=0.10)
    real_edge_bull = _oos_metrics(total_return=0.12, buyhold_return=0.05, sortino_period=0.10)

    assert _evaluate_oos_eligibility(no_edge_bear, _TCFG)["oos_eligible"] is False
    assert _evaluate_oos_eligibility(no_edge_bull, _TCFG)["oos_eligible"] is False
    assert _evaluate_oos_eligibility(real_edge_bear, _TCFG)["oos_eligible"] is True
    assert _evaluate_oos_eligibility(real_edge_bull, _TCFG)["oos_eligible"] is True


# ── Gate/Reward-Parität (reward._normalized_gate_distances spiegelt dieselbe Regime-Logik) ──────
_WEIGHTS = {}
_REWARD_TCFG = {
    "oos_min_trades": 1, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
    "oos_min_win_rate": 0.0, "max_drawdown": 0.3, "oos_min_excess_return": 0.0,
    "return_penalty_scale": 0.1,
}


def _m(*, total_return, buyhold_return, sortino_period):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=False, is_sortino_median=0.5,
        oos_sortino=1.0, oos_max_drawdown=0.02, oos_total_trades=50, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=total_return,
        oos_buyhold_return=buyhold_return, oos_excess_return=total_return - buyhold_return,
        oos_sortino_period=sortino_period,
    )


def test_reward_distance_uses_sortino_period_gap_in_bear_regime():
    m = _m(total_return=-0.05, buyhold_return=-0.20, sortino_period=-0.02)
    distances = _normalized_gate_distances(m, _WEIGHTS, 0.3, _REWARD_TCFG)
    assert distances["oos_min_excess_return"] == 0.02  # max(0, -(-0.02)) == 0.02


def test_reward_distance_zero_for_positive_sortino_in_bear_regime():
    m = _m(total_return=0.02, buyhold_return=-0.20, sortino_period=0.05)
    distances = _normalized_gate_distances(m, _WEIGHTS, 0.3, _REWARD_TCFG)
    assert distances["oos_min_excess_return"] == 0.0


def test_reward_distance_omits_key_for_undefined_sortino_in_bear_regime():
    """Analog zum oos_min_psr-Präzedenzfall: eine undefinierte Kennzahl erfindet KEINE Distanz."""
    m = _m(total_return=-0.05, buyhold_return=-0.20, sortino_period=None)
    distances = _normalized_gate_distances(m, _WEIGHTS, 0.3, _REWARD_TCFG)
    assert "oos_min_excess_return" not in distances


def test_reward_distance_uses_absolute_excess_gap_in_bull_regime():
    m = _m(total_return=0.02, buyhold_return=0.05, sortino_period=0.9)
    distances = _normalized_gate_distances(m, _WEIGHTS, 0.3, _REWARD_TCFG)
    # req_excess=0.0, excess=-0.03 ⇒ shortfall 0.03, normiert auf return_penalty_scale=0.1 ⇒ 0.3
    assert abs(distances["oos_min_excess_return"] - 0.3) < 1e-9
