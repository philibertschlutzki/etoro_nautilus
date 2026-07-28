"""Issue #756 (P0) — Sortino-Zähler arithmetisch, `total_return` geometrisch: die
Kohärenzverletzung war strukturell.

Root-Cause: `period_rets` (backtest_runner._calculate_stats) war das ARITHMETISCHE Mittel der
Perioden-Returns; `total_return` ist GEOMETRISCH kompoundiert (Π(1+rᵢ) − 1). Die Differenz ist der
Volatilitäts-Drag mean(r) − (1/T)·log(1+total_return) ≈ σ²/2 — für jede Strategie mit
|mean(r)| < σ²/2 (Edge nahe null, Vola dominant) divergieren die Vorzeichen. `tournament.json
['aggregation_note']` behauptete Kohärenz "per Konstruktion", was mathematisch falsch war.

Fix: `period_rets = np.log1p(mtm_series.pct_change(fill_method=None).dropna())`. Damit gilt exakt
Σ log(1+rᵢ) = log(1+total_return) ⇒ sign(mean(period_rets)) ≡ sign(total_return) PER KONSTRUKTION,
für jede Sequenz, ohne Toleranz.

Issue #801/#802 — die Produktion (`_calculate_stats`) bildet `period_rets` seit #801 algebraisch
(`np.diff(np.log(mtm))`) statt über pandas' pct-change/log1p-Umweg (fill_method-versionsabhängig),
UND nur unter der Vorbedingung strikt positiver Equity (`assert_positive_equity`); die
"PER KONSTRUKTION"-Garantie oben gilt seither unter genau dieser Vorbedingung, siehe
`test_issue_801_log_return_identity.py`.
"""
import numpy as np
import pandas as pd
import pytest

from automation.backtest_runner import _calculate_stats


def _mtm_from_returns(period_rets: list[float]) -> pd.Series:
    mtm = [1000.0]
    for r in period_rets:
        mtm.append(mtm[-1] * (1 + r))
    idx = pd.date_range("2025-01-01", periods=len(mtm), freq="1h", tz="UTC")
    return pd.Series(mtm, index=idx)


def _stats_for_returns(period_rets: list[float]) -> dict:
    series = _mtm_from_returns(period_rets)
    pnls = [r if r != 0 else 0.001 for r in period_rets]
    holds = [(3600 * 10**9, 1.0)] * len(pnls)
    return _calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=3)


# ── Deterministisches Minimalbeispiel aus der Issue-Beschreibung ──────────────────────────────────
def test_deterministic_zero_total_return_example_is_coherent():
    """r = [+10%, −9.0909...%] ⇒ total_return = 0 exakt. Der ARITHMETISCHE Mittelwert dieser Sequenz
    ist +0.4545% > 0 (VOR dem Fix: Sortino > 0 bei Return = 0 — inkohärent per Definition, sign(0)
    ist weder >0 noch <0). Der LOG-Mittelwert ist exakt 0 — dieselbe Nullaussage wie total_return."""
    r1, r2 = 0.10, -1.0 + (1.0 / 1.10)  # exakt: (1+r1)(1+r2) = 1
    assert abs((1 + r1) * (1 + r2) - 1.0) < 1e-12

    log_mean = float(np.mean([np.log1p(r1), np.log1p(r2)]))
    assert abs(log_mean) < 1e-9, "Log-Return-Mittel muss bei total_return=0 exakt 0 sein"

    arithmetic_mean = float(np.mean([r1, r2]))
    assert arithmetic_mean > 0.0, "Referenz: der ALTE arithmetische Zähler ist bei diesem Beispiel > 0"


def test_pooled_stats_zero_return_sequence_yields_non_positive_sortino():
    """Dieselbe Sequenz durch den echten `_calculate_stats`-Pfad: total_return ≈ 0, sortino_period
    darf NICHT strikt positiv sein (vor #756 wäre er es gewesen — der Bug-Reproduktionsfall)."""
    r1, r2 = 0.10, -1.0 + (1.0 / 1.10)
    rets = [r1, r2] * 20  # genug Perioden für min_trades_for_sortino
    m = _stats_for_returns(rets)
    assert m["total_return"] == pytest.approx(0.0, abs=1e-9)
    if m["sortino_period"] is not None:
        assert m["sortino_period"] <= 1e-6


# ── Monte-Carlo: Kohärenzverletzungen fallen auf (nahezu) 0 ───────────────────────────────────────
def test_monte_carlo_coherence_violations_vanish_under_h0():
    """H0 (kein Edge): über N synthetische Renditesequenzen mit |mean(r)| < σ (Vola-dominiertes
    Regime, genau das Regime, in dem die alte arithmetische Definition divergierte) darf die
    Kohärenzverletzungsrate NICHT mehr signifikant über 0 liegen. Kleineres N als der volle
    #756-Kalibrierlauf (20000), aber ausreichend, um eine strukturelle Verletzung (>1%) zu erkennen."""
    rng = np.random.default_rng(2026)
    violations = 0
    n_runs = 800
    for _ in range(n_runs):
        mu = rng.uniform(-0.001, 0.001)
        sigma = rng.uniform(0.01, 0.03)
        rets = rng.normal(mu, sigma, 60).tolist()
        m = _stats_for_returns(rets)
        sr, tr = m.get("sortino_ratio"), m.get("total_return")
        if sr is None or tr is None:
            continue
        if abs(tr) > 1e-6 and sr != 0.0 and (tr > 0.0) != (sr > 0.0):
            violations += 1
    rate = violations / n_runs
    assert rate < 0.02, f"Kohärenzverletzungsrate {rate:.4f} sollte nahe 0 liegen (Log-Return-Fix)"


# ── period_returns/oos_period_returns tragen jetzt Log-Returns (Bootstrap-CI/PBO-Konsumenten) ─────
def test_period_returns_are_log_returns_not_simple_returns():
    rets = [0.05, -0.03, 0.02, -0.01, 0.04, -0.02] * 5
    m = _stats_for_returns(rets)
    exported = m["period_returns"]
    assert exported, "period_returns darf nicht leer sein"
    # Fuer 0.05 muss log1p(0.05) exportiert werden, nicht 0.05 selbst.
    assert exported[0] == pytest.approx(np.log1p(0.05), abs=1e-9)
    assert exported[0] != pytest.approx(0.05, abs=1e-6)


# ── invariants.check_log_return_coherence ──────────────────────────────────────────────────────────
def test_check_log_return_coherence_passes_when_no_violations():
    from automation.optimizer.invariants import check_log_return_coherence
    trials = [{"oos_coherence_violation": False}, {"oos_coherence_violation": None}, {}]
    result = check_log_return_coherence(trials)
    assert result.passed is True
    assert result.actual == 0


def test_check_log_return_coherence_fails_on_any_violation():
    from automation.optimizer.invariants import check_log_return_coherence
    trials = [{"oos_coherence_violation": False}, {"oos_coherence_violation": True}]
    result = check_log_return_coherence(trials)
    assert result.passed is False
    assert result.actual == 1
    assert result.name == "check_log_return_coherence"


def test_report_study_record_wires_log_return_coherence_check():
    from automation.optimizer.report import _study_record

    class _T:
        def __init__(self, violation):
            self.value = 1.0
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True,
                               "oos_coherence_violation": violation}

    class _S:
        trials = [_T(False), _T(True)]
        best_value = 1.0
        user_attrs = {}

    record, checks = _study_record({"symbol": "AAA.ETORO", "strategy": "SmaCrossoverStrategy"}, _S())
    names = [c.name for c in checks]
    assert "check_log_return_coherence" in names
    lrc = next(c for c in checks if c.name == "check_log_return_coherence")
    assert lrc.passed is False
