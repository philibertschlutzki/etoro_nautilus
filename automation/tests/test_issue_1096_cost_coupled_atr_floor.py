"""Issue #1096 (Katalog #929) Fix Punkt 1 — ``cost_coupled_atr_floor_bps`` hebt den asset-class-
aufgelösten ATR-Floor (``resolve_atr_floor_bps``, #924) zusätzlich auf ``min_stop_to_cost_ratio ·
round_trip_cost_bps / atr_trailing_multiplier`` an, wenn dieser Wert grösser ist — derselbe
Schwellenwert, den ``invariants.check_stop_cost_ratio`` bereits prüft.

Root-Cause: ``atr_median_bps`` lag in 18 von 56 Studies exakt auf dem reinen Asset-Klassen-Floor,
mit nominalen Stopdistanzen von 2-7 bps gegen 100-200 bps realisierte adverse Bewegung UND < 3x der
Round-Trip-Kosten — die Position kann den Stop dann strukturell nicht überleben, bevor die Kosten
sie aufzehren.
"""
import sys
import types
import unittest.mock as mock

import pytest

if "nautilus_trader" not in sys.modules:
    class MockModule(types.ModuleType):
        def __getattr__(self, name):
            return mock.MagicMock()

    for _mod in (
        "nautilus_trader", "nautilus_trader.backtest", "nautilus_trader.backtest.engine",
        "nautilus_trader.backtest.models", "nautilus_trader.model", "nautilus_trader.model.data",
        "nautilus_trader.model.enums", "nautilus_trader.model.identifiers",
        "nautilus_trader.model.currencies", "nautilus_trader.model.objects",
        "nautilus_trader.model.instruments", "nautilus_trader.config", "nautilus_trader.common",
        "nautilus_trader.common.enums", "nautilus_trader.common.actor", "nautilus_trader.core",
        "nautilus_trader.core.message", "nautilus_trader.portfolio", "nautilus_trader.test_engine",
        "nautilus_trader.persistence", "nautilus_trader.persistence.catalog",
        "nautilus_trader.execution", "nautilus_trader.execution.messages",
    ):
        sys.modules[_mod] = MockModule(_mod)

import automation.backtest_runner as br


def test_cost_coupled_floor_raises_a_too_narrow_asset_class_floor():
    # base_floor=2.0bps, k=0.5, c_rt=5.0bps, ratio=3.0 -> 3.0*5.0/0.5 = 30.0bps > 2.0bps.
    result = br.cost_coupled_atr_floor_bps(
        2.0, atr_trailing_multiplier=0.5, round_trip_cost_bps=5.0, min_stop_to_cost_ratio=3.0)
    assert result == 30.0


def test_cost_coupled_floor_leaves_an_already_wide_asset_class_floor_unchanged():
    # base_floor=50.0bps schon weit ueber dem kostengekoppelten Minimum (6.0bps).
    result = br.cost_coupled_atr_floor_bps(
        50.0, atr_trailing_multiplier=2.5, round_trip_cost_bps=5.0, min_stop_to_cost_ratio=3.0)
    assert result == 50.0


def test_cost_coupled_floor_fails_open_without_a_trailing_multiplier():
    result = br.cost_coupled_atr_floor_bps(
        2.0, atr_trailing_multiplier=None, round_trip_cost_bps=100.0, min_stop_to_cost_ratio=3.0)
    assert result == 2.0


def test_cost_coupled_floor_fails_open_on_non_positive_multiplier():
    result = br.cost_coupled_atr_floor_bps(
        2.0, atr_trailing_multiplier=0.0, round_trip_cost_bps=100.0, min_stop_to_cost_ratio=3.0)
    assert result == 2.0


def test_cost_coupled_floor_matches_check_stop_cost_ratio_threshold_by_construction():
    """Die Anhebung ist per Konstruktion GENAU der Punkt, an dem
    invariants.check_stop_cost_ratio (Verhaeltnis realisierte Stopdistanz / c_rt) bestehen wuerde:
    k * floor == min_stop_to_cost_ratio * c_rt."""
    k, c_rt, ratio = 1.2, 8.0, 3.0
    floor = br.cost_coupled_atr_floor_bps(
        0.5, atr_trailing_multiplier=k, round_trip_cost_bps=c_rt, min_stop_to_cost_ratio=ratio)
    assert k * floor == pytest.approx(ratio * c_rt)
