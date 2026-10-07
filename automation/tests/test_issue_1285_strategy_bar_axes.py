"""Issue #1383 (GH #1285) — ``bar_axes`` je Strategie, Achsen-Bounds (``axis_bounds.json``), Sweep-Skip mit Event."""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from automation import bar_axis
from automation.optimizer import bounds, champions, config_profile, spaces, sweep
from automation.optimizer.sweep_diagnostics import load_strategy_bar_axes

_REPO = Path(__file__).resolve().parents[2]
_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_session_bars_per_param": 40, "min_oos_session_bars_per_fold": 91,
}
_ONEDAY_OK = {"Rsi2ReversionStrategy", "DonchianRegimeBreakoutStrategy", "SmaCrossoverStrategy",
              "MeanReversionStrategy", "DynamicBreakoutStrategy", "SqueezeBreakoutStrategy",
              "TrendPullbackStrategy", "AdxAtrMomentumStrategy"}
_HOURLY_ONLY = {"OpeningRangeBreakoutStrategy", "VwapExhaustionStrategy", "ComboTrendVwapStrategy",
                "HourlyMeanReversionStrategy", "FlashCrashReversalStrategy", "VolatilityBreakoutPumpStrategy"}


def _oneday(monkeypatch):
    monkeypatch.setattr(bar_axis, "active_axis_name", lambda cfg_dir=None: "OneDay")
    monkeypatch.setattr(bar_axis, "active_axis", lambda cfg_dir=None: bar_axis.AXES["OneDay"])


def test_first_admission_follows_the_proposal():
    axes = load_strategy_bar_axes()
    assert {s for s, a in axes.items() if "OneDay" in a} == _ONEDAY_OK
    assert {s for s, a in axes.items() if a == frozenset({"OneHour"})} >= _HOURLY_ONLY
    assert all("OneHour" in a for a in axes.values())


def test_missing_key_means_hourly_only_and_missing_file_means_no_restriction(tmp_path):
    (tmp_path / "strategies.json").write_text(json.dumps(
        {"strategies": [{"strategy_class": "A"}, {"strategy_class": "B", "bar_axes": ["OneDay"]}]}), "utf-8")
    assert load_strategy_bar_axes(tmp_path) == {"A": frozenset({"OneHour"}), "B": frozenset({"OneDay"})}
    assert load_strategy_bar_axes(tmp_path / "nope") == {}


def test_daily_axis_enumerates_only_matching_strategies_and_reports_the_rest(monkeypatch):
    _oneday(monkeypatch)
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    events = []
    monkeypatch.setattr(sweep, "emit_execution_event", lambda lg, t, p, level=logging.INFO: events.append((t, p)))
    pairs = sweep.enumerate_tunable_pairs(
        ["SmaCrossoverStrategy", "OpeningRangeBreakoutStrategy", "VwapExhaustionStrategy"], ["A.ETORO"],
        tier="all", available_bars={"A.ETORO": 10_000}, config=_GATE_CFG)
    assert pairs == [("SmaCrossoverStrategy", "A.ETORO", "OK")]
    skipped = {p["strategy"]: p for t, p in events if t == "STRATEGY_AXIS_SKIPPED"}
    assert set(skipped) == {"OpeningRangeBreakoutStrategy", "VwapExhaustionStrategy"}
    assert skipped["VwapExhaustionStrategy"]["bar_axis"] == "OneDay"
    assert skipped["VwapExhaustionStrategy"]["reason"] == "SKIPPED_AXIS_NOT_SUPPORTED"


def test_hourly_axis_enumerates_every_strategy_unchanged(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    names = sorted(_ONEDAY_OK | _HOURLY_ONLY)
    pairs = sweep.enumerate_tunable_pairs(names, ["A.ETORO"], tier="all", available_bars={"A.ETORO": 10_000},
                                          config=_GATE_CFG)
    assert {s for s, _, _ in pairs} == set(names)


# ─── Achsen-Bounds ────────────────────────────────────────────────────────────────────

def _all_strategies() -> list[str]:
    import re
    return sorted(set(re.findall(r'strategy == "(\w+Strategy)"', (_REPO / "automation/optimizer/spaces.py").read_text("utf-8"))))


def test_hourly_bounds_are_bit_identical_to_the_pre_change_tables():
    """Akzeptanzkriterium 3 — Referenz: die Stunden-Bounds vor #1285 (``git show origin/main`` zum Zeitpunkt der
    Umsetzung), als Literal eingefroren."""
    ref = json.loads((Path(__file__).parent / "fixtures" / "hourly_bounds_1285.json").read_text("utf-8"))
    now = {s: {k: list(v) for k, v in bounds.extract_numeric_bounds(s).items()} for s in _all_strategies()}
    assert now == ref


def test_daily_bounds_come_from_the_table_and_respect_the_lookback_cap(monkeypatch):
    _oneday(monkeypatch)
    table = json.loads((_REPO / "automation/config/axis_bounds.json").read_text("utf-8"))
    cap = table["_schema"]["max_lookback_bars"]
    for strategy in _ONEDAY_OK:
        b = bounds.extract_numeric_bounds(strategy)
        for param, (lo, hi) in table["axis_bounds"][strategy]["OneDay"].items():
            assert tuple(b[param]) == (lo, hi), (strategy, param)
            assert spaces.is_bounds_admissible(param, lo, hi)
            if param.endswith("_period"):
                assert hi <= cap
        assert b["max_bars_in_trade"][1] <= spaces._MAX_BARS_IN_TRADE_CAP
    # embargo_period_days >= groesster Lookback in Kalendertagen (21 Handelstage ≈ 29,4 Kalendertage)
    prof = json.loads((_REPO / "automation/config/config_profiles.json").read_text("utf-8"))["daily"]
    assert prof["backtest.json"]["walk_forward"]["embargo_period_days"] >= math.ceil(cap * 7 / 5)


def test_daily_sampling_never_leaves_the_axis_bounds(monkeypatch):
    import optuna
    optuna.logging.set_verbosity(optuna.logging.ERROR)
    _oneday(monkeypatch)
    for strategy in sorted(_ONEDAY_OK):
        b = bounds.extract_numeric_bounds(strategy)
        study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
        for _ in range(25):
            params = spaces.sample_params(strategy, study.ask())
            for k, (lo, hi) in b.items():
                if k in params:
                    assert lo <= params[k] <= hi, (strategy, k, params[k], (lo, hi))


def test_curated_symbol_overrides_do_not_apply_on_the_daily_axis(monkeypatch):
    base = spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "ema_period", 50, 300)
    _oneday(monkeypatch)
    assert spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "ema_period", 50, 300) == (8, 21)
    assert spaces._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "unknown_param", 1, 2) == (1, 2)
    assert base != (8, 21)


def test_axis_bounds_outside_the_domain_registry_fail_loud(tmp_path, monkeypatch):
    (tmp_path / "axis_bounds.json").write_text(json.dumps(
        {"axis_bounds": {"SmaCrossoverStrategy": {"OneDay": {"sma_period": [-5, 21]}}}}), "utf-8")
    monkeypatch.setenv("ETORO_CONFIG_DIR", str(tmp_path))
    with pytest.raises(spaces.AxisBoundsConfigError):
        spaces._load_axis_bounds()


def test_params_schema_signature_is_hourly_identical_and_carries_the_axis_otherwise(monkeypatch):
    sig_hour = champions._params_schema_version("SmaCrossoverStrategy")
    assert "|" not in sig_hour
    _oneday(monkeypatch)
    assert champions._params_schema_version("SmaCrossoverStrategy") == sig_hour + "|bar_axis=OneDay"


def test_daily_profile_end_to_end_in_a_fresh_process(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(_REPO / "automation" / "config", root / "automation" / "config")
    overlay = config_profile.materialize("daily", project_root=root)
    code = ("import json\nfrom automation.optimizer import bounds\n"
            "print(json.dumps(bounds.extract_numeric_bounds('Rsi2ReversionStrategy')['ema_period']))")
    res = subprocess.run([sys.executable, "-c", code], cwd=_REPO, capture_output=True, text=True,
                         env={**os.environ, "ETORO_CONFIG_DIR": str(overlay)})
    assert json.loads(res.stdout) == [10, 21], res.stderr[-400:]
