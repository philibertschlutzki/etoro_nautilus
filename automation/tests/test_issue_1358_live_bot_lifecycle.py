"""Issue #1358 (GH #1254, P0) — Live-Bot-Lebenszyklus.

Akzeptanzkriterien:
- zweiter Start bei gehaltener Sperre ⇒ Exit 4, kein ``TradingNode`` gebaut;
- Phase 5 mit unveränderter Whitelist startet keinen Prozess; mit leerer Whitelist erhält der
  Sperrinhaber ``SIGTERM``;
- ``SIGTERM`` ⇒ ``node.stop()`` aufgerufen, ``LIVE_BOT_SHUTDOWN`` geschrieben.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from automation import live_bot_lock as lbl
from automation.live_bot_lock import (
    EXIT_ALREADY_RUNNING, LiveBotAlreadyRunning, LiveBotLock, compute_whitelist_sha256,
    lock_is_held, read_lock_info, reconcile_live_bot,
)
from automation.live_risk import (
    LiveShutdownCoordinator, effective_shutdown_policy, open_positions_missing_broker_stop,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_POPEN = subprocess.Popen  # phase5_env patcht subprocess.Popen global

_CHILD = textwrap.dedent("""
    import signal, sys, time
    from pathlib import Path
    sys.path.insert(0, {root!r})
    from automation.live_bot_lock import LiveBotLock
    lock = LiveBotLock(Path(sys.argv[1]))
    lock.acquire(environment="demo", whitelist_sha256=sys.argv[2])
    marker = Path(sys.argv[3])
    def _h(signum, frame):
        marker.write_text("SIGTERM")
        lock.release()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _h)
    print("ready", flush=True)
    while True:
        time.sleep(0.1)
""")


@pytest.fixture()
def child_holder(tmp_path):
    """Ein ECHTER Kindprozess, der die Sperre hält und SIGTERM in eine Marker-Datei protokolliert."""
    procs: list = []

    def _start(lock_path: Path, sha: str):
        marker = tmp_path / "sigterm.marker"
        proc = _REAL_POPEN(
            [sys.executable, "-c", _CHILD.format(root=str(REPO_ROOT)), str(lock_path), sha, str(marker)],
            stdout=subprocess.PIPE, text=True)
        procs.append(proc)
        assert proc.stdout.readline().strip() == "ready"
        return proc, marker

    yield _start
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=10)


# ─── Sperre ───────────────────────────────────────────────────────────────────

def test_second_acquire_while_held_raises_with_holder_info(tmp_path):
    path = tmp_path / "live_bot.lock"
    first = LiveBotLock(path)
    info = first.acquire(environment="demo", whitelist_sha256="abc")
    assert set(info) == {"pid", "started_utc", "environment", "whitelist_sha256"}
    assert read_lock_info(path)["whitelist_sha256"] == "abc"
    assert lock_is_held(path) is True

    with pytest.raises(LiveBotAlreadyRunning) as exc:
        LiveBotLock(path).acquire(environment="demo", whitelist_sha256="zzz")
    assert exc.value.info["pid"] == os.getpid()
    assert exc.value.info["whitelist_sha256"] == "abc"   # der Halter wurde nicht überschrieben

    first.release()
    assert lock_is_held(path) is False
    again = LiveBotLock(path)
    again.acquire(environment="demo", whitelist_sha256="zzz")
    again.release()


def test_lock_is_released_by_the_os_when_the_holder_dies(tmp_path, child_holder):
    path = tmp_path / "live_bot.lock"
    proc, _marker = child_holder(path, "sha")
    assert lock_is_held(path) is True
    proc.kill()
    proc.wait(timeout=10)
    assert lock_is_held(path) is False        # keine verwaiste Sperre wie bei einer PID-Datei


def test_whitelist_sha_ignores_daily_metrics_but_tracks_strategy_and_live_params():
    a = {"AAA.ETORO": {"strategy": "S1", "oos_metrics": {"sortino": 1.0}, "live_params_sha256": "p1"}}
    b = {"AAA.ETORO": {"strategy": "S1", "oos_metrics": {"sortino": 9.9}, "live_params_sha256": "p1"}}
    c = {"AAA.ETORO": {"strategy": "S2", "live_params_sha256": "p1"}}
    d = {"AAA.ETORO": {"strategy": "S1", "live_params_sha256": "p2"}}
    assert compute_whitelist_sha256(a) == compute_whitelist_sha256(b)
    assert compute_whitelist_sha256(a) != compute_whitelist_sha256(c)
    assert compute_whitelist_sha256(a) != compute_whitelist_sha256(d)


# ─── Phase-5-Abgleich (echter Kindprozess als Sperrinhaber) ─────────────────────

def test_reconcile_unchanged_whitelist_does_not_stop_or_restart(tmp_path, child_holder):
    path = tmp_path / "live_bot.lock"
    _proc, marker = child_holder(path, "same")
    res = reconcile_live_bot(path, "same", stop_timeout_s=5)
    assert res.action == "unchanged" and res.start_new is False
    assert [e for e, _ in res.events] == ["LIVE_BOT_UNCHANGED"]
    assert not marker.exists()                 # kein SIGTERM


def test_reconcile_empty_whitelist_sends_sigterm_to_lock_holder(tmp_path, child_holder):
    path = tmp_path / "live_bot.lock"
    proc, marker = child_holder(path, "old")
    res = reconcile_live_bot(path, None, stop_timeout_s=10, reason="whitelist_empty")
    assert res.action == "stopped_on_demotion" and res.start_new is False
    assert [e for e, _ in res.events] == ["LIVE_BOT_STOPPED_ON_DEMOTION"]
    assert res.events[0][1]["reason"] == "whitelist_empty"
    assert marker.read_text() == "SIGTERM"
    assert lock_is_held(path) is False
    assert proc.wait(timeout=10) == 0


def test_reconcile_changed_whitelist_restarts_after_lock_is_released(tmp_path, child_holder):
    path = tmp_path / "live_bot.lock"
    _proc, marker = child_holder(path, "old")
    res = reconcile_live_bot(path, "new", stop_timeout_s=10)
    assert res.action == "restart" and res.start_new is True
    assert marker.read_text() == "SIGTERM"


def test_reconcile_timeout_aborts_without_second_start(tmp_path):
    path = tmp_path / "live_bot.lock"
    holder = LiveBotLock(path)
    holder.acquire(environment="demo", whitelist_sha256="old", pid=999_999_999)
    calls = []

    def _never_frees(lock_path, pid, *, timeout_s):
        calls.append((pid, timeout_s))
        return False

    res = reconcile_live_bot(path, "new", stop_timeout_s=7, stop_fn=_never_frees)
    holder.release()
    assert res.action == "stop_timeout" and res.start_new is False
    assert calls == [(999_999_999, 7)]
    assert res.events[0][0] == "LIVE_BOT_STOP_TIMEOUT"


def test_reconcile_without_holder_starts_or_does_nothing(tmp_path):
    path = tmp_path / "live_bot.lock"
    assert reconcile_live_bot(path, "x").start_new is True
    assert reconcile_live_bot(path, None).action == "nothing_running"


# ─── Phase 5 gegen den Orchestrator ─────────────────────────────────────────────

@pytest.fixture()
def phase5_env(tmp_path, monkeypatch):
    from automation import daily_orchestrator as orch
    import automation.optimizer.deployment_gate as dg
    import automation.optimizer.invariants as inv

    tournament = {
        "fully_eligible_pairs": 1, "oos_not_evaluable_pairs": 0, "oos_failed_pairs": 0,
        "per_symbol_winners": {"AAA.ETORO": {
            "strategy": "SmaCrossoverStrategy", "oos_eligible": True, "oos_evaluated": True}},
        "aggregate_winner": {
            "strategy": "SmaCrossoverStrategy", "win_count": 1, "oos_evaluated": True,
            "oos_eligible": True, "oos_metrics": {"sortino_ratio": 1.0, "max_drawdown": 0.10}},
    }
    tfile = tmp_path / "tournament.json"
    tfile.write_text(json.dumps(tournament), encoding="utf-8")
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "data" / "optimizer").mkdir(parents=True)
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "momentum_ls_run.py").touch()
    monkeypatch.setattr(orch, "logs_dir", lambda: tmp_path / "logs")
    lock_path = tmp_path / "data" / "state" / "live_bot.lock"
    monkeypatch.setattr(orch, "LIVE_BOT_LOCK_PATH", lock_path)

    state = {"admitted": True}

    def _decide(pair, _records, _cfg, **_kw):
        return SimpleNamespace(
            admitted=state["admitted"], blocking_clause=None if state["admitted"] else "x",
            clause_results={}, to_dict=lambda: {"admitted": state["admitted"]})

    monkeypatch.setattr(dg, "evaluate_deployment_eligibility", _decide)
    monkeypatch.setattr(dg, "load_promotion_records", lambda pairs, work_dir=None: {})
    monkeypatch.setattr(inv, "check_deployment_gate_completeness", lambda w: SimpleNamespace(
        name="check_deployment_gate_completeness", passed=True, expected=None, actual=None,
        detail="", severity="blocking"))
    popen = MagicMock()
    monkeypatch.setattr(orch.subprocess, "Popen", popen)
    logger = orch.logging.getLogger("t1358")
    logger.setLevel(orch.logging.INFO)
    return SimpleNamespace(orch=orch, tfile=tfile, lock_path=lock_path, state=state, popen=popen,
                           logger=logger)


def _expected_whitelist_sha(orch) -> str:
    """Der Fingerabdruck, den Phase 5 fuer AAA.ETORO/SmaCrossoverStrategy berechnet (Strategie +
    live_params_sha256 aus den echten Config-Dateien, Issue #1360)."""
    from automation.live_params import live_params_sha256, resolve_live_params
    live = resolve_live_params("SmaCrossoverStrategy", "AAA.ETORO", *orch._load_live_param_sources())
    return compute_whitelist_sha256({"AAA.ETORO": {
        "strategy": "SmaCrossoverStrategy", "live_params_sha256": live_params_sha256(live)}})


def test_phase5_unchanged_whitelist_starts_no_process(phase5_env, caplog):
    env = phase5_env
    holder = LiveBotLock(env.lock_path)
    holder.acquire(environment="demo", whitelist_sha256=_expected_whitelist_sha(env.orch))
    try:
        rc = env.orch.phase5_live_deployment(
            env.logger, {"universe": []}, {"tournament_path": str(env.tfile)})
    finally:
        holder.release()
    assert rc == 0
    env.popen.assert_not_called()
    assert "LIVE_BOT_UNCHANGED" in caplog.text


def test_phase5_empty_whitelist_sigterms_the_lock_holder(phase5_env, child_holder, caplog):
    env = phase5_env
    env.state["admitted"] = False                      # leere Whitelist
    proc, marker = child_holder(env.lock_path, "yesterday")
    rc = env.orch.phase5_live_deployment(
        env.logger, {"universe": []}, {"tournament_path": str(env.tfile)})
    assert rc == 0
    env.popen.assert_not_called()
    assert marker.read_text() == "SIGTERM"
    assert "LIVE_BOT_STOPPED_ON_DEMOTION" in caplog.text
    assert proc.wait(timeout=10) == 0


@pytest.mark.parametrize("variant", ["zero_pairs", "oos_not_evaluable", "oos_failed"])
def test_phase5_every_non_start_path_stops_a_running_bot(phase5_env, child_holder, variant):
    env = phase5_env
    t = json.loads(env.tfile.read_text())
    if variant == "zero_pairs":
        t["fully_eligible_pairs"] = 0
    elif variant == "oos_not_evaluable":
        t["aggregate_winner"]["oos_evaluated"] = False
    else:
        t["aggregate_winner"]["oos_eligible"] = False
        env.state["admitted"] = False
    env.tfile.write_text(json.dumps(t))
    _proc, marker = child_holder(env.lock_path, "yesterday")
    env.orch.phase5_live_deployment(env.logger, {"universe": []}, {"tournament_path": str(env.tfile)})
    assert marker.read_text() == "SIGTERM"
    env.popen.assert_not_called()


def test_phase5_no_deploy_never_touches_a_running_bot(phase5_env, child_holder):
    env = phase5_env
    _proc, marker = child_holder(env.lock_path, "yesterday")
    rc = env.orch.phase5_live_deployment(
        env.logger, {"universe": []}, {"tournament_path": str(env.tfile)}, no_deploy=True)
    assert rc == 0 and not marker.exists()


def test_phase5_changed_whitelist_restarts_exactly_one_bot_and_writes_no_pid_file(
        phase5_env, child_holder):
    env = phase5_env
    _proc, marker = child_holder(env.lock_path, "yesterday")
    env.popen.return_value = SimpleNamespace(pid=4242)
    rc = env.orch.phase5_live_deployment(
        env.logger, {"universe": []}, {"tournament_path": str(env.tfile)})
    assert rc == 0
    assert marker.read_text() == "SIGTERM"
    assert env.popen.call_count == 1
    assert not (env.orch.logs_dir() / "live_bot.pid").exists()


# ─── momentum_ls_run: zweiter Start ⇒ Exit 4, kein TradingNode ────────────────

def test_second_bot_start_exits_4_without_building_a_trading_node(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    from automation import momentum_ls_run as mls

    lock_path = tmp_path / "live_bot.lock"
    holder = LiveBotLock(lock_path)
    holder.acquire(environment="demo", whitelist_sha256="holder")
    monkeypatch.setattr(mls, "LOCK_PATH", lock_path)
    node_cls = MagicMock(name="TradingNode")
    monkeypatch.setattr(mls, "TradingNode", node_cls)

    from automation.live_params import (
        live_params_sha256, load_live_param_sources, resolve_live_params,
    )
    imap = json.loads((REPO_ROOT / "automation/config/instrument_map.json").read_text())["instruments"]
    symbol = next(v["symbol"] for v in imap.values() if isinstance(v, dict) and "symbol" in v)
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({
        "fetched_at": time_now_iso(), "universe": [{"symbol": symbol}]}))
    live = resolve_live_params(
        "SmaCrossoverStrategy", symbol, *load_live_param_sources(REPO_ROOT / "automation" / "config"))
    tournament = tmp_path / "tournament.json"
    tournament.write_text(json.dumps({"per_symbol_winners": {symbol: {
        "strategy": "SmaCrossoverStrategy", "deployment_gate": {"admitted": True},
        "proposed_instrument_override": {}, "live_params_sha256": live_params_sha256(live)}}}))
    monkeypatch.setenv("ETORO_API_KEY", "k")
    monkeypatch.setenv("ETORO_USER_KEY", "u")
    monkeypatch.setattr(sys, "argv", ["momentum_ls_run.py", "--universe", str(universe),
                                      "--tournament", str(tournament)])
    try:
        with pytest.raises(SystemExit) as exc:
            mls.main()
    finally:
        holder.release()
    assert exc.value.code == EXIT_ALREADY_RUNNING == 4
    node_cls.assert_not_called()


def time_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ─── SIGTERM ⇒ node.stop(), LIVE_BOT_SHUTDOWN ─────────────────────────────────

def _fake_node(*, positions=(), orders=None, strategy_ids=("S1",)):
    orders = orders or {}
    loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *a: fn(*a))
    cache = SimpleNamespace(
        positions_open=lambda: list(positions), order=lambda oid: orders.get(oid))
    return SimpleNamespace(
        get_event_loop=lambda: loop, cache=cache, stop=MagicMock(name="stop"),
        trader=SimpleNamespace(strategy_ids=list(strategy_ids), market_exit_strategy=MagicMock()))


def _pos(pid, oid):
    return SimpleNamespace(id=pid, opening_order_id=oid)


def test_sigterm_calls_node_stop_blocks_entries_and_writes_shutdown_event():
    node = _fake_node()
    events: list[tuple[str, dict]] = []
    blocked = MagicMock()
    coord = LiveShutdownCoordinator(
        node, policy="keep", block_entries=blocked, emit=lambda e, p: events.append((e, p)))
    coord.install()
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(100):
            if coord.requested.is_set():
                break
            time.sleep(0.01)
    finally:
        coord.uninstall()
    assert coord.requested.is_set()
    blocked.assert_called_once()
    node.stop.assert_called_once()
    assert [e for e, _ in events] == ["LIVE_BOT_SHUTDOWN"]
    assert events[0][1]["policy"] == "keep" and events[0][1]["signal"] == int(signal.SIGTERM)
    node.trader.market_exit_strategy.assert_not_called()


def test_second_signal_is_idempotent():
    node = _fake_node()
    coord = LiveShutdownCoordinator(node)
    assert coord.request_shutdown(signal.SIGTERM) is True
    assert coord.request_shutdown(signal.SIGINT) is False
    node.stop.assert_called_once()


def test_keep_requires_broker_stop_on_every_open_position_else_flatten():
    protected = SimpleNamespace(tags=["SL:0.10"])
    unprotected = SimpleNamespace(tags=[])
    node = _fake_node(
        positions=[_pos("P1", "O1"), _pos("P2", "O2")], orders={"O1": protected, "O2": unprotected})
    events: list[tuple[str, dict]] = []
    LiveShutdownCoordinator(node, policy="keep", emit=lambda e, p: events.append((e, p))).request_shutdown()
    payload = events[0][1]
    assert payload["policy_configured"] == "keep" and payload["policy"] == "flatten"
    assert payload["positions_without_broker_stop"] == ["P2"]
    assert sorted(payload["open_positions"]) == ["P1", "P2"]
    node.trader.market_exit_strategy.assert_called_once_with("S1")
    node.stop.assert_called_once()


def test_keep_is_honoured_when_all_positions_carry_a_broker_stop():
    node = _fake_node(positions=[_pos("P1", "O1")], orders={"O1": SimpleNamespace(tags=["SL:0.05"])})
    events: list[tuple[str, dict]] = []
    LiveShutdownCoordinator(node, policy="keep", emit=lambda e, p: events.append((e, p))).request_shutdown()
    assert events[0][1]["policy"] == "keep"
    node.trader.market_exit_strategy.assert_not_called()


def test_flatten_policy_always_flattens_and_unknown_opening_order_is_unprotected():
    cache = SimpleNamespace(positions_open=lambda: [_pos("P1", "O-missing")], order=lambda oid: None)
    assert open_positions_missing_broker_stop(cache) == ["P1"]
    assert effective_shutdown_policy("flatten", []) == "flatten"
    assert effective_shutdown_policy(None, []) == "keep"
    assert effective_shutdown_policy("keep", ["P1"]) == "flatten"
