"""Issue #1267 (GH #1137) — ``applied_slippage_bps`` und ``COST_MODEL_ZERO_REALISM`` aus einer
Quelle.

Symptom: ``COST_MODEL_ZERO_REALISM`` meldete ``slippage_bps_by_asset_class = 0.0`` fuer alle
Klassen und schloss „full_realism ist ein No-Op"; gleichzeitig trug jede Study
``applied_slippage_bps = 151,5869`` und die Kostenstress-Leiter zog genau diesen Betrag ab.

Root-Cause: ``sweep.warn_if_cost_model_zero_realism()`` liest die statische ``backtest.json``-
Konfiguration AM SWEEP-START (vor jeder Kalibrierung); die Leiter (``backtest_runner.
_full_realism_expectancy``) den kalibrierten Slippage-Cache. Zwei Quellen, ein Begriff.

Fix: ``report._emit_cost_model_realism_event`` (aufgerufen aus ``_build_report``, direkt neben der
Berechnung von ``cross_study.cost_model_realism_source``) emittiert das NACHTRAEGLICHE, aus den
tatsaechlich gemessenen ``applied_*``-Feldern abgeleitete Gegenstueck: ``COST_MODEL_ZERO_REALISM``
feuert nur noch, wenn die EFFEKTIVE Groesse (nicht die Config) null ist
(``cost_model_realism_source == 'config_zero'``); andernfalls ``COST_MODEL_REALISM_FROM_
CALIBRATION`` mit Quelle und dem gemessenen Median.
"""
import logging

from automation.optimizer import report as rpt


def _study(strategy, symbol, *, slippage, financing):
    return {
        "strategy": strategy, "symbol": symbol,
        "applied_slippage_bps": slippage, "applied_financing_bps_per_day": financing,
    }


# ---------------------------------------------------------------------------------------------
# _applied_slippage_bps_median_nonzero
# ---------------------------------------------------------------------------------------------

def test_median_of_nonzero_applied_slippage():
    studies = [
        _study("A", "X.ETORO", slippage=100.0, financing=0.5),
        _study("B", "Y.ETORO", slippage=151.5869, financing=0.4),
        _study("C", "Z.ETORO", slippage=200.0, financing=0.6),
    ]
    assert rpt._applied_slippage_bps_median_nonzero(studies) == 151.5869


def test_median_excludes_true_zero_studies():
    studies = [
        _study("A", "X.ETORO", slippage=0.0, financing=0.0),
        _study("B", "Y.ETORO", slippage=151.5869, financing=0.4),
    ]
    assert rpt._applied_slippage_bps_median_nonzero(studies) == 151.5869


def test_median_is_none_without_any_nonzero_study():
    studies = [_study("A", "X.ETORO", slippage=0.0, financing=0.0)]
    assert rpt._applied_slippage_bps_median_nonzero(studies) is None


def test_median_ignores_studies_without_resolved_applied_fields():
    studies = [
        {"strategy": "A", "symbol": "X.ETORO"},  # applied_* fehlt
        _study("B", "Y.ETORO", slippage=151.5869, financing=0.4),
    ]
    assert rpt._applied_slippage_bps_median_nonzero(studies) == 151.5869


# ---------------------------------------------------------------------------------------------
# _emit_cost_model_realism_event
# ---------------------------------------------------------------------------------------------

def _captured_events(monkeypatch):
    calls = []

    def _fake_emit(logger, event_type, payload, level=logging.INFO):
        calls.append((event_type, payload, level))

    monkeypatch.setattr(rpt, "emit_execution_event", _fake_emit)
    return calls


def test_config_zero_still_fires_the_original_event(monkeypatch):
    """Akzeptanzkriterium 2: bei tatsaechlich nullen Saetzen UND leerem Cache feuert weiterhin das
    Original-Event."""
    calls = _captured_events(monkeypatch)
    rpt._emit_cost_model_realism_event("config_zero", [
        _study("A", "X.ETORO", slippage=0.0, financing=0.0),
    ])
    assert len(calls) == 1
    event_type, payload, level = calls[0]
    assert event_type == "COST_MODEL_ZERO_REALISM"
    assert payload["cost_model_realism_source"] == "config_zero"
    assert level == logging.WARNING


def test_calibrated_cache_fires_the_new_event_with_measured_value(monkeypatch):
    """Akzeptanzkriterium 1: auf diesem Lauf nachgerechnet feuert COST_MODEL_ZERO_REALISM nicht
    mehr; stattdessen COST_MODEL_REALISM_FROM_CALIBRATION mit dem gemessenen Wert."""
    calls = _captured_events(monkeypatch)
    studies = [
        _study("ComboTrendVwap", "AAPL.ETORO", slippage=151.5869, financing=0.4),
        _study("ComboTrendVwap", "MSFT.ETORO", slippage=151.5869, financing=0.4),
    ]
    rpt._emit_cost_model_realism_event("calibrated_cache", studies)
    assert len(calls) == 1
    event_type, payload, level = calls[0]
    assert event_type == "COST_MODEL_REALISM_FROM_CALIBRATION"
    assert payload["cost_model_realism_source"] == "calibrated_cache"
    assert payload["applied_slippage_bps_median"] == 151.5869
    assert level == logging.INFO
    assert "COST_MODEL_ZERO_REALISM" not in [c[0] for c in calls]


def test_mixed_fires_the_new_event_not_zero_realism(monkeypatch):
    calls = _captured_events(monkeypatch)
    studies = [
        _study("Strat", "AAPL.ETORO", slippage=5.0, financing=0.4),
        _study("Strat", "TSLA.ETORO", slippage=0.0, financing=0.0),
    ]
    rpt._emit_cost_model_realism_event("mixed", studies)
    assert len(calls) == 1
    event_type, payload, level = calls[0]
    assert event_type == "COST_MODEL_REALISM_FROM_CALIBRATION"
    assert payload["cost_model_realism_source"] == "mixed"


def test_unknown_source_emits_nothing(monkeypatch):
    """Fail-open: ein unbekannter/nicht klassifizierbarer Quellwert emittiert kein Event (kein
    Raten ueber einen unbekannten Zustand)."""
    calls = _captured_events(monkeypatch)
    rpt._emit_cost_model_realism_event("bogus", [])
    assert calls == []


def test_build_report_wires_the_emission_call():
    import inspect
    source = inspect.getsource(rpt._build_report)
    assert "_emit_cost_model_realism_event(_cost_model_realism_source, studies_out)" in source
    # Reihenfolge-Kontrakt: NACH der _cost_model_realism_from_applied-Berechnung, EINE Quelle
    # fuer beide (den Report-JSON-Wert UND das Event).
    idx_classify = source.index("_cost_model_realism_from_applied(studies_out)")
    idx_emit = source.index("_emit_cost_model_realism_event(")
    assert idx_classify < idx_emit
