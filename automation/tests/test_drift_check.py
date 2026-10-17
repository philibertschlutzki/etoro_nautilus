import json
import pickle

import numpy as np

from automation import drift_check as dc

DAY = dc.DAY_NS


def _trades(rets_bps, end_day=1000, step=3, side="BUY"):
    out = []
    n = len(rets_bps)
    for k, r in enumerate(rets_bps):
        out.append({"side": side, "px_open": 100.0, "px_close": 100.0 * (1 + r / 1e4), "exit_ts": (end_day - (n - 1 - k) * step) * DAY})
    return out


def test_trade_returns_sign_and_skip_incomplete():
    ts, r = dc.trade_returns([{"side": "SELL", "px_open": 100.0, "px_close": 99.0, "exit_ts": 5}, {"side": "BUY", "px_open": 100.0}])
    assert len(r) == 1 and abs(r[0] - 100.0) < 1e-6


def test_flag_on_real_break_not_on_stable():
    rng = np.random.default_rng(1)
    broken = np.concatenate([rng.normal(40, 30, 120), rng.normal(-60, 30, 30)])   # letzte 30 Trades (90 Tage) kippen
    stable = rng.normal(40, 30, 150)
    pairs = {("1", "S"): dc.trade_returns(_trades(broken)), ("2", "S"): dc.trade_returns(_trades(stable))}
    rows = {r["id"]: r for r in dc.analyse(pairs, 1001 * DAY, 90.0)}
    assert rows["1"]["flag"] and not rows["2"]["flag"]


def test_too_few_trades_skipped():
    pairs = {("1", "S"): dc.trade_returns(_trades([10.0] * 10))}
    assert dc.analyse(pairs, 1001 * DAY, 90.0) == []


def test_validate_runs_and_main_writes_files(tmp_path, monkeypatch):
    rng = np.random.default_rng(2)
    d = tmp_path / "a_trades"
    d.mkdir()
    for i in range(60):
        (d / f"{i}.pkl").write_bytes(pickle.dumps({"S": _trades(rng.normal(10, 40, 400), end_day=1500, step=3)}))
    out, flags = tmp_path / "out", tmp_path / "flags.json"
    assert dc.main(["--trades-dir", str(d), "--out", str(out), "--flags", str(flags), "--validate"]) == 0
    assert list(out.glob("*.md")) and json.loads(flags.read_text())["pairs"]
    assert "Validierung" in next(out.glob("*.md")).read_text()


def test_main_without_data_exits_2(tmp_path):
    assert dc.main(["--trades-dir", str(tmp_path), "--out", str(tmp_path / "o"), "--flags", str(tmp_path / "f.json")]) == 2


def test_classes_fallback_auf_metadaten(tmp_path):
    md = tmp_path / "metadata.json"
    md.write_text(json.dumps({"InstrumentDisplayDatas": [
        {"InstrumentID": 1, "InstrumentTypeID": 1}, {"InstrumentID": 25, "InstrumentTypeID": 5},
        {"InstrumentID": 99, "InstrumentTypeID": 10}, {"InstrumentID": 987654, "InstrumentTypeID": 99}]}))
    c = dc.classes(md)
    assert c["1"] == "forex" and c["25"] == "equity" and c["99"] == "crypto" and "987654" not in c
    assert dc.classes(tmp_path / "fehlt.json").get("22") == "commodity"      # Karte greift weiter
