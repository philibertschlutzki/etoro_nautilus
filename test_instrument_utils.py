import pytest
from automation.adapters.instrument_utils import get_size_precision

def test_get_size_precision():
    assert get_size_precision("BTC.ETORO") == 8
    assert get_size_precision("AAPL.ETORO") == 2
