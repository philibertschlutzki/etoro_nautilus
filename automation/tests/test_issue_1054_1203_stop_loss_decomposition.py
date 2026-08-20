"""Issue #1054/#1203 (P0, Katalog #1196-1221) — Der realisierte Stop-Verlust ist nicht in
Stopdistanz und Gap zerlegt.

Symptom: ``Spearman(k·ATR, realisierter Verlust)`` ist in 12/13 Läufen NEGATIV — ohne eine
Zerlegung des realisierten Verlusts in den Anteil, der aus der konfigurierten Stopdistanz stammt,
und den Anteil, der aus der Absetzen-zu-Fill-Latenz (Gap) stammt, misst jede Kalibrierung von k
eine Grösse, die zu 35-62% nicht von k abhängt.

Fix:
1. ``hourly_strategy_base._check_exits_and_update`` stempelt zum TRAILING_STOP-Ausloese-Zeitpunkt
   Ankerpreis (``STOP_TRIGGER_ANCHOR_PRICE``) und Stopdistanz (``STOP_DISTANCE_BPS``) als neue
   Order-Tags.
2. ``backtest_runner._parse_exit_order_tags`` parst beide; ``_finalize_round_trip`` bildet
   ``trigger_to_fill_gap_bps`` UND ``realized_loss_bps`` mit DEMSELBEN Nenner (dem Anker) wie
   ``stop_distance_bps`` — nur so ist die Summenidentität exakt (<1e-6), nicht bloss
   näherungsweise.
3. ``_aggregate_exit_telemetry`` zählt je Round-Trip, ob die Identität hält
   (``n_stop_loss_identity_checked``/``_violations``).
4. Neue Invariante ``invariants.check_stop_loss_decomposition_identity`` (severity ``blocking``):
   FAILt, wenn der Verletzungsanteil > 0,1% (Akzeptanzkriterium "≥ 99,9% der Round-Trips").

Hinweis: wie ``test_issue_953_1119_stop_loss_vs_bar_range.py`` ist ``nautilus_trader``/``pyarrow``
in dieser Sandbox nicht in der exakt gepinnten Version installierbar — die reinen Parsing-/
Aggregations-Funktionen werden über dieselbe sys.modules-Mock-Technik verifiziert."""
import sys
import types
import unittest.mock as mock

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


# --- backtest_runner.py: Tag-Parsing --------------------------------------------------------------

def test_parse_exit_order_tags_reads_stop_distance_bps():
    meta = br._parse_exit_order_tags(["STOP_DISTANCE_BPS:42.12345678"])
    assert meta["stop_distance_bps"] == 42.12345678


def test_parse_exit_order_tags_reads_stop_trigger_anchor_price():
    meta = br._parse_exit_order_tags(["STOP_TRIGGER_ANCHOR_PRICE:105.50000000"])
    assert meta["stop_trigger_anchor_price"] == 105.5


def test_parse_exit_order_tags_ignores_malformed_stop_distance_value():
    meta = br._parse_exit_order_tags(["STOP_DISTANCE_BPS:not_a_number"])
    assert "stop_distance_bps" not in meta


# --- backtest_runner.py: Identitaets-Formel (dieselbe wie in _finalize_round_trip) -----------------
# _finalize_round_trip ist eine verschachtelte Closure innerhalb von extract_metrics (braucht eine
# echte BacktestEngine) -- diese Tests reproduzieren exakt dieselbe Formel als eigenstaendige
# Berechnung, um die Konstruktions-Garantie (gemeinsamer Nenner => exakte Identitaet) unabhaengig
# von einer Engine zu verifizieren.

def _decompose(anchor_px: float, stop_px: float, fill_px: float, *, is_short_close: bool) -> dict:
    sign = 1.0 if is_short_close else -1.0
    stop_distance_bps = sign * (stop_px - anchor_px) / anchor_px * 10_000.0
    trigger_to_fill_gap_bps = sign * (fill_px - stop_px) / anchor_px * 10_000.0
    realized_loss_bps = sign * (fill_px - anchor_px) / anchor_px * 10_000.0
    return {
        "stop_distance_bps": stop_distance_bps,
        "trigger_to_fill_gap_bps": trigger_to_fill_gap_bps,
        "realized_loss_bps": realized_loss_bps,
    }


def test_decomposition_identity_holds_exactly_for_long_with_adverse_gap():
    # LONG: anchor=110, Stop=108 (Distanz 2 -> ~181.8 bps advers), Fill=107 (weiterer Gap advers).
    d = _decompose(anchor_px=110.0, stop_px=108.0, fill_px=107.0, is_short_close=False)
    assert d["stop_distance_bps"] > 0  # advers
    assert d["trigger_to_fill_gap_bps"] > 0  # advers (schlechter als der Stop gefuellt)
    assert abs(d["realized_loss_bps"] - (d["stop_distance_bps"] + d["trigger_to_fill_gap_bps"])) < 1e-9


def test_decomposition_identity_holds_exactly_for_short_with_favorable_gap():
    # SHORT: anchor=100, Stop=102 (Distanz advers), Fill=101.5 (guenstiger als der Stop -> Gap negativ).
    d = _decompose(anchor_px=100.0, stop_px=102.0, fill_px=101.5, is_short_close=True)
    assert d["stop_distance_bps"] > 0
    assert d["trigger_to_fill_gap_bps"] < 0
    assert abs(d["realized_loss_bps"] - (d["stop_distance_bps"] + d["trigger_to_fill_gap_bps"])) < 1e-9


# --- backtest_runner._aggregate_exit_telemetry: Zaehlung + Median -----------------------------------

def test_aggregate_exit_telemetry_computes_decomposition_medians():
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "pnl_bps": -10.0,
         "stop_distance_bps": 10.0, "trigger_to_fill_gap_bps": 2.0, "realized_loss_bps": 12.0},
        {"exit_reason": "TRAILING_STOP", "pnl_bps": -8.0,
         "stop_distance_bps": 20.0, "trigger_to_fill_gap_bps": -1.0, "realized_loss_bps": 19.0},
    ]
    result = br._aggregate_exit_telemetry(meta_list)
    assert result["stop_distance_bps_median"] == 15.0
    assert result["trigger_to_fill_gap_bps_median"] == 0.5
    assert result["realized_loss_bps_median"] == 15.5
    assert result["n_stop_loss_identity_checked"] == 2
    assert result["n_stop_loss_identity_violations"] == 0


def test_aggregate_exit_telemetry_counts_identity_violation():
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "pnl_bps": -10.0,
         "stop_distance_bps": 10.0, "trigger_to_fill_gap_bps": 2.0,
         "realized_loss_bps": 999.0},  # verletzt die Identitaet absichtlich
    ]
    result = br._aggregate_exit_telemetry(meta_list)
    assert result["n_stop_loss_identity_checked"] == 1
    assert result["n_stop_loss_identity_violations"] == 1


def test_aggregate_exit_telemetry_decomposition_restricted_to_trailing_stop():
    """Anders als bar_range_median_bps ist die Verlust-Zerlegung NUR fuer TRAILING_STOP-Exits
    definiert (ein PROFIT_TARGET/TIME_BOX-Exit hat keinen Stop-Level)."""
    meta_list = [{"exit_reason": "TIME_BOX", "pnl_bps": 1.0,
                  "stop_distance_bps": 10.0, "trigger_to_fill_gap_bps": 1.0,
                  "realized_loss_bps": 11.0}]
    result = br._aggregate_exit_telemetry(meta_list)
    assert result["stop_distance_bps_median"] is None
    assert result["n_stop_loss_identity_checked"] == 0


# --- invariants.check_stop_loss_decomposition_identity ----------------------------------------------

def _study(strategy, symbol, *, checked, violations):
    return {"strategy": strategy, "symbol": symbol,
            "n_stop_loss_identity_checked": checked, "n_stop_loss_identity_violations": violations}


def test_inconclusive_without_any_checked_round_trips():
    result = inv.check_stop_loss_decomposition_identity([_study("A", "X.ETORO", checked=0, violations=0)])
    assert result.passed is True
    assert result.inconclusive is True


def test_passes_with_zero_violations():
    result = inv.check_stop_loss_decomposition_identity(
        [_study("A", "X.ETORO", checked=5000, violations=0)])
    assert result.passed is True
    assert result.evaluable is True


def test_passes_within_tolerance_below_one_permille():
    result = inv.check_stop_loss_decomposition_identity(
        [_study("A", "X.ETORO", checked=10_000, violations=5)])  # 0.05% < 0.1%
    assert result.passed is True


def test_fails_above_one_permille_violation_fraction():
    result = inv.check_stop_loss_decomposition_identity(
        [_study("A", "X.ETORO", checked=1000, violations=5)])  # 0.5% > 0.1%
    assert result.passed is False
    assert result.severity == "blocking"
    assert "A/X.ETORO" in result.actual
