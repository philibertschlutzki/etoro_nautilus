"""Wochenbericht: Fills aus Logs werden FIFO zu Round-Trips gepaart, Gebühren modelliert, Datei höchstens wöchentlich."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from automation import paper_weekly_report as pw

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def _line(ts, inst, side, qty, px, tid, comm="0.00 USD"):
    return (f"{ts} [INFO] S: OrderFilled(instrument_id={inst}, trade_id={tid}, order_side={side}, order_type=MARKET, "
            f"last_qty={qty}, last_px={px}, currency=USD, commission={comm}, liquidity_side=TAKER)")


def _log(tmp_path, *lines):
    p = tmp_path / "incubation_bot_20261012.log"
    p.write_text("\n".join(lines), "utf-8")
    return p


def test_long_round_trip_with_modelled_crypto_fee(tmp_path):
    p = _log(tmp_path, _line("2026-10-12T01:00:00.1Z", "BTC.ETORO", "BUY", 0.01, 80000, "a"),
             _line("2026-10-12T02:00:00.1Z", "BTC.ETORO", "SELL", 0.01, 81000, "b"))
    trips, open_qty = pw.pair_round_trips(pw.parse_fills([p]), {"BTC.ETORO": 200.0})
    assert open_qty == {} and len(trips) == 1
    assert round(trips[0].gross, 2) == 10.0
    assert round(trips[0].fee, 4) == round((800 + 810) * 0.01, 4)      # 1 % je Seite
    assert trips[0].net < 0


def test_short_partial_close_and_open_remainder(tmp_path):
    p = _log(tmp_path, _line("2026-10-12T01:00:00Z", "EURUSD.ETORO", "SELL", 10, 1.10, "a"),
             _line("2026-10-12T02:00:00Z", "EURUSD.ETORO", "BUY", 4, 1.08, "b"))
    trips, open_qty = pw.pair_round_trips(pw.parse_fills([p]), {})
    assert len(trips) == 1 and round(trips[0].gross, 4) == 0.08         # Short gewinnt 0.02 * 4
    assert round(open_qty["EURUSD.ETORO"], 6) == -6


def test_logged_commission_wins_and_duplicates_are_dropped(tmp_path):
    p = _log(tmp_path, _line("2026-10-12T01:00:00Z", "X.ETORO", "BUY", 1, 10, "a", "0.50 USD"),
             _line("2026-10-12T01:00:00Z", "X.ETORO", "BUY", 1, 10, "a", "0.50 USD"),
             _line("2026-10-12T02:00:00Z", "X.ETORO", "SELL", 1, 11, "b", "0.50 USD"))
    fills = pw.parse_fills([p])
    assert len(fills) == 2
    trips, _ = pw.pair_round_trips(fills, {"X.ETORO": 999.0})
    assert round(trips[0].fee, 2) == 1.00


def test_render_and_weekly_file_written_once(tmp_path):
    _log(tmp_path, _line("2026-10-11T01:00:00Z", "A.ETORO", "BUY", 1, 10, "a"),
         _line("2026-10-11T02:00:00Z", "A.ETORO", "SELL", 1, 12, "b"))
    state = pw.maybe_write_weekly({}, NOW, report_dir=tmp_path / "r", log_dir=tmp_path)
    files = list((tmp_path / "r").glob("paper_weekly_2026-W*.md"))
    assert len(files) == 1 and "Round-Trips" in files[0].read_text("utf-8") and state["last_weekly"]
    again = pw.maybe_write_weekly(state, NOW + timedelta(days=3), report_dir=tmp_path / "r2", log_dir=tmp_path)
    assert again == state and not (tmp_path / "r2").exists()
