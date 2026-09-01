"""automation/session_windows.py
================================
Issue #1332 (GH #1226) — Single Source of Truth für Handelszeit-Fenster-Tests.

Vor diesem Modul reimplementierten ``backtest_runner.is_within_session_hours`` und
``optimizer.sweep._is_ts_ns_within_session_utc`` dieselbe Punkt-Test-Semantik unabhängig
voneinander (zwei Zähler über dieselbe Grösse, die nicht dieselbe Funktion aufrufen — Pitfall
#435). Ausserdem testete jede Stelle, die eine Kerze/Bar gegen ein Session-Fenster prüfte, nur den
BEGINN des Intervalls (einen Zeitpunkt), nicht das Intervall selbst — eine Kerze, deren zweite
Hälfte in der Session liegt, wurde dadurch fälschlich verworfen (#1332 Symptom: 6 statt 7
RTH-Bins je Handelstag für EQUITY).

Bewusst OHNE jede schwere Abhängigkeit (kein nautilus_trader, kein Optuna, kein pandas) — analog
``automation.catalog_paths``/``automation.optimizer._contracts``: importierbar sowohl von
``backtest_runner.py`` (zieht ohnehin die volle nautilus_trader-Importkette) als auch von
``optimizer/sweep.py`` (das genau DIESE schwere Kette bewusst vermeidet, siehe dortige
Docstrings zu ``_resolve_session_window_utc``), ohne einen Import-Zyklus oder einen ungewollten
schweren Import zu riskieren.
"""
from __future__ import annotations

from datetime import datetime, timezone


def is_within_session_hours(
    ts_ns: int, open_utc: str, close_utc: str, *, weekdays_only: bool = True,
) -> bool:
    """Ist der UTC-Zeitpunkt ``ts_ns`` (Nanosekunden seit Epoch) innerhalb des Handelszeit-
    Fensters ``[open_utc, close_utc)`` (Strings ``'HH:MM'``, UTC-Uhrzeit-of-Day, ohne Datumsanteil
    — das Fenster gilt für jeden Handelstag identisch)? ``weekdays_only=True`` (Default)
    schliesst zusätzlich Samstag/Sonntag aus.

    Kanonische Punkt-Test-Implementierung (Issue #1332/GH #1226) — ``backtest_runner.
    is_within_session_hours`` und ``optimizer.sweep._is_ts_ns_within_session_utc`` importieren
    diese Funktion, statt sie zu reimplementieren."""
    dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
    if weekdays_only and dt.weekday() >= 5:
        return False
    open_h, open_m = (int(x) for x in open_utc.split(":"))
    close_h, close_m = (int(x) for x in close_utc.split(":"))
    time_of_day = dt.hour * 60 + dt.minute
    open_minutes = open_h * 60 + open_m
    close_minutes = close_h * 60 + close_m
    return open_minutes <= time_of_day < close_minutes


def interval_overlaps_session_hours(
    interval_start_ns: int,
    interval_end_ns: int,
    open_utc: str,
    close_utc: str,
    *,
    weekdays_only: bool = True,
) -> bool:
    """Schneidet das halboffene Intervall ``[interval_start_ns, interval_end_ns)`` (z. B. eine
    Kerze ``[candle_start, candle_end)``) das Session-Fenster ``[open_utc, close_utc)`` am
    Kalendertag von ``interval_start_ns``?

    Issue #1332 (GH #1226) Fix Punkt 2: eine Kerze gehört zur Session, wenn ihr Intervall das
    Fenster SCHNEIDET, nicht wenn ihr Startpunkt darin liegt — der Punkt-Test verwarf sonst z. B.
    die 13:00-Kerze bei Sessionbeginn 13:30, obwohl deren zweite Hälfte (13:30-14:00) in der
    Session liegt.

    Nur für Intervalle, die nicht über Mitternacht hinausreichen (jede Bar-Achse dieses Systems
    — OneHour/OneDay — erfüllt das): die Session-Grenzen werden aus dem Kalendertag von
    ``interval_start_ns`` abgeleitet. ``weekdays_only=True`` schliesst ein Intervall aus, dessen
    Start auf einen Samstag/Sonntag fällt (dieselbe Konvention wie ``is_within_session_hours``)."""
    start_dt = datetime.fromtimestamp(interval_start_ns / 1_000_000_000, tz=timezone.utc)
    if weekdays_only and start_dt.weekday() >= 5:
        return False
    open_h, open_m = (int(x) for x in open_utc.split(":"))
    close_h, close_m = (int(x) for x in close_utc.split(":"))
    day_start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_ns = int(day_start_dt.timestamp() * 1_000_000_000)
    session_open_ns = day_start_ns + (open_h * 60 + open_m) * 60_000_000_000
    session_close_ns = day_start_ns + (close_h * 60 + close_m) * 60_000_000_000
    # Standard-Überlappungstest für halboffene Intervalle [a,b) ∩ [c,d) ≠ ∅ ⟺ a < d ∧ b > c.
    return interval_start_ns < session_close_ns and interval_end_ns > session_open_ns
