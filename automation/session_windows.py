"""automation/session_windows.py
================================
Single Source of Truth für Handelszeit-Fenster.

Issue #1332 (GH #1226) — vor diesem Modul reimplementierten ``backtest_runner.is_within_session_hours``
und ``optimizer.sweep._is_ts_ns_within_session_utc`` dieselbe Punkt-Test-Semantik unabhängig voneinander
(zwei Zähler über dieselbe Grösse, die nicht dieselbe Funktion aufrufen — Pitfall #435), und jede Stelle,
die eine Kerze gegen ein Fenster prüfte, testete nur den BEGINN des Intervalls statt das Intervall selbst
(Symptom: 6 statt 7 RTH-Bins je Handelstag).

Issue #1356 (GH #1252, P0) — das Fenster ist eine BÖRSEN-LOKALZEIT-Grösse. ``13:30-20:00 UTC`` ist NYSE-RTH
nur während EDT; ab Mo 2026-11-02 (DST-Ende 2026-11-01) ist RTH ``14:30-21:00 UTC`` — ein fixes UTC-Fenster
nimmt dann die 13:00-Kerze (Pre-Market 08:00-09:00 ET) auf und verwirft die Schlussstunde (20:00-Kerze,
15:00-16:00 ET). Ab dem ersten Wintertag zerfällt jede Walk-Forward-Geometrie sonst in einen Sommer- und
einen Winterabschnitt mit verschiedenen Achsen.

* ``SessionWindow(tz, open, close, calendar)`` — Fenster in Börsen-Lokalzeit (``zoneinfo``, Standardbibliothek).
  Config: ``{"tz": "America/New_York", "open": "09:30", "close": "16:00"}``; ``null`` bleibt „durchgehend".
  Die alte UTC-Form ``{"open_utc", "close_utc"}`` wird mit WARNING ``SESSION_WINDOW_UTC_LEGACY`` gelesen
  (als ``tz="UTC"``), NIE still als Börsenzeit umgedeutet.
* ``session_bounds_utc_ns(day, window)`` — ``(open_ns, close_ns)`` des Handelstags ``day`` in Integer-
  Nanosekunden (kein Float-Zeitstempel: Grenz-Ticks sind exakt entscheidbar).
* ``is_within_session`` / ``interval_overlaps_session`` — der Handelstag wird in Börsen-LOKALZEIT bestimmt;
  Wochenenden UND Feiertage (``config/exchange_holidays.json``) sind Nicht-Handelstage, kein Datenloch.
* ``bars_per_trading_day`` / ``count_trading_days`` — die Achsen-Arithmetik (Coverage-Nenner, Holdout-
  Bar-Zahl, ``_contracts.BARS_PER_TRADING_DAY``) aus DERSELBEN Fenster-Definition.
* ``is_within_session_hours`` / ``interval_overlaps_session_hours`` — die Alt-API (``HH:MM``-UTC-Strings)
  bleibt als dünner Wrapper über ein ``tz="UTC"``-Fenster bestehen.

Bewusst OHNE schwere Abhängigkeit (kein nautilus_trader, kein Optuna, kein pandas) — importierbar von
``backtest_runner.py``, ``optimizer/sweep.py`` und ``optimizer/_contracts.py`` ohne Import-Zyklus.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

NS_PER_S = 1_000_000_000
NS_PER_HOUR = 3_600 * NS_PER_S
NS_PER_DAY = 86_400 * NS_PER_S
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_US = timedelta(microseconds=1)

HOLIDAYS_PATH = Path(__file__).resolve().parent / "config" / "exchange_holidays.json"
LEGACY_UTC_EVENT = "SESSION_WINDOW_UTC_LEGACY"
HOLIDAYS_OUT_OF_RANGE_EVENT = "EXCHANGE_HOLIDAYS_OUT_OF_RANGE"
# Standard-Kalender je Zeitzone, wenn ein Fenster keinen ``calendar`` nennt.
DEFAULT_CALENDAR_BY_TZ: dict[str, str] = {"America/New_York": "NYSE"}
# Referenztage der DST-Invarianzprüfung von ``bars_per_trading_day`` (Mittwoch im Winter / im Sommer).
_REFERENCE_DAYS = (date(2026, 1, 14), date(2026, 7, 15))


class SessionWindowConfigError(ValueError):
    """Ein ``session_hours_by_asset_class``-Eintrag / ein ``SessionWindow`` ist nicht auflösbar."""


def _parse_hhmm(text: str, *, field: str) -> int:
    """``'HH:MM'`` → Minuten seit lokaler Mitternacht (``24:00`` nur als Fensterende erlaubt)."""
    try:
        hh, mm = str(text).split(":")
        minutes = int(hh) * 60 + int(mm)
        if not (0 <= int(mm) < 60 and 0 <= minutes <= 24 * 60):
            raise ValueError
    except ValueError:
        raise SessionWindowConfigError(
            f"Session-Fenster: {field}={text!r} ist keine Uhrzeit 'HH:MM' (00:00-24:00).") from None
    return minutes


@lru_cache(maxsize=None)
def _tzinfo(name: str):
    if name in ("UTC", "Etc/UTC", "Z"):
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise SessionWindowConfigError(
            f"Session-Fenster: tz={name!r} ist keine bekannte IANA-Zeitzone (zoneinfo; auf Systemen ohne "
            f"tz-Datenbank hilft das Paket 'tzdata').") from None


@dataclass(frozen=True)
class SessionWindow:
    """Handelszeit-Fenster ``[open, close)`` in Börsen-Lokalzeit ``tz`` (siehe Moduldocstring).

    ``calendar`` nennt die Feiertagsliste in ``exchange_holidays.json`` (``None`` ⇒ nur Wochenenden sind
    Nicht-Handelstage). ``legacy_utc=True`` markiert ein aus der Alt-Form ``{open_utc, close_utc}``
    gelesenes Fenster (``tz == 'UTC'``)."""

    tz: str
    open: str
    close: str
    calendar: str | None = None
    legacy_utc: bool = False

    def __post_init__(self) -> None:
        _tzinfo(self.tz)
        o = _parse_hhmm(self.open, field="open")
        c = _parse_hhmm(self.close, field="close")
        if c <= o:
            raise SessionWindowConfigError(
                f"Session-Fenster {self.open}-{self.close} ({self.tz}): close muss nach open liegen "
                f"(Fenster über Mitternacht werden nicht unterstützt — in zwei Fenster teilen).")

    @property
    def open_minutes(self) -> int:
        return _parse_hhmm(self.open, field="open")

    @property
    def close_minutes(self) -> int:
        return _parse_hhmm(self.close, field="close")

    def to_config(self) -> dict:
        """Kanonische, JSON-serialisierbare Form (Strategie-Parameter, Telemetrie)."""
        out = {"tz": self.tz, "open": self.open, "close": self.close}
        if self.calendar:
            out["calendar"] = self.calendar
        return out

    def describe(self) -> str:
        return f"{self.open}-{self.close} {self.tz}"


_legacy_warned: set[tuple] = set()


def parse_session_window(entry, *, label: str | None = None) -> SessionWindow | None:
    """Ein ``session_hours_by_asset_class``-Eintrag → ``SessionWindow`` (``None``/fehlend ⇒ ``None`` =
    durchgehender Handel). Akzeptiert: ``None``, ein ``SessionWindow``, die neue Form
    ``{"tz", "open", "close"[, "calendar"]}`` und die ALTE UTC-Form ``{"open_utc", "close_utc"}`` (WARNING
    ``SESSION_WINDOW_UTC_LEGACY``, einmal je Fenster — ein UTC-Fenster ist NYSE-RTH nur in EDT, daher nie
    still als Börsenzeit deuten). Mischformen/unbekannte Felder ⇒ ``SessionWindowConfigError``."""
    if entry is None:
        return None
    if isinstance(entry, SessionWindow):
        return entry
    if not isinstance(entry, dict):
        raise SessionWindowConfigError(
            f"Session-Fenster{f' ({label})' if label else ''}: erwartet dict oder null, "
            f"bekam {type(entry).__name__}.")
    has_new = any(k in entry for k in ("tz", "open", "close"))
    has_legacy = any(k in entry for k in ("open_utc", "close_utc"))
    if has_new and has_legacy:
        raise SessionWindowConfigError(
            f"Session-Fenster{f' ({label})' if label else ''}: neue Form (tz/open/close) und Alt-Form "
            f"(open_utc/close_utc) gemischt: {sorted(entry)}.")
    if has_legacy:
        try:
            open_utc, close_utc = entry["open_utc"], entry["close_utc"]
        except KeyError as exc:
            raise SessionWindowConfigError(
                f"Session-Fenster{f' ({label})' if label else ''}: Alt-Form ohne {exc}.") from None
        key = (label, open_utc, close_utc)
        if key not in _legacy_warned:
            _legacy_warned.add(key)
            logger.warning(
                "%s: session_hours_by_asset_class%s nutzt die UTC-Form %s-%s UTC — das ist Börsenzeit nur "
                "in der Sommerzeit; Umstellung auf {\"tz\": \"America/New_York\", \"open\": \"09:30\", "
                "\"close\": \"16:00\"} (Issue #1356).",
                LEGACY_UTC_EVENT, f"[{label}]" if label else "", open_utc, close_utc)
        return SessionWindow("UTC", open_utc, close_utc, calendar=entry.get("calendar"), legacy_utc=True)
    unknown = sorted(set(entry) - {"tz", "open", "close", "calendar"})
    if unknown:
        raise SessionWindowConfigError(
            f"Session-Fenster{f' ({label})' if label else ''}: unbekannte Felder {unknown}.")
    missing = [k for k in ("tz", "open", "close") if k not in entry]
    if missing:
        raise SessionWindowConfigError(
            f"Session-Fenster{f' ({label})' if label else ''}: Pflichtfelder fehlen: {missing}.")
    calendar = entry.get("calendar") or DEFAULT_CALENDAR_BY_TZ.get(entry["tz"])
    return SessionWindow(entry["tz"], entry["open"], entry["close"], calendar=calendar)


def resolve_session_window(
    asset_class_key: str | None, session_hours_by_asset_class: dict | None,
) -> SessionWindow | None:
    """Auflösung je Asset-Class (Single Source für Backtest-Runner, Sweep-Preflights und Live-Bot): ``None``
    = kein Fenster (Key fehlt, Config fehlt ODER bewusst ``null`` — echter 24/7-Markt)."""
    if not session_hours_by_asset_class or not asset_class_key:
        return None
    return parse_session_window(
        session_hours_by_asset_class.get(asset_class_key), label=asset_class_key)


def load_session_window_for_symbol(symbol: str, config_dir: str | Path | None = None) -> SessionWindow | None:
    """Issue #1361 (GH #1257) — Fenster eines Symbols aus den Config-Dateien (Live-Bot): Asset-Class aus
    ``instrument_map.json`` (dieselbe Lesart wie ``backtest_runner._resolve_asset_class_for_symbol``:
    ``asset_class`` gross geschrieben), Fenster über ``resolve_session_window`` aus
    ``backtest.json['session_hours_by_asset_class']`` — DIESELBE Auflösung wie der Backtest-Runner.
    Unbekanntes Symbol/fehlende Asset-Class ⇒ ``None`` (kein Gate) mit WARNING."""
    base = Path(config_dir) if config_dir else Path(__file__).resolve().parent / "config"
    try:
        instruments = (json.loads((base / "instrument_map.json").read_text("utf-8")) or {}).get("instruments") or {}
        session_cfg = (json.loads((base / "backtest.json").read_text("utf-8")) or {}).get(
            "session_hours_by_asset_class")
    except (OSError, ValueError) as exc:
        logger.warning("Session-Fenster für %s nicht auflösbar (%s) — kein Session-Gate.", symbol, exc)
        return None
    asset_class = None
    for entry in instruments.values():
        if entry.get("symbol") == symbol:
            raw = (entry.get("asset_class") or "").strip()
            asset_class = raw.upper() if raw and raw.lower() != "unknown" else None
            break
    if asset_class is None:
        logger.warning("Session-Fenster: %s hat keine Asset-Class in instrument_map.json — kein Session-Gate.",
                       symbol)
        return None
    return resolve_session_window(asset_class, session_cfg)


# ─── Feiertage ───────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def load_exchange_holidays(path: str | None = None) -> tuple[dict, dict]:
    """``(holidays, coverage)`` aus ``exchange_holidays.json``: ``holidays[calendar] = frozenset[date]``,
    ``coverage[calendar] = (first_year, last_year)``. Fehlende/defekte Datei ⇒ leere Tabellen (fail-open:
    ein Feiertag wird dann wie vor #1356 als Handelstag behandelt) mit WARNING."""
    p = Path(path) if path else HOLIDAYS_PATH
    try:
        raw = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        logger.warning("%s: %s nicht lesbar — Feiertage werden als Handelstage behandelt.",
                       HOLIDAYS_OUT_OF_RANGE_EVENT, p)
        return {}, {}
    holidays = {name: frozenset(date.fromisoformat(d) for d in days)
                for name, days in (raw.get("calendars") or {}).items()}
    coverage = {name: (int(c["first_year"]), int(c["last_year"]))
                for name, c in (raw.get("coverage") or {}).items()}
    return holidays, coverage


_out_of_range_warned: set[tuple] = set()


def is_trading_day(day: date, window: SessionWindow) -> bool:
    """Mo-Fr UND kein Feiertag des ``window.calendar``. Ausserhalb des von der Tabelle abgedeckten
    Jahresbereichs gilt ein Wochentag als Handelstag (+ einmalige WARNING je Kalender/Jahr)."""
    if day.weekday() >= 5:
        return False
    if not window.calendar:
        return True
    holidays, coverage = load_exchange_holidays()
    span = coverage.get(window.calendar)
    if span is not None and not (span[0] <= day.year <= span[1]):
        key = (window.calendar, day.year)
        if key not in _out_of_range_warned:
            _out_of_range_warned.add(key)
            logger.warning(
                "%s: Kalender %s deckt nur %d-%d ab, %s wird als Handelstag behandelt — "
                "automation/config/exchange_holidays.json fortschreiben.",
                HOLIDAYS_OUT_OF_RANGE_EVENT, window.calendar, span[0], span[1], day.isoformat())
        return True
    return day not in holidays.get(window.calendar, frozenset())


# ─── Fenstergrenzen ───────────────────────────────────────────────────────────────────

def _local_wallclock_ns(day: date, minutes: int, tz) -> int:
    """UTC-Integer-Nanosekunden der lokalen Uhrzeit ``day`` + ``minutes`` Minuten (``1440`` = nächste
    Mitternacht). Nicht existierende/mehrdeutige Lokalzeiten (DST-Lücke 02:xx) lösen per ``fold=0``
    deterministisch auf — Börsenfenster liegen ausserhalb dieser Lücken."""
    if minutes >= 24 * 60:
        day = day + timedelta(days=1)
        minutes -= 24 * 60
    local = datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=tz)
    return ((local - _EPOCH_UTC) // _ONE_US) * 1000


@lru_cache(maxsize=8192)
def _bounds(tz_name: str, open_minutes: int, close_minutes: int, ordinal: int) -> tuple[int, int]:
    tz = _tzinfo(tz_name)
    day = date.fromordinal(ordinal)
    return (_local_wallclock_ns(day, open_minutes, tz), _local_wallclock_ns(day, close_minutes, tz))


def session_bounds_utc_ns(day: date, window: SessionWindow) -> tuple[int, int]:
    """``(open_ns, close_ns)`` des Handelstags ``day`` (lokales Kalenderdatum in ``window.tz``) in UTC-
    Integer-Nanosekunden, halboffen ``[open_ns, close_ns)``. Reine Zeitzonenarithmetik — kennt weder
    Wochenende noch Feiertag (dafür ``is_trading_day``)."""
    return _bounds(window.tz, window.open_minutes, window.close_minutes, day.toordinal())


def local_day(ts_ns: int, window: SessionWindow) -> date:
    """Das lokale Kalenderdatum (Börsen-Lokalzeit) des UTC-Zeitpunkts ``ts_ns`` — Ganzzahl-Sekunden, kein
    Float-Rundungsrisiko an Tagesgrenzen."""
    return datetime.fromtimestamp(ts_ns // NS_PER_S, tz=_tzinfo(window.tz)).date()


def is_within_session(ts_ns: int, window: SessionWindow, *, trading_days_only: bool = True) -> bool:
    """Liegt ``ts_ns`` in ``[open, close)`` des lokalen Handelstags? ``trading_days_only`` (Default)
    schliesst Wochenenden UND Feiertage aus."""
    day = local_day(ts_ns, window)
    if trading_days_only and not is_trading_day(day, window):
        return False
    open_ns, close_ns = session_bounds_utc_ns(day, window)
    return open_ns <= ts_ns < close_ns


def interval_overlaps_session(
    start_ns: int, end_ns: int, window: SessionWindow, *, trading_days_only: bool = True,
) -> bool:
    """Schneidet das halboffene Intervall ``[start_ns, end_ns)`` (z. B. eine Kerze) das Fenster eines
    Handelstags? Der Handelstag wird in Börsen-LOKALZEIT bestimmt (Issue #1356): geprüft werden alle
    lokalen Kalendertage, die das Intervall berührt. Überlappungstest ``a < d ∧ b > c`` (#1332)."""
    first = local_day(start_ns, window).toordinal()
    last = local_day(max(start_ns, end_ns - 1), window).toordinal()
    for ordinal in range(first, last + 1):
        day = date.fromordinal(ordinal)
        if trading_days_only and not is_trading_day(day, window):
            continue
        open_ns, close_ns = session_bounds_utc_ns(day, window)
        if start_ns < close_ns and end_ns > open_ns:
            return True
    return False


def trading_day_of_candle(
    candle_start_ns: int, candle_end_ns: int, window: SessionWindow,
) -> date | None:
    """Der lokale Handelstag, dessen Session die Kerze ``[candle_start_ns, candle_end_ns)`` schneidet —
    ``None``, wenn sie keine Session schneidet (Pre-/Post-Market, Wochenende, Feiertag). Grundlage des
    Opening-Range-Ankers (Issue #1356): die erste Kerze eines Handelstags ist die, die den LOKALEN Open
    überlappt (in EDT die 13:00-, in EST die 14:00-UTC-Kerze), nie eine UTC-Konstante."""
    first = local_day(candle_start_ns, window).toordinal()
    last = local_day(max(candle_start_ns, candle_end_ns - 1), window).toordinal()
    for ordinal in range(first, last + 1):
        day = date.fromordinal(ordinal)
        if not is_trading_day(day, window):
            continue
        open_ns, close_ns = session_bounds_utc_ns(day, window)
        if candle_start_ns < close_ns and candle_end_ns > open_ns:
            return day
    return None


def session_window_to_param(window: SessionWindow | None) -> str | None:
    """Kanonischer JSON-String des Fensters für das Strategie-Config-Feld ``session_window`` (ein String
    statt eines dicts: ``HourlyStrategyConfig`` ist ein eingefrorener, hashbarer ``msgspec``-Struct)."""
    if window is None:
        return None
    return json.dumps(window.to_config(), sort_keys=True, separators=(",", ":"))


def session_window_from_param(value) -> SessionWindow | None:
    """Gegenstück zu ``session_window_to_param`` (akzeptiert zusätzlich ``dict``/``SessionWindow``/``None``)."""
    if value is None or isinstance(value, (dict, SessionWindow)):
        return parse_session_window(value, label="session_window")
    try:
        return parse_session_window(json.loads(value), label="session_window")
    except ValueError as exc:
        if isinstance(exc, SessionWindowConfigError):
            raise
        raise SessionWindowConfigError(f"session_window={value!r} ist kein JSON-Objekt.") from None


class SessionMask:
    """``mask(ts_ns) -> bool`` — ``is_within_session`` mit einem Tages-Segment-Cache: bei zeitlich sortierten
    Ticks (Katalog-Reihenfolge) kostet ein Tick nur zwei Integer-Vergleiche; der Tageswechsel (lokale
    Mitternacht) berechnet Grenzen/Handelstag-Status neu."""

    def __init__(self, window: SessionWindow, *, trading_days_only: bool = True) -> None:
        self.window = window
        self._trading_days_only = trading_days_only
        self._lo = self._hi = 0
        self._open = self._close = 0

    def _load_day(self, ts_ns: int) -> None:
        w = self.window
        day = local_day(ts_ns, w)
        tz = _tzinfo(w.tz)
        self._lo = _local_wallclock_ns(day, 0, tz)
        self._hi = _local_wallclock_ns(day, 24 * 60, tz)
        if self._trading_days_only and not is_trading_day(day, w):
            self._open = self._close = self._lo     # leeres Fenster: ganzer Tag ausserhalb
        else:
            self._open, self._close = session_bounds_utc_ns(day, w)

    def __call__(self, ts_ns: int) -> bool:
        if not (self._lo <= ts_ns < self._hi):
            self._load_day(ts_ns)
        return self._open <= ts_ns < self._close


# ─── Achsen-Arithmetik ────────────────────────────────────────────────────────────────

def bars_in_session_on_day(day: date, window: SessionWindow, bar_interval_ns: int) -> int:
    """Zahl der epoch-ausgerichteten Bar-Intervalle ``[k·Δ, (k+1)·Δ)``, die das Fenster von ``day`` schneiden
    (die Aggregatoren richten Zeitbars an der UTC-Epoche aus). Reine Arithmetik, DST-exakt für ``day``."""
    open_ns, close_ns = session_bounds_utc_ns(day, window)
    first_start = open_ns - (open_ns % bar_interval_ns)
    return -(-(close_ns - first_start) // bar_interval_ns)


def bars_per_trading_day(
    window: SessionWindow, bar_interval_ns: int = NS_PER_HOUR, *, day: date | None = None,
) -> int:
    """Bars je Handelstag. Mit ``day``: exakt für diesen Tag. Ohne ``day``: der Wert muss im Sommer- UND im
    Winterzeit-Referenztag derselbe sein (``BARS_PER_TRADING_DAY`` ist eine Konstante der Bar-Achse) —
    sonst ``SessionWindowConfigError`` (eine DST-abhängige Achse darf nie als EINE Zahl gelten).
    NYSE 09:30-16:00 ET auf 1h-Bars: 7 in EDT UND EST."""
    if day is not None:
        return bars_in_session_on_day(day, window, bar_interval_ns)
    counts = {d.isoformat(): bars_in_session_on_day(d, window, bar_interval_ns) for d in _REFERENCE_DAYS}
    if len(set(counts.values())) != 1:
        raise SessionWindowConfigError(
            f"Bars je Handelstag sind DST-abhängig ({counts}) für {window.describe()} bei "
            f"Δ={bar_interval_ns // NS_PER_S}s — keine einzelne Konstante.")
    return next(iter(counts.values()))


def count_trading_days(first_day: date, last_day: date, window: SessionWindow) -> int:
    """Handelstage (Mo-Fr, ohne Feiertage des Kalenders) in ``[first_day, last_day]`` inklusive."""
    n = 0
    for ordinal in range(first_day.toordinal(), last_day.toordinal() + 1):
        if is_trading_day(date.fromordinal(ordinal), window):
            n += 1
    return n


def expected_trading_day_fraction(window: SessionWindow) -> float:
    """Issue #1367 (GH #1264) — langjähriger Anteil der Handelstage an den Kalendertagen: über die von der
    Feiertagstabelle abgedeckten Jahre gezählt (NYSE 2025-2027 ≈ 0,687 ≈ 251/365); ohne Kalender 5/7."""
    if not window.calendar:
        return 5.0 / 7.0
    _, coverage = load_exchange_holidays()
    span = coverage.get(window.calendar)
    if span is None:
        return 5.0 / 7.0
    first, last = date(span[0], 1, 1), date(span[1], 12, 31)
    total = last.toordinal() - first.toordinal() + 1
    return count_trading_days(first, last, window) / total


def expected_bars_between(
    start_ns: int, end_ns: int, window: SessionWindow, bar_interval_ns: int = NS_PER_HOUR,
) -> int:
    """Erwartete Session-Bars im Zeitraum ``[start_ns, end_ns]`` (inklusive Randtage, lokale Handelstage,
    DST-exakt je Tag) — Nenner von ``bar_coverage_ratio`` und exakte ``T_holdout``-Bar-Zahl."""
    total = 0
    for ordinal in range(local_day(start_ns, window).toordinal(), local_day(end_ns, window).toordinal() + 1):
        day = date.fromordinal(ordinal)
        if is_trading_day(day, window):
            total += bars_in_session_on_day(day, window, bar_interval_ns)
    return total


def calendar_days_for_session_bars(n_bars: float, window: SessionWindow, bar_interval_ns: int, end_ns: int) -> int:
    """Issue #1379 (GH #1281) — Kalendertage, die rückwärts ab ``end_ns`` nötig sind, damit das Fenster
    mindestens ``n_bars`` erwartete Session-Bars enthält (lokale Handelstage, Feiertage und Wochenenden
    zählen als Kalendertage ohne Bars). Gegenstück zu ``expected_bars_between`` (dort: Tage ⇒ Bars)."""
    total = 0
    n_days = 0
    ordinal = local_day(int(end_ns), window).toordinal()
    while total < n_bars and n_days < 366 * 20:
        day = date.fromordinal(ordinal)
        if is_trading_day(day, window):
            total += bars_in_session_on_day(day, window, bar_interval_ns)
        n_days += 1
        ordinal -= 1
    return n_days


@lru_cache(maxsize=512)
def min_bars_in_calendar_window(window: SessionWindow, calendar_days: int,
                                bar_interval_ns: int = NS_PER_HOUR) -> int:
    """Issue #1377 (GH #1279) — die KLEINSTE Zahl erwarteter Session-Bars in einem Fenster von
    ``calendar_days`` Kalendertagen, über jede Lage innerhalb der vom Feiertagskalender abgedeckten Jahre
    (ohne Kalender: eine Referenzjahr 2026). Ein 21-Tage-Fenster enthält bei bis zu zwei Feiertagen
    mindestens 13 Handelstage = 91 Bars à 7 (Mittel ≈ 100): die Untergrenze verhindert, dass ein Fold mit
    Feiertag abgewiesen wird, den die Kalenderstunden-Regel zuliess."""
    _, coverage = load_exchange_holidays()
    span = coverage.get(window.calendar) if window.calendar else None
    first_year, last_year = span if span is not None else (2026, 2026)
    # Ab dem Referenzjahr (2026): die Sonderschliessungen früherer Jahre (z. B. der Trauertag 2025-01-09)
    # sind für künftige Fold-Fenster ohne Aussage.
    first_year = max(first_year, _REFERENCE_DAYS[0].year)
    first, last = date(first_year, 1, 1), date(max(first_year, last_year), 12, 31)
    n_days = max(1, int(calendar_days))
    best: int | None = None
    ordinal = first.toordinal()
    while ordinal + n_days - 1 <= last.toordinal():
        total = 0
        for o in range(ordinal, ordinal + n_days):
            day = date.fromordinal(o)
            if is_trading_day(day, window):
                total += bars_in_session_on_day(day, window, bar_interval_ns)
        best = total if best is None else min(best, total)
        ordinal += 1
    return best if best is not None else 0


def snap_window_to_grid(window: SessionWindow, median_delta_t_s: float | None) -> SessionWindow:
    """Issue #1300 (GH #1177) — ``open`` ABWÄRTS, ``close`` AUFWÄRTS auf das nächste Vielfache des beobachteten
    Tick-Rasters (``median_delta_t_s`` auf volle Minuten gerundet, ≥ 1): eine Fenstergrenze darf nie feiner
    aufgelöst sein als das Raster der Daten (Pitfall #463) — ``09:30`` gegen ein Stundenraster verwürfe
    sonst die erste Session-Bar. Seit #1356 in LOKALZEIT gesnappt (NY 09:30→09:00, 16:00→16:00 ⇒ in
    EDT 13:00-20:00 UTC, in EST 14:00-21:00 UTC: je 7 Kerzenstarts). Kein Raster ⇒ unverändert."""
    if not median_delta_t_s or median_delta_t_s <= 0:
        return window
    grid = max(1, round(median_delta_t_s / 60.0))
    snapped_open = (window.open_minutes // grid) * grid
    snapped_close = min(24 * 60, -(-window.close_minutes // grid) * grid)
    if snapped_close <= snapped_open:      # entartet — Fenster nie verengen/invertieren
        return window
    return SessionWindow(
        window.tz, f"{snapped_open // 60:02d}:{snapped_open % 60:02d}",
        f"{snapped_close // 60:02d}:{snapped_close % 60:02d}",
        calendar=window.calendar, legacy_utc=window.legacy_utc)


# ─── Alt-API (HH:MM-UTC-Strings) ──────────────────────────────────────────────────────

@lru_cache(maxsize=256)
def _utc_window(open_utc: str, close_utc: str) -> SessionWindow:
    return SessionWindow("UTC", open_utc, close_utc, legacy_utc=True)


def is_within_session_hours(
    ts_ns: int, open_utc: str, close_utc: str, *, weekdays_only: bool = True,
) -> bool:
    """Alt-API (``HH:MM``-UTC-Strings, vor #1356 die kanonische Implementierung): ``ts_ns`` im UTC-Fenster
    ``[open_utc, close_utc)``; ``weekdays_only`` schliesst Sa/So aus. Dünner Wrapper über ein
    ``tz="UTC"``-Fenster — Börsenzeit-Fenster nutzen ``is_within_session``."""
    return is_within_session(ts_ns, _utc_window(open_utc, close_utc), trading_days_only=weekdays_only)


def interval_overlaps_session_hours(
    interval_start_ns: int, interval_end_ns: int, open_utc: str, close_utc: str,
    *, weekdays_only: bool = True,
) -> bool:
    """Alt-API: schneidet ``[interval_start_ns, interval_end_ns)`` das UTC-Fenster ``[open_utc,
    close_utc)`` (#1332)? Börsenzeit-Fenster nutzen ``interval_overlaps_session``."""
    return interval_overlaps_session(
        interval_start_ns, interval_end_ns, _utc_window(open_utc, close_utc),
        trading_days_only=weekdays_only)
