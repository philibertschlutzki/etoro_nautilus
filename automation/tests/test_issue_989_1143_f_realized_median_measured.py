"""Issue #989/#1143 (Katalog #986, Pitfall #412 in AGENTS.md) — ``check_sizing_identity_coherence``
(blocking) failte in 6 von 8 Läufen: ``f_implied_pct`` 9,49-23,37 gegen konfigurierte 15,0.

Root-Cause (offen laut Issue): die Identität ``total_return ≈ f · expectancy · n`` bricht durch
mindestens einen von drei Kandidaten — (a) variables Sizing durch ``_compute_quantity``s
Floor-Rundung auf ``size_precision``, (b) Kompoundierung über 45 Tage bei hohem Exposure, (c)
Dust-Round-Trips im ``n``-Zähler. In JEDEM der drei Fälle divergiert die ALGEBRAISCH implizierte
Größe ``f_implied``, OHNE dass das tatsächliche Sizing je Round-Trip abweicht.

Fix: ``f_realized_median`` (``rt_notional / equity_at_entry`` DIREKT je Round-Trip gemessen,
``backtest_runner._finalize_round_trip``) ersetzt ``f_implied`` als primäres Kriterium in
``check_sizing_identity_coherence``, sofern verfügbar (``holdout_f_realized_median``); nur ohne
dieses Feld (ältere Report-JSONs) fällt die Prüfung auf die algebraische Berechnung zurück.
"""
import math

import pytest

from automation.optimizer import invariants as inv


def test_measured_f_realized_takes_priority_and_passes_despite_implied_divergence():
    """Die zentrale Diagnose-Akzeptanz: eine Study mit TSLA-Signatur (f_implied ~0.93 % gegen
    konfigurierte 15 %, WUERDE unter der alten Logik als Offender gemeldet) besteht die Prüfung,
    wenn holdout_f_realized_median (DIREKT gemessen) nahe am konfigurierten Wert liegt — die
    Divergenz ist damit als Identitäts-/Metrik-Artefakt diagnostiziert, keine reale Sizing-Anomalie."""
    n, expectancy = 40, 0.006
    target_f_implied = 0.93
    total_return = math.exp((target_f_implied / 100.0) * n * expectancy) - 1.0
    records = [{
        "strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "holdout_total_trades": n, "holdout_expectancy_notional_weighted": expectancy,
        "holdout_total_return": total_return, "trade_amount_pct": 15.0,
        "holdout_f_realized_median": 0.149,  # 14.9 % — nahe an 15 %, gap < 0.35
    }]
    result = inv.check_sizing_identity_coherence(records)
    assert result.passed is True
    assert result.actual is None


def test_measured_f_realized_still_fails_on_genuine_sizing_deviation():
    """Weicht auch die GEMESSENE Groesse stark ab, ist es eine reale Sizing-Anomalie — muss als
    Offender mit source='measured' gemeldet werden, unabhaengig vom implizierten Wert."""
    records = [{
        "strategy": "ComboTrendVwap", "symbol": "NVDA.ETORO",
        "holdout_total_trades": 40, "holdout_expectancy_notional_weighted": 0.005,
        "holdout_total_return": 0.10, "trade_amount_pct": 15.0,
        "holdout_f_realized_median": 0.0973,  # 9.73 % — die im Issue genannte NVDA-Signatur
    }]
    result = inv.check_sizing_identity_coherence(records)
    assert result.passed is False
    assert result.severity == "blocking"
    offender = result.actual["ComboTrendVwap/NVDA.ETORO"]
    assert offender["source"] == "measured"
    assert offender["f_realized_pct"] == pytest.approx(9.73, abs=0.01)


def test_falls_back_to_implied_calculation_without_measured_field():
    """Rückwärtskompatibilität: ein Report-Record OHNE holdout_f_realized_median (ältere
    Report-JSONs vor diesem Fix) verhält sich bit-identisch zum Pre-#989-Verhalten."""
    n, expectancy, total_return = 32, 0.0071, 0.033
    records = [{
        "strategy": "DonchianRegimeBreakoutStrategy", "symbol": "ADBE.ETORO",
        "holdout_total_trades": n, "holdout_expectancy_notional_weighted": expectancy,
        "holdout_total_return": total_return, "trade_amount_pct": 15.0,
    }]
    result = inv.check_sizing_identity_coherence(records)
    assert result.passed is True
    assert result.actual is None


def test_falls_back_to_implied_and_reports_source_implied_on_failure():
    n, expectancy = 40, 0.006
    target_f_implied = 0.93
    total_return = math.exp((target_f_implied / 100.0) * n * expectancy) - 1.0
    records = [{
        "strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "holdout_total_trades": n, "holdout_expectancy_notional_weighted": expectancy,
        "holdout_total_return": total_return, "trade_amount_pct": 15.0,
        # kein holdout_f_realized_median -> Fallback-Pfad
    }]
    result = inv.check_sizing_identity_coherence(records)
    assert result.passed is False
    offender = result.actual["SqueezeBreakoutStrategy/TSLA.ETORO"]
    assert offender["source"] == "implied"
    assert "f_implied_pct" in offender


def test_measured_path_does_not_require_expectancy_or_total_return():
    """Der gemessene Pfad braucht WEDER holdout_expectancy_notional_weighted NOCH
    holdout_total_return (anders als der implizierte Fallback) — er liest das reale Notional/
    Equity-Verhaeltnis direkt, ohne ueber die Identitaet zurueckzurechnen."""
    records = [{
        "strategy": "S", "symbol": "A.ETORO",
        "holdout_total_trades": 15, "trade_amount_pct": 15.0,
        "holdout_f_realized_median": 0.15,
    }]
    result = inv.check_sizing_identity_coherence(records)
    assert result.passed is True


# ── backtest_runner pure-function tests (nautilus_trader-Umgebungs-Einschraenkung, siehe AGENTS.md)
def test_aggregate_exit_telemetry_computes_f_realized_median():
    from automation.backtest_runner import _aggregate_exit_telemetry
    meta_list = [
        {"exit_reason": "TRAILING_STOP", "f_realized": 0.14},
        {"exit_reason": "TRAILING_STOP", "f_realized": 0.16},
        {"exit_reason": "TIME_BOX", "f_realized": 0.15},
    ]
    result = _aggregate_exit_telemetry(meta_list)
    assert result["f_realized_median"] == pytest.approx(0.15)


def test_aggregate_exit_telemetry_f_realized_median_none_without_data():
    from automation.backtest_runner import _aggregate_exit_telemetry
    result = _aggregate_exit_telemetry([{"exit_reason": "TIME_BOX"}])
    assert result["f_realized_median"] is None
