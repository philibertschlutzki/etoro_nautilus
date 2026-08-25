"""Issue #1260 (GH #1130) — "RTH-Bar-Maske fuer EQUITY/COMMODITY".

Symptom: EQUITY/COMMODITY-Studien zeigen ``zero_range_bar_fraction=1.0`` in 14/14 Studien, weil
1H-Bars aktuell auf einem durchgehenden 24/7-Kalenderraster gebaut werden (NautilusTrader's
interner ``TimeBarAggregator`` fuellt Luecken ausserhalb der echten Handelszeit mit flachen
O=H=L=C-Bars aus dem letzten bekannten Preis). Fix-Punkt 1 des Issues waere, das Bar-Raster fuer
EQUITY/COMMODITY auf ein RTH-Fenster (regular trading hours) zu beschraenken.

BEWUSSTER SCOPE dieser Aenderung (siehe ``backtest.json['_schema']['fields']
['session_hours_by_asset_class']`` und die Docstrings von ``resolve_session_hours_by_asset_class``/
``is_within_session_hours`` in ``backtest_runner.py`` fuer die volle Begruendung): implementiert
werden hier NUR die Konfiguration (``session_hours_by_asset_class``) und zwei reine,
unit-testbare Hilfsfunktionen. Beide werden AKTUELL an KEINER Call-Site aufgerufen, die den
Tick-Lade-/Bar-Aufbau-Pfad (``load_ticks_from_catalog``/``engine.add_data(ticks)``) tatsaechlich
beeinflusst. Grund: eine Verdrahtung in den Live-Pfad braucht einen echten Marktdaten-Katalog zur
End-to-End-Verifikation ("erzeugt das RTH-Fenster tatsaechlich sinnvolle 1H-Bars, ohne die
Simulation stillschweigend zu verfaelschen?") — genau die Einschraenkung, die diese Sandbox hat
(kein ``nautilus_trader``-Import ohne Mock moeglich, kein reales Marktdaten-Katalog-Fixture,
Python 3.11 statt der fuer ``nautilus_trader`` erforderlichen 3.12+). Dieselbe Begruendung traegt
bereits den bestehenden #987/#1141-Margin-Call-Verzicht in ``backtest_runner.py``
(``engine.add_venue``-Umgebung). ``simulation_semantics_version`` wird NICHT erhoeht, da sich am
tatsaechlichen Simulationsverhalten nichts aendert.

Dieser Test deckt die neue Konfiguration und die beiden reinen Funktionen ab; er verifiziert
explizit NICHT irgendeine Aenderung an Bar-Konstruktion oder Backtest-Ergebnissen (es gibt keine).
"""
import json
import sys
import types
import unittest.mock as mock
from pathlib import Path

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

_BACKTEST_JSON_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "backtest.json"
)


def _ts_ns(iso: str) -> int:
    return int(pd.Timestamp(iso, tz="UTC").value)


# ---------------------------------------------------------------------------
# resolve_session_hours_by_asset_class
# ---------------------------------------------------------------------------

def test_resolve_session_hours_returns_none_when_config_is_missing():
    assert br.resolve_session_hours_by_asset_class("EQUITY", None) is None
    assert br.resolve_session_hours_by_asset_class("EQUITY", {}) is None


def test_resolve_session_hours_returns_none_for_an_explicit_null_entry():
    table = {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}, "FOREX": None, "CRYPTO": None}
    assert br.resolve_session_hours_by_asset_class("FOREX", table) is None
    assert br.resolve_session_hours_by_asset_class("CRYPTO", table) is None


def test_resolve_session_hours_returns_none_for_an_unmapped_asset_class_key():
    table = {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}
    assert br.resolve_session_hours_by_asset_class("COMMODITY", table) is None


def test_resolve_session_hours_resolves_equity_and_commodity_windows():
    table = {
        "EQUITY": {"open_utc": "13:30", "close_utc": "20:00"},
        "COMMODITY": {"open_utc": "13:30", "close_utc": "20:00"},
        "FOREX": None,
        "CRYPTO": None,
        "DEFAULT": None,
    }
    assert br.resolve_session_hours_by_asset_class("EQUITY", table) == ("13:30", "20:00")
    assert br.resolve_session_hours_by_asset_class("COMMODITY", table) == ("13:30", "20:00")


def test_resolve_session_hours_matches_the_shipped_backtest_json_config():
    cfg = json.loads(_BACKTEST_JSON_PATH.read_text())
    table = cfg["session_hours_by_asset_class"]
    assert br.resolve_session_hours_by_asset_class("EQUITY", table) == ("13:30", "20:00")
    assert br.resolve_session_hours_by_asset_class("COMMODITY", table) == ("13:30", "20:00")
    assert br.resolve_session_hours_by_asset_class("FOREX", table) is None
    assert br.resolve_session_hours_by_asset_class("CRYPTO", table) is None
    assert br.resolve_session_hours_by_asset_class("DEFAULT", table) is None


# ---------------------------------------------------------------------------
# is_within_session_hours
# ---------------------------------------------------------------------------

def test_is_within_session_hours_true_inside_the_window_on_a_weekday():
    ts = _ts_ns("2026-08-24T15:00:00Z")  # Montag, innerhalb 13:30-20:00
    assert br.is_within_session_hours(ts, "13:30", "20:00") is True


def test_is_within_session_hours_false_before_the_window_on_a_weekday():
    ts = _ts_ns("2026-08-24T10:00:00Z")  # Montag, vor Open
    assert br.is_within_session_hours(ts, "13:30", "20:00") is False


def test_is_within_session_hours_false_after_the_window_on_a_weekday():
    ts = _ts_ns("2026-08-24T21:00:00Z")  # Montag, nach Close
    assert br.is_within_session_hours(ts, "13:30", "20:00") is False


def test_is_within_session_hours_open_boundary_is_inclusive():
    ts = _ts_ns("2026-08-24T13:30:00Z")  # exakt Open
    assert br.is_within_session_hours(ts, "13:30", "20:00") is True


def test_is_within_session_hours_close_boundary_is_exclusive():
    ts = _ts_ns("2026-08-24T20:00:00Z")  # exakt Close
    assert br.is_within_session_hours(ts, "13:30", "20:00") is False


def test_is_within_session_hours_excludes_saturday_by_default():
    ts = _ts_ns("2026-08-22T15:00:00Z")  # Samstag, waere innerhalb des Tagesfensters
    assert br.is_within_session_hours(ts, "13:30", "20:00") is False


def test_is_within_session_hours_excludes_sunday_by_default():
    ts = _ts_ns("2026-08-23T15:00:00Z")  # Sonntag, waere innerhalb des Tagesfensters
    assert br.is_within_session_hours(ts, "13:30", "20:00") is False


def test_is_within_session_hours_weekdays_only_false_keeps_weekend_bars_in_window():
    ts = _ts_ns("2026-08-22T15:00:00Z")  # Samstag, innerhalb 13:30-20:00
    assert br.is_within_session_hours(ts, "13:30", "20:00", weekdays_only=False) is True


def test_is_within_session_hours_friday_evening_is_still_in_window():
    ts = _ts_ns("2026-08-21T18:00:00Z")  # Freitag, innerhalb 13:30-20:00
    assert br.is_within_session_hours(ts, "13:30", "20:00") is True


# ---------------------------------------------------------------------------
# Konfigurations-Schema
# ---------------------------------------------------------------------------

def test_backtest_json_declares_session_hours_by_asset_class_with_expected_shape():
    cfg = json.loads(_BACKTEST_JSON_PATH.read_text())
    table = cfg["session_hours_by_asset_class"]
    assert table["EQUITY"] == {"open_utc": "13:30", "close_utc": "20:00"}
    assert table["COMMODITY"] == {"open_utc": "13:30", "close_utc": "20:00"}
    assert table["FOREX"] is None
    assert table["CRYPTO"] is None
    assert table["DEFAULT"] is None


def test_backtest_json_schema_documents_session_hours_by_asset_class():
    cfg = json.loads(_BACKTEST_JSON_PATH.read_text())
    doc = cfg["_schema"]["fields"]["session_hours_by_asset_class"]
    assert isinstance(doc, str) and len(doc) > 0


# ---------------------------------------------------------------------------
# Verdrahtungs-Scope-Grenze (dokumentiert die bewusste Nicht-Verdrahtung)
# ---------------------------------------------------------------------------

def test_load_ticks_from_catalog_does_not_reference_the_new_session_hours_helpers():
    """Haelt die dokumentierte Scope-Grenze fest: Fix-Punkt 1 (Live-Verdrahtung in den
    Tick-Lade-Pfad) ist bewusst NICHT Teil dieser Aenderung — sollte das in einem Folge-Issue
    nachgeholt werden, MUSS dieser Test angepasst werden (kein stiller Drift)."""
    import inspect
    src = inspect.getsource(br.load_ticks_from_catalog)
    assert "is_within_session_hours" not in src
    assert "resolve_session_hours_by_asset_class" not in src
