"""Issue #951/#1117 (Katalog #960) — ATR-Floor bindet weiterhin und erzeugt Stopdistanzen unter
den Kosten.

Symptom. 13 von 39 Studies hatten ``atr_median_bps`` exakt auf ``atr_floor_bps_by_asset_class.
EQUITY = 2,0``; alle 13 hatten ``realized_stop_loss_ratio`` zwischen 32,3 und 53,6, 13 Studies eine
Stopdistanz unter den Round-Trip-Kosten (``check_stop_cost_ratio``, bis 0,68x). ``backtest.json``
stand unveraendert auf ``EQUITY: 2.0``.

Root-Cause (verifiziert in dieser Session): ``backtest_runner.cost_coupled_atr_floor_bps``
(#1096 Fix Punkt 1) existiert bereits und ist end-to-end bis in ``run_single_backtest_worker``
verdrahtet (die SIMULATION nutzt bereits den kostengekoppelten Floor) — aber
``report._atr_floor_bps_by_symbol`` (Rohmaterial fuer ``invariants.check_atr_scale_homogeneity``s
Floor-Bindungs-Diagnose) loeste bislang NUR die statische, rein asset-class-aufgeloeste Konstante
auf, nie den tatsaechlich pro Study kostengekoppelten (und damit hoeheren) Wert — der Report zeigte
den Floor als KONFIGURIERTE, nie als ABGELEITETE Groesse (Akzeptanzkriterium #951).

Fix: ``report._stamp_atr_floor_bps_derived`` stempelt ``atr_floor_bps_derived`` (die per-Study
kostengekoppelte Groesse) in jeden Study-Report-Eintrag; ``invariants.check_atr_scale_homogeneity``
vergleicht die Floor-Bindungs-Diagnose bevorzugt gegen dieses Feld statt gegen die grobe
Asset-Class-Konstante.

Fix Punkt 2 (ATR-Schaetzer auf informative Bars) und Fix Punkt 3 (spaces.py-Preflight via
constraints_func) bleiben BEWUSST zurückgestellt — dieselbe Risikoabwägung, die AGENTS.md bereits
für #1069 Punkte 2-4 dokumentiert (Änderung am Kern-ATR-Schätzer/Sampling-Pfad ohne echten
Mehrsymbol-Referenzlauf verifizierbar; in dieser Sandbox weiterhin kein installierbares
``nautilus_trader`` mit exakt gepinnter Version).
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

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


def _study(strategy, symbol, *, atr_median_bps, k_median, atr_floor_derived=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_median_bps": atr_median_bps,
        "atr_trailing_multiplier_median": k_median,
        "atr_floor_bps_derived": atr_floor_derived,
    }


def test_stamp_derives_a_higher_floor_than_the_static_asset_class_constant():
    """Reproduziert die #951-Rechnung: EQUITY-Floor 2.0bps, c_rt=4.0bps, k_median~1.7 -> ~7.06bps,
    deutlich ueber der statischen Konstante."""
    studies_out = [_study("A", "X.ETORO", atr_median_bps=2.0, k_median=1.7)]
    rpt._stamp_atr_floor_bps_derived(
        studies_out,
        atr_floor_bps_by_symbol={"X.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"X.ETORO": 4.0},
        min_stop_to_cost_ratio=3.0)
    assert studies_out[0]["atr_floor_bps_derived"] == round(3.0 * 4.0 / 1.7, 4)
    assert studies_out[0]["atr_floor_bps_derived"] > 2.0


def test_stamp_leaves_derived_floor_none_without_all_three_inputs():
    studies_out = [
        _study("A", "X.ETORO", atr_median_bps=2.0, k_median=None),
        _study("B", "Y.ETORO", atr_median_bps=2.0, k_median=1.5),
    ]
    rpt._stamp_atr_floor_bps_derived(
        studies_out,
        atr_floor_bps_by_symbol={"X.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={},
        min_stop_to_cost_ratio=3.0)
    assert studies_out[0]["atr_floor_bps_derived"] is None
    assert studies_out[1]["atr_floor_bps_derived"] is None


def test_stamp_does_not_lower_an_already_wide_static_floor():
    studies_out = [_study("A", "CRYPTO.ETORO", atr_median_bps=50.0, k_median=2.5)]
    rpt._stamp_atr_floor_bps_derived(
        studies_out,
        atr_floor_bps_by_symbol={"CRYPTO.ETORO": 50.0},
        round_trip_cost_bps_by_symbol={"CRYPTO.ETORO": 5.0},
        min_stop_to_cost_ratio=3.0)
    assert studies_out[0]["atr_floor_bps_derived"] == 50.0


def test_atr_scale_homogeneity_detects_binding_against_the_derived_floor_not_just_the_static_one():
    """Kernszenario #951: eine Study liegt exakt auf dem ABGELEITETEN (kostengekoppelten) Floor
    (7.06bps), NICHT auf der statischen Asset-Class-Konstante (2.0bps) -- die alte Fassung
    (nur atr_floor_bps_by_symbol) haette diese Bindung UEBERSEHEN."""
    derived = round(3.0 * 4.0 / 1.7, 4)
    records = [
        _study("A", "X.ETORO", atr_median_bps=derived, k_median=1.7, atr_floor_derived=derived),
        _study("B", "X.ETORO", atr_median_bps=derived * 20, k_median=1.7, atr_floor_derived=derived),
    ]
    result = inv.check_atr_scale_homogeneity(
        records, atr_floor_bps_by_symbol={"X.ETORO": 2.0})
    assert result.passed is False
    assert "A/X.ETORO" in result.provenance["atr_floor_binding_studies"]


def test_atr_scale_homogeneity_falls_back_to_the_static_floor_without_the_derived_field():
    """Rueckwaertskompatibilitaet: Legacy-Records ohne atr_floor_bps_derived (Pre-#951-Reports/
    aeltere Tests) fallen weiterhin auf die statische Asset-Class-Konstante zurueck."""
    records = [
        {"strategy": "A", "symbol": "X.ETORO", "atr_median_bps": 2.0},
        {"strategy": "B", "symbol": "X.ETORO", "atr_median_bps": 40.0},
    ]
    result = inv.check_atr_scale_homogeneity(
        records, atr_floor_bps_by_symbol={"X.ETORO": 2.0})
    assert result.passed is False
    assert "A/X.ETORO" in result.provenance["atr_floor_binding_studies"]


def test_atr_scale_homogeneity_does_not_flag_binding_against_a_higher_derived_floor_falsely():
    """Eine Study, deren atr_median_bps NICHT auf ihrem eigenen abgeleiteten Floor liegt, darf
    nicht als floor_binding gemeldet werden, auch wenn sie zufaellig ueber der statischen
    Asset-Class-Konstante liegt."""
    records = [
        _study("A", "X.ETORO", atr_median_bps=15.0, k_median=1.7, atr_floor_derived=7.06),
        _study("B", "X.ETORO", atr_median_bps=300.0, k_median=1.7, atr_floor_derived=7.06),
    ]
    result = inv.check_atr_scale_homogeneity(
        records, atr_floor_bps_by_symbol={"X.ETORO": 2.0})
    assert result.passed is False
    assert result.provenance is None or "A/X.ETORO" not in (
        result.provenance.get("atr_floor_binding_studies") or [])
