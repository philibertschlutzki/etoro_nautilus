"""Issue #922 — OpeningRangeBreakoutStrategy verankerte den "Handelstag" ausschliesslich auf
pd.Timestamp(bar.ts_init).day (Kalendertag-Wechsel um Mitternacht UTC), unabhaengig von der
tatsaechlichen RTH-Session eines Equity-Instruments auf dem 24/7-Stundenraster. Fix: ein
konfigurierbarer opening_range_session_anchor ('calendar_day' Default, bit-identisch |
'session_open_hour', asset-class-aufgeloest ueber backtest_runner.
resolve_opening_range_session_open_hour).
"""
import sys
import types
import unittest.mock as mock

import pandas as pd
import pytest

if "nautilus_trader" not in sys.modules:
    class MockModule(types.ModuleType):
        def __getattr__(self, name):
            return mock.MagicMock()

    for _mod in (
        "nautilus_trader",
        "nautilus_trader.backtest",
        "nautilus_trader.backtest.engine",
        "nautilus_trader.backtest.models",
        "nautilus_trader.model",
        "nautilus_trader.model.data",
        "nautilus_trader.model.enums",
        "nautilus_trader.model.identifiers",
        "nautilus_trader.model.currencies",
        "nautilus_trader.model.objects",
        "nautilus_trader.model.instruments",
        "nautilus_trader.config",
        "nautilus_trader.common",
        "nautilus_trader.common.enums",
        "nautilus_trader.common.actor",
        "nautilus_trader.core",
        "nautilus_trader.core.message",
        "nautilus_trader.portfolio",
        "nautilus_trader.test_engine",
        "nautilus_trader.persistence",
        "nautilus_trader.persistence.catalog",
        "nautilus_trader.execution",
        "nautilus_trader.execution.messages",
        "nautilus_trader.indicators",
        "nautilus_trader.trading",
        "nautilus_trader.trading.strategy",
    ):
        sys.modules[_mod] = MockModule(_mod)

import automation.backtest_runner as br
from automation.strategies.opening_range_breakout import session_day_key

_NS_PER_HOUR = 3_600_000_000_000


def _ts_ns(iso: str) -> int:
    return int(pd.Timestamp(iso, tz="UTC").value)


def test_calendar_day_anchor_is_bit_identical_to_the_old_behaviour():
    ts = _ts_ns("2026-03-01T05:00:00Z")
    assert session_day_key(ts, anchor="calendar_day", session_open_hour=13) == 1


def test_calendar_day_anchor_ignores_session_open_hour():
    ts = _ts_ns("2026-03-01T20:00:00Z")
    assert (session_day_key(ts, anchor="calendar_day", session_open_hour=13)
            == session_day_key(ts, anchor="calendar_day", session_open_hour=0))


def test_session_open_hour_groups_bars_within_the_same_24h_window_together():
    before_open = _ts_ns("2026-03-02T12:00:00Z")   # 1h vor 13 UTC-Open, gehoert noch zu Tag 1
    at_open = _ts_ns("2026-03-02T13:00:00Z")        # Session-Start
    later_same_session = _ts_ns("2026-03-02T23:00:00Z")
    assert session_day_key(at_open, anchor="session_open_hour", session_open_hour=13) == (
        session_day_key(later_same_session, anchor="session_open_hour", session_open_hour=13))
    assert session_day_key(before_open, anchor="session_open_hour", session_open_hour=13) != (
        session_day_key(at_open, anchor="session_open_hour", session_open_hour=13))


def test_session_open_hour_rolls_over_at_the_configured_hour_not_midnight():
    """Der Kern des #922-Fixes: 05:00 UTC gehoert unter 'calendar_day' zum NEUEN Tag, unter
    'session_open_hour'=13 aber noch zur VORTAGES-Session (die erst um 13 UTC endet/beginnt)."""
    just_after_midnight = _ts_ns("2026-03-02T05:00:00Z")
    prev_day_late = _ts_ns("2026-03-01T20:00:00Z")
    assert session_day_key(just_after_midnight, anchor="calendar_day", session_open_hour=13) != (
        session_day_key(prev_day_late, anchor="calendar_day", session_open_hour=13))
    assert session_day_key(
        just_after_midnight, anchor="session_open_hour", session_open_hour=13) == (
        session_day_key(prev_day_late, anchor="session_open_hour", session_open_hour=13))


def test_resolve_opening_range_session_open_hour_falls_back_to_the_dataclass_default():
    assert br.resolve_opening_range_session_open_hour("XOM.ETORO", None, "EQUITY") == 13
    assert br.resolve_opening_range_session_open_hour("XOM.ETORO", {}, "EQUITY") == 13


def test_resolve_opening_range_session_open_hour_resolves_per_asset_class():
    table = {"EQUITY": 13, "CRYPTO": 0, "DEFAULT": 13}
    assert br.resolve_opening_range_session_open_hour("XOM.ETORO", table, "EQUITY") == 13
    assert br.resolve_opening_range_session_open_hour("BTC.ETORO", table, "CRYPTO") == 0


def test_resolve_opening_range_session_open_hour_rejects_an_unmapped_asset_class_key():
    table = {"EQUITY": 13, "DEFAULT": 13}
    with pytest.raises(ValueError, match="opening_range_session_open_hour_by_asset_class"):
        br.resolve_opening_range_session_open_hour("XAUUSD.ETORO", table, "COMMODITY")


def test_spaces_lowers_or_bars_and_cooldown_bars_minimums():
    import optuna

    from automation.optimizer.spaces import sample_params

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study()
    or_bars_seen, cooldown_seen = set(), set()
    for _ in range(200):
        trial = study.ask()
        params = sample_params("OpeningRangeBreakoutStrategy", trial)
        or_bars_seen.add(params["or_bars"])
        cooldown_seen.add(params["cooldown_bars"])
        study.tell(trial, 0.0)
    assert min(or_bars_seen) == 1
    assert min(cooldown_seen) == 1


def test_spaces_search_space_overrides_are_wired_for_opening_range_breakout(monkeypatch):
    import optuna

    import automation.optimizer.spaces as sp

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    # Issue #1316 (GH #1193) — "axis" ist Pflicht fuer den bar-denominierten or_bars; reale
    # config_dir() (nicht monkeypatched hier) ⇒ run_axis='rth' (echtes optimizer.json).
    monkeypatch.setattr(sp, "_search_space_overrides_cache", {
        "OpeningRangeBreakoutStrategy": {"XOM.ETORO": {"axis": "rth", "or_bars": [4, 5]}}
    })
    study = optuna.create_study()
    for _ in range(20):
        trial = study.ask()
        params = sp.sample_params("OpeningRangeBreakoutStrategy", trial, symbol="XOM.ETORO")
        assert 4 <= params["or_bars"] <= 5
        study.tell(trial, 0.0)


def test_backtest_json_carries_the_new_table():
    import json

    from automation.optimizer.trial_config import config_dir

    with open(config_dir() / "backtest.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    table = cfg["opening_range_session_open_hour_by_asset_class"]
    assert table["EQUITY"] > table["CRYPTO"]


def test_run_single_backtest_worker_accepts_the_new_param():
    import inspect

    sig = inspect.signature(br.run_single_backtest_worker)
    assert "opening_range_session_open_hour_by_asset_class" in sig.parameters


def test_every_call_site_of_run_single_backtest_worker_threads_the_session_hour_table():
    import ast
    import inspect

    source = inspect.getsource(br)
    tree = ast.parse(source)
    call_sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "run_single_backtest_worker":
                call_sites.append(node)
    assert call_sites
    for node in call_sites:
        kw_names = {kw.arg for kw in node.keywords}
        assert "opening_range_session_open_hour_by_asset_class" in kw_names, (
            f"Aufruf in Zeile {node.lineno} uebergibt opening_range_session_open_hour_by_asset_class "
            "nicht -- Issue #922: die Session-Stunde wuerde an dieser Call-Site unbemerkt auf den "
            "Dataclass-Default 13 zurueckfallen."
        )
