"""Issue #1363 (GH #1259, P1) — Datenspanne: Vorwärts-Füllung im dokumentierten Betrieb tot, Backfill meldet
Erfolg ohne Zugewinn, keine Aktualitätsprüfung, keine ETA.

Abnahme:
* Fake-API mit festem 92-Tage-Fenster: ``gain_days = 0``, ``BACKFILL_NO_GAIN``, zweiter Aufruf innerhalb von
  7 Tagen ohne Netzabruf.
* ``--skip-api-fetch`` ⇒ der Vorwärts-Schritt läuft trotzdem (nur ``--offline`` verzichtet aufs Netz).
* jüngster Tick 5 Tage alt ⇒ ``REJECT_DATA_STALE``.
* heutige Zahlen (92,04 Tage gegen 444 mit #1357-Embargo, Stand 2026-10-04) ⇒ ``waiting_for_data``,
  ``eta_utc = 2027-09-21``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from automation import api_backfiller as ab
from automation import historical_fetcher as hf
from automation.optimizer import sweep

_NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
_SYM = "XOM.ETORO"


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    qt = tmp_path / "data" / "quote_tick"
    qt.mkdir(parents=True)
    monkeypatch.setattr(hf, "QUOTE_TICK_PATH", qt)
    monkeypatch.setattr(hf, "CATALOG_PATH", tmp_path)
    monkeypatch.setattr(ab, "QUOTE_TICK_PATH", qt)
    monkeypatch.setattr(hf, "INCEPTION_CACHE_PATH", tmp_path / "state" / "inception_bounds.json")
    # Issue #1372 — diese Fixture simuliert eine API, die `endTime` auswertet (Probe-Stempel end_time);
    # ohne Stempel gilt `count_only` (siehe test_issue_1372_candle_pagination_probe.py).
    for _itv in ("OneHour", "OneDay"):
        hf._save_pagination_probe(_SYM, _itv, "end_time")
    return tmp_path


class _FakeApi:
    """Liefert OneHour-Kerzen ausschliesslich im festen Fenster ``[now − 92 d, now]`` (die API-Tiefe), OneDay
    bis 400 Tage zurück; zählt jeden Abruf."""

    def __init__(self, now: datetime, hour_depth_days: float = 92.0, day_depth_days: float = 400.0):
        self.now = now
        self.calls: list[tuple[str, datetime, int]] = []
        self.depth = {"OneHour": timedelta(days=hour_depth_days), "OneDay": timedelta(days=day_depth_days)}
        self.step = {"OneHour": timedelta(hours=1), "OneDay": timedelta(days=1)}

    async def __call__(self, session, etoro_id, end_time, api_key, user_key, interval, count=1000):
        end_time = end_time or self.now
        self.calls.append((interval, end_time, count))
        oldest_allowed = self.now - self.depth[interval]
        step = self.step[interval]
        t = end_time.replace(minute=0, second=0, microsecond=0)
        if interval == "OneDay":
            t = t.replace(hour=0)
        if t >= end_time:
            t -= step
        out = []
        while t >= oldest_allowed and len(out) < count:
            out.append({"fromDate": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "open": 100.0, "high": 101.0,
                        "low": 99.0, "close": 100.5, "volume": 10.0})
            t -= step
        return out


def _span_days(catalog_root: Path) -> float:
    return hf.measure_span_days(_SYM, catalog_root)


# ─── Inception-Bounds je Intervall ────────────────────────────────────────────────────

def test_old_inception_format_is_migrated(catalog):
    hf.INCEPTION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    hf.INCEPTION_CACHE_PATH.write_text(json.dumps({_SYM: 1_750_000_000_000_000_000}), "utf-8")
    bounds = hf._load_inception_bounds()
    assert bounds[_SYM] == {"OneHour": 1_750_000_000_000_000_000, "observed_utc": None}
    assert not hf.inception_bound_is_fresh(_SYM, "OneHour")          # unbekannt beobachtet ⇒ nicht frisch
    hf._save_inception_bound(_SYM, 1_760_000_000_000_000_000, "OneDay", now=_NOW)
    entry = json.loads(hf.INCEPTION_CACHE_PATH.read_text("utf-8"))[_SYM]
    assert entry == {"OneHour": 1_750_000_000_000_000_000, "OneDay": 1_760_000_000_000_000_000,
                     "observed_utc": "2026-10-04T10:00:00Z"}


def test_fetch_symbol_registers_the_onehour_depth_although_oneday_reaches_the_target(catalog, monkeypatch):
    api = _FakeApi(datetime.now(timezone.utc))
    monkeypatch.setattr(hf, "_fetch_candle_chunk", api)
    monkeypatch.setattr(hf.asyncio, "sleep", _no_sleep)
    target = datetime.now(timezone.utc) - timedelta(days=300)
    ok = asyncio.run(hf._fetch_symbol(None, "1", _SYM, 12, "k", "u", 2, 2,
                                      start_ns=int(target.timestamp() * 1e9)))
    assert ok
    bounds = hf._load_inception_bounds()[_SYM]
    assert "OneHour" in bounds and bounds["observed_utc"]
    depth_days = (datetime.now(timezone.utc).timestamp() - bounds["OneHour"] / 1e9) / 86400
    assert 91.0 <= depth_days <= 92.5
    assert 91.0 <= _span_days(catalog) <= 92.5


async def _no_sleep(*_a, **_k):
    return None


# ─── Abnahme 1: kein Zugewinn ⇒ BACKFILL_NO_GAIN, zweiter Aufruf ohne Netz ──────────────

def test_no_gain_is_reported_and_the_second_call_within_7_days_skips_the_network(catalog, monkeypatch, caplog):
    api = _FakeApi(datetime.now(timezone.utc))
    monkeypatch.setattr(hf, "_fetch_candle_chunk", api)
    monkeypatch.setattr(hf.asyncio, "sleep", _no_sleep)

    async def _no_precisions(*_a, **_k):
        return {}
    monkeypatch.setattr(hf, "fetch_precisions_from_api", _no_precisions)
    # Bestand: bereits die volle API-Tiefe (92 Tage) im Katalog.
    asyncio.run(hf._fetch_symbol(None, "1", _SYM, 1, "k", "u", 2, 2,
                                 start_ns=int((datetime.now(timezone.utc) - timedelta(days=200)).timestamp() * 1e9)))
    api.calls.clear()
    hf.INCEPTION_CACHE_PATH.unlink(missing_ok=True)       # Tiefe noch nicht registriert (Altzustand)

    def _fetch(symbols, **kw):
        return asyncio.run(hf.run_historical_fetch("k", "u", {"1": _SYM}, months=16,
                                                   start_ns=int((datetime.now(timezone.utc) - timedelta(days=471)).timestamp() * 1e9)))

    wf = {"is_window_days": 180, "embargo_period_days": 21, "splits": 4, "oos_window_days": 45,
          "holdout_days": 60, "holdout_embargo_days": 3}
    span = _span_days(catalog)
    with caplog.at_level(logging.WARNING):
        report = hf.ensure_walkforward_history(
            [_SYM], wf, span_days_by_symbol={_SYM: span}, gate1_buffer_days=27, fetch_fn=_fetch,
            span_fn=lambda s: _span_days(catalog))
    assert api.calls, "erster Aufruf ruft die API ab"
    assert report["gain_days"][_SYM] < 1.0 and report["no_gain"] == [_SYM]
    assert any("BACKFILL_NO_GAIN" in r.getMessage() for r in caplog.records)
    assert hf.inception_bound_is_fresh(_SYM, "OneHour")

    api.calls.clear()
    called = []
    report2 = hf.ensure_walkforward_history(
        [_SYM], wf, span_days_by_symbol={_SYM: span}, gate1_buffer_days=27,
        fetch_fn=lambda *a, **k: called.append(1) or [], span_fn=lambda s: _span_days(catalog))
    assert called == [] and api.calls == []
    assert report2["skipped_known_depth"] == [_SYM]
    # Nach Ablauf der Retry-Frist wird wieder abgerufen.
    report3 = hf.ensure_walkforward_history(
        [_SYM], wf, span_days_by_symbol={_SYM: span}, gate1_buffer_days=27,
        fetch_fn=lambda *a, **k: called.append(1) or [], span_fn=lambda s: _span_days(catalog),
        now=datetime.now(timezone.utc) + timedelta(days=8))
    assert called == [1] and report3["skipped_known_depth"] == []


# ─── Vorwärts-Schritt ─────────────────────────────────────────────────────────────────

def test_forward_count_follows_the_gap():
    assert hf.forward_fill_count(0.5) == 25
    assert hf.forward_fill_count(200.2) == 225
    assert hf.forward_fill_count(5000) == 1000


def test_forward_step_paginates_until_it_overlaps_the_latest_local_tick():
    now = datetime(2026, 10, 4, 10, tzinfo=timezone.utc)
    api = _FakeApi(now, hour_depth_days=400)
    latest_local = now - timedelta(days=60)                  # 1440 h Lücke > 1000 je Seite
    candles = asyncio.run(hf.fetch_forward_candles(
        None, "1", _SYM, int(latest_local.timestamp() * 1e9), api_key="k", user_key="u", now=now,
        fetch_chunk=api, mode="end_time"))
    assert len(api.calls) == 2
    assert api.calls[0][2] == 1000
    oldest = min(datetime.fromisoformat(c["fromDate"].replace("Z", "+00:00")) for c in candles)
    assert oldest <= latest_local


def test_fetch_symbol_runs_the_forward_step_first(catalog, monkeypatch):
    now = datetime.now(timezone.utc)
    api = _FakeApi(now)
    monkeypatch.setattr(hf, "_fetch_candle_chunk", api)
    monkeypatch.setattr(hf.asyncio, "sleep", _no_sleep)
    asyncio.run(hf._fetch_symbol(None, "1", _SYM, 1, "k", "u", 2, 2,
                                 start_ns=int((now - timedelta(days=10)).timestamp() * 1e9)))
    # Katalog altert: die jüngsten 5 Tage entfernen.
    f = hf.QUOTE_TICK_PATH / _SYM / "OneHour" / "data.parquet"
    t = pq.read_table(str(f))
    cutoff = int((now - timedelta(days=5)).timestamp() * 1e9)
    import pyarrow.compute as pc
    pq.write_table(t.filter(pc.less(t.column("ts_event"), cutoff)), str(f))
    api.calls.clear()
    asyncio.run(hf._fetch_symbol(None, "1", _SYM, 1, "k", "u", 2, 2,
                                 start_ns=int((now - timedelta(days=10)).timestamp() * 1e9)))
    assert api.calls[0][0] == "OneHour" and api.calls[0][2] >= 5 * 24
    newest = hf._get_latest_ts_ns(f)
    assert (now.timestamp() - newest / 1e9) / 3600 < 2.0


# ─── Abnahme 2: --skip-api-fetch überspringt nur den Tiefen-Abruf ─────────────────────

@pytest.mark.parametrize("skip_api_fetch,offline,expect_forward,expect_depth", [
    (False, False, True, True), (True, False, True, False), (False, True, False, False)])
def test_skip_api_fetch_keeps_the_forward_step(monkeypatch, skip_api_fetch, offline, expect_forward,
                                               expect_depth):
    from automation import daily_orchestrator as orch

    calls = {"forward": 0, "depth": 0}

    async def _fake_backfill(**kw):
        calls["forward"] += 1
        return []

    async def _fake_hist(**kw):
        calls["depth"] += 1
        return []

    monkeypatch.setattr(orch, "run_backfill", _fake_backfill, raising=False)
    monkeypatch.setattr(orch, "_load_etoro_id_map", lambda _p: {"1": _SYM}, raising=False)
    monkeypatch.setattr(orch, "_find_all_zip_files", lambda _log: [])
    monkeypatch.setattr(hf, "run_historical_fetch", _fake_hist)
    monkeypatch.setattr(hf, "is_backtest_range_covered", lambda *a, **k: False)
    monkeypatch.setattr(ab, "_load_etoro_id_map", lambda _p: {"1": _SYM})
    orch.phase2_data_acquisition(logging.getLogger("t"), {"universe": [{"symbol": _SYM}]}, "k", "u",
                                 skip_api_fetch=skip_api_fetch, offline=offline)
    assert (calls["forward"] > 0) is expect_forward
    assert (calls["depth"] > 0) is expect_depth


def test_orchestrator_cli_has_offline_flag():
    from automation.daily_orchestrator import build_arg_parser

    args = build_arg_parser().parse_args(["--skip-api-fetch", "--offline"])
    assert args.skip_api_fetch and args.offline


# ─── Abnahme 3: Aktualität ────────────────────────────────────────────────────────────

def test_newest_tick_five_days_old_is_rejected_as_stale():
    newest = int((_NOW - timedelta(days=5)).timestamp() * 1e9)
    res = sweep.check_catalog_freshness(newest, now=_NOW)
    assert res["passed"] is False and res["reason"].startswith("REJECT_DATA_STALE")
    assert res["severity"] == "blocking"
    fresh = sweep.check_catalog_freshness(int((_NOW - timedelta(hours=70)).timestamp() * 1e9), now=_NOW)
    assert fresh["passed"] is True
    assert sweep.check_catalog_freshness(None, now=_NOW)["passed"] is None


def test_freshness_preflight_is_wired_and_rejects_symbols():
    src = Path(sweep.__file__).read_text("utf-8")
    assert '"name": "check_catalog_freshness"' in src
    assert '"reason": "REJECT_DATA_STALE"' in src
    cfg = json.loads((Path(sweep.__file__).resolve().parents[1] / "config" / "optimizer.json").read_text("utf-8"))
    assert cfg["max_catalog_staleness_h"] == 96 and cfg["backfill_retry_days"] == 7


# ─── Abnahme 4: Daten-Tiefen-Prognose ─────────────────────────────────────────────────

def test_todays_numbers_report_waiting_for_data_with_eta():
    res = sweep.check_data_depth_eta(92.04, 444, freshness_passed=True, now=_NOW)
    assert res["waiting"] is True and res["eta_utc"] == "2027-09-21"
    assert sweep.check_data_depth_eta(92.04, 441, freshness_passed=True, now=_NOW)["eta_utc"] == "2027-09-18"
    stale = sweep.check_data_depth_eta(92.04, 444, freshness_passed=False, now=_NOW)
    assert stale["waiting"] is True and stale["eta_utc"] is None and stale["reason"] == "CATALOG_NOT_FRESH"
    assert sweep.check_data_depth_eta(500, 444, freshness_passed=True, now=_NOW)["waiting"] is False


def test_completed_invalid_run_is_restated_as_waiting_for_data(tmp_path, monkeypatch):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"run_id": "r1", "run_status": "completed_invalid"}), "utf-8")
    monkeypatch.setattr(sweep, "_LAST_DATA_DEPTH_ETA", {
        "run_id": "r1", "waiting": True, "eta_utc": "2027-09-21", "required_span_days": 444,
        "per_symbol": {_SYM: {"eta_utc": "2027-09-21"}}, "reason": None})
    assert sweep._apply_waiting_for_data_status(report_path, "completed_invalid") == "waiting_for_data"
    written = json.loads(report_path.read_text("utf-8"))
    assert written["run_status"] == "waiting_for_data" and written["eta_utc"] == "2027-09-21"
    monkeypatch.setattr(sweep, "_LAST_DATA_DEPTH_ETA", {"run_id": "r1", "waiting": False})
    assert sweep._apply_waiting_for_data_status(report_path, "completed_invalid") == "completed_invalid"


def test_german_summary_names_the_waiting_status():
    from automation.optimizer import summary_de

    assert "waiting_for_data" in summary_de._RUN_STATUS_LABELS_DE
