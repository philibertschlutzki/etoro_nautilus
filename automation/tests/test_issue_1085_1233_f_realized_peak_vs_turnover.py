"""Issue #1085/#1233 (Katalog #1247+, P0) — ``f_realized`` misst Umschlag, der Deckel begrenzt
Exposure.

Symptom: ``check_sizing_identity_coherence`` (blocking) meldete auf KRYS drei Studies ueber dem
0,35-Toleranzband (AdxAtr 24,93% gegen ``trade_amount_pct`` 15,0 = +66,2%) und deklarierte bei
``source='measured'`` "eine reale Sizing-Anomalie" — genau dieses Symbol traegt die drei einzigen
positiven Alphas mit |t| >= 2 des Katalogs.

Root-Cause: ``backtest_runner._finalize_round_trip``: ``rt_notional = sum(m[4] for m in matches)``
— die SUMME aller Legs eines Round-Trips (Umschlag). Der #1209-Deckel begrenzt dagegen das
GLEICHZEITIGE Netto-Exposure (``hourly_strategy_base._compute_quantity``). Zwei Aufstockungen zu
je 15% mit zwischenzeitlichem Teilabbau ergeben ``f_realized = 30%`` bei nie ueberschrittenen 15%.
``rt_notional_peak`` wurde bereits berechnet, aber nicht durch ``equity_at_entry`` geteilt.

Fix:
1. ``f_realized_peak = rt_notional_peak / equity_at_entry`` je Round-Trip gestempelt; Median und
   Maximum durchgereicht (``backtest_runner`` -> ``parsing`` -> ``confirm`` -> ``report``).
2. ``check_sizing_identity_coherence`` (blocking) und ``check_sizing_cap_enforcement`` (blocking)
   konsumieren ``f_realized_peak``. Der alte ``f_realized`` bleibt als
   ``f_turnover_realized`` (Umschlagsdiagnose, severity low bei zukuenftigem eigenem Check) erhalten.
3. Offender-Struktur weist beide Werte nebeneinander aus.
"""
import inspect

import pytest

from automation.optimizer import invariants as inv


# --- Root-Cause-Reproduktion: Turnover vs. Peak-Exposure -----------------------------------------

def test_two_scale_ins_with_partial_close_reproduce_turnover_double_counting_peak_does_not():
    """Zwei Aufstockungen zu je 15% des Notionals, danach ein Teilabbau, dann Restschluss — der
    Umschlag (Summe aller Legs) zaehlt 30%, das Peak-Exposure (das je gleichzeitig offene Maximum)
    bleibt bei 15%. Reines Arithmetik-Modell (dieselbe Formel wie backtest_runner._finalize_round_
    trip/_round_trip_notional_peak; kein NautilusTrader-Setup noetig)."""
    equity_at_entry = 1000.0
    # Legs: (+15% Kauf, +15% Kauf (Scale-in, Peak jetzt 30%), -20% Teilverkauf, -10% Restschluss).
    # rt_notional (Umschlag) = Summe |notional| aller Legs.
    leg_notionals = [150.0, 150.0, 200.0, 100.0]
    rt_notional_turnover = sum(leg_notionals)
    # Peak = das groesste GLEICHZEITIG offene Netto-Notional entlang der Leg-Sequenz.
    running = 0.0
    peak = 0.0
    net_deltas = [150.0, 150.0, -200.0, -100.0]  # Kaeufe positiv, Verkaeufe negativ
    for d in net_deltas:
        running += d
        peak = max(peak, abs(running))
    f_turnover_realized = rt_notional_turnover / equity_at_entry
    f_realized_peak = peak / equity_at_entry

    assert f_turnover_realized == pytest.approx(0.6)  # 600/1000, deutlich > jede Einzelposition
    assert f_realized_peak == pytest.approx(0.30)  # nie mehr als 300/1000 gleichzeitig offen
    assert f_turnover_realized > f_realized_peak


# --- Produktionsquelle: strukturelle Regressionstests ---------------------------------------------

def test_f_realized_peak_is_stamped_from_rt_notional_peak_over_equity_at_entry():
    import automation.backtest_runner as br
    source = inspect.getsource(br.extract_metrics)
    assert "f_realized_peak = rt_notional_peak / float(_equity_at_entry)" in source


def test_aggregate_exit_telemetry_returns_both_turnover_and_peak_medians_and_maxima():
    from automation.backtest_runner import _aggregate_exit_telemetry
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "f_realized": 0.30, "f_realized_peak": 0.15},
        {"exit_reason": "TRAILING_STOP", "f_realized": 0.20, "f_realized_peak": 0.10},
        {"exit_reason": "TIME_BOX", "f_realized": 0.10, "f_realized_peak": 0.10},
    ]
    result = _aggregate_exit_telemetry(meta_list)
    assert result["f_turnover_realized_median"] == pytest.approx(0.20)
    assert result["f_turnover_realized_max"] == pytest.approx(0.30)
    assert result["f_realized_peak_median"] == pytest.approx(0.10)
    assert result["f_realized_peak_max"] == pytest.approx(0.15)


# --- invariants.check_sizing_identity_coherence: konsumiert jetzt f_realized_peak -----------------

def test_check_sizing_identity_coherence_no_longer_flags_krys_scale_in_turnover_as_an_anomaly():
    """Reproduziert das KRYS-Symptom: der ALTE turnover-basierte Wert (24,93%) haette als Offender
    gegolten; der PEAK-basierte Wert (nahe an trade_amount_pct) besteht die Pruefung."""
    record = {
        "strategy": "AdxAtrMomentumStrategy", "symbol": "KRYS.ETORO",
        "holdout_total_trades": 40, "trade_amount_pct": 15.0,
        "holdout_f_realized_peak_median": 0.153,  # nahe an 15%, gap < 0.35
        "holdout_f_turnover_realized_median": 0.2493,  # der alte, faelschlich gemeldete Wert
    }
    result = inv.check_sizing_identity_coherence([record])
    assert result.passed is True


def test_check_sizing_identity_coherence_still_flags_a_genuine_peak_exposure_anomaly():
    record = {
        "strategy": "S", "symbol": "X.ETORO",
        "holdout_total_trades": 40, "trade_amount_pct": 15.0,
        "holdout_f_realized_peak_median": 0.30,  # 2x trade_amount_pct -- echte Anomalie
    }
    result = inv.check_sizing_identity_coherence([record])
    assert result.passed is False
    offender = result.actual["S/X.ETORO"]
    assert offender["source"] == "measured"
    assert offender["f_realized_peak_pct"] == pytest.approx(30.0)


# --- invariants.check_sizing_cap_enforcement: konsumiert jetzt f_realized_peak_max ----------------

def test_check_sizing_cap_enforcement_classifies_peak_violation_vs_turnover_only():
    """Ein Befund mit peak > cap ist eine echte Hebelueberschreitung; turnover >> peak (aber peak
    <= cap) waere KEIN Offender mehr (siehe test_check_sizing_cap_enforcement_scale_in_turnover_
    alone_is_not_an_offender)."""
    record = {
        "strategy": "AdxAtrMomentumStrategy", "symbol": "KRYS.ETORO", "trade_amount_pct": 15.0,
        "holdout_f_realized_peak_max": 0.249,  # > 1.05 * 15% -> echte Hebelueberschreitung
        "holdout_f_turnover_realized_max": 0.40,
    }
    result = inv.check_sizing_cap_enforcement([record])
    assert result.passed is False
    offender = result.actual["AdxAtrMomentumStrategy/KRYS.ETORO"]
    assert offender["f_realized_peak_max_pct"] == pytest.approx(24.9)
    assert offender["f_turnover_realized_max_pct"] == pytest.approx(40.0)


def test_check_sizing_cap_enforcement_scale_in_turnover_alone_is_not_an_offender():
    """Peak bleibt innerhalb der Toleranz, obwohl der Umschlag (Scale-in ueber mehrere Legs) den
    konfigurierten Anteil weit uebersteigt — das ist KEIN Cap-Verstoss (#1233-Kernbehauptung)."""
    record = {
        "strategy": "S", "symbol": "X.ETORO", "trade_amount_pct": 15.0,
        "holdout_f_realized_peak_max": 0.153,  # <= 1.05 * 15%
        "holdout_f_turnover_realized_max": 0.30,  # Umschlag deutlich hoeher, aber irrelevant
    }
    result = inv.check_sizing_cap_enforcement([record])
    assert result.passed is True
