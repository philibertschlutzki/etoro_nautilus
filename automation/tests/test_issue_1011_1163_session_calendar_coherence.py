"""Issue #1011/#1163 (Katalog #1170, P1) — 1h-Bars für EQUITY über einen 24/7-Kalender
(Faktor 5,2).

Symptom (B-7): ``n = 1079`` Regressionsperioden über 45 Kalendertage in 26/26 Studies auf zwei
EQUITY-Symbolen ⇒ 24 Bars/Tag, 7 Tage/Woche. Erwartung für RTH-Equity: ≈ 209.

Root-Cause: die synthetische 1h-Bar-Erzeugung kennt keine Handelszeiten-Maske für EQUITY.

Fix (alles ohne Verhaltensänderung der Simulation — reine Messung):
1. Neue Telemetrie ``bars_per_calendar_day``/``session_coverage_fraction``
   (``backtest_runner._bar_calendar_telemetry``, je Trial gestempelt, Study-Median in report.py).
2. Neue Invariante ``check_session_calendar_coherence`` (severity ``high``): FAIL, wenn für
   EQUITY/COMMODITY ``bars_per_calendar_day > 8``.
3. summary_de.py Abschnitt 4 weist "24,00 h" zusätzlich als "= 24 synthetische Bars = 1
   Kalendertag" aus.
"""
import sys
import types
import unittest.mock as mock

import numpy as np
import pandas as pd
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

from automation.backtest_runner import _bar_calendar_telemetry, _calculate_stats
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sde


# --- backtest_runner._bar_calendar_telemetry --------------------------------------------------------

def test_reference_symptom_24_7_hourly_axis_yields_exactly_24_bars_per_calendar_day():
    """Reproduziert B-7: eine 1h-Bar-Achse ueber 45 Kalendertage OHNE Handelszeiten-Maske (24
    Bars/Tag, 7 Tage/Woche) -- exakt der aktuelle, dokumentierte Zustand."""
    idx = pd.date_range("2026-01-01", periods=45 * 24 + 1, freq="1h", tz="UTC")
    mtm = pd.Series(np.linspace(1000.0, 1010.0, len(idx)), index=idx)
    result = _bar_calendar_telemetry(mtm)
    assert result["bars_per_calendar_day"] == pytest.approx(24.0, abs=0.01)


def test_rth_only_equity_axis_yields_a_much_lower_bars_per_calendar_day():
    """Gegenprobe: eine Achse, die NUR Wochentage 13-21 UTC (8h-Fenster) traegt, liegt weit unter
    24 -- die von der Invariante erwartete Groessenordnung fuer echtes RTH-Equity."""
    full_idx = pd.date_range("2026-01-01", periods=45 * 24 + 1, freq="1h", tz="UTC")
    rth_idx = full_idx[(full_idx.weekday < 5) & (full_idx.hour >= 13) & (full_idx.hour < 21)]
    mtm = pd.Series(np.linspace(1000.0, 1010.0, len(rth_idx)), index=rth_idx)
    result = _bar_calendar_telemetry(mtm)
    assert result["bars_per_calendar_day"] < 8.0
    assert result["session_coverage_fraction"] == pytest.approx(1.0, abs=0.01)


def test_none_without_a_usable_time_index():
    result = _bar_calendar_telemetry(pd.Series([1.0, 2.0, 3.0]))
    assert result == {"bars_per_calendar_day": None, "session_coverage_fraction": None}


def test_none_for_a_missing_mtm_series():
    assert _bar_calendar_telemetry(None) == {
        "bars_per_calendar_day": None, "session_coverage_fraction": None}


# --- backtest_runner._calculate_stats wiring ---------------------------------------------------------

def test_calculate_stats_return_dict_carries_the_new_fields():
    idx = pd.date_range("2026-01-01", periods=45 * 24 + 1, freq="1h", tz="UTC")
    mtm = pd.Series(np.linspace(1000.0, 1010.0, len(idx)), index=idx)
    stats = _calculate_stats(
        [10.0, -5.0, 15.0], [(3600 * 1_000_000_000, 1.0)] * 3, 1000.0,
        mtm_series=mtm, min_trades_for_sortino=2)
    assert stats["bars_per_calendar_day"] == pytest.approx(24.0, abs=0.01)
    assert stats["session_coverage_fraction"] is not None


# --- invariants.check_session_calendar_coherence -----------------------------------------------------

def _study(symbol, strategy="Strat", bars_per_calendar_day=24.0):
    return {"symbol": symbol, "strategy": strategy, "bars_per_calendar_day": bars_per_calendar_day}


def test_reference_symptom_fails_for_equity_with_24_bars_per_day():
    result = inv.check_session_calendar_coherence(
        [_study("NVDA.ETORO", bars_per_calendar_day=24.0)],
        asset_class_by_symbol={"NVDA.ETORO": "EQUITY"})
    assert result.passed is False
    assert result.severity == "high"


def test_passes_for_a_plausible_rth_bar_density():
    result = inv.check_session_calendar_coherence(
        [_study("NVDA.ETORO", bars_per_calendar_day=4.6)],
        asset_class_by_symbol={"NVDA.ETORO": "EQUITY"})
    assert result.passed is True


def test_forex_and_crypto_are_not_gated_even_at_24_bars_per_day():
    """FOREX/CRYPTO sind ECHTE 24/7-Maerkte -- 24 Bars/Tag ist dort KEIN Symptom."""
    result = inv.check_session_calendar_coherence(
        [_study("BTC.ETORO", bars_per_calendar_day=24.0)],
        asset_class_by_symbol={"BTC.ETORO": "CRYPTO"})
    assert result.passed is True


def test_commodity_is_gated_like_equity():
    result = inv.check_session_calendar_coherence(
        [_study("GOLD.ETORO", bars_per_calendar_day=24.0)],
        asset_class_by_symbol={"GOLD.ETORO": "COMMODITY"})
    assert result.passed is False


def test_unresolved_asset_class_is_not_evaluated():
    result = inv.check_session_calendar_coherence(
        [_study("MYSTERY.ETORO", bars_per_calendar_day=24.0)], asset_class_by_symbol={})
    assert result.passed is True
    assert "nicht anwendbar" in result.detail


def test_check_is_wired_into_the_report():
    source = open(rpt.__file__, encoding="utf-8").read()
    assert "_inv.check_session_calendar_coherence(" in source


# --- report._asset_class_by_symbol -------------------------------------------------------------------

def test_asset_class_by_symbol_uses_the_default_policy_and_skips_unresolvable_symbols(monkeypatch):
    from automation import backtest_runner as btr

    def _fake_resolve(symbol, *, policy="reject"):
        assert policy == "default"
        if symbol == "NVDA.ETORO":
            return "EQUITY"
        raise ValueError("REJECT_INSTRUMENT_METADATA_INCOMPLETE")

    monkeypatch.setattr(btr, "_resolve_asset_class_for_symbol", _fake_resolve)
    result = rpt._asset_class_by_symbol(["NVDA.ETORO", "MYSTERY.ETORO"])
    assert result == {"NVDA.ETORO": "EQUITY"}


# --- summary_de.py Abschnitt 4: Bar-/Kalendertag-Hinweis -----------------------------------------------

def test_section_4_annotates_24h_as_24_bars_and_1_calendar_day():
    report = {
        "cross_study": {"longest_holding_studies": [
            {"strategy": "SqueezeBreakoutStrategy", "symbol": "NVDA.ETORO",
             "max_holding_time_s": 86400.0, "p95_holding_time_s": 43200.0},
        ]},
    }
    section = sde._section_4_longest_trades(report)
    assert "= 24 synthetische Bars = 1.00 Kalendertag(e)" in section
    assert "NICHT \"~1 Handelstag\"" in section
