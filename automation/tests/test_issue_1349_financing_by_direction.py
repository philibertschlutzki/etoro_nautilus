"""Issue #1349 (GH #1243, P2) — Overnight-Finanzierung 0.0 ist für Short- und Hebelpositionen
falsch und wurde nirgends geprüft.

Symptom. ``overnight_financing_bps_per_day_by_asset_class`` war durchgängig 0.0. Auf der RTH-Achse
hält jede Position, die die Zeitbox von mehr als einem Handelstag ausschöpft, mindestens eine
Nacht.

Root-Cause. Für eine ungehebelte Long-Position ist 0.0 sachlich richtig — eToro berechnet
Overnight-Gebühren auf gehebelte und Short-Positionen. Der Fehler ist, dass 0.0 als Default für
ALLES galt, nie gegen die tatsächlich gehandelte Richtung geprüft.

Fix.
1. ``overnight_financing_bps_per_day_by_asset_class`` wird zu
   ``{asset_class: {"long": …, "short": …}}``; ``resolve_financing_bps_per_day`` löst nach
   ``position_side`` auf, rückwärtskompatibel (Skalar = "nur Long").
2. Neue Invariante ``check_financing_applies_to_shorts``: FAIL, wenn eine Study Short-Round-Trips
   mit Haltedauer über Nacht ausweist UND ``financing_bps == 0.0``.
3. ``_finalize_round_trip`` wählt die Rate je Round-Trip nach der tatsächlich gehandelten Richtung
   (``is_short_close``), nicht mehr eine einzige asset-class-weite Rate.

Akzeptanzkriterien:
- Eine Short-Position über eine Nacht trägt Finanzierungskosten > 0 im Kostendrag.
- ``check_financing_applies_to_shorts`` erscheint im Invarianten-Strom und PASSt bzw. FAILt korrekt
  in beiden Testfällen.
- Der Long-Nullwert trägt in ``backtest.json`` einen Kommentar mit der Begründung.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv


# ── backtest_runner.resolve_financing_bps_per_day — Richtungsaufloesung ──────────────────────────

def test_new_dict_form_resolves_by_position_side():
    from automation.backtest_runner import resolve_financing_bps_per_day
    cfg = {"EQUITY": {"long": 0.0, "short": 0.79}}
    long_bps, _ = resolve_financing_bps_per_day(
        "AAPL.ETORO", cfg, asset_class_key="EQUITY", position_side="long")
    short_bps, _ = resolve_financing_bps_per_day(
        "AAPL.ETORO", cfg, asset_class_key="EQUITY", position_side="short")
    assert long_bps == 0.0
    assert short_bps == 0.79


def test_legacy_scalar_is_interpreted_as_long_only():
    from automation.backtest_runner import resolve_financing_bps_per_day
    long_bps, is_legacy_long = resolve_financing_bps_per_day(
        "BTC.ETORO", {"CRYPTO": 3.5}, asset_class_key="CRYPTO", position_side="long")
    short_bps, is_legacy_short = resolve_financing_bps_per_day(
        "BTC.ETORO", {"CRYPTO": 3.5}, asset_class_key="CRYPTO", position_side="short")
    assert long_bps == 3.5
    assert is_legacy_long is False
    assert short_bps == 0.0
    assert is_legacy_short is True


# ── backtest_runner.slippage_floor_bps_from_spread (Nachbarschaft #1242, dieselbe Datei) ─────────

def test_slippage_floor_is_half_the_spread():
    from automation.backtest_runner import slippage_floor_bps_from_spread
    assert slippage_floor_bps_from_spread(3.0) == pytest.approx(1.5)
    assert slippage_floor_bps_from_spread(0.0) == 0.0


# ── invariants.check_financing_applies_to_shorts ──────────────────────────────────────────────────

def _study(**overrides) -> dict:
    # median_bars_held=30 * 3600s/Bar = 30h > 1 Kalendertag — ein RTH-Bar-Zaehler unterschaetzt
    # die tatsaechliche Kalenderzeit strukturell (ein Bar-Zaehler-Cap von 7 Bars/Handelstag
    # entspricht real bis zu ~17,5h Session-Luecke pro Nacht, siehe #1343/GH#1237); dieser Fixture-
    # Wert testet die Formel selbst (median_bars_held * bar_seconds/86400 >= 1.0), unabhaengig von
    # der konkreten Bar-Zaehler-Obergrenze irgendeiner einzelnen Strategie.
    base = {
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
        "allow_short": True, "median_bars_held": 30.0, "applied_financing_bps_per_day": 0.79,
        "symbol_bar_quality": {"median_delta_t_s": 3600.0},
    }
    base.update(overrides)
    return base


def test_short_strategy_with_overnight_holding_and_zero_financing_fails():
    """Akzeptanzkriterium — Testfall 1: FAIL."""
    result = inv.check_financing_applies_to_shorts(
        [_study(applied_financing_bps_per_day=0.0)])
    assert result.passed is False
    assert result.severity == "high"
    assert "TrendPullbackStrategy/TSLA.ETORO" in result.actual["offenders"]


def test_short_strategy_with_overnight_holding_and_nonzero_financing_passes():
    """Akzeptanzkriterium — Testfall 2: PASS."""
    result = inv.check_financing_applies_to_shorts([_study()])
    assert result.passed is True


def test_long_only_strategy_with_zero_financing_is_not_an_offender():
    """allow_short=False -> ausserhalb des Anwendungsbereichs; die Study zaehlt nicht als Kandidat."""
    result = inv.check_financing_applies_to_shorts(
        [_study(allow_short=False, applied_financing_bps_per_day=0.0)])
    assert result.passed is None
    assert result.inconclusive is True


def test_short_strategy_with_intraday_only_holding_is_not_flagged():
    """median_bars_held so klein, dass die Haltedauer unter einem Kalendertag bleibt -> keine
    Uebernachtung -> 0.0-Finanzierung ist hier korrekt, kein Befund."""
    result = inv.check_financing_applies_to_shorts(
        [_study(median_bars_held=2.0, applied_financing_bps_per_day=0.0)])
    assert result.passed is True


def test_no_candidates_is_inconclusive_not_pass_or_fail():
    result = inv.check_financing_applies_to_shorts([])
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_missing_telemetry_fields_are_excluded_from_candidates():
    result = inv.check_financing_applies_to_shorts(
        [{"strategy": "S", "symbol": "X", "allow_short": True}])
    assert result.passed is None
    assert result.inconclusive is True


# ── report.py Verdrahtung ──────────────────────────────────────────────────────────────────────

def test_check_financing_applies_to_shorts_is_wired_into_build_report():
    import inspect
    from automation.optimizer import report
    src = inspect.getsource(report._build_report)
    assert "check_financing_applies_to_shorts" in src


# ── backtest.json ──────────────────────────────────────────────────────────────────────────────

def test_backtest_json_financing_is_dict_form_with_long_zero_and_documented():
    cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    financing = cfg["overnight_financing_bps_per_day_by_asset_class"]
    for asset_class, rates in financing.items():
        assert isinstance(rates, dict), f"{asset_class}: erwartet dict-Form, war {rates!r}"
        assert rates["long"] == 0.0
        assert rates["short"] > 0.0
    doc = cfg["_schema"]["fields"]["overnight_financing_bps_per_day_by_asset_class"]
    assert "1349" in doc
    assert "gebuehrenfrei" in doc or "keine Overnight-Gebuehr" in doc


def test_backtest_json_slippage_floor_values_are_half_the_spread():
    cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    spread = cfg["spread_bps_by_asset_class"]
    slippage = cfg["slippage_bps_by_asset_class"]
    for asset_class in spread:
        assert slippage[asset_class] == pytest.approx(0.5 * spread[asset_class])
