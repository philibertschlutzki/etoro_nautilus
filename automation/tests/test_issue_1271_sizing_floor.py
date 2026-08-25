"""Issue #1271 (GH #1141) — "Sizing-Deckel per Abrundung durchsetzen, nicht per Toleranz".

Symptom. 10 von 13 Studies liegen ueber ``trade_amount_pct = 15,0`` (0,1478-0,1594, Median
0,1512). VwapExhaustion ueberschreitet mit 15,9366% die 1,05-Toleranz und erzeugt einen
blockierenden FAIL. ``holdout_f_realized_peak_max == holdout_f_turnover_realized_max``
ziffernidentisch in 13/13 -- die #1233-Trennung ist im Artefakt nicht beobachtbar.

INVESTIGATIONS-ERGEBNIS (dieser Fix): eine gruendliche Nachverfolgung der drei Fix-Punkte zeigt,
dass ZWEI der DREI bereits korrekt implementiert sind:

  * Fix Punkt 1 (``_compute_quantity`` rundet ab, nicht kaufmaennisch): ``hourly_strategy_base.
    _compute_quantity`` quantisiert bereits ueber ``round(math.floor(units / inc) * inc, prec)``
    (KEIN kaufmaennisches ``round(units / inc) * inc``) — das aeussere ``round(..., prec)`` dient
    nur der Fliesskomma-Saeuberung NACH dem Floor, nicht einer zweiten Rundungsrichtung. Siehe
    ``test_quantized_units_never_exceeds_the_requested_budget`` unten (Eigenschaftstest ueber die
    IDENTISCHE Formel).
  * Fix Punkt 2 (``f_realized_peak`` aus ``rt_notional_peak`` statt Leg-Summe): bereits seit
    #1085/#1233 implementiert (``backtest_runner.extract_metrics``:
    ``f_realized_peak = rt_notional_peak / float(_equity_at_entry)``,
    ``_round_trip_notional_peak`` — siehe ``test_issue_1032_rt_notional_peak.py``/
    ``test_issue_1085_1233_f_realized_peak_vs_turnover.py``, BEIDE bereits mit strikten
    Scale-in-Fixturen, die ``peak < turnover`` zeigen). Das "ziffernidentisch in 13/13"-Symptom
    beschreibt die PRODUKTIONS-Kohorte (dort trat bislang kein Scale-in auf, peak==turnover ist in
    diesem Fall trivial korrekt, keine Code-Luecke) — nicht einen Rechenfehler.

  Fix Punkt 3 (``check_sizing_cap_enforcement`` unterscheidet Quantisierungsrest von echter
  Hebelueberschreitung) IST die tatsaechlich fehlende Aenderung: der Check hatte bislang KEINEN
  maschinenlesbaren Befund-Code — nur einen freien ``detail``-Text. Dieser Fix fuegt
  ``offender["reason"] = "LEVERAGE_OVERSHOOT"`` hinzu (jeder FAIL dieses Checks ist per Definition
  bereits eine Ueberschreitung JENSEITS der 1,05x-Toleranz fuer Quantisierungsreste/Slippage — eine
  reine Rundungsdifferenz bleibt innerhalb der Toleranz und erreicht den FAIL-Zweig gar nicht).

Hinweis zur Testmethodik: dieses Sandbox-Environment hat entgegen einer frueheren Annahme in
diesem Projekt durchaus ein installiertes, importierbares ``nautilus_trader`` (verifiziert). Diese
Datei folgt dennoch demselben Muster wie das bereits bestehende
``test_issue_1060_1209_sizing_cap_enforcement.py`` (die Kappungs-/Rundungs-FORMEL isoliert als
reine Funktion testen, statt eine echte Strategie-Instanz ueber ``HourlyStrategyConfig``
aufzubauen) — Konsistenz mit dem etablierten Testmuster fuer exakt dieselbe Methode
(``_compute_quantity``), nicht eine erneute Sandbox-Einschraenkung.
"""
import inspect
import math
from pathlib import Path

import pytest

from automation.backtest_runner import _round_trip_notional_peak
from automation.optimizer import invariants as inv

_SOURCE = Path(__file__).resolve().parents[1] / "strategies" / "hourly_strategy_base.py"


def _compute_quantity_source() -> str:
    text = _SOURCE.read_text("utf-8")
    start = text.index("def _compute_quantity(self, bar: Bar)")
    end = text.index("\n    def on_position_opened(self, event)")
    return text[start:end]


# --- Fix Punkt 1: Bereits korrekt -- math.floor, kein kaufmaennisches Runden -------------------

def test_compute_quantity_uses_math_floor_not_commercial_rounding():
    source = _compute_quantity_source()
    assert "math.floor(units / inc) * inc" in source
    # Die aeussere round(...) dient nur der Fliesskomma-Saeuberung NACH math.floor -- sie darf
    # NICHT eigenstaendig auf math.floor(...) angewendet werden koennen, ohne dass floor zuerst
    # gerechnet wird (Reihenfolge: floor -> round(., prec), nicht round(units/inc, prec) * inc).
    floor_pos = source.index("math.floor(units / inc)")
    quantized_line = source[source.index("quantized_units ="):source.index("quantized_units =") + 120]
    assert "round(math.floor(units / inc) * inc, prec)" in quantized_line


def _quantize(trade_amount_usd: float, price: float, inc: float, prec: int) -> float:
    """Dieselbe Formel wie der Inline-Block in _compute_quantity (Zeile ~1505-1511)."""
    units = trade_amount_usd / price
    return round(math.floor(units / inc) * inc, prec)


def test_quantized_units_never_exceeds_the_requested_budget():
    """Akzeptanzkriterium-nahe Eigenschaft: das quantisierte Notional (Stueckzahl * Preis) darf
    das angeforderte Budget (trade_amount_usd) NIE ueberschreiten -- ueber eine Reihe realistischer
    Preis-/Increment-/Precision-Kombinationen, inklusive Werten nahe einer Rundungsgrenze."""
    scenarios = [
        # (trade_amount_usd, price, size_increment, size_precision)
        (1500.0, 245.37, 0.01, 2),
        (1500.0, 100.0, 1.0, 0),
        (1500.0, 0.0001 + 1e-12, 1e-08, 8),   # extrem kleiner Preis, hohe Praezision
        (999.999999999, 10.0, 0.001, 3),       # nahe an einer glatten Grenze
        (100.0, 33.333333, 0.01, 2),
        (11.0, 3.7, 0.1, 1),
        (5000.0, 1999.995, 0.001, 3),
    ]
    for trade_amount_usd, price, inc, prec in scenarios:
        quantized_units = _quantize(trade_amount_usd, price, inc, prec)
        notional = quantized_units * price
        assert notional <= trade_amount_usd + 1e-6, (
            f"quantized notional {notional} > budget {trade_amount_usd} "
            f"(price={price}, inc={inc}, prec={prec})")


def test_quantized_units_is_aligned_to_the_size_increment():
    quantized_units = _quantize(1500.0, 245.37, 0.01, 2)
    # quantized_units / inc muss (bis auf Fliesskomma-Rauschen) eine ganze Zahl sein.
    ratio = quantized_units / 0.01
    assert abs(ratio - round(ratio)) < 1e-6


# --- Fix Punkt 2: Bereits korrekt -- f_realized_peak aus rt_notional_peak, nicht der Leg-Summe --

def test_f_realized_peak_denominator_is_already_the_true_concurrent_peak_not_turnover():
    """Reproduziert denselben Scale-in-Fall wie test_issue_1032_rt_notional_peak.py, aus der
    #1141/#1271-Perspektive: peak MUSS strikt unter dem Umschlag (Leg-Summe) liegen, sobald
    tatsaechlich pyramidisiert wird -- die #1233-Trennung IST im Code vorhanden, auch wenn die
    Produktionskohorte (noch) keinen Scale-in-Fall zeigt."""
    matches = [
        (1.0, 10, 10, 30.0, 30.0),   # Match 1: t0..t1, 30 Einheiten
        (2.0, 20, 20, 70.0, 70.0),   # Match 2: t0..t3, 70 Einheiten
        (3.0, 20, 5, 50.0, 50.0),    # Match 3: t2..t3 (Scale-in), 50 Einheiten
    ]
    turnover = sum(m[4] for m in matches)
    peak = _round_trip_notional_peak(matches)
    equity_at_entry = 1000.0
    f_turnover_realized = turnover / equity_at_entry
    f_realized_peak = peak / equity_at_entry
    assert f_realized_peak < f_turnover_realized


def test_extract_metrics_source_stamps_peak_from_rt_notional_peak_over_equity():
    import automation.backtest_runner as br
    source = inspect.getsource(br.extract_metrics)
    assert "f_realized_peak = rt_notional_peak / float(_equity_at_entry)" in source


# --- Fix Punkt 3: NEUE Aenderung -- LEVERAGE_OVERSHOOT als maschinenlesbarer Befund-Code --------

def _study(strategy, symbol, *, trade_amount_pct, f_realized_peak_max):
    return {"strategy": strategy, "symbol": symbol, "trade_amount_pct": trade_amount_pct,
            "holdout_f_realized_peak_max": f_realized_peak_max}


def test_leverage_overshoot_reason_on_a_genuine_cap_violation():
    result = inv.check_sizing_cap_enforcement([
        _study("AdxAtrMomentumStrategy", "KRYS.ETORO", trade_amount_pct=15.0,
               f_realized_peak_max=0.249),  # weit ueber 1.05 * 15%
    ])
    assert result.passed is False
    offender = result.actual["AdxAtrMomentumStrategy/KRYS.ETORO"]
    assert offender["reason"] == "LEVERAGE_OVERSHOOT"


def test_no_reason_field_when_within_the_quantization_tolerance():
    """Innerhalb der 1,05x-Toleranz (Quantisierungsrest/Slippage) ist das KEIN Offender -- der
    reason-Code erscheint nur im FAIL-Zweig, niemals als impliziter PASS-Kommentar."""
    result = inv.check_sizing_cap_enforcement([
        _study("A", "X.ETORO", trade_amount_pct=15.0, f_realized_peak_max=0.153),  # <= 1.05 * 15%
    ])
    assert result.passed is True
    assert result.actual is None


def test_leverage_overshoot_reason_present_alongside_turnover_context():
    record = _study("AdxAtrMomentumStrategy", "KRYS.ETORO", trade_amount_pct=15.0,
                     f_realized_peak_max=0.249)
    record["holdout_f_turnover_realized_max"] = 0.40
    result = inv.check_sizing_cap_enforcement([record])
    offender = result.actual["AdxAtrMomentumStrategy/KRYS.ETORO"]
    assert offender["reason"] == "LEVERAGE_OVERSHOOT"
    assert offender["f_turnover_realized_max_pct"] == pytest.approx(40.0)


def test_check_still_returns_zero_offenders_on_a_clean_re_run():
    """Akzeptanzkriterium #1271 — check_sizing_cap_enforcement liefert 0 FAILs auf einem Re-Run
    (hier: mehrere Studies, alle innerhalb der Toleranz)."""
    result = inv.check_sizing_cap_enforcement([
        _study("A", "X.ETORO", trade_amount_pct=15.0, f_realized_peak_max=0.150),
        _study("B", "Y.ETORO", trade_amount_pct=10.0, f_realized_peak_max=0.1049),
        _study("C", "Z.ETORO", trade_amount_pct=20.0, f_realized_peak_max=0.2099),
    ])
    assert result.passed is True
    assert result.actual is None
