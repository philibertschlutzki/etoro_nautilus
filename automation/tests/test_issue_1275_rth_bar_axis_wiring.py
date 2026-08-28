"""Issue #1275 (GH #1148, Katalog #1272-1297, P0, letzter Schritt vor Purge) — "RTH-Bar-Achse
(Schritt 2 aus #1011/#1163/#1176) umsetzen".

Symptom. ``bars_per_calendar_day = 24,00`` und ``session_coverage_fraction = 0,2389-0,2402`` in
56/56 Studies; ``check_session_calendar_coherence`` FAILt 4/4. Die "24 synthetische Bars = 1
Kalendertag"-Zeitbox entsprach 2,15-5,02 geschaetzten Handels-Bars. Alle abgeleiteten Groessen
(ATR, Annualisierung, oos_n_periods, PSR/DSR-T) rechneten auf einer Achse, die zu 76 % aus
Fuellbars bestand.

Root-Cause. Die Bar-Aggregation kannte keine Handelszeiten-Maske fuer EQUITY/COMMODITY. Schritt 1
(Deklaration, ``backtest.json['session_hours_by_asset_class']`` + die reinen Hilfsfunktionen
``resolve_session_hours_by_asset_class``/``is_within_session_hours``) war mit #1260/GH #1130
erledigt und explizit NICHT verdrahtet ("BEWUSSTER SCOPE" — kein Marktdaten-Katalog zur
End-to-End-Verifikation in der damaligen Sandbox verfuegbar). Schritt 2 (Erzeugung) schliesst
dieser Fix.

Fix.
1. ``backtest_runner._filter_ticks_to_session_hours`` (neu) filtert Ticks ausserhalb der
   aufgeloesten Session VOR jeder Precision-/Spread-Normalisierung in ``load_ticks_from_catalog``.
2. ``session_hours_by_asset_class`` wird EINMAL im Orchestrator (``run_backtest``) geladen und an
   jeden isolierten Worker-Prozess durchgereicht (dieselbe Konvention wie
   ``atr_floor_bps_by_asset_class``).
3. ``time_box_bars_axis`` 'calendar_24_7' → 'rth'; ``time_box_bars`` 24.0 → 5.76;
   ``_contracts.MAX_BARS_IN_TRADE_HARD_CAP``/``MIN_BARS_IN_TRADE_FLOOR`` und jede
   ``max_bars_in_trade``-Suchraum-Bound (``spaces.py``/``search_space_overrides.json``/
   ``strategy_defaults.json``) auf Faktor 0.24 umkalibriert (das GEMESSENE
   ``session_coverage_fraction`` des Referenzlaufs).
4. ``simulation_semantics_version`` 6 → 7 (Pflicht-Purge vor dem naechsten produktiven Re-Run);
   §4.1 des Reports verliert die "Handels-Bars (geschätzt)"-Spalte fuer jeden Report, der unter
   ``time_box_bars_axis='rth'`` gebaut wird.

Scope. Die Tick-Filterung selbst ist eine reine Listen-Operation auf ``QuoteTick``-aehnlichen
Objekten (Attribut ``ts_event``) — unit-testbar mit synthetischen Tick-Stubs, ohne einen echten
Marktdaten-Katalog oder einen laufenden ``BacktestEngine`` zu benoetigen (dieselbe Testbarkeits-
Ueberlegung wie ``is_within_session_hours`` selbst, #1260). Die NUMERISCHEN Akzeptanzkriterien
(``bars_per_calendar_day <= 8``, ``session_coverage_fraction >= 0,9`` auf echten historischen
Ticks) sind Sache eines echten, produktiven Optimierungslaufs -- wie jeder andere numerische
Befund in diesem Katalog, dessen Verifikation ausserhalb dieser Sandbox liegt.
"""
import json
from pathlib import Path

import pytest

from automation import backtest_runner as br


class _FakeTick:
    def __init__(self, ts_event: int):
        self.ts_event = ts_event


def _ts_ns(iso: str) -> int:
    import pandas as pd
    return int(pd.Timestamp(iso, tz="UTC").value)


# ---------------------------------------------------------------------------------------------
# backtest_runner._filter_ticks_to_session_hours — Fix Punkt 1/2
# ---------------------------------------------------------------------------------------------

def test_filter_drops_ticks_outside_the_session_window():
    # Issue #1300 (GH #1177, P0) — die Fenstergrenzen werden gegen das beobachtete Tick-Raster
    # gesnappt (``_snap_session_window_to_tick_grid``, ``_median_tick_delta_t_s``). Drei ISOLIERTE
    # Einzel-Ticks (11 Stunden auseinander) sind keine realistische Tick-Dichte — ihr Median-Delta
    # (Stunden) snappte das Fenster faelschlich um mehrere Stunden auf, statt der urspruenglich
    # beabsichtigten exakten 13:30-20:00-Grenze nahezukommen. Drei DICHTE Cluster (1s-Kadenz, wie
    # echte Marktdaten) um dieselben drei Zeitpunkte halten den Median klein (die Mehrheit der
    # aufeinanderfolgenden Deltas bleibt 1s, nur 2 von 14 sind die grossen Cluster-Luecken) und
    # bilden dieselbe "vor/innerhalb/nach"-Testabsicht auf realistischer Tick-Dichte ab.
    def _cluster(start_iso: str, n: int = 5) -> list["_FakeTick"]:
        base = _ts_ns(start_iso)
        return [_FakeTick(base + i * 1_000_000_000) for i in range(n)]

    before = _cluster("2026-08-24T10:00:00Z")   # Montag, vor Open (13:30 UTC)
    inside = _cluster("2026-08-24T15:00:00Z")   # Montag, innerhalb 13:30-20:00
    after = _cluster("2026-08-24T21:00:00Z")    # Montag, nach Close
    ticks = before + inside + after

    session_hours_by_asset_class = {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}
    out = br._filter_ticks_to_session_hours(ticks, session_hours_by_asset_class, "EQUITY")
    assert set(out) == set(inside)


def test_filter_drops_weekend_ticks():
    ticks = [
        _FakeTick(_ts_ns("2026-08-22T15:00:00Z")),  # Samstag, waere innerhalb des Tagesfensters
        _FakeTick(_ts_ns("2026-08-24T15:00:00Z")),  # Montag, innerhalb
    ]
    session_hours_by_asset_class = {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}
    out = br._filter_ticks_to_session_hours(ticks, session_hours_by_asset_class, "EQUITY")
    assert len(out) == 1


def test_filter_is_a_noop_without_an_asset_class_key():
    ticks = [_FakeTick(_ts_ns("2026-08-22T15:00:00Z"))]
    out = br._filter_ticks_to_session_hours(
        ticks, {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}, None)
    assert out is ticks


def test_filter_is_a_noop_for_a_24_7_market_like_crypto():
    """FOREX/CRYPTO tragen None in session_hours_by_asset_class (echte 24/7-Maerkte, siehe
    resolve_session_hours_by_asset_class-Docstring) -- die Filterung darf dort nichts veraendern,
    auch nicht am Wochenende."""
    ticks = [_FakeTick(_ts_ns("2026-08-22T15:00:00Z"))]  # Samstag
    session_hours_by_asset_class = {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}, "CRYPTO": None}
    out = br._filter_ticks_to_session_hours(ticks, session_hours_by_asset_class, "CRYPTO")
    assert out is ticks


def test_filter_is_a_noop_without_any_configured_table():
    ticks = [_FakeTick(_ts_ns("2026-08-24T10:00:00Z"))]
    out = br._filter_ticks_to_session_hours(ticks, None, "EQUITY")
    assert out is ticks


def test_filter_returns_empty_list_when_every_tick_is_outside_the_session():
    ticks = [_FakeTick(_ts_ns("2026-08-22T15:00:00Z")), _FakeTick(_ts_ns("2026-08-23T15:00:00Z"))]
    session_hours_by_asset_class = {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}
    out = br._filter_ticks_to_session_hours(ticks, session_hours_by_asset_class, "EQUITY")
    assert out == []


# ---------------------------------------------------------------------------------------------
# load_ticks_from_catalog / run_single_backtest_worker / _run_remaining_sequentially wiring —
# schliesst die #1260-dokumentierte Scope-Grenze (Quelltext-Regressionswaechter, analog
# test_issue_1260_rth_bar_axis.py's frueherem "does_not_reference"-Test, jetzt umgekehrt)
# ---------------------------------------------------------------------------------------------

def test_load_ticks_from_catalog_accepts_and_forwards_session_hours_params():
    import inspect
    sig = inspect.signature(br.load_ticks_from_catalog)
    assert "session_hours_by_asset_class" in sig.parameters
    assert "asset_class_key" in sig.parameters
    source = inspect.getsource(br.load_ticks_from_catalog)
    assert "_filter_ticks_to_session_hours(" in source


def test_run_single_backtest_worker_accepts_session_hours_param():
    import inspect
    sig = inspect.signature(br.run_single_backtest_worker)
    assert "session_hours_by_asset_class" in sig.parameters
    source = inspect.getsource(br.run_single_backtest_worker)
    assert "session_hours_by_asset_class=session_hours_by_asset_class" in source


def test_run_remaining_sequentially_forwards_session_hours_param():
    import inspect
    sig = inspect.signature(br._run_remaining_sequentially)
    assert "session_hours_by_asset_class" in sig.parameters
    source = inspect.getsource(br._run_remaining_sequentially)
    assert "session_hours_by_asset_class=session_hours_by_asset_class" in source


def test_orchestrator_loads_session_hours_by_asset_class_from_backtest_config():
    source = Path(br.__file__).read_text("utf-8")
    assert 'session_hours_by_asset_class = backtest_global_cfg.get("session_hours_by_asset_class", {})' in source


def test_all_worker_call_sites_thread_session_hours_by_asset_class():
    """Alle 5 Aufrufstellen (future.submit, direkter Aufruf, _run_remaining_sequentially UND
    dessen eigener innerer run_single_backtest_worker-Aufruf, load_ticks_from_catalog) reichen
    den Parameter durch -- kein stiller Verlust an irgendeiner der Verzweigungen."""
    source = Path(br.__file__).read_text("utf-8")
    assert source.count("session_hours_by_asset_class=session_hours_by_asset_class,") == 5


# ---------------------------------------------------------------------------------------------
# optimizer.json — Fix Punkt 3/4
# ---------------------------------------------------------------------------------------------

_OPTIMIZER_JSON_PATH = Path("automation/config/optimizer.json")


def test_time_box_bars_axis_is_rth():
    cfg = json.loads(_OPTIMIZER_JSON_PATH.read_text("utf-8"))
    assert cfg["time_box_bars_axis"] == "rth"


def test_time_box_bars_rescaled_by_the_0_24_factor():
    cfg = json.loads(_OPTIMIZER_JSON_PATH.read_text("utf-8"))
    assert cfg["time_box_bars"] == pytest.approx(24.0 * 0.24)


def test_simulation_semantics_version_is_7():
    cfg = json.loads(_OPTIMIZER_JSON_PATH.read_text("utf-8"))
    assert cfg["simulation_semantics_version"] == 7


def test_simulation_schema_v7_names_its_actual_trigger():
    cfg = json.loads(_OPTIMIZER_JSON_PATH.read_text("utf-8"))
    doc = cfg["_schema"]["fields"]["simulation_semantics_version"]
    v7_segment = doc[doc.index("v7 ="):]
    assert "#1275" in v7_segment
    assert "#1148" in v7_segment
    assert "_filter_ticks_to_session_hours" in v7_segment
    assert "purge_stale_studies" in v7_segment


# ---------------------------------------------------------------------------------------------
# _contracts.py / spaces.py — Fix Punkt 3 (Faktor 0.24)
# ---------------------------------------------------------------------------------------------

def test_max_bars_in_trade_hard_cap_is_6():
    from automation.optimizer._contracts import MAX_BARS_IN_TRADE_HARD_CAP
    assert MAX_BARS_IN_TRADE_HARD_CAP == 6


def test_min_bars_in_trade_floor_is_2():
    """Issue #1317/GH #1194 — der Floor wurde von der 0,24-Achsen-Skalierung (Wert 1) auf eine
    achsen-unabhaengige Rausch-Schwelle (Wert 2) umgestellt; siehe test_issue_1317_min_bars_floor_
    axis_independent.py fuer die volle #1194-Akzeptanzpruefung."""
    from automation.optimizer._contracts import MIN_BARS_IN_TRADE_FLOOR
    assert MIN_BARS_IN_TRADE_FLOOR == 2


def test_search_space_overrides_json_rescaled():
    cfg = json.loads(Path("automation/config/search_space_overrides.json").read_text("utf-8"))
    for strategy, per_symbol in cfg["overrides"].items():
        for symbol, params in per_symbol.items():
            if "max_bars_in_trade" in params:
                lo, hi = params["max_bars_in_trade"]
                assert hi <= 6, f"{strategy}/{symbol}: {params['max_bars_in_trade']} exceeds the new cap"
                assert lo >= 1


def test_strategy_defaults_json_rescaled():
    cfg = json.loads(Path("automation/config/strategy_defaults.json").read_text("utf-8"))
    for strategy, params in cfg.items():
        if isinstance(params, dict) and "max_bars_in_trade" in params:
            assert 1 <= params["max_bars_in_trade"] <= 6, f"{strategy}: {params['max_bars_in_trade']}"


def test_hourly_strategy_config_default_is_6():
    from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
    cfg = HourlyStrategyConfig(instrument_id="AAPL.ETORO", bar_type="AAPL.ETORO-1-HOUR-MID-INTERNAL")
    assert cfg.max_bars_in_trade == 6


# ---------------------------------------------------------------------------------------------
# summary_de.py §4.1 — Akzeptanzkriterium: Schaetzspalte entfaellt fuer einen 'rth'-Report
# ---------------------------------------------------------------------------------------------

def test_section_4_1_drops_the_estimate_column_for_an_rth_report():
    from automation.optimizer import summary_de as sde
    report = {
        "time_box_bars_axis": "rth",
        "studies": [
            {"strategy": "Strat", "symbol": "NVDA.ETORO", "time_box_exit_fraction": 0.49,
             "median_bars_held": 3.0, "session_coverage_fraction": 0.95},
        ],
    }
    section = sde._section_4_longest_trades(report)
    assert "Handels-Bars (geschätzt)" not in section
    assert "Median-Bars (Handel)" in section


def test_report_build_stamps_time_box_bars_axis_at_the_top_level():
    import inspect
    from automation.optimizer import report as rpt
    source = inspect.getsource(rpt._build_report)
    assert '"time_box_bars_axis": _declared_time_box_bars_axis' in source
