"""Zyklus-Entscheidung der Finanzierungsmessung (rein, ohne Orders)."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fin_cycle as fc

U = dt.timezone.utc
FRI_OPEN = dt.datetime(2026, 10, 23, 19, 40, tzinfo=U)      # Freitag


def test_opens_only_friday_window_without_open_cycle():
    assert fc.decide(FRI_OPEN, {}) == "open"
    assert fc.decide(dt.datetime(2026, 10, 23, 19, 56, tzinfo=U), {}) == "wait"
    assert fc.decide(dt.datetime(2026, 10, 22, 19, 40, tzinfo=U), {}) == "wait"        # Donnerstag


def test_holds_until_80_hours_then_closes():
    st = {"opened_utc": FRI_OPEN.isoformat()}
    assert fc.decide(dt.datetime(2026, 10, 26, 19, 0, tzinfo=U), st) == "wait"          # Montag, 71 h
    assert fc.decide(dt.datetime(2026, 10, 27, 7, 10, tzinfo=U), st) == "close"         # Dienstag, 83,5 h


def test_after_close_a_new_cycle_waits_for_next_friday():
    st = {"opened_utc": FRI_OPEN.isoformat(), "closed_utc": dt.datetime(2026, 10, 27, 7, 10, tzinfo=U).isoformat()}
    assert fc.decide(dt.datetime(2026, 10, 27, 19, 40, tzinfo=U), st) == "wait"
    assert fc.decide(dt.datetime(2026, 10, 30, 19, 40, tzinfo=U), st) == "open"


def test_disarmed_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "ARMED", str(tmp_path / "ARMED"))
    assert not fc.armed()
    (tmp_path / "ARMED").write_text("demo\n")
    assert fc.armed()
    (tmp_path / "ARMED").write_text("real")
    assert not fc.armed()


def test_expired_cycle_on_friday_window_rotates_and_manual_midweek_cycle_closes_next_friday():
    st = {"opened_utc": dt.datetime(2026, 10, 19, 14, 10, tzinfo=U).isoformat()}        # Montag manuell geöffnet
    assert fc.decide(dt.datetime(2026, 10, 22, 7, 10, tzinfo=U), st) == "wait"            # Donnerstag: 65 h
    assert fc.decide(dt.datetime(2026, 10, 23, 19, 40, tzinfo=U), st) == "rotate"         # Freitag: schliessen + neu öffnen
    assert fc.decide(dt.datetime(2026, 10, 27, 7, 10, tzinfo=U), {"opened_utc": FRI_OPEN.isoformat()}) == "close"


def test_open_now_refuses_when_cycle_already_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fc, "STATE", str(tmp_path / "s.json"))
    monkeypatch.setattr(fc, "ARMED", str(tmp_path / "ARMED"))
    fc.save_state({"opened_utc": FRI_OPEN.isoformat()})
    assert fc.main(["--open-now", "--now", "2026-10-26T14:10:00+00:00"]) == 1
