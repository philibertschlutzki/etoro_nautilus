import json
from datetime import datetime, timezone
from pathlib import Path

from automation import paper_weekly_report as w

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
INSTR = {"100000": {"symbol": "BTC.ETORO", "asset_class": "crypto"}, "14": {"symbol": "AUDJPY.ETORO", "asset_class": "forex"}}


def _row(pid, iid, net, inv, opened="2026-10-11T08:00:00Z", closed="2026-10-12T00:00:00Z", buy=True):
    return {"positionId": pid, "instrumentId": iid, "isBuy": buy, "netProfit": net, "investment": inv,
            "initialInvestment": inv, "openTimestamp": opened, "closeTimestamp": closed, "fees": 0.0}


def test_krypto_gebuehr_wird_nachgerechnet_am_echten_beispiel():
    # echter Fall: 199.56 USD Einsatz, Brutto +1.76 -> Gebühr 1 % von 199.56 + 1 % von 201.32 = 4.01
    t, = w.build_trades({"1": _row(1, 100000, 1.76, 199.56)}, instruments=INSTR, strategies={}, fee_rate=0.01)
    assert round(t.fee, 2) == 4.01 and round(t.net, 2) == round(1.76 - 4.01, 2)


def test_forex_hat_keine_externe_gebuehr():
    t, = w.build_trades({"1": _row(1, 14, -0.09, 1009.94)}, instruments=INSTR, strategies={"AUDJPY.ETORO": "Rsi2Reversion"},
                        fee_rate=0.01)
    assert t.fee == 0.0 and t.net == -0.09 and t.strategy == "Rsi2Reversion"


def test_ledger_sammelt_je_positionid_einmal(tmp_path):
    p = tmp_path / "trades.jsonl"
    rows = [_row(1, 14, 0.1, 100), _row(2, 14, -0.2, 100)]
    _, new = w.merge_ledger(rows, p)
    assert new == 2
    _, new = w.merge_ledger(rows + [_row(3, 14, 0.0, 100)], p)
    assert new == 1 and len(p.read_text("utf-8").splitlines()) == 3


def test_offene_position_ohne_closeTimestamp_wird_nicht_gesammelt(tmp_path):
    row = _row(9, 14, 0.0, 100)
    row["closeTimestamp"] = None
    _, new = w.merge_ledger([row], tmp_path / "t.jsonl")
    assert new == 0


def test_summarize_und_aussagekraft():
    ledger = {str(i): _row(i, 14, 0.5 if i % 2 else -0.4, 100) for i in range(1, 11)}
    trades = w.build_trades(ledger, instruments=INSTR, strategies={}, fee_rate=0.01)
    s = w.summarize(trades)
    assert s["n"] == 10 and s["wins"] == 5 and not s["enough"]
    lo, hi = s["wilson"]
    assert 0.2 < lo < 0.5 < hi < 0.8
    text = w.render(trades, since=NOW, now=NOW, equity=None, open_positions=[], new_trades=0)
    assert "Nur 10 abgeschlossene Trades" in text and "Mindestmenge" in text


def test_render_belastbar_ab_30_trades():
    ledger = {str(i): _row(i, 14, 1.0 if i % 3 else -1.0, 100) for i in range(1, 41)}
    trades = w.build_trades(ledger, instruments=INSTR, strategies={}, fee_rate=0.01)
    text = w.render(trades, since=NOW, now=NOW, equity=None, open_positions=[], new_trades=0)
    assert "belastbar genug" in text and "t-Wert" in text


def test_run_schreibt_wochen_und_latest_datei(tmp_path, monkeypatch):
    monkeypatch.setattr("automation.account_guard.verify_paper_account", lambda *a, **k: "real")
    env = {"ETORO_ENV": "demo", "ETORO_API_KEY": "k", "ETORO_USER_KEY": "u"}
    pnl = {"clientPortfolio": {"credit": 9000.0, "positions": []}}
    rc = w.run(days=7, now=NOW, environ=env, history=lambda *a, **k: [_row(1, 14, 0.2, 100)], pnl_fetch=lambda: pnl,
               report_dir=tmp_path / "r", trades_path=tmp_path / "t.jsonl")
    assert rc == 0
    names = sorted(p.name for p in (tmp_path / "r").iterdir())
    assert names == ["paper_weekly_2026-W42.md", "paper_weekly_latest.md"]
    assert "Wöchentlicher Paper-Bericht 2026-W42" in (tmp_path / "r" / "paper_weekly_latest.md").read_text("utf-8")


def test_demo_konto_nutzt_den_bot_ledger_statt_der_api_historie(tmp_path, monkeypatch):
    monkeypatch.setattr("automation.account_guard.verify_paper_account", lambda *a, **k: "demo")
    ledger = tmp_path / "demo_trades.jsonl"
    ledger.write_text(json.dumps(_row(11, 14, 0.4, 1000.0)) + "\n", encoding="utf-8")
    env = {"ETORO_ENV": "demo", "ETORO_API_KEY": "k", "ETORO_USER_KEY": "u"}

    def history(*a, **k):
        raise AssertionError("Demo hat keine API-Historie")

    rc = w.run(days=7, now=NOW, environ=env, history=history, pnl_fetch=lambda: {"clientPortfolio": {"credit": 5.0, "positions": []}},
               report_dir=tmp_path / "r", trades_path=ledger)
    assert rc == 0
    text = (tmp_path / "r" / "paper_weekly_latest.md").read_text("utf-8")
    assert "| alle Klassen | 1 |" in text
