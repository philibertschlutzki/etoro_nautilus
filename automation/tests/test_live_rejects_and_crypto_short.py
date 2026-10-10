"""Live-Befunde vom 2026-10-10: eToro lehnt Krypto-Shorts nachträglich ab (statusID 4, errorCode 747), der Bot
hielt die Orders für ACCEPTED; BTC/ETH konnten live nie handeln (Equity: size_increment 1)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from nautilus_trader.model.enums import OrderSide, PositionSide
from nautilus_trader.model.identifiers import InstrumentId

from automation.adapters.etoro_execution import order_rejection_reason
from automation.strategies.hourly_strategy_base import HourlyStrategyBase


def test_order_rejection_reason_reads_the_async_refusal():
    body = {"statusID": 4, "errorCode": 747, "orderType": 17,
            "errorMessage": "opening position is disallowed for Sell positions of this instrument"}
    reason = order_rejection_reason(body)
    assert reason is not None
    assert "747" in reason and "disallowed for Sell" in reason
    assert order_rejection_reason({"order": body}) == reason


def test_order_rejection_reason_ignores_pending_and_filled_orders():
    assert order_rejection_reason({"statusID": 1, "errorCode": 0}) is None
    assert order_rejection_reason({"statusID": 2}) is None
    assert order_rejection_reason({}) is None
    assert order_rejection_reason("not json") is None


def _stub(open_long: bool):
    positions = [object()] if open_long else []

    def positions_open(instrument_id=None, side=None):
        assert side == PositionSide.LONG
        return positions

    return SimpleNamespace(cache=SimpleNamespace(positions_open=positions_open))


def _order(symbol: str, side: OrderSide, reduce_only: bool = False):
    return SimpleNamespace(instrument_id=InstrumentId.from_str(symbol), side=side, is_reduce_only=reduce_only)


def test_crypto_short_entry_is_blocked():
    assert HourlyStrategyBase._is_blocked_crypto_short(_stub(False), _order("BTC.ETORO", OrderSide.SELL))


def test_crypto_sell_that_closes_a_long_passes():
    assert not HourlyStrategyBase._is_blocked_crypto_short(_stub(True), _order("BTC.ETORO", OrderSide.SELL))
    assert not HourlyStrategyBase._is_blocked_crypto_short(
        _stub(False), _order("BTC.ETORO", OrderSide.SELL, reduce_only=True))


def test_crypto_buy_and_stock_short_pass():
    assert not HourlyStrategyBase._is_blocked_crypto_short(_stub(False), _order("BTC.ETORO", OrderSide.BUY))
    assert not HourlyStrategyBase._is_blocked_crypto_short(_stub(False), _order("CAT.ETORO", OrderSide.SELL))


def test_live_instruments_allow_fractional_units_like_the_backtest():
    from automation.adapters.etoro_data import EToroDataClient

    added = []
    fake = SimpleNamespace(
        _clock=SimpleNamespace(timestamp_ns=lambda: 0),
        instrument_map={"1": InstrumentId.from_str("BTC.ETORO"), "2": InstrumentId.from_str("CAT.ETORO")},
        _instrument_provider=SimpleNamespace(add=added.append),
        _cache=SimpleNamespace(add_instrument=lambda _i: None),
        _msgbus=SimpleNamespace(publish=lambda **_k: None),
        _log=MagicMock(),
        _instrument_precision={},
        _instrument_size_precision={},
    )
    EToroDataClient._register_instruments(fake)
    by_symbol = {str(i.id): i for i in added}
    btc = by_symbol["BTC.ETORO"]
    assert float(btc.size_increment) == 1e-08
    # 2 % von ~10.000 USD bei 83.000 USD/BTC: muss eine handelbare Menge > 0 ergeben
    assert float(btc.make_qty(199.0 / 83016.51)) > 0
    assert float(by_symbol["CAT.ETORO"].size_increment) == 0.01
