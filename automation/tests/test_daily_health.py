from datetime import datetime, timezone

from automation import daily_health as dh


def test_recent_errors_counts_only_last_24h(tmp_path):
    now = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)
    (tmp_path / "incubation_bot_20261012.log").write_text(
        "2026-10-12T05:00:00.1Z [ERROR] alt\n2026-10-12T09:00:00.1Z [ERROR] verbindung 5\n", "utf-8")
    (tmp_path / "incubation_bot_20261013.log").write_text("2026-10-13T01:00:00.1Z [INFO] ok\n2026-10-13T02:00:00.1Z [ERROR] verbindung 7\n", "utf-8")
    n, top = dh.recent_errors(now, tmp_path)
    assert n == 2 and top[0][1] == 2


def test_report_flags_restart_and_daily_loss(monkeypatch):
    now = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(dh, "unit_info", lambda *_: {"NRestarts": "2", "ActiveState": "active", "ActiveEnterTimestamp": "neu"})
    monkeypatch.setattr(dh, "recent_errors", lambda *_: (0, []))
    monkeypatch.setattr(dh, "equity_view", lambda: {"hwm": 100.0, "start": 100.0, "last": 98.0, "pnl": -2.0, "pnl_pct": -0.02,
                                                    "daily_loss": 0.02, "dd": 0.02, "updated": now.isoformat(), "day_key": "x"})
    monkeypatch.setattr(dh, "positions_view", lambda: {"bot": 1, "foreign": 0, "account": 1, "foreign_amount": 0})
    _, findings, state = dh.build_report(now, {"n_restarts": 0, "active_enter": "alt", "checked_utc": "2026-10-12T06:55"})
    assert any("Neustart" in f for f in findings) and any("Tagesverlust" in f for f in findings)
    assert state["n_restarts"] == 2
