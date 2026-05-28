import pytest
from unittest.mock import Mock
from automation.fractional_trading import safe_compute_quantity, build_by_amount_payload

class TestFractionalTrading:
    def test_safe_compute_quantity_units_lt_size_increment(self):
        instrument = Mock()
        instrument.size_increment = 0.5

        # When units = amount / price < size_increment, should return None
        # safe_compute_quantity(instrument, amount_usd, price)
        # 0.4 / 1.0 = 0.4 < 0.5
        result = safe_compute_quantity(instrument, 0.4, 1.0)
        assert result is None

    def test_safe_compute_quantity_value_error(self):
        instrument = Mock()
        instrument.size_increment = 0.1
        instrument.make_qty.side_effect = ValueError("Rounding to zero")

        # 1.0 / 1.0 = 1.0 > 0.1
        result = safe_compute_quantity(instrument, 1.0, 1.0)
        assert result is None

    def test_safe_compute_quantity_success(self):
        instrument = Mock()
        instrument.size_increment = 0.1
        qty_mock = Mock()
        instrument.make_qty.return_value = qty_mock

        # 1.0 / 1.0 = 1.0 > 0.1
        result = safe_compute_quantity(instrument, 1.0, 1.0)
        assert result == qty_mock

    def test_build_by_amount_payload_buy(self):
        payload = build_by_amount_payload(
            etoro_id="1",
            is_buy=True,
            leverage=1,
            investment_amount_usd=100.0,
            stop_loss_pct=0.05,
            take_profit_pct=0.1,
            current_rate=200.0
        )
        assert payload["InstrumentId"] == 1
        assert payload["IsBuy"] is True
        assert payload["Leverage"] == 1
        assert payload["InvestmentAmount"] == 100.0
        assert payload["StopLossRate"] == round(200.0 * (1 - 0.05), 5)
        assert payload["TakeProfitRate"] == round(200.0 * (1 + 0.1), 5)

    def test_build_by_amount_payload_sell(self):
        payload = build_by_amount_payload(
            etoro_id="2",
            is_buy=False,
            leverage=2,
            investment_amount_usd=50.0,
            stop_loss_pct=0.1,
            take_profit_pct=None,
            current_rate=100.0
        )
        assert payload["InstrumentId"] == 2
        assert payload["IsBuy"] is False
        assert payload["Leverage"] == 2
        assert payload["InvestmentAmount"] == 50.0
        assert payload["StopLossRate"] == round(100.0 * (1 + 0.1), 5)
        assert "TakeProfitRate" not in payload
