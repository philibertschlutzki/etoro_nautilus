"""Issue #1343 (GH #1237, Katalog #1330-1351, P0, Merge-Stufe 3 aus #1246) — ``max_bars_in_trade``
mechanisch aus der Bar-Achse ableiten statt aus einer VOR #1332/GH #1226s Session-Ueberlappungs-Fix
GEMESSENEN ``session_coverage_fraction`` (Faktor 0.24, #1275/GH #1148) zu SCHAETZEN.

Symptom. ``_contracts.RTH_AXIS_FACTOR = 0.24`` war die 1275-Referenzlauf-Beobachtung
"6 von 24 Kalenderstunden ueberlappen die EQUITY-RTH-Session" — GENAU der 6-statt-7-RTH-Bins-Defekt,
den #1332/GH #1226 (Stage 1, session_windows.interval_overlaps_session_hours) strukturell behoben
hat (die 13:30-Uhr-Oeffnung ueberlappt auch das 13:00-14:00-Bar-Intervall, nicht nur volle
Bar-Intervalle INNERHALB der Session). Der Zeitbox-Deckel (``MAX_BARS_IN_TRADE_HARD_CAP``,
``TIME_BOX_BARS``) blieb bis zu diesem Fix an der ALTEN, jetzt bekannt falschen Zaehlung kalibriert.

Root-Cause. Zwei unabhaengige Fixes desselben Root-Cause (6-vs-7-RTH-Bins/Handelstag): #1332/GH
#1226 korrigierte die DATENSEITIGE Zaehlung (bar_coverage_ratio-Nenner, Sample-Filterung), liess
aber die STRATEGIESEITIGE Konsequenz (Zeitbox-Deckel/-Deadline) unangetastet.

Fix.
1. Neue Funktion ``_contracts._bars_per_trading_day()`` zaehlt via
   ``session_windows.interval_overlaps_session_hours`` mechanisch, wie viele 1h-Bar-Intervalle das
   EQUITY-RTH-Fenster (13:30-20:00 UTC) schneiden ⇒ ``BARS_PER_TRADING_DAY = 7``.
2. ``TIME_BOX_BARS = MAX_HANDELSTAGE_DEFAULT(1.0) * BARS_PER_TRADING_DAY`` (7.0, vormals 5.76);
   ``MAX_BARS_IN_TRADE_HARD_CAP = ceil(TIME_BOX_BARS)`` (7, vormals 6) — ``check_timebox_cap_
   coherence`` (invariants.py) bewacht die Kohaerenz weiterhin, PASST automatisch (Slack 0 < 1.0).
3. ``optimizer.json['time_box_bars']`` auf 7.0 aktualisiert.
4. ``hourly_strategy_base.DEFAULT_MAX_BARS_IN_TRADE`` UND ``HourlyStrategyConfig.max_bars_in_trade``
   (Feld-Default) referenzieren jetzt ``MAX_BARS_IN_TRADE_HARD_CAP`` direkt, statt je einer eigenen
   Literal-Kopie (6) — Grep-Test unten.
5. ``_contracts.MIN_BARS_IN_TRADE_FLOOR`` bleibt UNVERAENDERT ein eigenstaendiges Literal (=2) —
   Issue #1317/GH #1194 etablierte ausdruecklich, dass dieser Floor eine achsen-UNABHAENGIGE
   Rausch-Schwelle ist, keine Achsen-Groesse (siehe test_issue_1066_1067_1068_search_space_
   governance.py::test_min_bars_in_trade_floor_is_not_derived_from_rth_axis_factor).
"""
import inspect

from automation.optimizer import _contracts, invariants as inv, spaces
from automation.strategies.hourly_strategy_base import (
    DEFAULT_MAX_BARS_IN_TRADE,
    MAX_BARS_IN_TRADE_HARD_CAP,
    HourlyStrategyConfig,
)


def test_bars_per_trading_day_is_7_for_the_equity_rth_session():
    assert _contracts.BARS_PER_TRADING_DAY == 7


def test_time_box_bars_is_7_0():
    assert _contracts.TIME_BOX_BARS == 7.0


def test_max_bars_in_trade_hard_cap_is_7():
    assert _contracts.MAX_BARS_IN_TRADE_HARD_CAP == 7
    assert MAX_BARS_IN_TRADE_HARD_CAP == 7


def test_min_bars_in_trade_floor_stays_the_axis_independent_literal_2():
    assert _contracts.MIN_BARS_IN_TRADE_FLOOR == 2


def test_hard_cap_still_derives_from_time_box_bars_via_ceil():
    import math
    assert _contracts.MAX_BARS_IN_TRADE_HARD_CAP == math.ceil(_contracts.TIME_BOX_BARS)


def test_timebox_cap_coherence_still_passes_for_the_shipped_config():
    result = inv.check_timebox_cap_coherence()
    assert result.passed is True
    assert result.severity == "blocking"


def test_optimizer_json_time_box_bars_matches_contracts():
    import json
    from pathlib import Path
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["time_box_bars"] == _contracts.TIME_BOX_BARS


# ── Grep-Test: kein eigenstaendiges Literal 24/6 mehr fuer die Zeitbox-Grenze ────────────────────

def test_hourly_strategy_config_field_default_references_the_shared_constant_not_a_literal():
    src = inspect.getsource(HourlyStrategyConfig)
    assignment_line = next(
        line for line in src.splitlines() if line.strip().startswith("max_bars_in_trade:"))
    assert "MAX_BARS_IN_TRADE_HARD_CAP" in assignment_line
    assert "= 6" not in assignment_line
    assert "= 24" not in assignment_line


def test_default_max_bars_in_trade_constant_references_the_shared_constant_not_a_literal():
    import automation.strategies.hourly_strategy_base as mod
    src = inspect.getsource(mod)
    assignment_line = next(
        line for line in src.splitlines()
        if line.startswith("DEFAULT_MAX_BARS_IN_TRADE ="))
    assert assignment_line.strip() == "DEFAULT_MAX_BARS_IN_TRADE = MAX_BARS_IN_TRADE_HARD_CAP"


def test_default_max_bars_in_trade_equals_the_new_hard_cap():
    assert DEFAULT_MAX_BARS_IN_TRADE == 7


def test_base_config_default_is_7():
    cfg = HourlyStrategyConfig(instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL")
    assert cfg.max_bars_in_trade == 7


def test_search_space_upper_bound_tracks_the_new_cap():
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study()
    trial = study.ask()
    params = spaces.sample_params("TrendPullbackStrategy", trial)
    if "max_bars_in_trade" in params:
        assert params["max_bars_in_trade"] <= 7
