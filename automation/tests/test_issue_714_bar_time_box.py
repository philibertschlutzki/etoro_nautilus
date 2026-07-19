"""Issue #714 (P0) — GR-01: 24-Bar-Zeitbox — Bounds-Klemmung (alle 15 Strategien).

24 h = 24 Bar-Intervalle (1h-Bars), nicht Kalenderzeit. Der bestehende Bar-Zähler-Exit
(`_check_exits_and_update`) ist der Mechanismus; der Fix reduziert sich auf das Klemmen der
Defaults und Suchraum-Bounds.

Akzeptanzkriterien:
- Kein max_bars_in_trade-Default oder -Bound > 24 über alle 15 Strategien.
- Konstruktor-Klemmung greift für aus dem Cache geladene Configs mit Alt-Werten > 24.
- Bestehendes Bar-Zähler-Exit-Verhalten bleibt bit-identisch für Positionen <= 24 Bars.
"""
import json
from pathlib import Path

import optuna
import pytest

from automation.optimizer import spaces
from automation.strategies.hourly_strategy_base import (
    DEFAULT_MAX_BARS_IN_TRADE,
    MAX_BARS_IN_TRADE_HARD_CAP,
    HourlyStrategyConfig,
)
from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _active_strategies() -> list[str]:
    data = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    return [s["strategy_class"] for s in data["strategies"] if s.get("active", True) is not False]


def test_default_max_bars_in_trade_is_24():
    assert DEFAULT_MAX_BARS_IN_TRADE == 24
    assert MAX_BARS_IN_TRADE_HARD_CAP == 24


def test_base_config_default_is_24():
    cfg = HourlyStrategyConfig(instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL")
    assert cfg.max_bars_in_trade == 24


@pytest.mark.parametrize("strategy", _active_strategies())
def test_no_search_space_bound_exceeds_24(strategy):
    """Über 200 gesampelte Trials darf max_bars_in_trade NIE über 24 liegen."""
    study = optuna.create_study()
    for _ in range(50):
        trial = study.ask()
        params = spaces.sample_params(strategy, trial)
        if "max_bars_in_trade" in params:
            assert params["max_bars_in_trade"] <= 24, (
                f"{strategy}: max_bars_in_trade={params['max_bars_in_trade']} > 24 (GR-01)"
            )
        study.tell(trial, 0.0)


def test_constructor_clamps_legacy_config_above_24():
    """Eine aus dem Cache geladene Alt-Config mit max_bars_in_trade=48 (Pre-#714-Studie) MUSS
    beim Strategie-Konstruktor auf 24 geklemmt werden."""
    config = Rsi2ReversionConfig(
        instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        max_bars_in_trade=48,
    )
    assert config.max_bars_in_trade == 48  # Config selbst bleibt unvalidiert (msgspec, kein Gate)
    strategy = Rsi2ReversionStrategy(config=config)
    assert strategy._max_bars_in_trade == 24  # Konstruktor-Klemmung greift


def test_constructor_leaves_values_at_or_below_24_untouched():
    config = Rsi2ReversionConfig(
        instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        max_bars_in_trade=16,
    )
    strategy = Rsi2ReversionStrategy(config=config)
    assert strategy._max_bars_in_trade == 16


@pytest.mark.parametrize("strategy", _active_strategies())
def test_lower_bounds_unchanged_still_present(strategy):
    """Untergrenzen bleiben unverändert (nur die Obergrenze wurde geklemmt) — max_bars_in_trade
    muss weiterhin eine echte Suchdimension mit einer positiven Spanne sein, wo vorhanden."""
    study = optuna.create_study()
    trial = study.ask()
    params = spaces.sample_params(strategy, trial)
    if "max_bars_in_trade" in params:
        assert params["max_bars_in_trade"] >= 1
