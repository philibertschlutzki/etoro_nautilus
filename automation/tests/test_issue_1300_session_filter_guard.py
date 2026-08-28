"""Issue #1300 (GH #1177, P0) — RTH-Fenstergrenze liegt feiner als das Tick-Raster; kein
Nicht-Leerheits-Wächter.

Symptom. ``_filter_ticks_to_session_hours`` kann die gesamte Tick-Menge verwerfen, ohne dass
irgendetwas das bemerkt.

Root-Cause (zwei Teile).
1. ``session_hours_by_asset_class.EQUITY = {"open_utc": "13:30", ...}`` — eine HALBSTUNDEN-Grenze
   gegen ein STUNDEN-Tick-Raster verwirft systematisch die erste Session-Bar.
2. ``_filter_ticks_to_session_hours`` hatte keinen Wächter: ``[]`` war von "kein Fenster
   konfiguriert" nur am Aufrufer unterscheidbar.

Fix.
1. Fenstergrenzen werden auf das beobachtete Tick-Raster gesnappt (``_snap_session_window_to_tick_
   grid``, Median-Δt der ungefilterten Menge).
2. Harter Wächter: leere Menge nach dem Filter, aber nicht davor ⇒ ``SessionFilterEmptyError``
   (statt ``[]``), abgefangen von ``run_single_backtest_worker``
   (``error="session_filter_removed_all_ticks"``).
3. Weicher Wächter: Verwurfsanteil ausserhalb [0,5; 0,95] ⇒ ``SESSION_FILTER_YIELD``-Ereignis.
"""
import datetime as dt

import pytest

from automation import backtest_runner as br


_NS_PER_HOUR = 3_600_000_000_000


class _FakeTick:
    def __init__(self, ts_event: int):
        self.ts_event = ts_event


def _hourly_ticks_over_days(n_days: int, start_utc: dt.datetime) -> list:
    """24/7-Stundenraster über n_days Tage, ab einem festen UTC-Anker (Montag 00:00)."""
    base_ns = int(start_utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1_000_000_000)
    ticks = []
    for h in range(n_days * 24):
        ticks.append(_FakeTick(base_ns + h * _NS_PER_HOUR))
    return ticks


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 1 — 7 Ticks je Werktag überleben (13:00–19:00), nicht 6.
# ---------------------------------------------------------------------------------------------

def test_hourly_raster_yields_seven_ticks_per_weekday_not_six():
    # 2026-01-05 ist ein Montag (deterministischer Anker).
    monday = dt.datetime(2026, 1, 5, 0, 0, 0)
    ticks = _hourly_ticks_over_days(30, monday)
    filtered = br._filter_ticks_to_session_hours(
        ticks, {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}, "EQUITY")

    # Erster volle Handelstag (Montag): erwartete Stunden 13,14,...,19 UTC (7 Ticks).
    day0_hours = sorted({
        dt.datetime.fromtimestamp(t.ts_event / 1e9, tz=dt.timezone.utc).hour
        for t in filtered
        if dt.datetime.fromtimestamp(t.ts_event / 1e9, tz=dt.timezone.utc).date() == monday.date()
    })
    assert day0_hours == [13, 14, 15, 16, 17, 18, 19]
    assert len(day0_hours) == 7  # NICHT 6 (die Root-Cause dieses Issues).


def test_snap_session_window_to_tick_grid_hourly_raster():
    open_utc, close_utc = br._snap_session_window_to_tick_grid("13:30", "20:00", 3600.0)
    assert (open_utc, close_utc) == ("13:00", "20:00")


def test_snap_session_window_no_grid_leaves_window_unchanged():
    open_utc, close_utc = br._snap_session_window_to_tick_grid("13:30", "20:00", None)
    assert (open_utc, close_utc) == ("13:30", "20:00")


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 2 — Fenster gegen ausschliesslich :30-Ticks ⇒ SessionFilterEmptyError.
# ---------------------------------------------------------------------------------------------

def test_window_disjoint_from_tick_distribution_raises_instead_of_silent_empty_list():
    """Ein Fenster, das komplett ausserhalb der TATSAECHLICHEN Tick-Verteilung liegt (nicht nur
    feiner aufgeloest, siehe test_hourly_raster_yields_seven_ticks_per_weekday_not_six oben — DAS
    Snapping fixt den Fall "zu fein"), bleibt auch nach dem Snapping leer: das Snapping erweitert
    ein Fenster auf das Rastermass, verschiebt es aber nicht dorthin, wo die Daten liegen."""
    # 2026-01-05 (Montag), Ticks NUR zwischen 03:50 und 03:59 UTC (5-Minuten-Raster), an 5 Tagen.
    monday = dt.datetime(2026, 1, 5, 0, 0, 0)
    base_ns = int(monday.replace(tzinfo=dt.timezone.utc).timestamp() * 1_000_000_000)
    _NS_PER_MIN = 60_000_000_000
    ticks = []
    for day in range(5):
        for minute in (50, 55):
            ticks.append(_FakeTick(base_ns + day * 24 * 3600 * 1_000_000_000
                                   + 3 * 3600 * 1_000_000_000 + minute * _NS_PER_MIN))

    # Zielfenster [10:00, 11:00) -- disjunkt von der 03:50-03:59-Verteilung, unabhaengig vom
    # 5-Minuten-Grid-Snapping (das Fenster wandert nicht, es wird nur an den Rand-Minuten geglaettet).
    with pytest.raises(br.SessionFilterEmptyError) as exc_info:
        br._filter_ticks_to_session_hours(
            ticks, {"EQUITY": {"open_utc": "10:00", "close_utc": "11:00"}}, "EQUITY")
    msg = str(exc_info.value)
    assert "n_before=10" in msg
    assert "session_window_snapped" in msg
    assert "time_of_day_histogram_utc_hour" in msg


def test_session_filter_empty_error_not_raised_when_input_already_empty():
    """Kein Fehler, wenn die EINGABE bereits leer war (die Unterscheidung gilt nur fuer "nicht-leer
    -> leer")."""
    result = br._filter_ticks_to_session_hours(
        [], {"EQUITY": {"open_utc": "03:00", "close_utc": "04:00"}}, "EQUITY")
    assert result == []


def test_session_filter_empty_error_not_raised_when_all_input_is_weekend_only():
    """Regressionsschutz (bei der Implementierung von Issue #1314/GH #1191 aufgedeckt): eine
    Eingabemenge, die AUSSCHLIESSLICH aus Wochenend-Ticks besteht, liefert IMMER ein leeres
    Ergebnis (``is_within_session_hours`` schliesst Sa/So unbedingt aus, unabhaengig vom
    [gesnappten] Tagesfenster) — das ist die korrekte, erwartete Wochenend-Ausschluss-Antwort,
    KEIN degeneriertes Fenster. Der Waechter darf hier NICHT werfen."""
    saturday = dt.datetime(2026, 1, 3, 15, 0, 0, tzinfo=dt.timezone.utc)  # 2026-01-03 ist Samstag.
    sunday = dt.datetime(2026, 1, 4, 15, 0, 0, tzinfo=dt.timezone.utc)
    ticks = [
        _FakeTick(int(saturday.timestamp() * 1_000_000_000)),
        _FakeTick(int(sunday.timestamp() * 1_000_000_000)),
    ]
    result = br._filter_ticks_to_session_hours(
        ticks, {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}, "EQUITY")
    assert result == []


def test_session_filter_empty_error_still_raised_when_a_weekday_tick_is_present_but_excluded():
    """Regressionsschutz in die andere Richtung: sobald mindestens EIN Tick auf einen Handelstag
    faellt, aber selbst NACH dem Snapping keiner ins Fenster faellt, bleibt der harte Waechter
    aktiv — dieselbe Situation wie test_window_disjoint_from_tick_distribution_raises_instead_of_
    silent_empty_list oben (dichte 5-Minuten-Ticks, dieselbe Realismus-Anforderung an das Median-
    Delta), hier zusaetzlich mit einem (irrelevanten) Wochenend-Tick gemischt."""
    monday = dt.datetime(2026, 1, 5, 0, 0, 0, tzinfo=dt.timezone.utc)  # Montag.
    saturday = dt.datetime(2026, 1, 3, 0, 0, 0, tzinfo=dt.timezone.utc)
    _NS_PER_MIN = 60_000_000_000
    ticks = []
    for day_base, day_count in ((saturday, 1), (monday, 5)):
        for day in range(day_count):
            for minute in (50, 55):
                ticks.append(_FakeTick(
                    int(day_base.timestamp() * 1_000_000_000)
                    + day * 24 * 3600 * 1_000_000_000 + 3 * 3600 * 1_000_000_000
                    + minute * _NS_PER_MIN))
    with pytest.raises(br.SessionFilterEmptyError):
        br._filter_ticks_to_session_hours(
            ticks, {"EQUITY": {"open_utc": "10:00", "close_utc": "11:00"}}, "EQUITY")


# ---------------------------------------------------------------------------------------------
# Akzeptanzkriterium 3 — session_hours_by_asset_class=None ⇒ bit-identisch.
# ---------------------------------------------------------------------------------------------

def test_none_session_hours_is_bit_identical():
    ticks = [_FakeTick(i * _NS_PER_HOUR) for i in range(48)]
    result = br._filter_ticks_to_session_hours(ticks, None, "EQUITY")
    assert result is ticks


def test_missing_asset_class_key_is_bit_identical():
    ticks = [_FakeTick(i * _NS_PER_HOUR) for i in range(48)]
    result = br._filter_ticks_to_session_hours(
        ticks, {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}, None)
    assert result is ticks


def test_asset_class_with_null_window_is_bit_identical():
    ticks = [_FakeTick(i * _NS_PER_HOUR) for i in range(48)]
    result = br._filter_ticks_to_session_hours(
        ticks, {"CRYPTO": None}, "CRYPTO")
    assert result is ticks


# ---------------------------------------------------------------------------------------------
# session_window_snapped erscheint in der load_ticks_from_catalog-out-Telemetrie (#1298).
# ---------------------------------------------------------------------------------------------

def test_median_tick_delta_t_s_hourly():
    ticks = [_FakeTick(i * _NS_PER_HOUR) for i in range(10)]
    assert br._median_tick_delta_t_s(ticks) == pytest.approx(3600.0)


def test_median_tick_delta_t_s_insufficient_ticks_returns_none():
    assert br._median_tick_delta_t_s([_FakeTick(0)]) is None
    assert br._median_tick_delta_t_s([]) is None


def test_filter_ticks_to_session_hours_stamps_out_dict_with_snapped_window():
    monday = dt.datetime(2026, 1, 5, 0, 0, 0)
    ticks = _hourly_ticks_over_days(2, monday)
    out: dict = {}
    br._filter_ticks_to_session_hours(
        ticks, {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}, "EQUITY", out=out)
    assert out["session_window_snapped"] == ("13:00", "20:00")
