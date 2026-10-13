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


def test_crypto_round_trip_fee_is_two_percent_equity_ten_bps_and_others_keep_the_global_commission():
    from automation.backtest_runner import resolve_commission_bps, _read_default_round_trip_cost_bps
    from automation.strategies.hourly_strategy_base import resolve_commission_bps_model

    assert resolve_commission_bps("CRYPTO", 1.0) == 200.0
    assert resolve_commission_bps("EQUITY", 1.0) == 10.0    # 1 USD je Seite bei ~2000 USD je Paar (Stage C, 2026-10-12)
    assert resolve_commission_bps("FOREX", 1.0) == 1.0
    assert resolve_commission_bps_model("BTC.ETORO") == 200.0
    assert resolve_commission_bps_model("CAT.ETORO") == 10.0
    assert _read_default_round_trip_cost_bps("BTC.ETORO") >= 200.0


def test_crypto_trailing_take_profit_never_locks_below_the_fees():
    from automation.strategies.hourly_strategy_base import (
        compute_trailing_tp_lock, resolve_commission_bps_model, resolve_spread_bps_model)

    min_lock_bps = 2.0 * resolve_spread_bps_model("BTC.ETORO") + resolve_commission_bps_model("BTC.ETORO")
    entry, atr = 100.0, 0.5
    # +3 %: unter 2× Mindest-Lock (4,6 %), Trailing-TP bleibt aus statt einen Verlust nach Gebühren zu sichern
    assert compute_trailing_tp_lock(entry, 103.0, atr, "LONG", 0.5, 0.3, min_lock_bps) is None
    lock = compute_trailing_tp_lock(entry, 106.0, atr, "LONG", 0.5, 0.3, min_lock_bps)
    assert lock is not None and lock >= entry * (1 + 230 / 10_000)


_PNL_WITH_BTC = {"clientPortfolio": {"positions": [
    {"positionID": 3602662774, "instrumentID": 100000, "isBuy": True, "units": 0.002406,
     "openRate": 82942.5, "amount": 199.56, "stopLossRate": 74646.58},
], "credit": 9777.24}}


def test_net_open_positions_reads_the_live_btc_position():
    from automation.adapters.etoro_execution import net_open_positions

    net = net_open_positions(_PNL_WITH_BTC)
    assert net["100000"]["units"] == 0.002406
    assert net["100000"]["avg_px"] == 82942.5
    assert net["100000"]["position_ids"] == ["3602662774"]
    assert net_open_positions({"clientPortfolio": {"positions": []}}) == {}


def test_open_etoro_position_becomes_a_position_report_at_start():
    import asyncio

    from nautilus_trader.model.enums import PositionSide as PS
    from nautilus_trader.model.identifiers import AccountId

    from automation.adapters import etoro_execution as ex
    from automation.backtest_runner import create_mock_instrument

    class _Resp:
        status = 200

        async def json(self):
            return _PNL_WITH_BTC

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    btc = create_mock_instrument("BTC.ETORO", price_precision=2, size_precision=8)
    fake = SimpleNamespace(
        _dry_run=False,
        _session=SimpleNamespace(get=lambda *a, **k: _Resp()),
        _pnl_base="https://x/api/v1/trading/info/real/pnl",
        _make_headers=lambda: {},
        _clock=SimpleNamespace(timestamp_ns=lambda: 1),
        _cache=SimpleNamespace(instrument=lambda iid: btc if str(iid) == "BTC.ETORO" else None),
        _log=MagicMock(),
        account_id=AccountId("ETORO-REAL-001"),
    )
    reports = asyncio.run(ex.EToroExecutionClient.generate_position_status_reports(fake))
    assert len(reports) == 1
    rep = reports[0]
    assert str(rep.instrument_id) == "BTC.ETORO"
    assert rep.position_side == PS.LONG
    assert float(rep.quantity) == 0.002406
    assert float(rep.avg_px_open) == 82942.5
