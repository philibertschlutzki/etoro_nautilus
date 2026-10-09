"""Der Daten-/Exec-Client darf nicht weniger Instrumente kennen als config/instrument_map.json."""
import json
from pathlib import Path

from automation.adapters.instrument_map import ETORO_INSTRUMENTS

_JSON = Path(__file__).resolve().parents[1] / "config" / "instrument_map.json"


def test_adapter_map_covers_config_map():
    cfg = json.loads(_JSON.read_text("utf-8"))["instruments"]
    missing = {k: v["symbol"] for k, v in cfg.items() if ETORO_INSTRUMENTS.get(k) != v["symbol"]}
    assert not missing, missing


def test_incubation_candidates_are_known():
    assert ETORO_INSTRUMENTS["9506"] == "MOD.ETORO" and ETORO_INSTRUMENTS["9555"] == "CRS.ETORO"
