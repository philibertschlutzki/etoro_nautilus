"""Issue #893 (Katalog #866, #1042) — Ökonomische Abnahmekriterien: CVaR/Kosten-Stress.

Ausgangsbefund: ``max_drawdown`` trägt laut ``check_gate_marginal_contribution`` über 16 002
Auswertungen ``marginal_delta = 0`` — es trifft nur den EINEN schlimmsten Pfad und trennt nichts.
Eine Median-Expectancy von 3,63 bps gegen 1 bps Kommission liegt am Break-even; ein Kosten-
Stressband macht sichtbar, welche Kandidaten bei realistisch höheren Kosten (Spread-Drift,
Slippage) noch tragen.

Scope-Hinweis (siehe Kommentare in ``backtest_runner.py`` an den Berechnungsstellen): implementiert
sind E-1 (Kosten-Stressband, additive Telemetrie: ``expectancy_cost_stress_1_5x``/``_2x``) und E-3
(CVaR/ES als additive Telemetrie: ``cvar_95``/``es_99``) — beide ohne jede Änderung an
``expectancy``/``max_drawdown`` selbst (Zero-Regression auf jede kalibrierte Schwelle). E-2
(Sizing-Parität Backtest↔MomentumLSAllocator) und E-4 (Information Ratio, braucht eine
Holdout-skopierte PERIODISCHE Benchmark-Renditeserie, die die Pipeline heute nur als Skalar
führt) sind als dokumentierter Scope-Cut zurückgestellt (siehe PR-Beschreibung) — eine blind
implementierte Sizing-Umstellung würde jede historisch kalibrierte Schwelle ändern, ohne dass
dieses Environment einen echten Re-Kalibrierungslauf gegen Marktdaten fahren kann.
"""
import numpy as np
import pandas as pd

from automation.backtest_runner import _calculate_stats, _expectancy_cost_stress
from automation.optimizer import invariants as inv


# ── E-1: _expectancy_cost_stress (standalone, direkt testbar wie #1032s _round_trip_notional_peak) ──
def test_cost_stress_2x_subtracts_one_additional_full_commission_charge():
    """commission_bps=100 (1 %) bereits im PnL abgezogen; 2x-Stress zieht GENAU EINE weitere
    volle Kommission ab (Multiplikator 2 = Basis + 1 zusaetzliche Einheit)."""
    pnls_notionals = [(10.0, 1000.0), (10.0, 1000.0)]
    result = _expectancy_cost_stress(pnls_notionals, commission_bps=100.0, multiplier=2.0)
    # Zusaetzlicher Abzug je Trade: 1.0 * (100/10000) * 1000 = 10.0 -> stressed pnl = 0.0
    assert abs(result - 0.0) < 1e-9


def test_cost_stress_1_5x_subtracts_half_the_commission():
    pnls_notionals = [(10.0, 1000.0)]
    result = _expectancy_cost_stress(pnls_notionals, commission_bps=100.0, multiplier=1.5)
    # Zusaetzlicher Abzug: 0.5 * (100/10000) * 1000 = 5.0 -> stressed pnl = 5.0 / 1000 = 0.005
    assert abs(result - 0.005) < 1e-9


def test_cost_stress_zero_commission_leaves_expectancy_unchanged():
    pnls_notionals = [(10.0, 1000.0), (-2.0, 1000.0)]
    result = _expectancy_cost_stress(pnls_notionals, commission_bps=0.0, multiplier=2.0)
    assert abs(result - (8.0 / 2000.0)) < 1e-9


def test_cost_stress_applies_same_notional_floor_as_expectancy_capital_weighted():
    """Ein Mikro-Notional-Trade (< 5 % des Median) wird ausgeschlossen — dieselbe #1031-Regel."""
    pnls_notionals = [(10.0, 1000.0)] * 19 + [(500.0, 1.0)]
    result = _expectancy_cost_stress(pnls_notionals, commission_bps=100.0, multiplier=2.0)
    # Nur die 19 homogenen Trades zaehlen: stressed_pnl je Trade = 10 - 1*(0.01)*1000 = 0.0
    assert abs(result - 0.0) < 1e-9


def test_cost_stress_none_without_positive_notionals():
    assert _expectancy_cost_stress([], commission_bps=100.0, multiplier=2.0) is None
    assert _expectancy_cost_stress([(10.0, 0.0)], commission_bps=100.0, multiplier=2.0) is None


# ── E-3: cvar_95/es_99 (_calculate_stats, aus derselben Perioden-Rendite-Serie wie #619) ────────
def _mtm_from_log_returns(log_returns: list[float], *, start: float = 1000.0) -> pd.Series:
    values = [start] + list(start * np.exp(np.cumsum(log_returns)))
    idx = pd.date_range("2026-01-01", periods=len(values), freq="h")
    return pd.Series(values, index=idx)


def test_cvar_and_es_distinguish_tail_severity():
    """90 milde positive Perioden, 9 moderate Verlustperioden (-2 %), 1 schwere (-10 %). cvar_95
    (Mittel der schlechtesten 5 von 100 = 1 schwere + 4 moderate) und es_99 (schlechteste 1 von
    100 = nur die schwere) muessen sich UNTERSCHEIDEN -- ein einzelner Extremwert darf cvar_95
    nicht dominieren, es_99 dagegen schon (per Definition der schaerferen Quantilsgrenze)."""
    log_returns = [0.001] * 90 + [-0.02] * 9 + [-0.10]
    mtm = _mtm_from_log_returns(log_returns)

    m = _calculate_stats([10.0], [(3600 * 1_000_000_000, 1.0)], 1000.0, mtm_series=mtm)

    assert m["cvar_95"] is not None and m["es_99"] is not None
    # cvar_95 = mean(-0.10, -0.02, -0.02, -0.02, -0.02) = -0.036
    assert abs(m["cvar_95"] - (-0.036)) < 1e-6
    # es_99 = -0.10 (die eine schwerste Periode)
    assert abs(m["es_99"] - (-0.10)) < 1e-6
    assert m["es_99"] < m["cvar_95"]  # ES_99 ist die schaerfere (weiter im Tail liegende) Grenze


def test_cvar_and_es_none_below_minimum_period_threshold():
    """< 20 Perioden -- kein belastbares 5-/1-%-Quantil, None statt Scheinpraezision."""
    log_returns = [0.001] * 5
    mtm = _mtm_from_log_returns(log_returns)
    m = _calculate_stats([10.0], [(3600 * 1_000_000_000, 1.0)], 1000.0, mtm_series=mtm)
    assert m["cvar_95"] is None
    assert m["es_99"] is None


def test_cvar_and_es_none_without_mtm_series():
    """Legacy-Pfad ohne Equity-Kurve -- keine Perioden-Renditeserie, kein stiller Fake-Wert."""
    m = _calculate_stats([10.0, 5.0, -2.0], [], 1000.0)
    assert m["cvar_95"] is None
    assert m["es_99"] is None


# ── E-2: check_sizing_parity_backtest_vs_allocator (Sichtbarkeits-Waechter, kein Gate) ───────────
def test_sizing_parity_passes_when_trade_amount_pct_matches_allocator_cap():
    result = inv.check_sizing_parity_backtest_vs_allocator(
        {"SmaCrossoverStrategy": 10.0}, max_symbol_exposure_fraction=0.10)
    assert result.passed is True
    assert result.actual is None


def test_sizing_parity_fails_on_known_999_divergence():
    """Beobachtete Signatur (#1042 E-2): Backtest sized mit 15 % je Position, der live gefahrene
    MomentumLSAllocator (#999) deckelt dieselbe Position auf 10 %."""
    result = inv.check_sizing_parity_backtest_vs_allocator(
        {"FlashCrashReversalStrategy": 15.0}, max_symbol_exposure_fraction=0.10)
    assert result.passed is False
    assert result.severity == "medium"
    assert "FlashCrashReversalStrategy" in result.actual
    assert result.actual["FlashCrashReversalStrategy"]["backtest_trade_amount_pct"] == 15.0
    assert result.actual["FlashCrashReversalStrategy"]["allocator_max_symbol_pct"] == 10.0


def test_sizing_parity_within_tolerance_passes():
    result = inv.check_sizing_parity_backtest_vs_allocator(
        {"S": 10.05}, max_symbol_exposure_fraction=0.10, tolerance=0.01)
    assert result.passed is True


def test_sizing_parity_not_applicable_without_allocator_config():
    result = inv.check_sizing_parity_backtest_vs_allocator(
        {"S": 15.0}, max_symbol_exposure_fraction=None)
    assert result.passed is True
    assert result.actual is None


def test_sizing_parity_not_applicable_without_trade_amount_pct_data():
    result = inv.check_sizing_parity_backtest_vs_allocator({}, max_symbol_exposure_fraction=0.10)
    assert result.passed is True
    assert result.actual is None


def test_sizing_parity_only_reports_offending_strategies():
    result = inv.check_sizing_parity_backtest_vs_allocator(
        {"Matching": 10.0, "Divergent": 15.0}, max_symbol_exposure_fraction=0.10)
    assert result.passed is False
    assert "Divergent" in result.actual
    assert "Matching" not in result.actual
