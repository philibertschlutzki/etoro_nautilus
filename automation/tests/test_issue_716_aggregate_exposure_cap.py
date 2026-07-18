"""Issue #716 (P1) — GR-03: Systemweiter Aggregat-Exposure-Cap + Notional-Deckel.

Ein node-weiter `max_aggregate_open_positions` wird im Basis-Entry-Pfad (`_compute_quantity`)
ZUSÄTZLICH zum (unverändert bestehenden) per-Strategie `max_open_positions`-Check geprüft; ein
harter `max_order_notional`-Deckel begrenzt die absolute Ordergrösse.

Akzeptanzkriterien:
- Bei erreichtem Aggregat-Limit wird jedes neue Signal (strategieübergreifend) blockiert + geloggt.
- Keine Order überschreitet max_order_notional.
- Per-Strategie-Cap bleibt als zusätzliche Schranke wirksam.
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy


class _FakeBar:
    def __init__(self, close: float):
        self.close = close


@pytest.fixture
def strategy_env():
    def _build(max_aggregate_open_positions=5, max_order_notional=2000.0, trade_amount_pct=15.0,
               spread_gate_bps=0.0):
        config = Rsi2ReversionConfig(
            instrument_id="TSLA.ETORO",
            bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
            max_aggregate_open_positions=max_aggregate_open_positions,
            max_order_notional=max_order_notional,
            trade_amount_pct=trade_amount_pct,
            # Spread-Gate (#715) für diese Tests deaktivieren (spread_gate_bps=0.0 -> threshold
            # bleibt 0.0 -> Gate inaktiv, da compute_quantity nur bei threshold_bps > 0 prüft
            # ... hier stattdessen einfach kein QuoteTick liefern, siehe unten).
        )
        strategy = Rsi2ReversionStrategy(config=config)
        strategy._get_current_balance = MagicMock(return_value=10000.0)

        mock_log = MagicMock()
        mock_cache = MagicMock()
        mock_instrument = MagicMock()
        mock_instrument.size_increment = 0.01
        mock_instrument.size_precision = 2
        mock_instrument.make_qty = lambda q: q
        mock_cache.instrument.return_value = mock_instrument
        mock_cache.accounts.return_value = []
        mock_cache.quote_tick.return_value = None  # Spread-Gate inaktiv (Cold-Start, fail-open)
        mock_cache.positions_open.return_value = []

        stack.enter_context(patch.object(type(strategy), "_log", new_callable=PropertyMock, return_value=mock_log))
        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        return strategy, mock_cache, mock_log

    with ExitStack() as stack:
        yield _build


def test_aggregate_cap_blocks_when_reached(strategy_env):
    strategy, mock_cache, mock_log = strategy_env(max_aggregate_open_positions=5)
    mock_cache.positions_open.return_value = [MagicMock() for _ in range(5)]  # == cap

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is None
    mock_log.warning.assert_called_once()
    assert "AGGREGATE_EXPOSURE_CAP_REJECT" in mock_log.warning.call_args[0][0]


def test_aggregate_cap_allows_below_threshold(strategy_env):
    strategy, mock_cache, mock_log = strategy_env(max_aggregate_open_positions=5)
    mock_cache.positions_open.return_value = [MagicMock() for _ in range(4)]  # < cap

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is not None
    mock_log.warning.assert_not_called()


def test_aggregate_cap_blocks_regardless_of_which_strategy_asks(strategy_env):
    """'strategieübergreifend': cache.positions_open() zählt SYSTEMWEIT (alle Instrumente/
    Strategien im Node), nicht nur die eigene Instanz — cache.positions_open() ohne
    instrument_id-Filter ist bereits die systemweite Zählung."""
    strategy, mock_cache, mock_log = strategy_env(max_aggregate_open_positions=3)
    other_strategy_positions = [MagicMock() for _ in range(3)]
    mock_cache.positions_open.return_value = other_strategy_positions

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is None
    mock_cache.positions_open.assert_any_call()  # ohne instrument_id-Filter = node-weit


def test_default_aggregate_cap_is_conservative_but_active():
    """Issue #716 verlangt EXPLIZIT konservative, AKTIVE Defaults (Schutz gegen Capital Starvation
    im Paper-Konto) — anders als die opt-in Track-1-Reward-Issues ist dies ein Guardrail und daher
    NICHT None/deaktiviert per Default."""
    cfg = HourlyStrategyConfig(instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL")
    assert cfg.max_aggregate_open_positions == 5
    assert cfg.max_order_notional == 2000.0


# ── Notional-Deckel ───────────────────────────────────────────────────────────────────────────
def test_notional_cap_limits_order_size(strategy_env):
    """trade_amount_pct=15% von 10000 USD Equity = 1500 USD (unter dem Deckel) — mit einem
    NIEDRIGEREN Deckel (500 USD) MUSS die tatsächliche Order-Notional darauf gekappt werden."""
    strategy, mock_cache, mock_log = strategy_env(max_order_notional=500.0, trade_amount_pct=15.0)

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is not None
    notional = float(qty) * 100.0
    assert notional <= 500.0 + 1e-6


def test_notional_cap_inactive_when_below_normal_sizing(strategy_env):
    """Ein hoher Deckel (weit über dem normalen 15%-Trade) darf das normale Sizing NICHT
    beeinflussen (bit-identisch zum Pre-#716-Pfad für normale Trades)."""
    strategy_capped, mock_cache_capped, _ = strategy_env(max_order_notional=1_000_000.0, trade_amount_pct=15.0)
    qty_capped = strategy_capped._compute_quantity(_FakeBar(close=100.0))
    assert qty_capped is not None
    assert float(qty_capped) * 100.0 == pytest.approx(1500.0, rel=0.01)


def test_no_order_ever_exceeds_max_order_notional(strategy_env):
    """Über mehrere Preis-/Sizing-Kombinationen: keine Order überschreitet max_order_notional."""
    for price in (10.0, 50.0, 100.0, 500.0):
        strategy, mock_cache, _ = strategy_env(max_order_notional=300.0, trade_amount_pct=90.0)
        qty = strategy._compute_quantity(_FakeBar(close=price))
        if qty is not None:
            assert float(qty) * price <= 300.0 + 1e-6


# ── Per-Strategie-Cap bleibt zusätzlich wirksam (unveränderter Subklassen-Check) ────────────────
def test_per_strategy_cap_still_enforced_in_subclass(strategy_env):
    """max_open_positions (per-Strategie) wird weiterhin VOR _compute_quantity in den Subklassen
    geprüft (_on_buy_signal/_on_sell_signal) — dieser Test verifiziert, dass der bestehende Check
    unverändert (Pre-#716) funktioniert, additiv zum neuen Aggregat-Cap."""
    strategy, mock_cache, mock_log = strategy_env(max_aggregate_open_positions=100)  # Aggregat inaktiv
    config = Rsi2ReversionConfig(
        instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
        max_open_positions=1,
    )
    strategy2 = Rsi2ReversionStrategy(config=config)
    strategy2.submit_order = MagicMock()
    mock_cache2 = MagicMock()

    def _positions_open(instrument_id=None):
        # Kein Position für DIESES Instrument, aber node-weit bereits 1 offen (anderes Instrument)
        # >= max_open_positions=1 -> der per-Strategie-Cap-Check greift.
        return [] if instrument_id is not None else [MagicMock()]

    mock_cache2.positions_open.side_effect = _positions_open
    mock_cache2.orders_open.return_value = []
    with patch.object(type(strategy2), "cache", new_callable=PropertyMock, return_value=mock_cache2):
        strategy2._on_buy_signal(_FakeBar(close=100.0))
    strategy2.submit_order.assert_not_called()  # per-Strategie-Cap blockiert VOR _compute_quantity
