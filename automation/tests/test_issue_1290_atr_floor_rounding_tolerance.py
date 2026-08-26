"""Issue #1290 (GH #1163, Katalog #1272-1297, P2) — ``check_atr_floor_enforcement`` FAILt an der
Rundung des Reportfeldes.

Symptom (Lauf ``d50a7280``, Vwap/TSLA): ``atr_floor_bps_derived`` 3,6406 (gerundet auf 4
Nachkommastellen), ``k`` 2,4720860650593246 (ungerundet), gespeichertes
``effective_stop_distance_bps`` 8,9999, ``c_rt`` 3,0. Die feste ``1e-6``-Toleranz verwirft den Fall
gegen ``9,0 - 1e-6``, obwohl die TATSAECHLICH gemessene (ungerundete) Distanz 9,0008776 betraegt —
UEBER der Schranke. Die Rundung auf 4 Nachkommastellen induziert einen Fehler bis
``0,5·10⁻⁴ · k ≈ 1,2·10⁻⁴`` bps, Faktor ~124 ueber der alten Toleranz.

Fix: Vergleichstoleranz ``max(1e-6, 5e-5 · k)`` statt eines fixen ``1e-6`` (Pitfall #460)."""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, *, atr_median, floor, k, c_rt, stop_distance_bps_modelled):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_median_bps": atr_median,
        "atr_floor_bps_derived": floor,
        "atr_trailing_multiplier_median": k,
        "stop_distance_bps_modelled": stop_distance_bps_modelled,
        "round_trip_cost_bps": c_rt,
    }


def test_d50a7280_vwap_tsla_no_longer_fails_on_rounding_artifact():
    """Reproduziert den exakten Zahlensatz aus dem Katalogbefund: die gerundete, gespeicherte
    Distanz (8,9999) liegt knapp unter dem NOMINALEN Schwellenwert (9,0), aber innerhalb der
    Rundungspraezision — der Check darf hier nicht mehr FAILen."""
    result = inv.check_atr_floor_enforcement([
        _study("Vwap", "TSLA.ETORO", atr_median=3.6406, floor=3.6406,
               k=2.4720860650593246, c_rt=3.0, stop_distance_bps_modelled=8.9999),
    ])
    assert result.passed is True
    assert result.evaluable is True


def test_genuine_true_underrun_still_fails():
    """Eine ECHTE Unterschreitung (8,90 statt 8,9999 bei sonst identischen Parametern) muss
    weiterhin FAILen -- die erweiterte Toleranz darf keine echten Offender maskieren."""
    result = inv.check_atr_floor_enforcement([
        _study("Vwap", "TSLA.ETORO", atr_median=3.6406, floor=3.6406,
               k=2.4720860650593246, c_rt=3.0, stop_distance_bps_modelled=8.90),
    ])
    assert result.passed is False
    offender = result.actual["Vwap/TSLA.ETORO"]
    assert offender["effective_stop_distance_bps"] == 8.90


def test_tolerance_floor_is_never_below_1e_minus_6():
    """Bei sehr kleinem k darf die Toleranz nicht unter die alte feste Grenze fallen."""
    result = inv.check_atr_floor_enforcement([
        _study("A", "X.ETORO", atr_median=10.0, floor=10.0,
               k=0.001, c_rt=3.0, stop_distance_bps_modelled=9.0 - 5e-7),
    ])
    # min_required=9.0, tol=max(1e-6, 5e-5*0.001)=1e-6 -> 9.0-5e-7 >= 9.0-1e-6 -> PASS
    assert result.passed is True
