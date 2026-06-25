"""Issue #446 (Pitfall #81) — Sampling↔Config-Bindungs-Assertion.

Grundwahrheit ist die Menge der `*Config`-Felder (msgspec `__struct_fields__`, inkl. der vererbten
`HourlyStrategyConfig`/`StrategyConfig`-Felder) — exakt die `valid_keys`, gegen die der Backtest-
Worker filtert (`run_single_backtest_worker`: `dropped = {k for k in params if k not in valid_keys}`).
Jeder gesampelte Parameter, der dort nicht existiert, wird vom Worker STILL verworfen
(`_dropped_params`) ⇒ Optuna tunt einen Parameter, der das Backtest-Ergebnis nie beeinflusst.

Dieser Test fängt jeden künftigen Drift dieser Klasse fail-fast ab (er hätte alle in #446
behobenen Defekte — `bb_std`, `vwap_window`, `vol_surge_multiplier`, `vol_window`, `vol_threshold`,
`rsi_period`/`rsi_extreme` in VwapExhaustion, `trend_tolerance_pct` — sofort erkannt).
"""
import importlib
import json
from pathlib import Path

import pytest

from automation.optimizer.spaces import sample_params


class _FakeTrial:
    """Minimaler Optuna-Trial-Ersatz: liefert für jeden Vorschlag einen gültigen Wert (die untere
    Grenze bzw. die erste Kategorie). Wir interessieren uns nur für die gesampelten *Schlüssel*."""

    def suggest_int(self, name, low, high, **kw):
        return low

    def suggest_float(self, name, low, high, **kw):
        return float(low)

    def suggest_categorical(self, name, choices):
        return choices[0]


def _active_strategies():
    data = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    return [s for s in data["strategies"] if s.get("active", True) is not False]


def _config_fields(strategy_module: str, config_class: str) -> set[str]:
    mod = importlib.import_module(strategy_module)
    cfg = getattr(mod, config_class)
    # msgspec __struct_fields__ enthält bereits die vererbten Felder (HourlyStrategyConfig +
    # nautilus StrategyConfig). Das ist exakt die valid_keys-Menge des Workers.
    return set(getattr(cfg, "__struct_fields__", ()))


@pytest.mark.parametrize("strat", _active_strategies(), ids=lambda s: s["strategy_class"])
def test_sampled_params_bind_to_config(strat):
    """Für JEDE aktive Strategie: sample_params(strategy).keys() ⊆ Config-Felder."""
    name = strat["strategy_class"]
    sampled = sample_params(name, _FakeTrial())
    fields = _config_fields(strat["strategy_module"], strat["config_class"])
    unbound = set(sampled.keys()) - fields
    assert not unbound, (
        f"{name}: gesampelte Parameter ohne `{strat['config_class']}`-Feld ⇒ der Worker "
        f"verwirft sie still (Phantom-Tuning, Pitfall #81): {sorted(unbound)}"
    )


def test_all_active_strategies_have_a_search_space():
    """Jede aktive Strategie MUSS in spaces.sample_params auftauchen (sonst kein Tuning)."""
    for strat in _active_strategies():
        name = strat["strategy_class"]
        sampled = sample_params(name, _FakeTrial())
        assert isinstance(sampled, dict) and sampled, f"{name}: leerer/fehlender Suchraum"


def test_removed_phantom_keys_are_gone():
    """Regressions-Riegel: die in #446 entfernten Phantom-Keys dürfen NICHT zurückkehren."""
    ft = _FakeTrial()
    assert "vol_surge_multiplier" not in sample_params("FlashCrashReversalStrategy", ft)
    vb = sample_params("VolatilityBreakoutPumpStrategy", ft)
    assert "bb_std" not in vb and "vol_window" not in vb and "vol_threshold" not in vb
    assert "bb_std_dev" in vb  # korrekt umbenannt
    vw = sample_params("VwapExhaustionStrategy", ft)
    assert "vwap_window" not in vw and "rsi_period" not in vw and "rsi_extreme" not in vw
    assert "vwap_period" in vw  # korrekt umbenannt
    assert "trend_tolerance_pct" in sample_params("ComboTrendVwapStrategy", ft)
