"""Issue #531 (P1, bug/data/walk-forward) — Datenfenster < Walk-Forward-Geometrie.

Deckt die vier Phasen des Fixes ab:
  * Phase 1 — Strict Walk-Forward Geometry Check (``gate.required_span_days`` /
    ``gate.assert_walk_forward_geometry`` + Fail-Loud-Guard in ``trial_config.build_trial``).
  * Phase 2 — Pre-Sweep-Backfill-Trigger (``historical_fetcher.ensure_walkforward_history``).
  * Phase 3 — ``GATE_1_REJECTION``-Telemetry (``log_manager.emit_gate1_rejection``) mit
    ``available_days``/``required_days``/``delta_days``.
  * Verdrahtung — ``sweep.enumerate_tunable_pairs`` macht INSUFFICIENT_HISTORY als
    strukturiertes Event sichtbar, statt still zu klemmen.

Invarianten: required = ``is_window + splits*oos + holdout`` (= 405 bei 180/45/4/45); der Fail-Loud
ersetzt jede stille ``.loc``-Klemmung (No-Clamping-Policy).
"""
import datetime as dt
import json
import logging

import pytest

from automation.optimizer.gate import (
    required_span_days, assert_walk_forward_geometry, InsufficientGeometryError,
)
from automation.log_manager import emit_gate1_rejection
from automation.historical_fetcher import ensure_walkforward_history
from automation.optimizer import sweep
from automation.optimizer.trial_config import build_trial, freeze_study_config, resolve_wf_settings, config_dir

UTC = dt.timezone.utc

# Die verifizierte Produktions-Geometrie aus backtest.json (180 + 4·45 + 45 = 405).
WF = {"is_window_days": 180, "oos_window_days": 45, "splits": 4, "holdout_days": 45}
REQUIRED = 405


# ── Phase 1: gate — reine Geometrie ──────────────────────────────────────────────

def test_required_span_days_is_is_plus_folds_oos_plus_holdout():
    # Bewusst OHNE embargo/gate1_buffer — das ist der Fail-Loud-Floor, nicht die Backfill-Schwelle.
    assert required_span_days(WF) == REQUIRED
    assert required_span_days({}) == 0  # None-/leer-sicher


def test_assert_geometry_raises_below_required():
    with pytest.raises(InsufficientGeometryError) as ei:
        assert_walk_forward_geometry(actual_span_days=360, walk_forward_dict=WF, symbol="TSLA.ETORO")
    err = ei.value
    assert err.code == "REJECT_DATA_INSUFFICIENT_GEOMETRY"
    assert err.actual == 360.0 and err.required == 405.0 and err.delta == 45.0
    assert err.symbol == "TSLA.ETORO"
    assert "No-Clamping" in str(err)


@pytest.mark.parametrize("span", [405, 405.0, 450, 1000])
def test_assert_geometry_passes_at_or_above_required(span):
    # Gibt required zurück und wirft nicht, sobald die Spanne die Geometrie erreicht.
    assert assert_walk_forward_geometry(actual_span_days=span, walk_forward_dict=WF) == float(REQUIRED)


def test_error_taxonomy_delta_is_required_minus_actual():
    err = InsufficientGeometryError(actual=300.4, required=405.0)
    assert err.code == "REJECT_DATA_INSUFFICIENT_GEOMETRY"
    assert err.delta == pytest.approx(104.6)


# ── Phase 3: log_manager — GATE_1_REJECTION-Telemetry ───────────────────────────

def _capture(logger_name: str) -> tuple[logging.Logger, list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class _Cap(logging.Handler):
        def emit(self, record):  # noqa: D401
            records.append(record)

    log = logging.getLogger(logger_name)
    log.setLevel(logging.DEBUG)
    log.addHandler(_Cap())
    log.propagate = False
    return log, records


def _json_events(records) -> list[dict]:
    out = []
    for r in records:
        msg = r.getMessage()
        if "[JSON_EVENT]" in msg:
            out.append(json.loads(msg.split("[JSON_EVENT] ", 1)[1]))
    return out


def test_emit_gate1_rejection_payload_shape():
    log, records = _capture("test531.telemetry")
    emit_gate1_rejection(log, available_days=360.0, required_days=405.0, symbol="TSLA.ETORO")
    events = _json_events(records)
    assert len(events) == 1
    ev = events[0]
    # Mandatory Payload (Issue #531 / AGENTS.md §14).
    assert ev["event"] == "GATE_1_REJECTION"
    assert ev["error_category"] == "REJECT_DATA_INSUFFICIENT_GEOMETRY"
    assert ev["available_days"] == 360.0
    assert ev["required_days"] == 405.0
    assert ev["delta_days"] == 45.0


# ── Phase 2: historical_fetcher — Pre-Sweep-Backfill ─────────────────────────────

def test_ensure_history_triggers_backfill_below_threshold():
    seen = {}

    def fake_fetch(symbols, **kw):
        seen["symbols"] = list(symbols)
        seen["kw"] = kw
        return list(symbols)

    # 360 < 435 (=405+30) ⇒ deficient; 500 ⇒ ausreichend.
    report = ensure_walkforward_history(
        ["TSLA.ETORO", "AAPL.ETORO"], WF,
        span_days_by_symbol={"TSLA.ETORO": 360.0, "AAPL.ETORO": 500.0},
        gate1_buffer_days=30, fetch_fn=fake_fetch,
    )
    assert report["required_days"] == 405
    assert report["threshold_days"] == 435
    assert report["deficient"] == ["TSLA.ETORO"]
    assert report["backfilled"] == ["TSLA.ETORO"]
    assert seen["symbols"] == ["TSLA.ETORO"]
    assert seen["kw"]["required_days"] == 405 and seen["kw"]["buffer_days"] == 30


def test_ensure_history_noop_when_all_sufficient():
    calls = []
    report = ensure_walkforward_history(
        ["AAPL.ETORO"], WF, span_days_by_symbol={"AAPL.ETORO": 500.0},
        gate1_buffer_days=30, fetch_fn=lambda *a, **k: calls.append(a) or [],
    )
    assert report["deficient"] == [] and report["backfilled"] == []
    assert calls == []  # kein Backfill wenn die Historie reicht


def test_ensure_history_failopen_when_backfill_raises():
    def boom(*a, **k):
        raise RuntimeError("network down")

    # Fail-open: der Backfill-Fehler darf den Sweep NIE crashen.
    report = ensure_walkforward_history(
        ["TSLA.ETORO"], WF, span_days_by_symbol={"TSLA.ETORO": 100.0},
        gate1_buffer_days=30, fetch_fn=boom,
    )
    assert report["deficient"] == ["TSLA.ETORO"]
    assert report["backfilled"] == []  # nichts nachgeladen, aber kein Crash


# ── Verdrahtung: sweep — Diskrepanz sichtbar ────────────────────────────────────

_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def test_enumerate_emits_gate1_rejection_on_insufficient_history(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    log, records = _capture("test531.enumerate")
    bars = {"A.ETORO": 10_000, "C.ETORO": 50}  # C hat viel zu wenig Historie

    pairs = sweep.enumerate_tunable_pairs(
        ["SmaCrossoverStrategy"], ["A.ETORO", "C.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG, logger=log,
    )
    # Auswahl unverändert: nur A bleibt tunbar.
    assert pairs == [("SmaCrossoverStrategy", "A.ETORO", "OK")]

    events = [e for e in _json_events(records) if e.get("symbol") == "C.ETORO"]
    assert len(events) == 1, "GATE_1_REJECTION für das verworfene Symbol fehlt"
    ev = events[0]
    assert ev["event"] == "GATE_1_REJECTION"
    assert ev["error_category"] == "REJECT_DATA_INSUFFICIENT_GEOMETRY"
    # required = reine Geometrie 120 + 4·30 + 45 = 285; available = 50 bars / 24.
    assert ev["required_days"] == 285.0
    assert ev["available_days"] == pytest.approx(50 / 24.0, abs=0.01)


# ── Phase 1: trial_config.build_trial — Fail-Loud gegen reale Spanne ─────────────

def test_build_trial_fail_loud_on_insufficient_catalog_span():
    now = dt.datetime(2026, 5, 18, tzinfo=UTC)
    # Default-Geometrie fordert 405 Tage; nur 360 real vorhanden ⇒ deterministischer Abbruch.
    with pytest.raises(InsufficientGeometryError) as ei:
        build_trial(
            "ComboTrendVwapStrategy", {}, study_name="issue531_fail",
            trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4,
            instruments=["TSLA.ETORO"], copy_config=False, catalog_span_days=360.0,
        )
    assert ei.value.code == "REJECT_DATA_INSUFFICIENT_GEOMETRY"
    assert ei.value.required == 405.0 and ei.value.actual == 360.0


def test_build_trial_noop_without_catalog_span(tmp_path):
    now = dt.datetime(2026, 5, 18, tzinfo=UTC)
    # Issue #796 — copy_config=False verlangt eine per freeze_study_config eingefrorene Config.
    wf_settings = resolve_wf_settings(config_dir(), holdout_days=45, n_folds=4)
    study_cfg_dir = freeze_study_config("issue531_noop", wf_settings)
    # Ohne catalog_span_days bleibt das Verhalten bit-identisch (rückwärtskompatibel).
    trial_dir, manifest_path = build_trial(
        "ComboTrendVwapStrategy", {}, study_name="issue531_noop",
        trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4,
        instruments=["TSLA.ETORO"], copy_config=False, study_config_dir=study_cfg_dir,
    )
    assert manifest_path.exists()


def test_build_trial_passes_with_sufficient_catalog_span():
    now = dt.datetime(2026, 5, 18, tzinfo=UTC)
    # Issue #796 — copy_config=False verlangt eine per freeze_study_config eingefrorene Config.
    wf_settings = resolve_wf_settings(config_dir(), holdout_days=45, n_folds=4)
    study_cfg_dir = freeze_study_config("issue531_ok", wf_settings)
    # 450 ≥ 405 ⇒ die Geometrie passt, kein Reject.
    trial_dir, manifest_path = build_trial(
        "ComboTrendVwapStrategy", {}, study_name="issue531_ok",
        trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4,
        instruments=["TSLA.ETORO"], copy_config=False, catalog_span_days=450.0,
        study_config_dir=study_cfg_dir,
    )
    assert manifest_path.exists()
