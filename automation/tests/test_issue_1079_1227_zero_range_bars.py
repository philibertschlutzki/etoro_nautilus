"""Issue #1079/#1227 (Katalog #1247+, P0) — ``bar_range_median_bps == 0`` in 154/154:
Nullspannen-Bars dominieren den Median.

Symptom: der blockierende ``check_stop_loss_vs_bar_range`` meldete in 11/11 Laeufen
``POPULATION_UNAVAILABLE_AFTER_FIX`` bei 1 360 745 realen TRAILING_STOP-Exits.

Root-Cause: ``hourly_strategy_base.py`` nahm JEDE Bar in ``_position_bar_range_bps_readings`` auf,
auch synthetische Fuellbars mit ``high == low`` (Kalenderluecken bei niedriger
``session_coverage_fraction``). Bei einer typischen Median-Haltedauer von 13 Bars ist der
Positions-Median dadurch strukturell 0.

Fix:
1. ``_position_bar_range_bps_readings`` nimmt nur noch Bars mit ``high > low`` auf;
   ``_position_bar_count``/``_position_zero_range_bar_count`` fuehren die volle Population +
   den ausgeschlossenen Anteil separat mit.
2. Drei Tags am schliessenden Order: ``BAR_RANGE_MEDIAN_BPS`` (jetzt ueber der bereinigten
   Population), ``BAR_RANGE_P75_BPS``, ``ZERO_RANGE_BAR_FRACTION`` — durchgereicht bis in den
   Study-Record (``bar_range_p75_bps``, ``zero_range_bar_fraction``).
3. ``check_stop_loss_vs_bar_range`` weist ``zero_range_bar_fraction`` als Kontext aus.
4. Neue Invariante ``check_zero_range_bar_share`` (severity ``high``).
"""
import inspect
import re

import pytest

from automation.optimizer import invariants as inv
from automation.strategies import hourly_strategy_base as hsb

# Hinweis: automation/tests/conftest.py importiert das echte nautilus_trader VOR jeder
# Testkollektion, sodass die aelteren, mock-installierenden Testmodule (die sonst per
# ``if "nautilus_trader" not in sys.modules`` gaeten) ihren Mock ueberspringen — dieses Modul kann
# sich deshalb auf einen normalen, direkten Import verlassen (siehe
# test_issue_1080_1228_trigger_stop_price.py, dieselbe Absicherung).


# --- Strukturtest gegen die Produktionsquelle (dieselbe Technik wie
# test_issue_1022_1171_bar_range_median_bps.py, das Modul selbst benoetigt kein volles
# NautilusTrader-Strategy-Setup fuer diese Guard-Pruefung) --------------------------------------

def _execute_market_close_source() -> str:
    return inspect.getsource(hsb.HourlyStrategyBase._execute_market_close)


def _check_exits_source() -> str:
    # Der Lese-/Filter-Block liegt in _check_exits_and_update (dort werden die Bar-Spannen
    # waehrend der offenen Position gesammelt).
    return inspect.getsource(hsb.HourlyStrategyBase._check_exits_and_update)


def test_bar_range_readings_are_only_appended_for_bars_with_positive_range():
    source = _check_exits_source()
    # Die Append-Anweisung an _position_bar_range_bps_readings muss hinter einer
    # ``high > low``-Bedingung stehen (nicht mehr unbedingt fuer jede Bar).
    assert "self._position_bar_range_bps_readings.append(" in source
    match = re.search(
        r"if float\(bar\.high\) > float\(bar\.low\):\s*\n\s*self\._position_bar_range_bps_readings\.append\(",
        source,
    )
    assert match, (
        "self._position_bar_range_bps_readings.append(...) ist nicht mehr hinter einem "
        "'high > low'-Guard — Root-Cause von #1079/#1227 waere sonst nicht behoben."
    )


def test_zero_range_bar_count_and_total_bar_count_are_tracked():
    source = inspect.getsource(hsb.HourlyStrategyBase.__init__)
    assert "self._position_bar_count: int = 0" in source
    assert "self._position_zero_range_bar_count: int = 0" in source


def test_three_new_tags_emitted_on_closing_order():
    # Issue #1259 (GH #1129) — BAR_RANGE_MEDIAN_BPS/_P75_BPS wurden in die gemeinsame Hilfsfunktion
    # ``_bar_range_bps_tags`` extrahiert (zweite Aufrufstelle: der Dyn-TP-Limit-Order-Pfad,
    # #1034); ``_execute_market_close`` ruft sie auf, statt die Tags selbst zu bilden.
    close_source = _execute_market_close_source()
    assert "self._bar_range_bps_tags()" in close_source
    assert "ZERO_RANGE_BAR_FRACTION:" in close_source
    tags_source = inspect.getsource(hsb.HourlyStrategyBase._bar_range_bps_tags)
    assert "BAR_RANGE_MEDIAN_BPS:" in tags_source
    assert "BAR_RANGE_P75_BPS:" in tags_source
    assert "BAR_RANGE_POPULATION_N:" in tags_source


def test_nearest_rank_percentile_matches_backtest_runner_methodology():
    """Dieselbe Nearest-Rank-Arithmetik wie backtest_runner._pctl (Konsistenz der
    Perzentil-Methodik ueber das Modul-Paar hinweg, siehe Docstring an der Deklaration)."""
    import automation.backtest_runner as br

    vals = sorted([1.0, 2.0, 3.0, 4.0, 10.0, 12.0, 30.0])
    for p in (0.5, 0.75, 0.9):
        assert hsb._nearest_rank_percentile(vals, p) == br._pctl(vals, p)


def test_synthetic_series_reproduces_zero_median_before_and_positive_median_after_filtering():
    """Akzeptanzkriterium — eine synthetische Bar-Serie aus 76% Nullspannen-Bars reproduziert den
    heutigen Median 0 (unbereinigt) und den bereinigten Median > 0 (nur high > low)."""
    import statistics
    # 76 Nullspannen-Bars (high == low), 24 Bars mit echter Spanne — dieselbe ungefaehre Quote wie
    # das Symptom (session_coverage_fraction ~= 0.24).
    ranges_bps = [0.0] * 76 + [5.0 + i * 0.1 for i in range(24)]
    assert statistics.median(ranges_bps) == 0.0  # Vor-Fix-Verhalten (unbereinigt).

    cleaned = [r for r in ranges_bps if r > 0.0]
    assert statistics.median(cleaned) > 0.0  # Nach-Fix-Verhalten (bereinigt).
    zero_range_bar_fraction = 1.0 - len(cleaned) / len(ranges_bps)
    assert zero_range_bar_fraction == pytest.approx(0.76)


# --- invariants.check_zero_range_bar_share ------------------------------------------------------

def _record(zero_range_bar_fraction, strategy="S", symbol="SYM.ETORO"):
    return {"strategy": strategy, "symbol": symbol,
            "zero_range_bar_fraction": zero_range_bar_fraction}


def test_check_zero_range_bar_share_passes_when_high_share_is_rare():
    records = [_record(0.1, symbol=f"S{i}.ETORO") for i in range(9)] + [_record(0.9, symbol="S9.ETORO")]
    result = inv.check_zero_range_bar_share(records)
    assert result.passed is True
    assert result.severity == "high"


def test_check_zero_range_bar_share_fails_when_high_share_is_common():
    """Reproduziert die Symptombeschreibung: session_coverage_fraction ~= 0.24 impliziert
    zero_range_bar_fraction ~= 0.76 in der ueberwiegenden Mehrheit der Studies."""
    records = [_record(0.76, symbol=f"S{i}.ETORO") for i in range(10)]
    result = inv.check_zero_range_bar_share(records)
    assert result.passed is False
    assert result.actual == 1.0
    assert len(result.provenance["offenders"]) == 10


def test_check_zero_range_bar_share_inconclusive_without_telemetry():
    result = inv.check_zero_range_bar_share([{"strategy": "S", "symbol": "SYM.ETORO"}])
    assert result.passed is None
    assert result.evaluable is False


# --- invariants.check_stop_loss_vs_bar_range surfaces zero_range_bar_fraction as context --------

def test_check_stop_loss_vs_bar_range_surfaces_zero_range_bar_fraction_as_context():
    record = {
        "strategy": "S", "symbol": "SYM.ETORO",
        "bar_range_median_bps": 10.0,
        "oos_gross_loss_mean_bps_trailing_stop_pooled": 10.0,  # ratio 1.0, im Default-Band [0.7,1.4]
        "realized_stop_loss_ratio": 8.0,  # > min_realized_stop_loss_ratio (5.0) -> Offender
        "oos_n_trailing_stop_losses": 50,
        "zero_range_bar_fraction": 0.42,
    }
    result = inv.check_stop_loss_vs_bar_range([record])
    assert result.passed is False
    key = "S/SYM.ETORO"
    assert result.actual[key]["zero_range_bar_fraction"] == 0.42
