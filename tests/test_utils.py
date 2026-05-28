import pytest
from automation.utils import _fallback_precisions

class TestUtils:
    def test_fallback_precisions(self):
        # Test Crypto
        assert _fallback_precisions("BTC") == (2, 8)
        assert _fallback_precisions("ETH") == (2, 8)

        # Test special Crypto (PEPE/SHIB)
        assert _fallback_precisions("PEPExM") == (8, 8)
        assert _fallback_precisions("SHIBxM") == (8, 8)

        # Test Fractional
        assert _fallback_precisions("NATGAS") == (5, 5)

        # Test Equities / Default
        assert _fallback_precisions("AAPL") == (2, 0)
        assert _fallback_precisions("TSLA") == (2, 0)
