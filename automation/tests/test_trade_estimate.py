import json
import math
from datetime import datetime, timezone

from automation.account_view import AccountSnapshot, EToroPosition
from automation.trade_estimate import build_backtest_estimate, calibration_lines, infer_exit_reason, net_result_usd
from automation.trade_ledger import TradeLedger

T0 = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc).timestamp()
WINNER = {"strategy": "RSI2", "oos_metrics": {"trade_return_bps_mean": 20.0, "trade_return_bps_std": 100.0,
                                               "trade_return_bps_n": 100}, "oos_win_rate": 0.55}


def test_schaetzung_konfidenz_aus_holdout_statistik():
    est = build_backtest_estimate(WINNER)
    assert est["mean_bps"] == 20.0 and est["n_trades"] == 100 and est["win_rate"] == 0.55
    assert est["ci95_low_bps"] == round(20 - 1.959964 * 10, 3) and est["ci95_high_bps"] == round(20 + 1.959964 * 10, 3)
    assert est["p_positive"] == round(0.5 * (1 + math.erf(2.0 / math.sqrt(2))), 4)   # t = 2.0
    assert build_backtest_estimate({"strategy": "X"}) is None and build_backtest_estimate(None) is None


def test_netto_enthaelt_eroeffnungs_und_schlussgebuehr():
    r = net_result_usd(10.0, 1000.0, 1.0, 0.0, 200.0)       # Krypto-Modell: 1 % je Seite
    assert r["open_fee_usd"] == 10.0 and r["close_fee_usd"] == 10.1 and r["net_usd"] == -10.1
    r = net_result_usd(10.0, 1000.0, 1.0, 10.0, 200.0)       # gemeldete Eröffnungsgebühr hat Vorrang
    assert r["fee_source"] == "etoro+modell" and r["open_fee_usd"] == 10.0


def test_austrittsgrund_stop_oder_bot():
    # long 100 -> Stop 98; PnL -2 % = Kurs 98 -> Stop
    assert infer_exit_reason(True, 100.0, 98.0, 1000.0, 1.0, -20.0) == "broker_stop_vermutet"
    assert infer_exit_reason(True, 100.0, 98.0, 1000.0, 1.0, 5.0) == "bot_ausstieg_vermutet"
    assert infer_exit_reason(False, 100.0, 102.0, 1000.0, 1.0, -20.0) == "broker_stop_vermutet"
    assert infer_exit_reason(True, None, 98.0, 1000.0, 1.0, 1.0) == "unbekannt"


def _pos(pid, pnl, fees=0.0):
    return EToroPosition(position_id=str(pid), instrument_id=14, is_buy=True, amount=1000.0, leverage=1.0, pnl=pnl,
                         stop_loss_rate=98.0, open_rate=100.0, opened_iso="2026-10-17T09:00:00.000Z", external_fees=fees)


def test_ledger_loggt_start_mit_schaetzung_und_close_mit_delta(tmp_path):
    class Log:
        lines = []
        def info(self, m): self.lines.append(m)
        def warning(self, m): self.lines.append(m)
    log, t = Log(), [T0]
    est = build_backtest_estimate(WINNER)
    led = TradeLedger([14], ledger_path=tmp_path / "t.jsonl", open_path=tmp_path / "o.json", log=log, clock=lambda: t[0],
                      meta={14: {"symbol": "AAPL", "strategy": "RSI2", "estimate": est}},
                      commission_rt_bps_fn=lambda _i: 10.0, events_path=tmp_path / "e.jsonl")
    led.observe(AccountSnapshot(credit=0.0, positions=(_pos(1, 0.0),)))
    t[0] += 3600
    led.observe(AccountSnapshot(credit=0.0, positions=(_pos(1, 4.0),)))
    led.observe(AccountSnapshot(credit=0.0, positions=()))
    start = [x for x in log.lines if x.startswith("[TRADE_START]")]
    close = [x for x in log.lines if x.startswith("[TRADE_CLOSE]")]
    assert len(start) == 1 and "+20.00 bps = +2.00 USD" in start[0] and "P(Erwartung>0) 0.98" in start[0]
    assert len(close) == 1 and "Delta" in close[0]
    ev = [json.loads(x) for x in (tmp_path / "e.jsonl").read_text().splitlines()]
    assert [e["event"] for e in ev] == ["open", "close"] and ev[0]["positionId"] == ev[1]["positionId"] == "1"
    res = ev[1]["result"]
    assert res["net_usd"] == round(4.0 - 0.5 - 0.5 * 1004 / 1000, 4)      # 5 bps je Seite
    assert ev[1]["deltaUsd"] == round(res["net_usd"] - 2.0, 4) and ev[1]["insideCi95"] is True
    row = json.loads((tmp_path / "t.jsonl").read_text())
    assert row["netAfterFees"] == res["net_usd"] and row["estimatedUsd"] == 2.0


def test_ledger_ohne_schaetzung_loggt_hinweis_statt_zu_scheitern(tmp_path):
    led = TradeLedger([14], ledger_path=tmp_path / "t.jsonl", open_path=tmp_path / "o.json", events_path=tmp_path / "e.jsonl")
    led.observe(AccountSnapshot(credit=0.0, positions=(_pos(2, 0.0),)))
    led.observe(AccountSnapshot(credit=0.0, positions=()))
    ev = [json.loads(x) for x in (tmp_path / "e.jsonl").read_text().splitlines()]
    assert ev[0]["estimate"] is None and "keine Backtest-Schätzung" in ev[0]["estimateNote"] and ev[1]["deltaUsd"] is None
    assert "noch keine" in calibration_lines(ev)[0]


def test_whitelist_traegt_eingefrorene_schaetzung_und_fuellt_fehlende_nach(tmp_path):
    from automation import incubation as inc
    stages = inc.DeploymentStages(tmp_path / "stages.json")
    for sym, source in (("AAPL", {"backtest_estimate": build_backtest_estimate(WINNER)}), ("MSFT", {})):
        rec = inc.write_incubation_record("RSI2", sym, {"p": 1}, directory=tmp_path, source=source)
        stages.transition("RSI2", sym, inc.CANDIDATE, reason="t")
        stages.transition("RSI2", sym, inc.INCUBATING, reason="t", params_sha256=rec["params_sha256"])
    cur = {"MSFT": {**WINNER, "oos_metrics": {**WINNER["oos_metrics"], "trade_return_bps_mean": 5.0}}}
    out = inc.write_incubation_whitelist(stages, directory=tmp_path, path=tmp_path / "w.json", winners=cur)
    w = out["per_symbol_winners"]
    assert w["AAPL"]["backtest_estimate"]["mean_bps"] == 20.0 and w["MSFT"]["backtest_estimate"]["mean_bps"] == 5.0


def _close(cls, est, net_usd, inv=1000.0):
    return {"event": "close", "assetClass": cls, "estimate": {"mean_bps": est}, "investment": inv, "leverage": 1.0,
            "result": {"net_usd": net_usd}}


def test_kalibrierung_schrumpft_nur_mit_genug_trades(tmp_path):
    from automation.trade_estimate import calibrate, calibrated_fields
    few = calibrate([_close("EQUITY", 100.0, 1.0)] * 5)
    assert few["global"] is None and few["by_class"] == {}
    evs = [_close("EQUITY", 100.0, 5.0)] * 40 + [_close("FOREX", 20.0, 0.5)] * 5       # EQUITY: realisiert 50 bps
    cal = calibrate(evs)
    assert cal["by_class"]["EQUITY"]["lambda"] == 0.5 and cal["by_class"]["EQUITY"]["basis"] == "klasse"
    assert cal["by_class"]["FOREX"]["basis"] == "gesamt"                               # zu wenige Trades: erbt global
    neg = calibrate([_close("EQUITY", 100.0, -5.0)] * 40)
    assert neg["by_class"]["EQUITY"]["lambda"] == 0.0 and neg["by_class"]["EQUITY"]["lambda_raw"] < 0
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cal))
    f = calibrated_fields({"mean_bps": 80.0}, path, "EQUITY")
    assert f["calibrated_mean_bps"] == 40.0 and f["calibration_lambda"] == 0.5
    assert calibrated_fields({"mean_bps": 80.0}, tmp_path / "fehlt.json", "EQUITY")["calibration_lambda"] == 1.0


def test_kalibrierungs_cli_schreibt_datei_und_bericht(tmp_path, monkeypatch):
    from automation import calibrate_estimates as ce
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    ev = tmp_path / "e.jsonl"
    ev.write_text("\n".join(json.dumps({**_close("EQUITY", 100.0, 5.0), "ts": now, "deltaUsd": -5.0,
                                        "estimateUsd": {"expected_usd": 10.0}, "result": {"net_usd": 5.0}}) for _ in range(31)))
    monkeypatch.setattr(ce, "EVENTS_PATH", ev)
    monkeypatch.setattr(ce, "CALIBRATION_PATH", tmp_path / "cal.json")
    monkeypatch.setattr(ce, "REPORT_DIR", tmp_path / "rep")
    assert ce.main() == 0
    assert json.loads((tmp_path / "cal.json").read_text())["global"]["lambda"] == 0.5
    assert list((tmp_path / "rep").glob("*.md"))
