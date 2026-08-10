"""Issue #924 — der ATR-Trailing-Stop-Floor (hourly_strategy_base._effective_atr_value, #897
Fix 4: max(ATR, atr_floor_bps * Preis)) war IM CODE bereits angewandt, aber ``atr_floor_bps``
selbst war ein flacher, in ``HourlyStrategyConfig`` hart kodierter Default (2.0 bps) ohne
Konfigurationsschlüssel in backtest.json/strategy_defaults.json/tournament.json und ohne
Asset-Class-Auflösung (für Krypto, siehe #920, strukturell zu eng).

Fix: ``resolve_atr_floor_bps`` (analog ``resolve_spread_bps``, #566/#898) löst den Floor über
dieselbe Asset-Class-Tabelle wie den Spread auf und speist ihn als ``params["atr_floor_bps"]``
in die Strategie-Config — NIE als Suchraum-Parameter überschreibbar.
"""
import sys
import types
import unittest.mock as mock

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
    ):
        sys.modules[_mod] = MockModule(_mod)

import automation.backtest_runner as br


def test_resolve_atr_floor_bps_falls_back_to_the_old_flat_default_when_unconfigured():
    assert br.resolve_atr_floor_bps("BTC.ETORO", None, "CRYPTO") == 2.0
    assert br.resolve_atr_floor_bps("BTC.ETORO", {}, "CRYPTO") == 2.0


def test_resolve_atr_floor_bps_resolves_per_asset_class():
    table = {"CRYPTO": 10.0, "EQUITY": 2.0, "DEFAULT": 2.0}
    assert br.resolve_atr_floor_bps("BTC.ETORO", table, "CRYPTO") == 10.0
    assert br.resolve_atr_floor_bps("XOM.ETORO", table, "EQUITY") == 2.0


def test_resolve_atr_floor_bps_rejects_an_unmapped_asset_class_key():
    table = {"CRYPTO": 10.0, "EQUITY": 2.0, "DEFAULT": 2.0}
    with pytest.raises(ValueError, match="atr_floor_bps_by_asset_class"):
        br.resolve_atr_floor_bps("XAUUSD.ETORO", table, "COMMODITY")


def test_crypto_floor_is_strictly_wider_than_equity_in_the_shipped_config():
    """Regressionssperre gegen die #924-Root-Cause: Krypto braucht einen strukturell groesseren
    Floor als Equity (siehe #920 fuer dieselbe Asset-Class-Ratio beim Spread)."""
    import json

    from automation.optimizer.trial_config import config_dir

    with open(config_dir() / "backtest.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    table = cfg["atr_floor_bps_by_asset_class"]
    assert table["CRYPTO"] > table["EQUITY"]


def test_run_single_backtest_worker_accepts_atr_floor_bps_by_asset_class():
    import inspect

    sig = inspect.signature(br.run_single_backtest_worker)
    assert "atr_floor_bps_by_asset_class" in sig.parameters


def test_every_call_site_of_run_single_backtest_worker_threads_the_asset_class_table():
    """AST-Vertragstest (analog test_issue_913_guard_injection.py): jeder Aufrufer von
    run_single_backtest_worker (ausser der Definition selbst) muss atr_floor_bps_by_asset_class
    als Keyword durchreichen, sonst faellt der Floor unbemerkt auf den flachen 2.0-Default
    zurueck — exakt die #924-Root-Cause, nur an einer neuen Call-Site reproduziert."""
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
    assert call_sites, "Kein Aufrufer von run_single_backtest_worker gefunden — Extraktion kaputt?"
    for node in call_sites:
        kw_names = {kw.arg for kw in node.keywords}
        assert "atr_floor_bps_by_asset_class" in kw_names, (
            f"Aufruf in Zeile {node.lineno} uebergibt atr_floor_bps_by_asset_class nicht — "
            "Issue #924: der ATR-Floor wuerde an dieser Call-Site unbemerkt auf 2.0 zurueckfallen."
        )
