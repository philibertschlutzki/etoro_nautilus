import pytest
import os
import json
from unittest.mock import patch, MagicMock

from dev_scripts.momentum_ls_universe import parse_and_save

def test_missing_instrument_id(tmp_path):
    # Mock data where 9999999 is definitely not in ETORO_INSTRUMENTS
    data = {
        "clientPortfolio": {
            "positions": [
                {"instrumentID": 100000, "instrumentName": "Bitcoin"},
                {"instrumentID": 9999999, "instrumentName": "UnknownAsset"}
            ]
        }
    }

    out_file = tmp_path / "momentum_ls.json"

    # parse_and_save should NOT exit/crash, and it should write the file
    parse_and_save(data, "mock_user", str(out_file))

    assert out_file.exists()

    with open(out_file, "r") as f:
        result = json.load(f)

    assert result["username"] == "mock_user"
    assert len(result["universe"]) == 2

    # Bitcoin should have symbol mapped
    assert result["universe"][0]["etoro_id"] == "100000"
    assert result["universe"][0]["symbol"] == "BTC.ETORO"

    # Unknown asset should have symbol as None (null in JSON)
    assert result["universe"][1]["etoro_id"] == "9999999"
    assert result["universe"][1]["symbol"] is None
    assert result["universe"][1]["raw_name"] == "UnknownAsset"
