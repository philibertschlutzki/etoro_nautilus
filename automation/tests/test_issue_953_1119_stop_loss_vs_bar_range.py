"""Issue #953/#1119 (Katalog #960) — Ein-Bar-Ausführungslatenz als eigentliche Verlustuntergrenze.

Symptom (B-11): innerhalb eines Symbols ist der realisierte Stop-Verlust nahezu konstant, während
die Stopdistanz um Faktor 20 variiert — mit "Verlust = Stopdistanz + Überschiessen" nicht
vereinbar, wohl aber mit "Verlust = adverse Bewegung EINER Bar", weil der Exit ein Marktauftrag auf
der Folge-Bar ist (der Stop ist ein Bar-Schluss-Signal, kein echter ``StopMarketOrder``, #1092B).

Fix:
1. ``bar_range_median_bps`` wird jetzt gemessen (``hourly_strategy_base``: je-Bar Bar-Spannen-
   Ablesungen waehrend offener Positionen, BAR_RANGE_MEDIAN_BPS-Exit-Tag) und bis in den Report
   exportiert (``backtest_runner._aggregate_exit_telemetry``/``_parse_exit_order_tags`` ->
   ``parsing.TournamentMetrics.oos_bar_range_median_bps`` -> ``report._study_record``s
   ``bar_range_median_bps``-Feld), zusammen mit dem bereits bestehenden ``oos_stop_exit_lag_bars``
   (#1095).
2. Neue Invariante ``invariants.check_stop_loss_vs_bar_range`` (severity ``blocking``): FAILt, wenn
   der Stop-Verlust GLEICHZEITIG in der Groessenordnung einer Bar-Spanne UND ein grosses Vielfaches
   der konfigurierten Stopdistanz ist — der Verlust ist dann latenz-, nicht stopgetrieben.

Hinweis: die Aenderungen an ``hourly_strategy_base.py``/``backtest_runner.py`` sind nicht
end-to-end in dieser Sandbox lauffaehig (kein installierbares ``nautilus_trader`` mit exakt
gepinnter Version, Python 3.11 statt der geforderten >=3.12) — die reinen Aggregations-/Parsing-
Funktionen werden ueber dieselbe sys.modules-Mock-Technik wie
``test_issue_1096_cost_coupled_atr_floor.py`` verifiziert; die neue Invariante selbst (reine
Python-Logik ueber Report-Dicts) ist voll unit-testbar.
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
        sys.modules[_mod] = MockModule(_mod)

import automation.backtest_runner as br
from automation.optimizer import invariants as inv


# --- backtest_runner.py: parsing + aggregation (pure functions, mock-importable) -----------------

def test_parse_exit_order_tags_reads_bar_range_median_bps():
    meta = br._parse_exit_order_tags(["EXIT_REASON:TRAILING_STOP", "BAR_RANGE_MEDIAN_BPS:12.3456"])
    assert meta["bar_range_median_bps"] == 12.3456


def test_parse_exit_order_tags_ignores_malformed_bar_range_value():
    meta = br._parse_exit_order_tags(["BAR_RANGE_MEDIAN_BPS:not_a_number"])
    assert "bar_range_median_bps" not in meta


def test_aggregate_exit_telemetry_computes_median_bar_range_across_round_trips():
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "pnl_bps": -10.0, "bar_range_median_bps": 5.0},
        {"exit_reason": "TRAILING_STOP", "pnl_bps": -12.0, "bar_range_median_bps": 15.0},
        {"exit_reason": "TIME_BOX", "pnl_bps": 3.0, "bar_range_median_bps": 25.0},
    ]
    result = br._aggregate_exit_telemetry(meta_list)
    assert result["bar_range_median_bps"] == 15.0  # median(5, 15, 25)


def test_aggregate_exit_telemetry_bar_range_is_not_restricted_to_trailing_stop_exits():
    """Anders als gross_loss_mean_bps_trailing_stop ist bar_range_median_bps UNBEDINGT (jede
    offene Position traegt Bar-Spannen-Telemetrie, unabhaengig vom Exit-Grund) -- eine reine
    Marktdaten-Eigenschaft, keine Stop-spezifische Groesse."""
    meta_list = [{"exit_reason": "PROFIT_TARGET", "pnl_bps": 8.0, "bar_range_median_bps": 9.0}]
    result = br._aggregate_exit_telemetry(meta_list)
    assert result["bar_range_median_bps"] == 9.0


def test_aggregate_exit_telemetry_bar_range_is_none_without_any_reading():
    result = br._aggregate_exit_telemetry([{"exit_reason": "TIME_BOX", "pnl_bps": 1.0}])
    assert result["bar_range_median_bps"] is None


# --- invariants.check_stop_loss_vs_bar_range ------------------------------------------------------

def _study(strategy, symbol, *, loss_pooled, bar_range, stop_loss_ratio, n_ts_losses=40):
    return {
        "strategy": strategy, "symbol": symbol,
        "oos_gross_loss_mean_bps_trailing_stop_pooled": loss_pooled,
        "bar_range_median_bps": bar_range,
        "realized_stop_loss_ratio": stop_loss_ratio,
        "oos_n_trailing_stop_losses": n_ts_losses,
    }


def test_inconclusive_without_enough_trailing_stop_evidence():
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=10.0, bar_range=10.0, stop_loss_ratio=6.0,
               n_ts_losses=5),
    ])
    # Issue #995/#1147 (Pitfall #413) — seit diesem Fix ist ``passed=None`` (nicht ``True``) bei
    # fehlender Evidenz das kanonische Signal (top-level ``evaluable=False`` statt nur verschachtelt
    # in ``evaluability``); diese Assertion war seither stale (dieser Test wurde bei #995/#1147
    # nicht mitgezogen).
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False
    assert result.severity == "blocking"


def test_reproduces_b11_latency_driven_loss_signature():
    """Verlust (10bps) liegt nahe der Bar-Spanne (12bps, ratio 0.83, im Band) UND
    realized_stop_loss_ratio (40) ist weit ueber der Schwelle 5 -- latenzgetrieben, FAIL."""
    result = inv.check_stop_loss_vs_bar_range([
        _study("SqueezeBreakout", "PLTR.ETORO", loss_pooled=10.0, bar_range=12.0,
               stop_loss_ratio=40.0),
    ])
    assert result.passed is False
    assert result.severity == "blocking"
    assert "SqueezeBreakout/PLTR.ETORO" in result.actual


def test_passes_when_loss_is_far_from_bar_range_even_with_high_stop_ratio():
    """realized_stop_loss_ratio > 5, aber der Verlust ist NICHT in der Groessenordnung einer
    Bar-Spanne (ratio 3.0, ausserhalb [0.7, 1.4]) -- kein Latenz-Beweis, PASS."""
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=30.0, bar_range=10.0, stop_loss_ratio=8.0),
    ])
    assert result.passed is True


def test_passes_when_stop_ratio_is_below_threshold_even_with_matching_bar_range():
    """Verlust nahe der Bar-Spanne, aber realized_stop_loss_ratio unter der Schwelle 5 -- der Stop
    reagiert (noch) plausibel auf seinen Multiplikator, kein Latenz-Beweis."""
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=10.0, bar_range=10.0, stop_loss_ratio=3.0),
    ])
    assert result.passed is True


def test_passes_at_exactly_the_band_boundaries_requires_both_conditions():
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=7.0, bar_range=10.0, stop_loss_ratio=5.0),  # ratio==5.0, not > 5.0
    ])
    assert result.passed is True


def test_provenance_carries_both_ratios_for_every_measured_study():
    result = inv.check_stop_loss_vs_bar_range([
        _study("A", "X.ETORO", loss_pooled=10.0, bar_range=12.0, stop_loss_ratio=40.0),
        _study("B", "Y.ETORO", loss_pooled=30.0, bar_range=10.0, stop_loss_ratio=8.0),
    ])
    assert "A/X.ETORO" in result.provenance["ratios_by_study"]
    assert "B/Y.ETORO" in result.provenance["ratios_by_study"]
    assert result.provenance["ratios_by_study"]["A/X.ETORO"]["realized_stop_loss_ratio"] == 40.0
