"""Issue #448 (P0, Pitfall #80) — robuster, fail-loud Fill-Zeitstempel & korrekter IS/OOS-Split.

Der frühere stille ``getattr(f, 'ts_event', getattr(f, 'ts_init', 0))`` defaultete bei fehlendem
Feld auf ``0`` und schob damit JEDEN Round-Trip vor ``start_ns + is_window`` ⇒ struktureller
OOS=0-Kollaps. Diese Tests pinnen:

  * ``_fill_ts_ns`` extrahiert robust (ts_event → ts_last → ts_init) und wirft fail-loud, statt
    still auf 0 zu defaulten;
  * der IS/OOS-Split klassifiziert In-Window-Fills korrekt (OOS-Round-Trips > 0);
  * der bekannte Reproduktionsfall (142 uniforme Exits über [2025-05-15, 2026-05-10]) liefert
    IS=72 / OOS=70.
"""
import math
from collections import namedtuple
from unittest.mock import MagicMock

import pandas as pd
import pytest

from automation.backtest_runner import _fill_ts_ns, extract_metrics

_DAY_NS = 86400 * 1_000_000_000
START_NS = int(pd.Timestamp("2025-05-15", tz="UTC").value)
WF = {"is_window_days": 180, "oos_window_days": 45, "splits": 4}

_Row = namedtuple("_Row", ["ts_event", "ts_last", "ts_init"])


# ---------------------------------------------------------------------------
# _fill_ts_ns — robuste, fail-loud Extraktion
# ---------------------------------------------------------------------------
def test_fill_ts_ns_prefers_ts_event_int():
    assert _fill_ts_ns(_Row(ts_event=123, ts_last=None, ts_init=None)) == 123


def test_fill_ts_ns_converts_timestamp():
    ts = pd.Timestamp("2025-06-01", tz="UTC")
    assert _fill_ts_ns(_Row(ts_event=ts, ts_last=None, ts_init=None)) == int(ts.value)


def test_fill_ts_ns_falls_back_to_ts_last():
    # generate_order_fills_report-Fallback hat KEIN ts_event, sondern ts_last.
    OnlyLast = namedtuple("OnlyLast", ["ts_last", "ts_init"])
    assert _fill_ts_ns(OnlyLast(ts_last=999, ts_init=111)) == 999


def test_fill_ts_ns_falls_back_to_ts_init():
    OnlyInit = namedtuple("OnlyInit", ["ts_init"])
    assert _fill_ts_ns(OnlyInit(ts_init=222)) == 222


def test_fill_ts_ns_skips_nan_to_next_field():
    assert _fill_ts_ns(_Row(ts_event=float("nan"), ts_last=None, ts_init=333)) == 333


def test_fill_ts_ns_raises_when_no_timestamp():
    NoTs = namedtuple("NoTs", ["instrument_id"])
    with pytest.raises(ValueError, match="ts_event/ts_last/ts_init"):
        _fill_ts_ns(NoTs(instrument_id="AAA.ETORO"))


# ---------------------------------------------------------------------------
# IS/OOS-Split über extract_metrics
# ---------------------------------------------------------------------------
def _engine_with_fills(records):
    df = pd.DataFrame.from_records(records)
    engine = MagicMock()
    engine.trader.generate_fills_report.return_value = df
    return engine


def _round_trip(ts_exit_ns, px_in=100.0, px_out=101.0, iid="AAA.ETORO"):
    """Ein geschlossener Round-Trip: BUY kurz vor dem Exit, SELL am Exit-Timestamp."""
    return [
        {"instrument_id": iid, "last_qty": 1.0, "last_px": px_in,
         "order_side": "BUY", "ts_event": ts_exit_ns - 3600 * 1_000_000_000},
        {"instrument_id": iid, "last_qty": 1.0, "last_px": px_out,
         "order_side": "SELL", "ts_event": ts_exit_ns},
    ]


def test_oos_split_yields_nonzero_oos_trades():
    """In-Window-Fills über [start, start+360d] ⇒ OOS-Round-Trips > 0 (kein struktureller OOS=0)."""
    records = []
    for i in range(40):
        exit_ns = START_NS + int((i + 0.5) / 40 * 360 * _DAY_NS)
        records.extend(_round_trip(exit_ns))
    engine = _engine_with_fills(records)
    out = extract_metrics(engine, starting_capital=10000.0, walk_forward_dict=WF, start_ns=START_NS)

    assert out["metrics"]["total_trades"] > 0
    assert out["oos_metrics"]["total_trades"] > 0, "OOS-Trades dürfen nicht strukturell 0 sein (#448)"
    assert (out["metrics"]["total_trades"] + out["oos_metrics"]["total_trades"]) == 40


def test_reproduction_case_142_uniform_exits_is72_oos70():
    """Regressions-Anker: 142 uniforme Exits über [2025-05-15, 2026-05-10] ⇒ IS=72 / OOS=70."""
    records = []
    for i in range(142):
        exit_ns = START_NS + round(i / 141 * 360 * _DAY_NS)
        records.extend(_round_trip(exit_ns))
    engine = _engine_with_fills(records)
    out = extract_metrics(engine, starting_capital=10000.0, walk_forward_dict=WF, start_ns=START_NS)

    assert out["metrics"]["total_trades"] == 72
    assert out["oos_metrics"]["total_trades"] == 70


def test_fill_ts_span_exposed_for_telemetry():
    """extract_metrics liefert die beobachtete Fill-ts-Spanne (für den data_window-Block, #444)."""
    exit_ns = START_NS + 200 * _DAY_NS
    out = extract_metrics(_engine_with_fills(_round_trip(exit_ns)),
                          starting_capital=10000.0, walk_forward_dict=WF, start_ns=START_NS)
    assert out["_fill_ts_max"] == exit_ns
    assert out["_fill_ts_min"] == exit_ns


def test_implausible_fill_domain_fails_loud():
    """Liegen ALLE Fills vor Fensterbeginn (Domänen-Defekt), kollabiert der Split NICHT still auf
    IS, sondern extract_metrics liefert NULL (die Plausibilitäts-Assertion greift, Pitfall #80)."""
    # Exits weit VOR start_ns (z. B. relative/0-nahe Clock) ⇒ fill_ts_max < start_ns.
    bad_exit = START_NS - 500 * _DAY_NS
    out = extract_metrics(_engine_with_fills(_round_trip(bad_exit)),
                          starting_capital=10000.0, walk_forward_dict=WF, start_ns=START_NS)
    # Outer-Guard fängt die ValueError ⇒ NULL (0 Trades) statt stiller IS-Klassifizierung.
    assert out["metrics"]["total_trades"] == 0
    assert out["oos_metrics"]["total_trades"] == 0
