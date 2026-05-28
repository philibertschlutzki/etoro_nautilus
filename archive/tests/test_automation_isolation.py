import json
from pathlib import Path

class TestAutomationIsolation:
    def test_instrument_map_json_valid(self):
        data = json.loads(
            Path("automation/config/instrument_map.json").read_text()
        )
        assert "instruments" in data
        for eid, entry in data["instruments"].items():
            assert "symbol" in entry
            assert "asset_class" in entry
            assert "price_precision" in entry
            assert "size_precision" in entry
