"""Issue #1362 (GH #1258, P1) — Circuit-Breaker: das Drawdown-Gedächtnis überlebt den Bot-Neustart.

Akzeptanzkriterien:
- zwei Watchdog-Instanzen nacheinander mit −8 % / −8 % ⇒ die zweite löst bei kumuliert ≥ 10 % aus;
- Tagesverlust 3 % ⇒ Auslöser C; der Folgetag startet mit neuer Tagesbasis, aber altem HWM;
- ohne Holdout-Felder ⇒ Start-Event mit Grund; mit Feldern ⇒ z über Round-Trip-Renditen.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from automation.live_equity_state import (
    HwmEnvironmentMismatch, PersistentEquityState, RoundTripReturnCollector, distribution_references,
    exchange_day_key, round_trip_return_bps,
)
from automation.live_risk import (
    LiveCircuitBreakerWatchdog, evaluate_circuit_breaker, evaluate_distribution_trigger,
)


def _node(equity_holder: dict, closed: list | None = None):
    loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *a: fn(*a))
    return SimpleNamespace(
        portfolio=SimpleNamespace(equity=lambda venue: {"USD": equity_holder["v"]}),
        cache=SimpleNamespace(positions_closed=lambda: list(closed if closed is not None else [])),
        get_event_loop=lambda: loop,
        trader=SimpleNamespace(strategy_ids=[], market_exit_strategy=lambda sid: None),
        stop=lambda: None)


def _watchdog(node, state=None, **kw):
    wd = LiveCircuitBreakerWatchdog(node, venue="ETORO", poll_interval_s=3600.0, equity_state=state, **kw)
    return wd


def _tick(wd, holder, equity):
    holder["v"] = equity
    wd._tick()


def _now(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T15:00:00+00:00")


# ─── Akzeptanz 1: kumulierter Drawdown über zwei Bot-Lebensdauern ───────────────────

def test_two_bot_lifetimes_minus_8_percent_each_trip_at_cumulative_15_percent(tmp_path):
    path = tmp_path / "live_equity_hwm.json"
    holder = {"v": 10_000.0}
    wd1 = _watchdog(_node(holder), PersistentEquityState(path, environment="demo"))
    _tick(wd1, holder, 10_000.0)
    _tick(wd1, holder, 9_200.0)                      # −8 % an Tag 1: keine Auslösung
    assert not wd1.tripped_event.is_set()
    wd1.stop()

    state2 = PersistentEquityState(path, environment="demo")      # Neustart (Cron, #1358)
    state2.load()
    assert state2.hwm == 10_000.0                                  # Gedächtnis überlebt
    wd2 = _watchdog(_node(holder), state2)
    _tick(wd2, holder, 9_200.0)
    assert not wd2.tripped_event.is_set()
    _tick(wd2, holder, 8_464.0)                                    # weitere −8 % ⇒ kumuliert −15,36 %
    assert wd2.tripped_event.is_set()
    assert wd2.last_decision.trigger == "drawdown"
    assert wd2.last_decision.dd_live == pytest.approx(0.1536, abs=1e-4)
    wd2.stop()


def test_without_persistence_the_second_lifetime_would_not_trip():
    """Das Vor-#1362-Verhalten (Peak je Prozess bei None): −8 % / −8 % löst NIE aus."""
    holder = {"v": 9_200.0}
    wd = _watchdog(_node(holder), None)
    _tick(wd, holder, 9_200.0)
    _tick(wd, holder, 8_464.0)
    assert not wd.tripped_event.is_set()
    wd.stop()


def test_hwm_never_decreases_and_survives_a_reload(tmp_path):
    path = tmp_path / "hwm.json"
    s = PersistentEquityState(path, environment="real")
    s.observe(10_000.0)
    s.observe(9_000.0)
    s.observe(10_500.0)
    s.observe(9_900.0)
    reloaded = PersistentEquityState(path, environment="real")
    data = reloaded.load()
    assert data["hwm"] == 10_500.0 and set(data) >= {"environment", "hwm", "hwm_utc", "updated_utc"}
    assert reloaded.persisted_drawdown() == pytest.approx(1 - 9_900.0 / 10_500.0)


def test_state_of_another_environment_is_not_silently_adopted(tmp_path):
    path = tmp_path / "hwm.json"
    PersistentEquityState(path, environment="demo").observe(10_000.0)
    with pytest.raises(HwmEnvironmentMismatch):
        PersistentEquityState(path, environment="real").load()


# ─── Akzeptanz 2: Tagesverlust-Auslöser C, neue Tagesbasis, alter HWM ──────────────

def test_daily_loss_trips_and_the_next_day_starts_with_a_new_base_but_the_old_hwm(tmp_path):
    path = tmp_path / "hwm.json"
    holder = {"v": 10_000.0}
    day = {"d": "2026-10-05"}
    key = lambda now: day["d"]                                               # noqa: E731
    wd1 = _watchdog(_node(holder), PersistentEquityState(path, environment="demo"),
                    daily_loss_halt_fraction=0.03, day_key_fn=key, now_fn=lambda: _now(day["d"]))
    _tick(wd1, holder, 10_000.0)
    _tick(wd1, holder, 9_800.0)
    assert not wd1.tripped_event.is_set()
    _tick(wd1, holder, 9_700.0)                                              # genau −3,0 %
    assert wd1.tripped_event.is_set()
    assert wd1.last_decision.trigger == "daily_loss"
    assert wd1.last_decision.daily_loss == pytest.approx(0.03)
    assert wd1.last_decision.dd_live < 0.10                                  # A hat NICHT ausgelöst
    wd1.stop()

    day["d"] = "2026-10-06"                                                  # Folgetag, neuer Prozess
    state2 = PersistentEquityState(path, environment="demo")
    state2.load()
    assert state2.day_start_equity == 10_000.0                               # Basis von gestern
    wd2 = _watchdog(_node(holder), state2, daily_loss_halt_fraction=0.03, day_key_fn=key,
                    now_fn=lambda: _now(day["d"]))
    _tick(wd2, holder, 9_700.0)
    assert not wd2.tripped_event.is_set()
    assert state2.day_start_equity == 9_700.0                                # neue Tagesbasis
    assert state2.hwm == 10_000.0                                            # alter HWM
    assert wd2.last_decision.daily_loss == 0.0
    wd2.stop()


def test_evaluate_circuit_breaker_priority_a_before_c_before_b():
    both = evaluate_circuit_breaker(
        8_800.0, 10_000.0, equity_day_start=9_000.0, daily_loss_halt_fraction=0.02,
        dd_halt_fraction=0.10)
    assert both.trigger == "drawdown"                                        # 12 % ≥ 10 %
    only_c = evaluate_circuit_breaker(
        9_700.0, 10_000.0, equity_day_start=10_000.0, daily_loss_halt_fraction=0.03)
    assert only_c.trigger == "daily_loss"
    disabled = evaluate_circuit_breaker(
        9_000.0, 9_500.0, equity_day_start=10_000.0, daily_loss_halt_fraction=None)
    assert disabled.trigger is None and disabled.daily_loss == pytest.approx(0.10)


def test_exchange_day_key_uses_exchange_local_time_across_dst():
    assert exchange_day_key(datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)) == "2026-10-29"   # EDT
    assert exchange_day_key(datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)) == "2026-11-01"    # EST
    assert exchange_day_key(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)) == "2026-11-02"


# ─── Akzeptanz 3: Verteilungs-Auslöser B je Paar auf Round-Trip-Skala ───────────────

def _closed(n: int, ret_bps: float, instrument: str = "TSLA.ETORO", start: int = 0) -> list:
    out = []
    for i in range(n):
        pnl = ret_bps / 10_000.0 * (100.0 * 10.0)                 # Notional 1000
        out.append(SimpleNamespace(
            id=f"P-{instrument}-{start + i}", instrument_id=instrument, avg_px_open=100.0,
            peak_qty=10.0, realized_pnl=SimpleNamespace(as_double=lambda p=pnl: p)))
    return out


def test_round_trip_return_is_bps_of_the_position_notional():
    pos = _closed(1, -25.0)[0]
    assert round_trip_return_bps(pos) == pytest.approx(-25.0)
    assert round_trip_return_bps(SimpleNamespace(avg_px_open=0.0, peak_qty=1, realized_pnl=1.0)) is None
    assert round_trip_return_bps(SimpleNamespace()) is None


def test_collector_counts_each_closed_position_once_and_groups_by_instrument():
    c = RoundTripReturnCollector()
    batch = _closed(3, 10.0) + _closed(2, -5.0, instrument="NVDA.ETORO")
    assert c.update(batch) == 5
    assert c.update(batch) == 0
    assert len(c.returns_by_instrument["TSLA.ETORO"]) == 3
    assert len(c.returns_by_instrument["NVDA.ETORO"]) == 2


def test_distribution_trigger_needs_n_min_and_a_reference():
    returns = [-200.0] * 30
    tripped, z, n = evaluate_distribution_trigger(returns, 20.0, 50.0, z_halt=2.5, n_min_periods=30)
    assert tripped is True and n == 30 and z < -2.5
    assert evaluate_distribution_trigger(returns[:29], 20.0, 50.0, n_min_periods=30)[0] is False
    assert evaluate_distribution_trigger(returns, None, 50.0, n_min_periods=30)[0] is False


def test_without_holdout_fields_the_start_event_names_the_reason_with_fields_z_is_used():
    winners = {
        "TSLA.ETORO": {"strategy": "S", "holdout_trade_return_bps_mean": 20.0,
                       "holdout_trade_return_bps_std": 50.0, "holdout_trade_return_bps_n": 80},
        "NVDA.ETORO": {"strategy": "S"},                                           # keine Felder
        "AAPL.ETORO": {"strategy": "S", "holdout_trade_return_bps_mean": 1.0,
                       "holdout_trade_return_bps_std": 0.0, "holdout_trade_return_bps_n": 5},
    }
    refs, reasons = distribution_references(winners)
    assert refs == {"TSLA.ETORO": {"mean": 20.0, "std": 50.0, "n": 80}}
    assert reasons == {"NVDA.ETORO": "missing_holdout_trade_return_bps_fields",
                       "AAPL.ETORO": "holdout_trade_return_bps_std_not_positive"}


def test_watchdog_trips_on_the_per_pair_round_trip_distribution(tmp_path):
    holder = {"v": 10_000.0}
    closed: list = []
    refs = {"TSLA.ETORO": {"mean": 20.0, "std": 50.0, "n": 80}}
    wd = _watchdog(_node(holder, closed), PersistentEquityState(tmp_path / "h.json", environment="demo"),
                   distribution_refs=refs, n_min_round_trips=30, z_halt=2.5)
    closed.extend(_closed(29, -200.0))
    _tick(wd, holder, 10_000.0)
    assert not wd.tripped_event.is_set()                       # n = 29 < 30: fail-open
    closed.extend(_closed(1, -200.0, start=29))
    _tick(wd, holder, 10_000.0)
    assert wd.tripped_event.is_set()
    assert wd.last_decision.trigger == "distribution"
    assert wd.last_decision.distribution_pair == "TSLA.ETORO"
    assert wd.last_decision.z_live < -2.5
    wd.stop()


def test_a_pair_without_reference_never_trips_the_distribution_trigger(tmp_path):
    holder = {"v": 10_000.0}
    closed = _closed(40, -500.0, instrument="NVDA.ETORO")
    wd = _watchdog(_node(holder, closed), PersistentEquityState(tmp_path / "h.json", environment="demo"),
                   distribution_refs={"TSLA.ETORO": {"mean": 20.0, "std": 50.0, "n": 80}})
    _tick(wd, holder, 10_000.0)
    assert not wd.tripped_event.is_set()
    wd.stop()


# ─── --reset-hwm ───────────────────────────────────────────────────────────────────

def test_reset_hwm_is_the_only_reset_path_and_emits_the_event(tmp_path, caplog):
    from automation import momentum_ls_run as mls
    hwm_path, lock_path = tmp_path / "hwm.json", tmp_path / "live_bot.lock"
    PersistentEquityState(hwm_path, environment="demo").observe(12_345.0)
    with caplog.at_level(logging.WARNING):
        assert mls._reset_hwm("demo", hwm_path=hwm_path, lock_path=lock_path) == 0
    assert not hwm_path.exists()
    assert "LIVE_HWM_RESET" in caplog.text and "12345" in caplog.text


def test_reset_hwm_is_refused_while_a_bot_holds_the_lock(tmp_path):
    from automation import momentum_ls_run as mls
    from automation.live_bot_lock import LiveBotLock
    hwm_path, lock_path = tmp_path / "hwm.json", tmp_path / "live_bot.lock"
    PersistentEquityState(hwm_path, environment="demo").observe(12_345.0)
    holder = LiveBotLock(lock_path)
    holder.acquire(environment="demo", whitelist_sha256="x")
    try:
        assert mls._reset_hwm("demo", hwm_path=hwm_path, lock_path=lock_path) == 4
    finally:
        holder.release()
    assert json.loads(hwm_path.read_text())["hwm"] == 12_345.0


# ─── Holdout-Round-Trip-Statistik: Backtest → Proposal → Whitelist ──────────────────

def test_aggregate_exit_telemetry_emits_trade_return_statistics():
    from automation.backtest_runner import _aggregate_exit_telemetry
    agg = _aggregate_exit_telemetry([{"pnl_bps": 10.0}, {"pnl_bps": -30.0}, {"pnl_bps": 0.0}, {}])
    assert agg["trade_return_bps_n"] == 3
    assert agg["trade_return_bps_mean"] == pytest.approx(-20.0 / 3)
    assert agg["trade_return_bps_std"] == pytest.approx(20.8167, abs=1e-3)
    empty = _aggregate_exit_telemetry([])
    assert empty["trade_return_bps_n"] == 0 and empty["trade_return_bps_mean"] is None
    assert _aggregate_exit_telemetry([{"pnl_bps": 5.0}])["trade_return_bps_std"] is None


def test_promotion_record_carries_the_holdout_trade_return_fields():
    from automation.optimizer.deployment_gate import build_promotion_record_from_proposal
    rec = build_promotion_record_from_proposal({"holdout": {"symbol": {
        "oos_trade_return_bps_mean": 12.5, "oos_trade_return_bps_std": 40.0,
        "oos_trade_return_bps_n": 77}}})
    assert (rec["holdout_trade_return_bps_mean"], rec["holdout_trade_return_bps_std"],
            rec["holdout_trade_return_bps_n"]) == (12.5, 40.0, 77)


def test_tournament_metrics_parse_the_trade_return_fields():
    from automation.optimizer.parsing import TournamentMetrics
    fields = TournamentMetrics.__dataclass_fields__
    assert {"oos_trade_return_bps_mean", "oos_trade_return_bps_std", "oos_trade_return_bps_n"} <= set(fields)
