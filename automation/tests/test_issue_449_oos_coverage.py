"""Forensik-Runde #449–#457 — OOS-Abdeckung, Plateau-Stop & Fenster-Single-Source-of-Truth.

Gemeinsame Regressionsdatei für drei verzahnte Optimizer-Fixes:

  * **#455 (P0, Pitfall #82)** — OOS-Abdeckungs-Blindstelle: `extract_metrics`/`write_tournament_json`
    weisen `oos_window_start_ns`/`oos_covered`/`oos_coverage_gap_days` aus (WARN statt `raise`),
    `gate.data_reaches_oos_window` ist rein & fail-open, `sweep.enumerate_tunable_pairs`
    überspringt unerreichbare Symbole VOR dem Sweep.
  * **#456 (P1, Pitfall #83)** — `floor_plateau_callback(stop_on_plateau=True)` ruft `study.stop()`
    (beide Zweige, crash-sicher); Default `False` lässt Bestandstests unverändert.
  * **#457 (P2, Pitfall #84)** — `compute_walk_forward_window` ist die EINZIGE Quelle der
    Fenster-Arithmetik; `build_trial` delegiert; das Sweep-Preflight nutzt dieselbe Grenze.
"""
import datetime as dt
import json
import logging
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock

import optuna
import pandas as pd
import pytest

from automation.optimizer import gate, sweep
from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config
from automation.optimizer.trial_config import compute_walk_forward_window
from automation.optimizer.parsing import parse_tournament
from automation.backtest_runner import extract_metrics, write_tournament_json

_DAY_NS = 86400 * 1_000_000_000
START_NS = int(pd.Timestamp("2025-05-16", tz="UTC").value)
WF = {"is_window_days": 180, "oos_window_days": 45, "splits": 4}


# ===========================================================================
# #457 — compute_walk_forward_window (Single Source of Truth)
# ===========================================================================
def test_window_reproduces_known_dates():
    """now=2026-06-25 ⇒ start=2025-05-16, end=2026-05-11 (exakt die data_window-Werte des Sweep-Logs)."""
    now = dt.datetime(2026, 6, 25, 14, 30, tzinfo=dt.timezone.utc)
    start, end = compute_walk_forward_window(
        now=now, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
    assert start.date() == dt.date(2025, 5, 16)
    assert end.date() == dt.date(2026, 5, 11)


def test_window_sunday_rollback():
    """Fällt now auf einen Sonntag, rollt end VOR dem Holdout-Abzug auf Samstag zurück."""
    sunday = dt.datetime(2026, 6, 28, tzinfo=dt.timezone.utc)
    assert sunday.weekday() == 6
    _start, end = compute_walk_forward_window(
        now=sunday, holdout_days=0, is_window_days=180, oos_window_days=45, n_folds=4)
    # ohne Holdout: end == Samstag (27.) statt Sonntag (28.)
    assert end.date() == dt.date(2026, 6, 27)


def test_build_trial_uses_shared_window(tmp_path, monkeypatch):
    """build_trial delegiert an compute_walk_forward_window (keine Inline-Kopie der Arithmetik)."""
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    sentinel_start = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    sentinel_end = dt.datetime(2020, 6, 1, tzinfo=dt.timezone.utc)
    called = {}

    def _fake_window(**kwargs):
        called.update(kwargs)
        return sentinel_start, sentinel_end

    monkeypatch.setattr(trial_config, "compute_walk_forward_window", _fake_window)
    _trial_dir, manifest_path = trial_config.build_trial(
        strategy_class="SmaCrossoverStrategy", sampled={"sma_period": 10},
        study_name="ssot", trial_number=0, seed=42, n_folds=4, holdout_days=45,
        base_cfg=Path("automation/config"),
    )
    manifest = json.loads(Path(manifest_path).read_text("utf-8"))
    gs = manifest["global_settings"]
    assert called, "build_trial MUSS compute_walk_forward_window aufrufen (keine Inline-Arithmetik)"
    assert gs["start_time"] == "2020-01-01T00:00:00Z"
    assert gs["end_time"] == "2020-06-01T00:00:00Z"


# ===========================================================================
# #455 — gate.data_reaches_oos_window (rein, fail-open)
# ===========================================================================
def test_gate_oos_reachable_when_newest_reaches_boundary():
    wf_dict = {"is_window_days": 180, "oos_window_days": 30, "splits": 2, "embargo_period_days": 0, "holdout_days": 0}
    ok, reason, gap = gate.data_reaches_oos_window(START_NS + 200 * _DAY_NS, START_NS, wf_dict)
    assert ok and reason == "OK"


def test_gate_oos_unreachable_when_newest_before_boundary():
    wf_dict = {"is_window_days": 180, "oos_window_days": 30, "splits": 2, "embargo_period_days": 0, "holdout_days": 0}
    ok, reason, gap = gate.data_reaches_oos_window(START_NS + 170 * _DAY_NS, START_NS, wf_dict)
    assert not ok and reason == "OOS_WINDOW_UNREACHABLE"


def test_gate_oos_failopen_on_missing_telemetry():
    wf_dict = {"is_window_days": 180, "oos_window_days": 30, "splits": 2, "embargo_period_days": 0, "holdout_days": 0}
    assert gate.data_reaches_oos_window(None, START_NS, wf_dict) == (True, "OOS_PREFLIGHT_UNAVAILABLE", None)
    assert gate.data_reaches_oos_window(START_NS, None, wf_dict) == (True, "OOS_PREFLIGHT_UNAVAILABLE", None)
    assert gate.data_reaches_oos_window(START_NS, START_NS, None) == (True, "OOS_PREFLIGHT_UNAVAILABLE", None)


# ===========================================================================
# #455 — extract_metrics: oos_window_start_ns / oos_covered + WARN (kein raise)
# ===========================================================================
def _engine_with_fills(records):
    df = pd.DataFrame.from_records(records)
    engine = MagicMock()
    engine.trader.generate_fills_report.return_value = df
    return engine


def _round_trip(ts_exit_ns, px_in=100.0, px_out=101.0, iid="TSLA.ETORO"):
    return [
        {"instrument_id": iid, "last_qty": 1.0, "last_px": px_in,
         "order_side": "BUY", "ts_event": ts_exit_ns - 3600 * 1_000_000_000},
        {"instrument_id": iid, "last_qty": 1.0, "last_px": px_out,
         "order_side": "SELL", "ts_event": ts_exit_ns},
    ]


def test_extract_metrics_oos_covered_true_when_fills_reach_window():
    records = []
    for i in range(40):
        exit_ns = START_NS + int((i + 0.5) / 40 * 360 * _DAY_NS)  # bis +360d ⇒ erreicht OOS
        records.extend(_round_trip(exit_ns))
    out = extract_metrics(_engine_with_fills(records), starting_capital=10000.0,
                          walk_forward_dict=WF, start_ns=START_NS)
    assert out["_oos_window_start_ns"] == START_NS + 180 * _DAY_NS
    assert out["_oos_covered"] is True
    assert out["oos_metrics"]["total_trades"] > 0


def test_extract_metrics_oos_covered_false_and_warns_without_raise():
    """Stoppen die Fills bei +170d (< is_window 180d), ist OOS strukturell 0 — extract_metrics
    WARNT (kein raise, keine NULL-Rückgabe) und liefert oos_covered=False (Pitfall #82)."""
    logs = []
    records = []
    for i in range(30):
        exit_ns = START_NS + int((i + 0.5) / 30 * 170 * _DAY_NS)  # alle < +180d
        records.extend(_round_trip(exit_ns))
    out = extract_metrics(_engine_with_fills(records), starting_capital=10000.0,
                          log_fn=logs.append, walk_forward_dict=WF, start_ns=START_NS)
    assert out["_oos_covered"] is False
    assert out["oos_metrics"]["total_trades"] == 0   # strukturell, nicht parameterseitig
    assert out["metrics"]["total_trades"] == 30      # IS-Aktivität ist vorhanden
    assert any("OOS-Abdeckung" in m for m in logs), "muss sichtbar WARNen, nicht still scheitern"


# ===========================================================================
# #455 — write_tournament_json + parse_tournament Round-Trip der Coverage-Felder
# ===========================================================================
def test_write_and_parse_tournament_oos_coverage_roundtrip(tmp_path):
    oos_start = START_NS + 180 * _DAY_NS
    fill_max = START_NS + 170 * _DAY_NS  # 10 Tage vor OOS-Grenze ⇒ nicht abgedeckt
    all_results = [{
        "symbol": "TSLA.ETORO", "strategy": "SmaCrossoverStrategy",
        "metrics": {"total_trades": 172}, "oos_metrics": {"total_trades": 0},
        "_fill_ts_min": START_NS + 5 * _DAY_NS, "_fill_ts_max": fill_max,
        "_oos_window_start_ns": oos_start, "_oos_covered": False,
    }]
    out_path = tmp_path / "tournament_result.json"
    write_tournament_json(all_results, str(out_path), per_symbol_winners={},
                          aggregate_winner=None, warnings_list=[], is_eligible_count=0,
                          fully_eligible_count=0, tournament_cfg={"min_trades": 20},
                          start_ns=START_NS, end_ns=START_NS + 360 * _DAY_NS)
    data = json.loads(out_path.read_text("utf-8"))
    dw = data["data_window"]
    assert dw["oos_window_start_ns"] == oos_start
    assert dw["oos_covered"] is False
    assert dw["oos_coverage_gap_days"] == pytest.approx(10.0)

    metrics = parse_tournament(out_path)
    assert metrics.oos_window_start_ns == oos_start
    assert metrics.oos_covered is False
    assert metrics.oos_coverage_gap_days == pytest.approx(10.0)


# ===========================================================================
# #455 — enumerate_tunable_pairs Preflight (skip unreachable / fail-open)
# ===========================================================================
_GATE_CFG = {
    "walk_forward": {"is_window_days": 180, "oos_window_days": 45, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def _ample_bars(symbols):
    # großzügig über allen Gate-1-Schwellen ⇒ Gate 1 (Daten-Suffizienz) lässt alle durch.
    return {s: 1_000_000 for s in symbols}


def test_enumerate_preflight_skips_unreachable_symbol():
    syms = ["GOOD.ETORO", "STALE.ETORO"]
    start_ns = START_NS
    latest = {"GOOD.ETORO": START_NS + 200 * _DAY_NS, "STALE.ETORO": START_NS + 100 * _DAY_NS}
    pairs = sweep.enumerate_tunable_pairs(
        ["SmaCrossoverStrategy"], syms, tier="all", available_bars=_ample_bars(syms),
        config=_GATE_CFG, latest_ts=latest, start_ns=start_ns)
    out_syms = {sym for _s, sym, _r in pairs}
    assert "GOOD.ETORO" in out_syms
    assert "STALE.ETORO" not in out_syms, "unerreichbares Symbol muss übersprungen werden (#455)"


def test_enumerate_preflight_failopen_without_telemetry():
    """Ohne latest_ts/oos_window_start_ns bleibt das Preflight aus ⇒ bit-identisch (beide behalten)."""
    syms = ["GOOD.ETORO", "STALE.ETORO"]
    pairs = sweep.enumerate_tunable_pairs(
        ["SmaCrossoverStrategy"], syms, tier="all", available_bars=_ample_bars(syms),
        config=_GATE_CFG)  # latest_ts=None, oos_window_start_ns=None
    out_syms = {sym for _s, sym, _r in pairs}
    assert out_syms == {"GOOD.ETORO", "STALE.ETORO"}


def test_compute_oos_window_start_ns_matches():
    now = dt.datetime(2026, 6, 25, tzinfo=dt.timezone.utc)
    start_ns = sweep.compute_oos_window_start_ns(_GATE_CFG, now=now)
    start, _end = compute_walk_forward_window(
        now=now, holdout_days=45, is_window_days=180, oos_window_days=45, n_folds=4)
    expected = int((start).timestamp()) * 1_000_000_000
    assert start_ns == expected


# ===========================================================================
# #456 — floor_plateau_callback(stop_on_plateau)
# ===========================================================================
W = {"penalty_unevaluable_oos": -10.0, "unevaluable_shaping_span": 0.25, "n_startup_trials": 16}


class _FakeTrial:
    def __init__(self, value, oos_evaluated=None, state=optuna.trial.TrialState.COMPLETE):
        self.value = value
        self.state = state
        self.user_attrs = {} if oos_evaluated is None else {"oos_evaluated": oos_evaluated}


class _FakeStudy:
    def __init__(self, trials, stop_raises=False):
        self.trials = trials
        self._attrs = {}
        self.stop_calls = 0
        self._stop_raises = stop_raises

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v

    def stop(self):
        self.stop_calls += 1
        if self._stop_raises:
            raise RuntimeError("stop() außerhalb optimize()-Kontext")


def _silent_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    return lg


def test_plateau_stop_optin_calls_stop_evaluable_branch():
    lg = _silent_logger("t456a")
    trials = [_FakeTrial(-9.85, oos_evaluated=False) for _ in range(3)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3,
                              logger=lg, stop_on_plateau=True)
    assert study.stop_calls == 1


def test_plateau_stop_optin_calls_stop_legacy_branch():
    lg = _silent_logger("t456b")
    floor = W["penalty_unevaluable_oos"] + W["unevaluable_shaping_span"]  # -9.75, kein oos_evaluated-Attr
    trials = [_FakeTrial(floor) for _ in range(3)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3,
                              logger=lg, stop_on_plateau=True)
    assert study.stop_calls == 1


def test_plateau_default_does_not_stop():
    lg = _silent_logger("t456c")
    trials = [_FakeTrial(-9.85, oos_evaluated=False) for _ in range(3)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3, logger=lg)
    assert study.stop_calls == 0   # Default False ⇒ Bestandstests unverändert


def test_plateau_no_stop_when_some_evaluable():
    lg = _silent_logger("t456d")
    trials = [_FakeTrial(-9.85, oos_evaluated=False),
              _FakeTrial(0.5, oos_evaluated=True),
              _FakeTrial(-9.9, oos_evaluated=False)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3,
                              logger=lg, stop_on_plateau=True)
    assert study.stop_calls == 0


def test_plateau_stop_is_crash_safe():
    """study.stop() darf werfen (Study außerhalb optimize()) ⇒ kein Crash, Warnung genügt."""
    lg = _silent_logger("t456e")
    trials = [_FakeTrial(-9.85, oos_evaluated=False) for _ in range(3)]
    study = _FakeStudy(trials, stop_raises=True)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=3,
                              logger=lg, stop_on_plateau=True)  # darf NICHT werfen
    assert study.stop_calls == 1


def test_production_optimize_symbol_binds_stop_on_plateau(tmp_path, monkeypatch):
    """Die Produktions-`partial`-Bindung in optimize_symbol setzt stop_on_plateau=True (Akzeptanz #456).
    Verifiziert via Spy auf floor_plateau_callback, das die effektiven Callback-Kwargs einfängt."""
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))

    captured = {}

    def _spy(study, trial, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(ro, "floor_plateau_callback", _spy)

    def _fake(trial_dir, manifest_path, **_kwargs):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 0, "aggregate_winner": None,
                                   "full_results": [{"metrics": {"total_trades": 50}}]}), "utf-8")
        return out

    monkeypatch.setattr(ro, "run_backtest", _fake)
    ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    assert captured.get("stop_on_plateau") is True
