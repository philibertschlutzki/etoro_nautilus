"""Paper-Report: Positionsänderungen werden gemeldet, nichts wird gesendet, falsches Konto bricht ab."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from automation import paper_report as pr

NOW = datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)
NAMES = {100000: "BTC.ETORO"}


def _pnl(*ids, credit=9900.0):
    return {"clientPortfolio": {"credit": credit, "positions": [
        {"positionID": i, "instrumentID": 100000, "isBuy": True, "amount": 50.0, "netProfit": 1.5} for i in ids]}}


def _events(state, pnl, **kw):
    args = dict(now=NOW, names=NAMES, bot=True, ledgers={}, errors=0, force_summary=False)
    args.update(kw)
    return pr.build_events(state, pr.parse_positions(pnl), pr.parse_credit(pnl), **args)


def test_first_run_takes_baseline_without_events_but_summarizes():
    lines, state = _events({}, _pnl(1))
    assert len(lines) == 1 and "ZUSAMMENFASSUNG" in lines[0] and "ERÖFFNET" not in lines[0]
    assert set(state["positions"]) == {"1"}


def test_open_and_close_are_reported_and_quiet_otherwise():
    _, state = _events({}, _pnl(1))
    state["last_summary"] = NOW.isoformat()
    lines, state = _events(state, _pnl(1, 2))
    assert len(lines) == 1 and "ERÖFFNET Long BTC.ETORO, Position 2" in lines[0]
    lines, state = _events(state, _pnl(2))
    assert len(lines) == 1 and "GESCHLOSSEN" in lines[0] and "Position 1" in lines[0]
    lines, _ = _events(state, _pnl(2))
    assert lines == []                                   # nichts passiert => still


def test_summary_due_after_interval_counts_changes():
    _, state = _events({}, _pnl(1))
    state["last_summary"] = (NOW - timedelta(hours=5)).isoformat()
    lines, state = _events(state, _pnl(1, 2))
    assert any("ZUSAMMENFASSUNG" in ln and "2 offene" in ln for ln in lines)
    assert state["trades_since_summary"] == 0


def test_bot_down_and_new_errors_warn():
    _, state = _events({}, _pnl())
    state["last_summary"] = NOW.isoformat()
    lines, _ = _events(state, _pnl(), bot=False, errors=3)
    assert sum("WARNUNG" in ln for ln in lines) == 2


def test_run_is_read_only_and_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(pr, "ALERTS_PATH", tmp_path / "a.log")
    monkeypatch.setattr(pr, "REPORT_DIR", tmp_path / "r")
    monkeypatch.setattr(pr, "PROJECT_ROOT", tmp_path)       # Bot-Log/Ledger/Sperrdatei nicht aus dem echten Repo lesen
    monkeypatch.setattr(pr, "BOT_LOCK", tmp_path / "none.lock")
    monkeypatch.setattr(pr, "INCUBATION_DIR", tmp_path / "inc")
    env = {"ETORO_API_KEY": "k", "ETORO_USER_KEY": "u"}
    assert pr.run(environ={**env, "ETORO_ENV": "real"}, fetch=lambda *a: _pnl()) == 2
    assert pr.run(environ={"ETORO_ENV": "demo"}, fetch=lambda *a: _pnl()) == 2
    def boom(*a): raise pr.ReportError("offline")
    assert pr.run(environ=env, fetch=boom) == 1
    assert pr.run(environ=env, fetch=lambda *a: _pnl(1), force_summary=True, now=NOW) == 0
    assert (tmp_path / "a.log").read_text().count("ZUSAMMENFASSUNG") == 1


def test_push_failure_never_raises():
    def bad(*a, **k): raise OSError("x")
    pr.push(["a"], "https://example.invalid/t", opener=bad)
    pr.push(["a"], None, opener=bad)


def test_cron_help_names_flock_and_absolute_paths():
    text = pr.cron_help()
    assert "flock -n" in text and "*/15" in text and str(pr.PROJECT_ROOT) in text


def test_object_pnl_from_etoro_does_not_crash_and_is_flattened():
    pnl = {"clientPortfolio": {"credit": 9777.24, "positions": [
        {"positionID": 1, "instrumentID": 100000, "isBuy": True, "amount": 199.56, "openRate": 82942.5,
         "unrealizedPnL": {"pnL": 0.28, "closeRate": 83058.7}}]}}
    positions = pr.parse_positions(pnl)
    assert positions["1"]["pnl"] == 0.28
    # auch ein alter Zustand mit Objekt-PnL darf die Formatierung nicht abstürzen lassen
    assert "PnL +0.28" in pr._fmt_position("1", {"instrument_id": 1, "is_buy": True, "amount": 1,
                                                  "pnl": {"pnL": 0.28}}, {1: "BTC"})


def test_markdown_snapshot_has_positions_and_no_secrets(tmp_path):
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    positions = {"1": {"instrument_id": 1, "is_buy": True, "amount": 199.56, "open_rate": 82942.5, "pnl": 0.28}}
    text = pr.render_markdown(positions, 9777.24, now=now, names={1: "BTC"}, bot=True, ledgers={"a": 3}, errors=0,
                              recent_events=["x ERÖFFNET Long BTC"])
    assert "| BTC | Long | 199.56 | 82942.5 | +0.28 |" in text and "9777.24" in text
    path = pr.write_snapshot(text, tmp_path)
    assert path.name == "paper_report_latest.md" and path.read_text("utf-8") == text
    assert pr.REPORT_DIR.name == "paper_reports"     # nicht im per .gitignore ausgeschlossenen reports/
