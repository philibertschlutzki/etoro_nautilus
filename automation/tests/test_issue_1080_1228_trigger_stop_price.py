"""Issue #1080/#1228 (Katalog #1247+, P0, Semantik-Bump Simulation) — Trigger-Zeit-Stop und
Absetz-Zeit-Stop divergieren.

Symptom: ``check_stop_loss_decomposition_identity`` (blocking) feuerte fuer alle 14 NATGAS-Studies,
71924 Verletzungen bei 103590 Pruefungen (48,2-92,9%). In allen 140 uebrigen Studies: 0 Verletzungen.

Root-Cause: der Fill-Preis kuerzt sich algebraisch heraus; die Identitaet kann nur brechen, wenn
``stop_distance_bps != (anchor - stop_px)/anchor * 1e4``. Die beiden Referenzen stammten aus
verschiedenen Zeitpunkten: ``stop_distance_bps`` (Strategie-Tag) wird zum AUSLOESE-Zeitpunkt
gebildet; ``trigger_to_fill_gap_bps`` (backtest_runner) wurde bislang gegen ``trailing_stop_price``
(TRAILING_STOP_PRICE-Tag, Absetz-Zeitpunkt) gebildet. Ratscht der Stop zwischen Ausloesung und
Absetzen (monoton seit #1094), divergieren beide Referenzen um genau den Ratschenbetrag.

Fix:
1. ``self._trigger_stop_price`` wird ZUSAMMEN mit Anker/Distanz im Ausloese-Zweig eingefroren.
2. Neuer Tag ``STOP_TRIGGER_STOP_PRICE`` am Absetzen; ``TRAILING_STOP_PRICE`` bleibt UNVERAENDERT
   (Nenner von ``resolve_stop_exit_slippage_bps``, Zero-Regression).
3. ``trigger_to_fill_gap_bps`` wird jetzt gegen ``stop_trigger_stop_price`` gebildet, nicht gegen
   ``trailing_stop_price``.
4. Die Trigger-Trias wird NACH der Tag-Emission auf ``None`` gesetzt.
5. ``stop_ratchet_between_trigger_and_submit_bps`` macht den Ratschenbetrag selbst sichtbar.

Hinweis: ``automation/tests/conftest.py`` importiert das echte ``nautilus_trader`` VOR jeder
Testkollektion, sodass die aelteren, mock-installierenden Testmodule (die sonst per
``if "nautilus_trader" not in sys.modules`` gaeten) ihren Mock ueberspringen — dieses Modul kann
sich deshalb auf einen normalen, direkten Import verlassen.
"""
import inspect

import automation.backtest_runner as br
from automation.strategies import hourly_strategy_base as hsb


# --- backtest_runner._parse_exit_order_tags: neue Tags ------------------------------------------

def test_parses_stop_trigger_stop_price():
    meta = br._parse_exit_order_tags(["STOP_TRIGGER_STOP_PRICE:107.50000000"])
    assert meta["stop_trigger_stop_price"] == 107.5


def test_parses_stop_ratchet_between_trigger_and_submit_bps():
    meta = br._parse_exit_order_tags(["STOP_RATCHET_BETWEEN_TRIGGER_AND_SUBMIT_BPS:3.14159265"])
    assert abs(meta["stop_ratchet_between_trigger_and_submit_bps"] - 3.14159265) < 1e-9


# --- Root-Cause-Reproduktion: dieselbe Formel wie _finalize_round_trip --------------------------
# _finalize_round_trip ist eine verschachtelte Closure innerhalb von extract_metrics (braucht eine
# echte BacktestEngine); diese Tests reproduzieren die Zerlegungsformel eigenstaendig, um die
# Root-Cause UND den Fix unabhaengig von einer Engine zu belegen (dieselbe Technik wie
# test_issue_1054_1203_stop_loss_decomposition.py).

def _decompose_against(anchor_px, stop_distance_reference_px, gap_reference_px, fill_px, *,
                        is_short_close: bool) -> dict:
    """``stop_distance_reference_px``/``gap_reference_px`` sind bewusst getrennte Parameter — vor
    dem Fix war ``gap_reference_px`` (trailing_stop_price, Absetz-Zeitpunkt) potenziell UNGLEICH
    ``stop_distance_reference_px`` (der Stop-Level, gegen den STOP_DISTANCE_BPS gebildet wurde,
    Ausloese-Zeitpunkt) — genau das reproduziert die NATGAS-Verletzung."""
    sign = 1.0 if is_short_close else -1.0
    stop_distance_bps = sign * (stop_distance_reference_px - anchor_px) / anchor_px * 10_000.0
    trigger_to_fill_gap_bps = sign * (fill_px - gap_reference_px) / anchor_px * 10_000.0
    realized_loss_bps = sign * (fill_px - anchor_px) / anchor_px * 10_000.0
    return {
        "stop_distance_bps": stop_distance_bps,
        "trigger_to_fill_gap_bps": trigger_to_fill_gap_bps,
        "realized_loss_bps": realized_loss_bps,
        "identity_holds": abs(
            realized_loss_bps - (stop_distance_bps + trigger_to_fill_gap_bps)) < 1e-9,
    }


def test_root_cause_reproduced_when_gap_reference_uses_the_ratcheted_absetz_time_price():
    """Vor dem Fix: trigger_to_fill_gap_bps wurde gegen trailing_stop_price (Absetz-Zeitpunkt)
    gebildet, waehrend stop_distance_bps gegen den Ausloese-Zeitpunkt-Stop gebildet wurde. Ratscht
    der Stop zwischen beiden Zeitpunkten (LONG: der Stop steigt monoton), bricht die Identitaet."""
    d = _decompose_against(
        anchor_px=110.0, stop_distance_reference_px=108.0,  # Stop bei Ausloesung
        gap_reference_px=108.5,  # Stop hat bis zum Absetzen weiter geratscht (LONG, gestiegen)
        fill_px=107.0, is_short_close=False)
    assert d["identity_holds"] is False


def test_fix_reproduced_when_gap_reference_uses_the_frozen_trigger_time_price():
    """Nach dem Fix: beide Referenzen (stop_distance_bps UND trigger_to_fill_gap_bps) verwenden
    DENSELBEN eingefrorenen Ausloese-Zeitpunkt-Stop (stop_trigger_stop_price) — die Identitaet
    haelt exakt, unabhaengig davon, wie stark der Stop zwischenzeitlich geratscht ist."""
    frozen_trigger_stop_price = 108.0
    d = _decompose_against(
        anchor_px=110.0, stop_distance_reference_px=frozen_trigger_stop_price,
        gap_reference_px=frozen_trigger_stop_price,  # derselbe Wert wie stop_distance_reference_px
        fill_px=107.0, is_short_close=False)
    assert d["identity_holds"] is True


# --- Produktionsquelle: strukturelle Regressionstests --------------------------------------------

def test_trigger_to_fill_gap_bps_is_built_against_the_frozen_trigger_stop_price_not_trailing_stop_price():
    source = inspect.getsource(br.extract_metrics)
    assert "_sign * (closing_price - _trigger_stop_px) / _anchor_px" in source, (
        "trigger_to_fill_gap_bps wird nicht mehr gegen _trigger_stop_px (stop_trigger_stop_price) "
        "gebildet."
    )
    assert '_trigger_stop_px = None if is_data_end_fallback else meta.get("stop_trigger_stop_price")' in source


def test_stop_exit_slippage_still_uses_trailing_stop_price_zero_regression():
    """Zero-Regression (Fix Punkt 2): stop_exit_slippage_bps bleibt UNVERAENDERT gegen
    trailing_stop_price (Absetz-Zeitpunkt) gebildet."""
    source = inspect.getsource(br.extract_metrics)
    assert '_stop_px = None if is_data_end_fallback else meta.get("trailing_stop_price")' in source
    assert "resolve_stop_exit_slippage_bps(\n                closing_price, _stop_px" in source


def test_trigger_stop_price_field_declared_and_reset_after_position_open():
    init_source = inspect.getsource(hsb.HourlyStrategyBase.__init__)
    assert "self._trigger_stop_price: float | None = None" in init_source
    on_opened_source = inspect.getsource(hsb.HourlyStrategyBase.on_position_opened)
    assert "self._trigger_stop_price = None" in on_opened_source


def test_trigger_stop_price_is_frozen_together_with_anchor_and_distance():
    source = inspect.getsource(hsb.HourlyStrategyBase._check_exits_and_update)
    assert "self._trigger_stop_price = self._trailing_stop_price" in source


def test_trigger_triad_is_reset_after_tag_emission():
    """Fix Punkt 4 — ein watchdog-erzwungener Close ohne frischen Trigger-Bar darf keine
    veralteten Werte einer frueheren Ausloesung erben."""
    source = inspect.getsource(hsb.HourlyStrategyBase._execute_market_close)
    reset_idx = source.find("self._trigger_anchor_price = None")
    emission_idx = source.find("tags = tag_list")
    assert reset_idx != -1 and emission_idx != -1
    assert reset_idx > emission_idx, (
        "Die Trigger-Trias muss NACH der Tag-Emission (tags = tag_list) zurueckgesetzt werden."
    )
