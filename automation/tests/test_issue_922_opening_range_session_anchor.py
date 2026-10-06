"""Issue #922 — OpeningRangeBreakoutStrategy verankerte den "Handelstag" ausschliesslich auf
pd.Timestamp(bar.ts_init).day (Kalendertag-Wechsel um Mitternacht UTC), unabhaengig von der
tatsaechlichen RTH-Session eines Equity-Instruments auf dem 24/7-Stundenraster.

Issue #1356 (GH #1252) — der #922-Anker 'session_open_hour' (UTC-Stunde 13 aus
opening_range_session_open_hour_by_asset_class) war NYSE-Open nur in EDT und ist ersatzlos entfallen;
der Default-Anker 'trading_day' folgt dem Session-Fenster in Börsen-Lokalzeit
(test_issue_1356_session_windows_dst.py). Hier bleiben 'calendar_day' (bit-identisch), der Suchraum
und die Verdrahtung des Session-Fensters durch alle Worker-Call-Sites.
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
    assert session_day_key(ts, anchor="calendar_day") == 1


def test_calendar_day_anchor_ignores_the_session_window():
    from automation.session_windows import parse_session_window

    ts = _ts_ns("2026-03-02T20:00:00Z")
    window = parse_session_window({"tz": "America/New_York", "open": "09:30", "close": "16:00"})
    assert (session_day_key(ts, anchor="calendar_day", session_window=window, bar_interval_ns=_NS_PER_HOUR)
            == session_day_key(ts, anchor="calendar_day") == 2)


def test_session_open_hour_anchor_and_resolver_are_gone():
    """Issue #1356 — die UTC-Stunden-Konstante ist entfallen (statt still weiterzuwirken)."""
    with pytest.raises(ValueError, match="1356"):
        session_day_key(_ts_ns("2026-03-02T13:00:00Z"), anchor="session_open_hour")
    assert not hasattr(br, "resolve_opening_range_session_open_hour")


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


def test_backtest_json_no_longer_carries_the_utc_hour_table():
    import json

    from automation.optimizer.trial_config import config_dir

    with open(config_dir() / "backtest.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert "opening_range_session_open_hour_by_asset_class" not in cfg
    assert cfg["session_hours_by_asset_class"]["EQUITY"]["tz"] == "America/New_York"


def test_run_single_backtest_worker_takes_the_session_window_source_not_the_hour_table():
    import inspect

    sig = inspect.signature(br.run_single_backtest_worker)
    assert "session_hours_by_asset_class" in sig.parameters
    assert "opening_range_session_open_hour_by_asset_class" not in sig.parameters


def test_every_call_site_of_run_single_backtest_worker_threads_the_session_window_source():
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
        assert "session_hours_by_asset_class" in kw_names, (
            f"Aufruf in Zeile {node.lineno} uebergibt session_hours_by_asset_class nicht -- Issue #1356: "
            "Tick-Filter UND Opening-Range-Anker (HourlyStrategyConfig.session_window) fielen an dieser "
            "Call-Site unbemerkt auf 'kein Fenster' zurueck."
        )
