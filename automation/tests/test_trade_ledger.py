import json
from datetime import datetime, timezone

from automation.account_view import AccountSnapshot, EToroAccountView, EToroPosition, parse_snapshot
from automation.trade_ledger import TradeLedger

T0 = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc).timestamp()


def _pos(pid, iid=14, pnl=0.5, amount=1000.0, buy=True, rate=110.4, opened="2026-10-12T18:00:11.870Z", fees=0.0):
    return EToroPosition(position_id=str(pid), instrument_id=iid, is_buy=buy, amount=amount, leverage=1.0, pnl=pnl,
                         stop_loss_rate=100.0, open_rate=rate, opened_iso=opened, external_fees=fees)


def _snap(*positions):
    return AccountSnapshot(credit=1000.0, positions=tuple(positions))


def _ledger(tmp_path, ids=(14,), clock=None):
    t = [T0]
    led = TradeLedger(ids, ledger_path=tmp_path / "t.jsonl", open_path=tmp_path / "o.json",
                      clock=clock or (lambda: t[0]))
    return led, t


def _rows(tmp_path):
    p = tmp_path / "t.jsonl"
    return [json.loads(x) for x in p.read_text("utf-8").splitlines()] if p.exists() else []


def test_verschwundene_position_wird_als_trade_mit_letztem_pnl_gebucht(tmp_path):
    led, t = _ledger(tmp_path)
    assert led.observe(_snap(_pos(1, pnl=1.0))) == []
    t[0] += 10
    led.observe(_snap(_pos(1, pnl=2.5)))
    t[0] += 10
    closed = led.observe(_snap())
    assert len(closed) == 1
    row, = _rows(tmp_path)
    assert row["positionId"] == "1" and row["netProfit"] == 2.5 and row["isBuy"] is True
    assert row["investment"] == 1000.0 and row["openRate"] == 110.4 and row["pnlAgeS"] == 10.0
    assert row["openTimestamp"] == "2026-10-12T18:00:11.870Z" and row["closeTimestamp"].startswith("2026-10-12T18:00:20")


def test_fremde_instrumente_und_noch_offene_positionen_werden_nicht_gebucht(tmp_path):
    led, _ = _ledger(tmp_path, ids=(14,))
    led.observe(_snap(_pos(1), _pos(2, iid=1053)))
    assert led.observe(_snap(_pos(1))) == []                    # 1053 gehört nicht dem Bot
    assert _rows(tmp_path) == []


def test_zustand_uebersteht_neustart_und_erfasst_schliessung_waehrend_der_pause(tmp_path):
    led, t = _ledger(tmp_path)
    led.observe(_snap(_pos(7, pnl=3.0)))
    led2, t2 = _ledger(tmp_path, clock=lambda: T0 + 600)           # neuer Prozess, gleiche Dateien
    led2.observe(_snap())
    row, = _rows(tmp_path)
    assert row["positionId"] == "7" and row["netProfit"] == 3.0 and row["pnlAgeS"] == 600.0


def test_kein_doppelter_eintrag(tmp_path):
    led, _ = _ledger(tmp_path)
    led.observe(_snap(_pos(1)))
    led.observe(_snap())
    led.observe(_snap())
    assert len(_rows(tmp_path)) == 1


def test_account_view_ruft_beobachter_und_ueberlebt_deren_fehler():
    seen, calls = [], []

    def boom(snap):
        calls.append(1)
        raise RuntimeError("kaputt")

    pnl = {"clientPortfolio": {"credit": 10.0, "positions": []}}
    view = EToroAccountView("k", "u", "demo", fetch=lambda *a: pnl, observers=[boom, seen.append], log=None)
    snap = view.snapshot(fresh=True)
    assert snap is not None and calls == [1] and len(seen) == 1


def test_parse_snapshot_liest_einstieg_und_gebuehr():
    item = {"positionID": 5, "instrumentID": 100000, "isBuy": True, "amount": 197.67, "openRate": 84041.4,
            "openDateTime": "2026-10-11T08:00:00.463Z", "totalExternalFees": 1.98, "units": 0.0023,
            "unrealizedPnL": {"pnL": -1.2}}
    p, = parse_snapshot({"clientPortfolio": {"credit": 1.0, "positions": [item]}}).positions
    assert p.open_rate == 84041.4 and p.opened_iso == "2026-10-11T08:00:00.463Z" and p.external_fees == 1.98 and p.pnl == -1.2
