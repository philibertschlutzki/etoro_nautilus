"""Mindest-Positionsbetrag je Instrument bei eToro.

eToro lehnt Orders unter dem Mindestbetrag asynchron ab (``error_720: ... leveraged InitialPositionAmount:
197.92 MinimumPositionAmount: 1000``). Forex hat 1000 USD. Der Wert steht als Standard je Anlageklasse hier;
zusätzlich lernt der Adapter jeden gemeldeten Mindestbetrag je Instrument (``data/state/min_position_amounts.json``),
damit z. B. Rohstoffe ohne Codeänderung erfasst werden. Der Allocator hebt die Positionsgröße auf den Mindestbetrag
an, wenn Budget und Konzentrationsgrenze es zulassen, sonst überspringt er das Instrument, statt stündlich
eine abgelehnte Order zu senden.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_MIN_BY_ASSET_CLASS = {"FOREX": 1000.0}
LEARNED_PATH = Path(__file__).resolve().parent.parent / "data" / "state" / "min_position_amounts.json"
_MIN_RE = re.compile(r"MinimumPositionAmount:\s*([0-9]+(?:\.[0-9]+)?)")


def parse_minimum(message: str) -> float | None:
    m = _MIN_RE.search(message or "")
    return float(m.group(1)) if m else None


def _load(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}


def learn_from_rejection(instrument_id: str, message: str, path: Path = LEARNED_PATH) -> float | None:
    """Merkt sich den in einer Ablehnung genannten Mindestbetrag. Rückgabe: der Betrag oder None."""
    minimum = parse_minimum(message)
    if minimum is None:
        return None
    known = _load(path)
    if known.get(instrument_id) != minimum:
        known[instrument_id] = minimum
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(known, indent=1, sort_keys=True), "utf-8")
        except OSError:
            pass
    return minimum


def min_notional(instrument_id: str, asset_class: str, path: Path = LEARNED_PATH) -> float:
    """Größerer Wert aus Klassen-Standard und gelerntem Instrument-Minimum (0.0 = kein bekanntes Minimum)."""
    return max(DEFAULT_MIN_BY_ASSET_CLASS.get((asset_class or "").upper(), 0.0),
               _load(path).get(instrument_id, 0.0))
