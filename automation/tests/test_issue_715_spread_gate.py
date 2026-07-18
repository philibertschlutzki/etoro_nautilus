"""Issue #715 (P1) — GR-02: Pre-Trade-Spread-Gate (Laufzeit).

Vor jeder Order wird der aktuelle Bid/Ask-Spread geprüft; das Einstiegssignal wird bei
Überschreitung eines Schwellwerts verworfen + strukturiert geloggt. Die Schwelle
(`spread_gate_bps`) ist standardmäßig `k_spread · spread_bps_model` — Single Source of Truth mit
dem Backtest-Kostenmodell (`backtest.json`).

Akzeptanzkriterien:
- Unit-Test mit Mock-QuoteTick: Verwerfen oberhalb, Durchlassen unterhalb der Schwelle.
- Rejection wird geloggt.
- Backtest-Verhalten bei s <= Schwelle unverändert.
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from automation.strategies.hourly_strategy_base import resolve_spread_bps_model
from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy


# ── resolve_spread_bps_model — Single Source of Truth mit backtest.json ─────────────────────
def test_symbol_override_takes_precedence():
    assert resolve_spread_bps_model("TSLA.ETORO") == 2.0


def test_asset_class_fallback_for_known_equity():
    # CAT.ETORO (instrument_map.json) -> asset_class "equity" -> backtest.json EQUITY=3.0
    assert resolve_spread_bps_model("CAT.ETORO") == 3.0


def test_default_fallback_for_unknown_instrument():
    assert resolve_spread_bps_model("UNKNOWN_SYMBOL.ETORO") == 4.0


class _FakeBar:
    def __init__(self, close: float):
        self.close = close


def _make_quote(bid: float, ask: float):
    quote = MagicMock()
    quote.bid_price = bid
    quote.ask_price = ask
    return quote


@pytest.fixture
def strategy_env():
    """Baut eine echte Rsi2ReversionStrategy-Instanz mit gepatchten cache/_log-Properties
    (beide sind read-only Cython-Attribute auf der echten Nautilus Strategy-Klasse). Der Patch
    wird über den Fixture-Teardown sauber wieder entfernt (kein Leak über Tests hinweg)."""
    def _build(spread_gate_bps=None, k_spread=2.0, instrument_id="TSLA.ETORO"):
        config = Rsi2ReversionConfig(
            instrument_id=instrument_id,
            bar_type=f"{instrument_id}-1-HOUR-MID-INTERNAL",
            spread_gate_bps=spread_gate_bps,
            k_spread=k_spread,
            trade_amount_pct=15.0,
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
        mock_cache.positions_open.return_value = []

        stack.enter_context(patch.object(type(strategy), "_log", new_callable=PropertyMock, return_value=mock_log))
        stack.enter_context(patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=mock_cache))
        return strategy, mock_cache, mock_log

    with ExitStack() as stack:
        yield _build


def test_wide_spread_rejects_signal_and_logs(strategy_env):
    """TSLA: spread_bps_model=2.0, k_spread=2.0 (Default) ⇒ threshold=4.0bps. Ein Spread von
    ~20bps (deutlich oberhalb) MUSS die Order verwerfen und strukturiert loggen."""
    strategy, mock_cache, mock_log = strategy_env()
    mock_cache.quote_tick.return_value = _make_quote(bid=99.90, ask=100.10)  # ~20 bps

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is None
    mock_log.warning.assert_called_once()
    logged_msg = mock_log.warning.call_args[0][0]
    assert "SPREAD_GATE_REJECT" in logged_msg


def test_tight_spread_passes_signal(strategy_env):
    """Ein Spread von 1bps (deutlich unterhalb der 4bps-Schwelle) MUSS durchgelassen werden."""
    strategy, mock_cache, mock_log = strategy_env()
    mock_cache.quote_tick.return_value = _make_quote(bid=99.995, ask=100.005)  # 1 bps

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is not None
    mock_log.warning.assert_not_called()


def test_missing_quote_tick_leaves_gate_inactive_fail_open(strategy_env):
    """Kein QuoteTick verfügbar (Cold-Start) ⇒ Gate bleibt inaktiv (fail-open, kein erfundener
    Spread-Wert) — das bestehende Sizing-Verhalten bleibt unangetastet."""
    strategy, mock_cache, mock_log = strategy_env()
    mock_cache.quote_tick.return_value = None

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is not None


def test_explicit_spread_gate_bps_overrides_derivation(strategy_env):
    strategy, mock_cache, mock_log = strategy_env(spread_gate_bps=1.0)
    mock_cache.quote_tick.return_value = _make_quote(bid=99.99, ask=100.01)  # 2 bps > explicit 1.0

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is None


def test_backtest_spread_equal_to_model_never_trips_gate(strategy_env):
    """Backtest-Bit-Identität: die Ticks im Backtest werden GENAU mit spread_bps_model konstruiert
    (resolve_spread_bps, backtest_runner.py). Bei der Hälfte der Schwelle (1x model vs. 2x-
    Schwellenfaktor) darf das Gate NIE greifen."""
    strategy, mock_cache, mock_log = strategy_env()  # TSLA model=2.0bps -> threshold=4.0bps
    half_spread_frac = 2.0 / 10000.0 / 2.0
    mid = 100.0
    bid = mid * (1 - half_spread_frac)
    ask = mid * (1 + half_spread_frac)
    mock_cache.quote_tick.return_value = _make_quote(bid=bid, ask=ask)

    qty = strategy._compute_quantity(_FakeBar(close=100.0))

    assert qty is not None
    mock_log.warning.assert_not_called()


def test_spread_gate_bps_and_k_spread_default_bit_identical_config():
    cfg = Rsi2ReversionConfig(instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL")
    assert cfg.spread_gate_bps is None
    assert cfg.k_spread == 2.0
