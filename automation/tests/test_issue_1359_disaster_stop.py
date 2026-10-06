"""Issue #1359 (GH #1255, P0) — jede Entry-Order trägt einen Broker-Stop.

Akzeptanzkriterien:
- Dry-Run-Payload-Test für jede der 14 aktiven Strategien: ``IsNoStopLoss == False``,
  ``StopLossRate`` auf der korrekten Seite, Abstand in [2 %, 15 %];
- Backtest-Test: Gap über das Katastrophen-Niveau ⇒ Exit ``DISASTER_STOP`` zum ersten Preis jenseits
  des Niveaus (echte ``BacktestEngine``);
- AST-Test: jeder Entry-``order_factory.market(``-Aufruf in ``automation/strategies/*.py`` übergibt
  ``tags=self._entry_order_tags(...)`` (vor dem Fix rot für 13 aktive Strategien).
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from automation import disaster_stop as ds
from automation.adapters.etoro_execution import EToroExecutionClient

REPO = Path(__file__).resolve().parents[2]
STRATEGIES_DIR = REPO / "automation" / "strategies"
_STRATEGIES_CFG = json.loads((REPO / "automation/config/strategies.json").read_text("utf-8"))
ACTIVE = [s for s in _STRATEGIES_CFG["strategies"] if s.get("active", True) is not False]
_DEFAULTS = json.loads((REPO / "automation/config/strategy_defaults.json").read_text("utf-8"))


# ─── Arithmetik / Konfiguration ─────────────────────────────────────────────────

def test_pct_is_clamped_between_min_and_max_and_scales_with_k():
    assert ds.compute_disaster_stop_pct(150.0) == pytest.approx(0.045)        # 3 · 150 bps
    assert ds.compute_disaster_stop_pct(10.0) == 0.02                         # untere Klemme
    assert ds.compute_disaster_stop_pct(900.0) == 0.15                        # obere Klemme
    assert ds.compute_disaster_stop_pct(100.0, k_disaster=4.0) == pytest.approx(0.04)
    assert ds.compute_disaster_stop_pct(float("nan")) == 0.15                 # degeneriert ⇒ weit
    assert ds.format_sl_tag(0.045) == "SL:0.0450"
    assert ds.parse_sl_pct_from_tags(["X:1", "SL:0.0450"]) == pytest.approx(0.045)
    assert ds.parse_sl_pct_from_tags(["SL:0"]) is None
    assert ds.parse_sl_pct_from_tags(MagicMock()) is None


def test_shipped_config_block_defines_the_documented_defaults():
    assert _DEFAULTS["_disaster_stop"] == {
        "k_disaster": 3.0, "disaster_stop_min_pct": 0.02, "disaster_stop_max_pct": 0.15}
    assert (ds.K_DISASTER_DEFAULT, ds.DISASTER_STOP_MIN_PCT_DEFAULT,
            ds.DISASTER_STOP_MAX_PCT_DEFAULT) == (3.0, 0.02, 0.15)


@pytest.mark.parametrize("bad", [
    None, {}, {"k_disaster": 3.0},
    {"k_disaster": 0, "disaster_stop_min_pct": 0.02, "disaster_stop_max_pct": 0.15},
    {"k_disaster": 3, "disaster_stop_min_pct": 0.2, "disaster_stop_max_pct": 0.1},
    {"k_disaster": 3, "disaster_stop_min_pct": 0.02, "disaster_stop_max_pct": 1.5},
])
def test_invalid_block_is_rejected_fail_loud(bad):
    with pytest.raises(ValueError):
        ds.parse_disaster_stop_params(bad)


def test_disaster_params_are_not_in_any_strategy_block_nor_the_search_space():
    for name, block in _DEFAULTS.items():
        if name.startswith("_"):
            continue
        assert not {"k_disaster", "disaster_stop_min_pct", "disaster_stop_max_pct"} & set(block)
    from automation.optimizer import spaces
    src = Path(spaces.__file__).read_text("utf-8")
    assert "k_disaster" not in src and "disaster_stop" not in src


# ─── AST-Test: jeder Entry-market()-Aufruf trägt den Katastrophen-Stop-Tag ────────

def _strategy_modules() -> list[Path]:
    return sorted(p for p in STRATEGIES_DIR.glob("*.py")
                  if p.name not in ("__init__.py", "hourly_strategy_base.py"))


def test_every_entry_market_order_passes_entry_order_tags():
    offenders: list[str] = []
    n_calls = 0
    for path in _strategy_modules():
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "market"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "order_factory"):
                n_calls += 1
                tags = next((k.value for k in node.keywords if k.arg == "tags"), None)
                ok = (isinstance(tags, ast.Call) and isinstance(tags.func, ast.Attribute)
                      and tags.func.attr == "_entry_order_tags"
                      and isinstance(tags.func.value, ast.Name) and tags.func.value.id == "self")
                if not ok:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert n_calls >= 2 * len(ACTIVE)          # mindestens Buy+Sell je aktiver Strategie
    assert offenders == []


def test_no_fixed_sl_literal_remains_in_the_strategies():
    for path in _strategy_modules():
        assert '"SL:' not in path.read_text("utf-8"), path.name


# ─── Dry-Run-Payload je aktiver Strategie ──────────────────────────────────────────

def _make_strategy(entry: dict):
    mod = importlib.import_module(entry["strategy_module"])
    cfg_cls = getattr(mod, entry["config_class"])
    strat_cls = getattr(mod, entry["strategy_class"])
    params = {**_DEFAULTS.get(entry["strategy_class"], {}), **entry.get("params", {})}
    params.pop("trade_amount_usd", None)
    cfg = cfg_cls(instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL", **params)
    return strat_cls(config=cfg)


def _bar(close: float):
    return SimpleNamespace(close=close, high=close, low=close, open=close)


def _fake_client(quote_price: float):
    quote = SimpleNamespace(ask_price=quote_price, bid_price=quote_price)
    instrument = SimpleNamespace(price_precision=4)
    cache = SimpleNamespace(quote_tick=lambda _id: quote, instrument=lambda _id: instrument)
    return SimpleNamespace(_cache=cache, _rest_base="https://x", _enable_trailing_stop=False,
                           _log=MagicMock())


def _payload(strategy, side, price):
    from automation.adapters.etoro_execution import OrderSide   # dasselbe Objekt wie im Adapter
    tags = strategy._entry_order_tags(_bar(price))
    order = SimpleNamespace(
        tags=tags, side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
        instrument_id="TSLA.ETORO", quantity=1.0)
    payload, _url = EToroExecutionClient._build_market_open_payload(_fake_client(price), order, 1)
    return payload, tags


def test_there_are_14_active_strategies():
    assert len(ACTIVE) == 14


@pytest.mark.parametrize("entry", ACTIVE, ids=[e["strategy_class"] for e in ACTIVE])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_dry_run_payload_carries_a_broker_stop_on_the_correct_side(entry, side):
    strategy = _make_strategy(entry)
    price = 250.0
    payload, tags = _payload(strategy, side, price)
    assert [t for t in tags if t.startswith("SL:")] and len(tags) == 1
    assert payload["IsNoStopLoss"] is False
    rate = payload["StopLossRate"]
    distance = abs(rate - price) / price
    assert 0.02 - 1e-6 <= distance <= 0.15 + 1e-6
    assert (rate < price) if side == "BUY" else (rate > price)


def test_stop_distance_follows_the_effective_trailing_distance():
    entry = next(e for e in ACTIVE if e["strategy_class"] == "SmaCrossoverStrategy")
    strategy = _make_strategy(entry)
    strategy._exit_atr = SimpleNamespace(initialized=True, value=1.0)     # ATR 1,00 bei Preis 100
    strategy._atr_trailing_multiplier = 1.5
    # Distanz 1,5 · 1,00 = 150 bps ⇒ k=3 ⇒ 4,5 %
    assert strategy._entry_order_tags(_bar(100.0)) == ["SL:0.0450"]
    strategy._exit_atr = SimpleNamespace(initialized=True, value=5.0)     # 750 bps · 3 = 22,5 % ⇒ 15 %
    assert strategy._entry_order_tags(_bar(100.0)) == ["SL:0.1500"]
    strategy._exit_atr = SimpleNamespace(initialized=False, value=0.0)    # Warmup ⇒ ATR-Floor ⇒ 2 %
    assert strategy._entry_order_tags(_bar(100.0)) == ["SL:0.0200"]


def test_all_active_config_classes_expose_the_disaster_stop_fields():
    for entry in ACTIVE:
        mod = importlib.import_module(entry["strategy_module"])
        fields = set(getattr(mod, entry["config_class"]).__struct_fields__)
        assert {"k_disaster", "disaster_stop_min_pct", "disaster_stop_max_pct",
                "disaster_stop_mode"} <= fields, entry["strategy_class"]


def test_disaster_stop_cannot_be_disabled():
    entry = ACTIVE[0]
    mod = importlib.import_module(entry["strategy_module"])
    cfg_cls = getattr(mod, entry["config_class"])
    cfg = cfg_cls(instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-MID-INTERNAL",
                  disaster_stop_mode="off")
    with pytest.raises(ValueError):
        getattr(mod, entry["strategy_class"])(config=cfg)


# ─── Adapter: fail-closed, wenn der Stop nicht darstellbar ist ──────────────────────

def test_adapter_flags_an_entry_whose_requested_stop_cannot_be_placed():
    from automation.adapters.etoro_execution import OrderSide
    order = SimpleNamespace(tags=["SL:0.0450"], side=OrderSide.BUY, instrument_id="TSLA.ETORO",
                            quantity=1.0)
    no_quote = SimpleNamespace(
        _cache=SimpleNamespace(quote_tick=lambda _id: None, instrument=lambda _id: None),
        _rest_base="https://x", _enable_trailing_stop=False, _log=MagicMock())
    payload, _ = EToroExecutionClient._build_market_open_payload(no_quote, order, 1)
    assert payload["IsNoStopLoss"] is True
    assert EToroExecutionClient._entry_stop_requested_but_missing(order, payload) is True

    ok_payload, _ = EToroExecutionClient._build_market_open_payload(_fake_client(100.0), order, 1)
    assert EToroExecutionClient._entry_stop_requested_but_missing(order, ok_payload) is False
    untagged = SimpleNamespace(tags=None, side=OrderSide.BUY, instrument_id="TSLA.ETORO", quantity=1.0)
    p2, _ = EToroExecutionClient._build_market_open_payload(no_quote, untagged, 1)
    assert EToroExecutionClient._entry_stop_requested_but_missing(untagged, p2) is False


# ─── Invariante check_disaster_stop_non_binding ─────────────────────────────────────

def test_non_binding_invariant_passes_below_and_fails_above_one_percent():
    from automation.optimizer import invariants as inv
    ok = [{"strategy": "A", "symbol": "X", "exit_reason_histogram": {"TRAILING_STOP": 99, "DISASTER_STOP": 1}}]
    assert inv.check_disaster_stop_non_binding(ok).passed is True
    bad = [{"strategy": "A", "symbol": "X", "exit_reason_histogram": {"TRAILING_STOP": 90, "DISASTER_STOP": 10}}]
    r = inv.check_disaster_stop_non_binding(bad)
    assert r.passed is False and r.severity == "high" and "A/X" in r.actual
    empty = inv.check_disaster_stop_non_binding([{"strategy": "A", "symbol": "X"}])
    assert empty.passed is True and empty.inconclusive is True


# ─── Echte BacktestEngine: Gap über das Katastrophen-Niveau ─────────────────────────
# Die Szenarien laufen in einem eigenen Prozess (``_disaster_stop_engine_harness``): ältere Testmodule
# installieren unvollständige ``nautilus_trader``-Mocks in ``sys.modules``, die eine echte Engine im
# Suite-Prozess stören (siehe conftest.py) — der Subprozess ist davon unabhängig.

def _run_gap_backtest(*, steps: list[float]) -> dict:
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "result.json"
        proc = subprocess.run(
            [sys.executable, "-m", "automation.tests._disaster_stop_engine_harness",
             json.dumps({"steps": steps}), str(out)],
            cwd=str(REPO), capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr[-2000:]
        return json.loads(out.read_text("utf-8"))


def test_gap_beyond_the_disaster_level_exits_at_the_first_price_beyond_it():
    r = _run_gap_backtest(steps=[90.0])
    assert r["positions_open"] == 0
    assert len(r["stops"]) == 1
    stop = r["stops"][0]
    assert stop["reduce_only"] is True
    assert stop["exit_reason_meta"] == "DISASTER_STOP"
    # Entry @ Ask 100,01, ATR im Warmup ⇒ Floor ⇒ Distanz 50·2 bps = 100 bps ⇒ ·3 = 3 % ⇒ Niveau 97,01.
    assert stop["trigger_price"] == pytest.approx(97.01, abs=0.011)
    # Gap: der erste Preis jenseits des Niveaus ist der Gap-Tick (Bid 89,99), NICHT das Niveau 97,01.
    assert stop["fill_px"] == pytest.approx(89.99, abs=0.011)
    assert stop["fill_px"] < stop["trigger_price"]


def test_slow_drift_exits_at_the_first_tick_through_the_level():
    r = _run_gap_backtest(steps=[99.0, 98.0, 97.5, 97.0, 96.0, 95.0])
    assert len(r["stops"]) == 1 and r["positions_open"] == 0
    assert r["stops"][0]["fill_px"] == pytest.approx(96.99, abs=0.011)   # Bid des ersten Ticks <= 97,01


def test_without_breakthrough_exactly_one_resting_stop_remains():
    r = _run_gap_backtest(steps=[100.0])
    assert len(r["stops"]) == 1
    assert r["positions_open"] == 1
    assert r["stops"][0]["is_open"] is True
