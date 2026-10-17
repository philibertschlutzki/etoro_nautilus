"""Tests der Stufe-C-Schutzschicht (ohne Netzwerk, ohne Orders)."""
import datetime as dt, os, sys
import pytest
sys.path.insert(0, os.path.dirname(__file__))
import guard


def mk(live=False, confirm=None, excl=()):
    return guard.Gate(live, confirm, set(excl), "/tmp/ebc_state_test.json", "c4", lambda m: None)


def test_dry_run_never_posts(monkeypatch):
    called = []
    monkeypatch.setattr(guard, "_req", lambda *a, **k: called.append(a) or (200, {}))
    st, body = mk().post(guard.EXEC + "/x", {"a": 1})
    assert body == {"dryrun": True} and not called


def test_live_requires_matching_confirmation(monkeypatch):
    monkeypatch.setattr(guard, "ENV_MODE", "real")
    monkeypatch.setenv("ETORO_PAPER_ACCOUNT_CID", "123")
    monkeypatch.setattr(guard, "_req", lambda *a, **k: (200, {}))
    with pytest.raises(guard.Refused):
        mk(live=True, confirm="999").post(guard.EXEC + "/x", {})
    assert mk(live=True, confirm="123").post(guard.EXEC + "/x", {})[0] == 200


def test_open_refusals(monkeypatch):
    monkeypatch.setattr(guard, "verify_account", lambda: "1")
    monkeypatch.setattr(guard, "positions", lambda pnl=None: [])
    monkeypatch.setattr(guard, "market_open", lambda k, now=None: True)
    g = mk(excl={5})
    with pytest.raises(guard.Refused): g.check_open(5, 10, True, "K1")        # Bot-Instrument
    with pytest.raises(guard.Refused): g.check_open(6, 500, True, "K1")       # Betrag
    with pytest.raises(guard.Refused): g.check_open(6, 10, False, "K5")       # Krypto short
    g.check_open(6, 10, True, "K1")                                           # ok
    g.state["positions"] = {str(i): {} for i in range(20)}
    with pytest.raises(guard.Refused): g.check_open(7, 10, True, "K1")        # > 20 Testpositionen


def test_foreign_position_blocks(monkeypatch):
    monkeypatch.setattr(guard, "verify_account", lambda: "1")
    monkeypatch.setattr(guard, "market_open", lambda k, now=None: True)
    monkeypatch.setattr(guard, "positions", lambda pnl=None: [{"instrumentID": 6, "positionID": 99}])
    with pytest.raises(guard.Refused): mk().check_open(6, 10, True, "K1")


def test_close_only_own_positions(monkeypatch):
    monkeypatch.setattr(guard, "verify_account", lambda: "1")
    g = mk()
    with pytest.raises(guard.Refused): g.check_close("42")
    g.state["positions"]["42"] = {"instrument": 1}
    g.check_close("42")


def test_account_mismatch_aborts(monkeypatch):
    monkeypatch.setenv("ETORO_PAPER_ACCOUNT_CID", "123")
    monkeypatch.setattr(guard, "get", lambda url: {"realCid": 555})
    with pytest.raises(guard.Refused): guard.verify_account()


def test_market_hours():
    sat = dt.datetime(2026, 10, 10, 15, 0, tzinfo=dt.timezone.utc)
    mon = dt.datetime(2026, 10, 12, 15, 0, tzinfo=dt.timezone.utc)      # 11:00 New York
    assert guard.market_open("K5", sat) and not guard.market_open("K1", sat) and not guard.market_open("K2", sat)
    assert guard.market_open("K1", mon) and guard.market_open("K2", mon)


def test_cfd_classes_blocked_by_default_and_fx_minimum(monkeypatch):
    monkeypatch.setattr(guard, "verify_account", lambda: "1")
    monkeypatch.setattr(guard, "positions", lambda pnl=None: [])
    monkeypatch.setattr(guard, "market_open", lambda k, now=None: True)
    g = mk()
    with pytest.raises(guard.Refused): g.check_open(8, 20, True, "K2")        # K2 nicht freigegeben
    g.classes = ("K1", "K2", "K5")
    with pytest.raises(guard.Refused): g.check_open(8, 50, True, "K2")        # FX nur mit genau 1010 USD
    g.phase = "c2"
    with pytest.raises(guard.Refused): g.check_open(8, 1500, True, "K2")      # nicht 1010
    monkeypatch.setattr(guard, "account_equity", lambda: 10000.0)
    with pytest.raises(guard.Refused): g.check_open(8, 1010, True, "K2")      # 4-%-Exposure-Standard bindet (1010 = 10 %)
    g.fx_max_exposure = 0.25
    g.check_open(8, 1010, True, "K2")                                         # nach expliziter Freigabe ok
    g.state["positions"] = {str(i): {"klasse": "K2", "amount": 1010} for i in range(4)}
    g.fx_max_exposure = 1.0
    with pytest.raises(guard.Refused): g.check_open(9, 1010, True, "K2")      # max. 4 FX-Testpositionen


def test_recover_adopts_pending_by_order_id(monkeypatch, tmp_path):
    import run_stage_c
    monkeypatch.setattr(guard, "positions", lambda pnl=None: [{"orderID": 77, "positionID": 5, "units": 1.5}])
    g = guard.Gate(False, None, set(), str(tmp_path / "s.json"), "c4", lambda m: None)
    g.state["pending"]["77"] = {"instrument": 9, "klasse": "K1", "is_buy": False, "amount": 20.0}
    g.state["pending"]["88"] = {"instrument": 9, "klasse": "K1", "is_buy": False, "amount": 20.0}   # abgelehnt, keine Position
    run_stage_c.recover(g)
    assert "5" in g.state["positions"] and not g.state["pending"]


def test_w8ben_phase_cap_is_10():
    assert guard.CAPS["w8"] == 10.0


def test_whoami_scrubs_secrets():
    import whoami
    out = whoami.scrub({"x-api-key": "abc", "name": "n", "nested": [{"accessToken": "t", "v": 1}]})
    assert out["x-api-key"] == "<entfernt>" and out["nested"][0]["accessToken"] == "<entfernt>" and out["nested"][0]["v"] == 1


def test_demo_mode_paths_and_confirmation(monkeypatch):
    monkeypatch.setenv("ETORO_PAPER_ACCOUNT_CID", "123")
    monkeypatch.setattr(guard, "_req", lambda *a, **k: (200, {}))
    monkeypatch.setattr(guard, "get", lambda url, tries=6: {})
    guard.FORCE_ENV[0] = "demo"
    try:
        assert guard.verify_account() == "demo"
        assert guard.EXEC.endswith("/execution/demo") and "/demo/pnl" in guard.PNL
        with pytest.raises(guard.Refused): mk(live=True, confirm="123").post(guard.EXEC + "/x", {})
        assert mk(live=True, confirm="demo").post(guard.EXEC + "/x", {})[0] == 200
    finally:
        guard.FORCE_ENV[0] = None
        guard.set_environment()


def test_feeprobe_cap_1000():
    assert guard.CAPS["fp"] == 1000.0
