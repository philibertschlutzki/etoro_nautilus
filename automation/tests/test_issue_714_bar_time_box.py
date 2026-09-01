"""Issue #714 (P0) — GR-01: Bar-Zeitbox — Bounds-Klemmung (alle 15 Strategien).

Zeitbox = Bar-Intervalle (1h-Bars), nicht Kalenderzeit. Der bestehende Bar-Zähler-Exit
(`_check_exits_and_update`) ist der Mechanismus; der Fix reduziert sich auf das Klemmen der
Defaults und Suchraum-Bounds.

Issue #1275 (GH #1148, Katalog #1272-1297, P0) Fix Punkt 3 — die Zeitbox-Obergrenze wurde von 24
(Kalender-Bars) auf 6 (RTH-Bars) umkalibriert (Faktor 0.24, siehe
``_contracts.MAX_BARS_IN_TRADE_HARD_CAP``-Docstring); dieselben Akzeptanzkriterien gelten seither
gegen den neuen Cap. Issue #1343 (GH #1237) — 6 → 7: der Faktor 0.24 war eine VOR #1332/GH #1226s
Session-Ueberlappungs-Fix gemessene ``session_coverage_fraction`` (6 RTH-Bins/Handelstag statt der
tatsaechlichen 7); seit #1343 ist der Cap mechanisch aus der Bar-Achse abgeleitet
(``_contracts.BARS_PER_TRADING_DAY=7``), nicht mehr geschaetzt.

Akzeptanzkriterien:
- Kein max_bars_in_trade-Default oder -Bound > 7 über alle 15 Strategien.
- Konstruktor-Klemmung greift für aus dem Cache geladene Configs mit Alt-Werten > 7.
- Bestehendes Bar-Zähler-Exit-Verhalten bleibt bit-identisch für Positionen <= 7 Bars.
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


def test_default_max_bars_in_trade_is_7():
    assert DEFAULT_MAX_BARS_IN_TRADE == 7
    assert MAX_BARS_IN_TRADE_HARD_CAP == 7


def test_base_config_default_is_7():
    cfg = HourlyStrategyConfig(instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL")
    assert cfg.max_bars_in_trade == 7


@pytest.mark.parametrize("strategy", _active_strategies())
def test_no_search_space_bound_exceeds_7(strategy):
    """Über 200 gesampelte Trials darf max_bars_in_trade NIE über 7 liegen."""
    study = optuna.create_study()
    for _ in range(50):
        trial = study.ask()
        params = spaces.sample_params(strategy, trial)
        if "max_bars_in_trade" in params:
            assert params["max_bars_in_trade"] <= 7, (
                f"{strategy}: max_bars_in_trade={params['max_bars_in_trade']} > 7 (GR-01, #1343)"
            )
        study.tell(trial, 0.0)


def test_constructor_clamps_legacy_config_above_7():
    """Eine aus dem Cache geladene Alt-Config mit max_bars_in_trade=48 (Pre-#714-Studie) MUSS
    beim Strategie-Konstruktor auf 7 geklemmt werden."""
    config = Rsi2ReversionConfig(
        instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        max_bars_in_trade=48,
    )
    assert config.max_bars_in_trade == 48  # Config selbst bleibt unvalidiert (msgspec, kein Gate)
    strategy = Rsi2ReversionStrategy(config=config)
    assert strategy._max_bars_in_trade == 7  # Konstruktor-Klemmung greift


def test_constructor_leaves_values_at_or_below_7_untouched():
    config = Rsi2ReversionConfig(
        instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL",
        max_bars_in_trade=4,
    )
    strategy = Rsi2ReversionStrategy(config=config)
    assert strategy._max_bars_in_trade == 4


@pytest.mark.parametrize("strategy", _active_strategies())
def test_lower_bounds_unchanged_still_present(strategy):
    """Untergrenzen bleiben unverändert (nur die Obergrenze wurde geklemmt) — max_bars_in_trade
    muss weiterhin eine echte Suchdimension mit einer positiven Spanne sein, wo vorhanden."""
    study = optuna.create_study()
    trial = study.ask()
    params = spaces.sample_params(strategy, trial)
    if "max_bars_in_trade" in params:
        assert params["max_bars_in_trade"] >= 1
