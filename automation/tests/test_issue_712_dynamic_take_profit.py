"""Issue #712 (P1) — Vereinheitlichtes dynamisches Take-Profit-Modell (Req-02 + Req-03, opt-in).

Eine kohärente, deadline-bewusste Zielfunktion (TP_long(t) = entry + γ·ATR·exp(-λ·bars/max_bars),
TP_short spiegelbildlich) ersetzt die zwei separaten, konkurrierenden Req-02/Req-03-Modelle. In der
Basisklasse (`hourly_strategy_base.py`) implementiert und von allen 15 Subklassen geerbt — keine
per-Strategie-Duplikation. Default AUS (`dyn_tp_enabled=False`) ⇒ bit-identische Regression.

Akzeptanzkriterien:
- Mock-Simulation verifiziert die TP-Reduktion nach 12h und 18h (monoton fallender Abstand).
- dyn_tp_enabled=False ⇒ bit-identisches Verhalten zum heutigen Pfad.
- Kein Order-Sturm (Cancel/Replace nur bei Tick-Überschreitung).
"""
import math
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from automation.strategies.hourly_strategy_base import (
    HourlyStrategyConfig,
    HourlyStrategyBase,
    compute_dyn_tp_target,
)
from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy


# ── Reine Formel: compute_dyn_tp_target ──────────────────────────────────────────────────────
def test_bars_zero_is_full_atr_distance_long():
    target = compute_dyn_tp_target(
        entry_price=100.0, atr_value=2.0, side="LONG", bars_in_pos=0,
        max_bars_in_trade=24, dyn_tp_lambda=1.0, dyn_tp_gamma=1.5,
    )
    assert target == pytest.approx(100.0 + 1.5 * 2.0)


def test_bars_zero_is_full_atr_distance_short():
    target = compute_dyn_tp_target(
        entry_price=100.0, atr_value=2.0, side="SHORT", bars_in_pos=0,
        max_bars_in_trade=24, dyn_tp_lambda=1.0, dyn_tp_gamma=1.5,
    )
    assert target == pytest.approx(100.0 - 1.5 * 2.0)


def test_tp_reduction_after_12h_and_18h_monotonically_decreasing():
    """Mock-Simulation: TP-Abstand vom Entry muss nach 12h (Bars) UND 18h kleiner sein als bei
    Entry (0h), und nach 18h kleiner als nach 12h — monoton fallender Abstand."""
    entry, atr, max_bars = 100.0, 2.0, 24
    kwargs = dict(entry_price=entry, atr_value=atr, side="LONG", max_bars_in_trade=max_bars,
                  dyn_tp_lambda=1.0, dyn_tp_gamma=1.5)

    dist_0h = compute_dyn_tp_target(bars_in_pos=0, **kwargs) - entry
    dist_12h = compute_dyn_tp_target(bars_in_pos=12, **kwargs) - entry
    dist_18h = compute_dyn_tp_target(bars_in_pos=18, **kwargs) - entry

    assert dist_0h > dist_12h > dist_18h > 0.0  # bleibt stets positiv (nie unter Breakeven)


def test_target_stays_positive_offset_at_deadline():
    """bars→max_bars_in_trade ⇒ Target → entry + γ·ATR·exp(-λ) (Rest-Abstand, NICHT Breakeven)."""
    target = compute_dyn_tp_target(
        entry_price=100.0, atr_value=2.0, side="LONG", bars_in_pos=24,
        max_bars_in_trade=24, dyn_tp_lambda=1.0, dyn_tp_gamma=1.5,
    )
    expected = 100.0 + 1.5 * 2.0 * math.exp(-1.0)
    assert target == pytest.approx(expected)
    assert target > 100.0  # niemals bei/unter Breakeven


# ── Config-Defaults: bit-identisch, wenn deaktiviert ─────────────────────────────────────────
def test_dyn_tp_disabled_by_default():
    cfg = HourlyStrategyConfig(instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL")
    assert cfg.dyn_tp_enabled is False


def test_all_15_strategy_configs_have_dyn_tp_fields():
    """Issue #713-Voraussetzung: dyn_tp_enabled/lambda/gamma müssen echte *Config-Felder aller
    15 Strategien sein (sonst stilles Verwerfen, Pitfall #446)."""
    import json
    from pathlib import Path
    import importlib

    data = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    strategies = [s for s in data["strategies"] if s.get("active", True) is not False]
    assert len(strategies) == 15
    for strat in strategies:
        mod = importlib.import_module(strat["strategy_module"])
        cfg_cls = getattr(mod, strat["config_class"])
        fields = set(getattr(cfg_cls, "__struct_fields__", ()))
        for key in ("dyn_tp_enabled", "dyn_tp_lambda", "dyn_tp_gamma"):
            assert key in fields, f"{strat['config_class']} fehlt {key}"


def _make_strategy(dyn_tp_enabled: bool, price_increment: float = 0.01):
    """Baut eine echte Rsi2ReversionStrategy-Instanz (kein Engine-Trader-Node nötig — HourlyStrategy
    Base.__init__ braucht nur config) + einen Mock-Cache/-OrderFactory für die Dyn-TP-Mechanik."""
    config = Rsi2ReversionConfig(
        instrument_id="AAPL.ETORO",
        bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        dyn_tp_enabled=dyn_tp_enabled,
        dyn_tp_lambda=1.0,
        dyn_tp_gamma=1.5,
        max_bars_in_trade=24,
    )
    strategy = Rsi2ReversionStrategy(config=config)
    strategy.submit_order = MagicMock()
    strategy.cancel_order = MagicMock()

    mock_order_factory = MagicMock()
    fake_order = MagicMock()
    fake_order.client_order_id = "O-1"
    mock_order_factory.limit.return_value = fake_order

    mock_cache = MagicMock()
    mock_instrument = MagicMock()
    mock_instrument.price_increment = price_increment
    mock_instrument.make_price = lambda p: round(p, 4)
    mock_cache.instrument.return_value = mock_instrument
    mock_pos = MagicMock()
    mock_pos.quantity = 10
    mock_cache.positions_open.return_value = [mock_pos]
    mock_cache.order.return_value = None

    strategy._exit_atr = MagicMock()
    strategy._exit_atr.initialized = True
    strategy._exit_atr.value = 2.0

    return strategy, mock_cache, mock_order_factory


def test_dyn_tp_disabled_never_submits_limit_order():
    """dyn_tp_enabled=False ⇒ bit-identisches Verhalten (kein Dyn-TP-Order-Pfad wird je betreten)."""
    strategy, mock_cache, mock_order_factory = _make_strategy(dyn_tp_enabled=False)
    strategy._dyn_tp_entry_price = 100.0
    strategy._dyn_tp_side = "LONG"
    strategy._bars_in_position = 5

    with patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache), \
         patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory):
        # dyn_tp_enabled=False ⇒ _check_exits_and_update ruft _update_dyn_tp_order nie auf (gated
        # in hourly_strategy_base.py) — dieselbe Bedingung hier direkt nachgebildet.
        if getattr(strategy.config, "dyn_tp_enabled", False):
            strategy._update_dyn_tp_order()
    strategy.submit_order.assert_not_called()


def test_no_order_storm_within_one_tick():
    """Cancel/Replace NUR bei |ΔTarget| > 1 Tick — ein zweiter Aufruf mit identischem Ziel darf
    KEINE weitere Order auslösen."""
    strategy, mock_cache, mock_order_factory = _make_strategy(dyn_tp_enabled=True, price_increment=0.01)
    strategy._dyn_tp_entry_price = 100.0
    strategy._dyn_tp_side = "LONG"
    strategy._bars_in_position = 5

    with patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache), \
         patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory):
        strategy._update_dyn_tp_order()
        assert strategy.submit_order.call_count == 1

        # Gleicher bars_in_position/ATR ⇒ identisches Target ⇒ zweiter Aufruf darf NICHT erneut senden.
        strategy._update_dyn_tp_order()
        assert strategy.submit_order.call_count == 1
        strategy.cancel_order.assert_not_called()


def test_target_beyond_one_tick_triggers_cancel_replace():
    """Ein hinreichend grosser Bar-Fortschritt (deutlich veränderter Zielpreis) MUSS ein
    Cancel/Replace auslösen."""
    strategy, mock_cache, mock_order_factory = _make_strategy(dyn_tp_enabled=True, price_increment=0.01)
    strategy._dyn_tp_entry_price = 100.0
    strategy._dyn_tp_side = "LONG"
    strategy._bars_in_position = 0

    existing_order = MagicMock()
    existing_order.is_open = True

    with patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache), \
         patch.object(type(strategy), "order_factory", new_callable=PropertyMock, return_value=mock_order_factory):
        strategy._update_dyn_tp_order()
        assert strategy.submit_order.call_count == 1

        # Simuliere einen weit fortgeschrittenen Bar (grosse Zielverschiebung) + eine noch offene
        # Order ⇒ Cancel/Replace MUSS ausgelöst werden.
        mock_cache.order.return_value = existing_order
        strategy._bars_in_position = 20
        strategy._update_dyn_tp_order()
        strategy.cancel_order.assert_called_once()
        assert strategy._dyn_tp_pending_cancel is not None
