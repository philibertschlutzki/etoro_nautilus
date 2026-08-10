"""Issue #937/#938 (Katalog A, Pitfalls #294/#295) — ``emit_execution_event`` darf niemals eine
Exception werfen, wenn ein Payload nicht-skalare Dict-Keys (z. B. ``(strategy, symbol)``-Tuples)
enthält: ``json.dumps(default=str)`` schützt nur Werte, nicht Keys. Root-Cause: ein 143-Symbol-
Sweep starb an genau dieser Stelle nach dem zweiten Symbol
(``sweep._offending_pairs_for_fail_fast_check`` lieferte ``dict[tuple[str, str], object]``).

#938 ergänzt den kanonischen ``pair_key``-Konstruktor, der ab jetzt die einzige Quelle für
(Strategie, Symbol)-Paar-Schlüssel im Telemetrie-/Report-Pfad ist.
"""
import json
import logging

import pytest

from automation.log_manager import emit_execution_event, _sanitize_for_json, _canonical_key
from automation.optimizer._contracts import pair_key, split_pair_key, PAIR_KEY_SEP
from automation.optimizer.sweep import _offending_pairs_for_fail_fast_check


class _CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


def _logger_with_capture(name: str) -> tuple[logging.Logger, _CapturingHandler]:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _CapturingHandler()
    logger.addHandler(handler)
    return logger, handler


def test_emit_execution_event_survives_tuple_dict_keys():
    logger, handler = _logger_with_capture("test_937_tuple_keys")
    emit_execution_event(logger, "SOME_EVENT", {
        "pairs": {("A", "B"): 1.0, ("C", "D"): 2.5},
    })
    assert len(handler.records) == 1
    line = handler.records[0]
    assert line.startswith("[JSON_EVENT] ")
    payload = json.loads(line[len("[JSON_EVENT] "):])
    assert payload["pairs"] == {"A/B": 1.0, "C/D": 2.5}


def test_emit_execution_event_survives_nan_value():
    logger, handler = _logger_with_capture("test_937_nan")
    emit_execution_event(logger, "SOME_EVENT", {"pairs": {("A", "B"): float("nan")}})
    payload = json.loads(handler.records[0][len("[JSON_EVENT] "):])
    # allow_nan=False turns this into a TELEMETRY_SERIALIZATION_FAILED replacement event, not a
    # crash and not a silently non-parsable "NaN" token in the JSON output.
    assert payload["event_type"] == "TELEMETRY_SERIALIZATION_FAILED"


def test_emit_execution_event_never_raises_even_when_logger_log_throws():
    logger, _ = _logger_with_capture("test_937_logger_throws")

    def _boom(*a, **kw):
        raise IOError("disk full")

    logger.log = _boom  # type: ignore[method-assign]
    # Must not raise — a broken sink must never abort the caller (Pitfall #295).
    emit_execution_event(logger, "SOME_EVENT", {"pairs": {("A", "B"): 1.0}})


def test_sanitize_for_json_canonicalizes_nested_tuple_keys():
    sanitized = _sanitize_for_json({"outer": {("X", "Y"): [1, 2, {("Z",): 3}]}})
    assert sanitized == {"outer": {"X/Y": [1, 2, {"Z": 3}]}}


def test_canonical_key_matches_pair_key_format():
    assert _canonical_key(("StratA", "SYM.ETORO")) == pair_key("StratA", "SYM.ETORO")


def test_pair_key_roundtrip():
    key = pair_key("DonchianRegimeBreakoutStrategy", "LQDA.ETORO")
    assert key == "DonchianRegimeBreakoutStrategy/LQDA.ETORO"
    assert split_pair_key(key) == ("DonchianRegimeBreakoutStrategy", "LQDA.ETORO")


def test_pair_key_rejects_separator_in_component():
    with pytest.raises(ValueError):
        pair_key("Strat/Weird", "SYM.ETORO")


def test_offending_pairs_for_fail_fast_check_returns_string_keys():
    checks = [{
        "name": "check_holding_time_cap",
        "actual": {"TrendPullbackStrategy/LQDA.ETORO": 0.3718, "Rsi2Reversion/GSAT.ETORO": 0.4823},
    }]
    pairs, symbols = _offending_pairs_for_fail_fast_check(checks, "check_holding_time_cap")
    assert set(pairs) == {"TrendPullbackStrategy/LQDA.ETORO", "Rsi2Reversion/GSAT.ETORO"}
    assert symbols == {"LQDA.ETORO", "GSAT.ETORO"}
    # The returned dict must itself be safe to json.dumps directly (no tuple keys survive).
    json.dumps(pairs)


def test_offending_pairs_result_is_end_to_end_telemetry_safe():
    """Regression for the exact #937 crash path: the offending-pairs dict flows straight into an
    emit_execution_event payload (sweep.py SWEEP_ABORTED_ON_FAIL_FAST_INVARIANT/
    SYMBOL_QUARANTINED_ON_FAIL_FAST_INVARIANT)."""
    checks = [{"name": "check_holding_time_cap",
               "actual": {"DonchianRegimeBreakout/LQDA.ETORO": 0.6509}}]
    pairs, _ = _offending_pairs_for_fail_fast_check(checks, "check_holding_time_cap")
    logger, handler = _logger_with_capture("test_937_end_to_end")
    emit_execution_event(logger, "SWEEP_ABORTED_ON_FAIL_FAST_INVARIANT", {
        "check": "check_holding_time_cap", "offending_studies": pairs,
    })
    payload = json.loads(handler.records[0][len("[JSON_EVENT] "):])
    assert payload["offending_studies"] == {"DonchianRegimeBreakout/LQDA.ETORO": 0.6509}
