"""Issue #1261 (GH #1131) — "Der Zeitbox-Vertrag muss auf Handels-Bars zählen".

Symptom: §4.1 rechnet "Handels-Bars (geschätzt) = Median-Bars · session_coverage_fraction" und
weist Werte von 0,96 bis 5,02 aus — bei einer nominellen Zeitbox von 24 Bars. Die tatsächliche
Haltedauer beträgt also 1-5 Handelsstunden, nicht "~1 Handelstag".

Root-Cause: ``time_box_bars = 24.0`` (``optimizer.json``) wird auf der 24/7-Kalenderachse
gezählt, dieselbe Achse, die #1011/#1163/``check_session_calendar_coherence`` bereits für die
Bar-Erzeugung selbst dokumentiert.

BEWUSSTER SCOPE dieser Änderung: das Issue sagt "Nach #1260 zählt die Zeitbox auf der RTH-
Achse" — das setzt voraus, dass #1260 (GH #1130) die Bar-Erzeugung LIVE auf RTH umgestellt hat.
#1260/#1130 implementiert in dieser Codebasis jedoch bewusst NUR Konfiguration
(``backtest.json['session_hours_by_asset_class']``) und zwei reine Hilfsfunktionen
(``backtest_runner.resolve_session_hours_by_asset_class``/``is_within_session_hours``), OHNE die
Bar-Erzeugung selbst live umzustellen — eine Live-Verdrahtung braucht einen echten Marktdaten-
Katalog zur End-to-End-Verifikation ("erzeugt das RTH-Fenster tatsächlich sinnvolle 1H-Bars, ohne
die Simulation stillschweigend zu verfälschen?"), die in dieser Sandbox nicht verfügbar ist
(kein ``nautilus_trader``-Import ohne Mock möglich, kein reales Marktdaten-Katalog-Fixture,
Python 3.11 statt der erforderlichen 3.12+ — dieselbe Einschränkung wie der bestehende
#987/#1141-Margin-Call-Verzicht in ``backtest_runner.py``).

Deshalb implementiert diese Änderung NUR den testbaren, unabhängig wertvollen Teil:
1. ``optimizer.json['time_box_bars_axis']`` (Default ``'calendar_24_7'``, EHRLICHER Ist-Zustand
   — NICHT ``'rth'``) deklariert explizit, auf welcher Achse ``time_box_bars`` (und jede
   ``max_bars_in_trade``-Suchraum-Bound in ``spaces.py``) gezählt wird.
2. ``invariants.check_timebox_unit_coherence`` (neu, severity ``high``) vergleicht diese
   DEKLARIERTE Achse gegen die BEOBACHTETE Achse (``bars_per_calendar_day``-Telemetrie, dieselbe
   Schwelle wie ``check_session_calendar_coherence``) und FAILt bei Divergenz.

``time_box_bars``/die ``max_bars_in_trade``-Suchraum-Bounds werden NICHT auf eine RTH-Achse
umkalibriert — das wäre eine Rekalibrierung auf eine Achse, die die Simulation tatsächlich noch
nicht zählt, und würde die Konfiguration von der Realität entkoppeln (schlimmer als der Status
quo). Die "Handels-Bars (geschätzt)"-Spalte in §4.1 (``summary_de.py``) bleibt aus demselben
Grund bestehen; sie referenziert jetzt zusätzlich ``time_box_bars_axis`` als expliziten
Ist-Zustand-Hinweis, statt zu behaupten, Schritt 2 sei bereits erfolgt.
"""
import json
import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest

if "nautilus_trader" not in sys.modules:
    class _MockModule(types.ModuleType):
        def __getattr__(self, name):
            return mock.MagicMock()

    for _mod in (
        "nautilus_trader", "nautilus_trader.backtest", "nautilus_trader.backtest.engine",
        "nautilus_trader.backtest.models", "nautilus_trader.model", "nautilus_trader.model.data",
        "nautilus_trader.model.enums", "nautilus_trader.model.identifiers",
        "nautilus_trader.model.currencies", "nautilus_trader.model.objects",
        "nautilus_trader.model.instruments", "nautilus_trader.config", "nautilus_trader.common",
        "nautilus_trader.common.enums", "nautilus_trader.common.actor", "nautilus_trader.core",
        "nautilus_trader.core.message", "nautilus_trader.portfolio", "nautilus_trader.test_engine",
        "nautilus_trader.persistence", "nautilus_trader.persistence.catalog",
        "nautilus_trader.execution", "nautilus_trader.execution.messages",
    ):
        sys.modules[_mod] = _MockModule(_mod)

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sde

_OPTIMIZER_JSON_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "optimizer.json"
)


def _study(symbol, strategy="Strat", bars_per_calendar_day=24.0):
    return {"symbol": symbol, "strategy": strategy, "bars_per_calendar_day": bars_per_calendar_day}


# --- invariants.check_timebox_unit_coherence --------------------------------------------------

def test_acceptance_fixture_fails_on_calendar_declared_axis_with_rth_counting():
    """Akzeptanzkriterium (#1131 wörtlich): "FAILt auf einer Fixture mit Kalender-Zeitbox und
    RTH-Zählung" — die Konfiguration behauptet ``'calendar_24_7'``, die tatsächlich gemessene
    Achse ist aber RTH (``bars_per_calendar_day`` unter der Schwelle)."""
    result = inv.check_timebox_unit_coherence(
        [_study("NVDA.ETORO", bars_per_calendar_day=4.6)],
        declared_axis="calendar_24_7",
        asset_class_by_symbol={"NVDA.ETORO": "EQUITY"})
    assert result.passed is False
    assert result.severity == "high"


def test_acceptance_fixture_passes_when_declared_and_observed_axis_agree_on_calendar():
    """Akzeptanzkriterium (#1131 wörtlich): "PASSt bei Übereinstimmung" — hier der aktuelle,
    ehrliche Ist-Zustand: deklariert UND gemessen ``'calendar_24_7'``."""
    result = inv.check_timebox_unit_coherence(
        [_study("NVDA.ETORO", bars_per_calendar_day=24.0)],
        declared_axis="calendar_24_7",
        asset_class_by_symbol={"NVDA.ETORO": "EQUITY"})
    assert result.passed is True


def test_fails_when_rth_is_declared_but_calendar_is_still_measured():
    """Die symmetrische Divergenz-Richtung: ``declared_axis='rth'`` behauptet, die Zeitbox zaehle
    Handelsstunden, aber die Bars sind weiterhin 24/7-aufgefuellt."""
    result = inv.check_timebox_unit_coherence(
        [_study("NVDA.ETORO", bars_per_calendar_day=24.0)],
        declared_axis="rth",
        asset_class_by_symbol={"NVDA.ETORO": "EQUITY"})
    assert result.passed is False
    assert result.severity == "high"


def test_passes_when_declared_and_observed_axis_agree_on_rth():
    result = inv.check_timebox_unit_coherence(
        [_study("NVDA.ETORO", bars_per_calendar_day=4.6)],
        declared_axis="rth",
        asset_class_by_symbol={"NVDA.ETORO": "EQUITY"})
    assert result.passed is True


def test_forex_and_crypto_are_not_gated_regardless_of_declared_axis():
    """FOREX/CRYPTO sind echte 24/7-Maerkte -- die RTH/Kalender-Unterscheidung ist dort
    bedeutungslos, unabhaengig davon, was ``time_box_bars_axis`` global deklariert."""
    result = inv.check_timebox_unit_coherence(
        [_study("BTC.ETORO", bars_per_calendar_day=24.0)],
        declared_axis="rth",
        asset_class_by_symbol={"BTC.ETORO": "CRYPTO"})
    assert result.passed is True


def test_unresolved_asset_class_is_not_evaluated():
    result = inv.check_timebox_unit_coherence(
        [_study("MYSTERY.ETORO", bars_per_calendar_day=24.0)],
        declared_axis="calendar_24_7", asset_class_by_symbol={})
    assert result.passed is True
    assert "nicht anwendbar" in result.detail


def test_mixed_cohort_flags_only_the_diverging_studies():
    result = inv.check_timebox_unit_coherence(
        [
            _study("NVDA.ETORO", bars_per_calendar_day=24.0),  # stimmt mit 'calendar_24_7' ueberein
            _study("XOM.ETORO", bars_per_calendar_day=4.6),    # weicht ab (beobachtet 'rth')
        ],
        declared_axis="calendar_24_7",
        asset_class_by_symbol={"NVDA.ETORO": "EQUITY", "XOM.ETORO": "EQUITY"})
    assert result.passed is False
    assert "Strat/XOM.ETORO" in result.actual
    assert "Strat/NVDA.ETORO" not in result.actual


def test_check_is_wired_into_the_report():
    source = open(rpt.__file__, encoding="utf-8").read()
    assert "_inv.check_timebox_unit_coherence(" in source
    assert 'optimizer_cfg.get("time_box_bars_axis"' in source


# --- optimizer.json ------------------------------------------------------------------------------

def test_optimizer_json_declares_time_box_bars_axis_as_the_honest_calendar_default():
    cfg = json.loads(_OPTIMIZER_JSON_PATH.read_text())
    assert cfg["time_box_bars_axis"] == "calendar_24_7"


def test_optimizer_json_schema_documents_time_box_bars_axis():
    cfg = json.loads(_OPTIMIZER_JSON_PATH.read_text())
    doc = cfg["_schema"]["fields"]["time_box_bars_axis"]
    assert isinstance(doc, str) and len(doc) > 0


# --- summary_de.py §4.1: keine falsche "Schritt 2 ist erfolgt"-Behauptung ----------------------

def test_section_4_1_still_shows_the_estimate_column_since_step_2_has_not_shipped():
    """Das Issue verlangt woertlich "§4.1 zeigt keine Schätzspalte mehr" — das setzt voraus, dass
    die Bar-Erzeugung tatsaechlich auf RTH umgestellt ist (Schritt 2). Da dieser Fix Schritt 2
    bewusst NICHT enthaelt (siehe Moduldocstring), MUSS die Schaetzspalte weiterhin erscheinen;
    sie zu entfernen waere eine falsche Tatsachenbehauptung. Der Test haelt diese bewusste
    Abweichung fest, statt sie stillschweigend zu ignorieren."""
    report = {
        "studies": [
            {"strategy": "Strat", "symbol": "NVDA.ETORO", "time_box_exit_fraction": 0.49,
             "median_bars_held": 3.0, "session_coverage_fraction": 0.19},
        ],
    }
    section = sde._section_4_longest_trades(report)
    assert "Handels-Bars (geschätzt)" in section
    assert "time_box_bars_axis" in section


def test_section_4_1_estimate_note_references_the_new_declarative_axis_config():
    report = {
        "studies": [
            {"strategy": "Strat", "symbol": "NVDA.ETORO", "time_box_exit_fraction": 0.49,
             "median_bars_held": 3.0, "session_coverage_fraction": 0.19},
        ],
    }
    section = sde._section_4_longest_trades(report)
    assert "check_timebox_unit_coherence" in section
