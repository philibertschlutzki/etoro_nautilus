"""Issue #1071/#1221 (P2, Katalog #1196-1221) — Symbolweite Ranglisten sind nicht kommensurabel.

Symptom: ``check_annualization_commensurability`` failt in 14/14 Läufen. sqrt(F)-Spannweite
zwischen Studies DESSELBEN Symbols: 1,948 (KRYS) bis 5,662 (NATGAS); TSLA driftet über vier
separate Läufe von 2,028 auf 3,866.

Root-Cause: ``sortino_annualized`` wurde vom #823-INFORMATIVEN Annualisierungsfaktor getrieben
(``_informative_annualization_factor``, aus der Anzahl Bars MIT tatsächlicher Rendite — eine
handelsfrequenz-abhängige, nicht symbolweit stabile Größe). Der #980/#1134-Vorlauf-Fix
cachte F nur IN-PROZESS — praktisch wirkungslos, da jeder Backtest in einem frischen
``ProcessPoolExecutor``-Worker läuft (kein langlebiger Prozess, in dem der Cache je träfe).

Fix: F wird jetzt AUSSCHLIESSLICH aus der Bar-ACHSE abgeleitet (``bars_per_calendar_day · 365``,
unabhängig von der Handelsfrequenz einer bestimmten Strategie) UND zusätzlich zum In-Prozess-Cache
in einem WORK-gescopten, plattenpersistenten Store (``annualization_factor_by_symbol.json``)
gehalten — derselbe Wert überlebt damit Worker- UND Lauf-Grenzen. Der #823-informative Faktor
bleibt als separate Telemetrie (``annualization_factor_study_specific``) erhalten.
"""
import json

import pandas as pd
import pytest

from automation import backtest_runner as br


@pytest.fixture(autouse=True)
def _clear_in_process_caches():
    """Jeder Test startet mit einem LEEREN In-Prozess-Cache — simuliert einen frischen
    ProcessPoolExecutor-Worker (die eigentliche Produktionsrealität, siehe Root-Cause oben)."""
    br._annualization_factor_by_symbol_cache.clear()
    br._informative_annualization_factor_by_symbol_cache.clear()
    yield
    br._annualization_factor_by_symbol_cache.clear()
    br._informative_annualization_factor_by_symbol_cache.clear()


def _mtm_series(n_bars: int, *, freq: str = "1h", start: str = "2026-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=n_bars + 1, freq=freq, tz="UTC")
    return pd.Series(range(len(idx)), index=idx, dtype=float)


# ── _bars_per_calendar_day_from_mtm_series ---------------------------------------------------------

def test_bars_per_calendar_day_is_24_for_1h_bars():
    s = _mtm_series(30 * 24)
    assert br._bars_per_calendar_day_from_mtm_series(s) == pytest.approx(24.0, rel=1e-6)


def test_bars_per_calendar_day_stable_across_window_length():
    """Kernaussage des Fixes: dieselbe Bar-Kadenz liefert denselben Wert, unabhängig von der
    Fenstergröße (im Gegensatz zur handelsfrequenz-abhängigen #823-informativen Zahl)."""
    long_window = br._bars_per_calendar_day_from_mtm_series(_mtm_series(60 * 24))
    short_window = br._bars_per_calendar_day_from_mtm_series(_mtm_series(3 * 24))
    assert long_window == pytest.approx(short_window, rel=1e-6)


def test_bars_per_calendar_day_none_without_datetime_index():
    s = pd.Series(range(10), dtype=float)  # RangeIndex, kein Zeit-Index
    assert br._bars_per_calendar_day_from_mtm_series(s) is None


def test_bars_per_calendar_day_none_for_single_bar():
    s = _mtm_series(0)
    assert br._bars_per_calendar_day_from_mtm_series(s) is None


# ── Persistenter Cache -------------------------------------------------------------------------

def test_persisted_cache_round_trips(tmp_path):
    assert br._read_persisted_annualization_factor("TSLA.ETORO", tmp_path) is None
    br._write_persisted_annualization_factor(
        "TSLA.ETORO", 8760.0, "bar_axis_symbol_wide_fresh", tmp_path)
    assert br._read_persisted_annualization_factor("TSLA.ETORO", tmp_path) == (
        8760.0, "bar_axis_symbol_wide_fresh")


def test_persisted_cache_survives_a_simulated_fresh_worker(tmp_path):
    """Kernaussage: ein NEUER 'Prozess' (In-Prozess-Cache manuell geleert, wie zwischen zwei
    ProcessPoolExecutor-Workern) liest denselben Wert aus dem plattenpersistenten Store zurück,
    statt ihn neu (und potenziell abweichend) zu bestimmen."""
    s = _mtm_series(30 * 24)
    factor1, source1 = br._get_annualization_factor_with_source(
        s, symbol="TSLA.ETORO", work_dir=tmp_path)
    assert source1 == "bar_axis_symbol_wide_fresh"

    # Simuliert einen frischen Worker-Prozess: der In-Prozess-Cache ist leer.
    br._annualization_factor_by_symbol_cache.clear()

    factor2, source2 = br._get_annualization_factor_with_source(
        s, symbol="TSLA.ETORO", work_dir=tmp_path)
    assert source2 == "bar_axis_symbol_wide_cached"
    assert factor2 == pytest.approx(factor1, rel=1e-9)


def test_different_window_same_symbol_yields_identical_cached_factor(tmp_path):
    """Das eigentliche #1221-Akzeptanzkriterium: zwei Studies DESSELBEN Symbols mit
    UNTERSCHIEDLICH grossen mtm_series-Fenstern (unterschiedliche Handelsfrequenz/Walk-Forward-
    Konfiguration) erhalten denselben F -- sqrt(F)-Spannweite == 1.0."""
    br._annualization_factor_by_symbol_cache.clear()
    factor_a, _ = br._get_annualization_factor_with_source(
        _mtm_series(90 * 24), symbol="NATGAS.ETORO", work_dir=tmp_path)
    br._annualization_factor_by_symbol_cache.clear()  # neuer "Worker"
    factor_b, _ = br._get_annualization_factor_with_source(
        _mtm_series(4 * 24), symbol="NATGAS.ETORO", work_dir=tmp_path)
    assert factor_a == factor_b
    assert (factor_a ** 0.5) / (factor_b ** 0.5) == pytest.approx(1.0)


def test_config_override_takes_precedence_over_persisted_cache(tmp_path, monkeypatch):
    br._write_persisted_annualization_factor(
        "TSLA.ETORO", 8760.0, "bar_axis_symbol_wide_fresh", tmp_path)
    monkeypatch.setattr(br, "_read_annualization_periods", lambda: 252.0)
    factor, source = br._get_annualization_factor_with_source(
        _mtm_series(24), symbol="TSLA.ETORO", work_dir=tmp_path)
    assert (factor, source) == (252.0, "config_override")


def test_no_time_index_falls_back_to_neutral():
    s = pd.Series(range(10), dtype=float)
    factor, source = br._get_annualization_factor_with_source(s, symbol="TSLA.ETORO")
    assert (factor, source) == (1.0, "neutral_fallback")


def test_corrupt_cache_file_fails_open_to_fresh_computation(tmp_path):
    (tmp_path / "annualization_factor_by_symbol.json").write_text("not valid json", "utf-8")
    factor, source = br._get_annualization_factor_with_source(
        _mtm_series(30 * 24), symbol="TSLA.ETORO", work_dir=tmp_path)
    assert source == "bar_axis_symbol_wide_fresh"
    assert factor == pytest.approx(8760.0, rel=1e-6)


def test_missing_symbol_argument_recomputes_every_call_bit_identical_to_legacy():
    """Legacy-/Direkt-Unit-Aufrufer ohne ``symbol`` bleiben unverändert je Aufruf empirisch (keine
    Cache-Beteiligung) -- nur die FORMEL selbst wechselt (bar_axis statt n_periods/span)."""
    s = _mtm_series(30 * 24)
    factor, source = br._get_annualization_factor_with_source(s, symbol=None)
    assert source == "bar_axis_symbol_wide_fresh"
    assert factor == pytest.approx(24.0 * 365.0, rel=1e-6)
    assert "TSLA.ETORO" not in br._annualization_factor_by_symbol_cache


# ── _informative_annualization_factor no longer drives sortino_annualized -----------------------

def test_informative_factor_is_independent_of_the_bar_axis_factor():
    """Regressionsschutz gegen eine erneute Vermischung: der #823-informative Faktor (wenige
    tatsaechlich rendite-tragende Bars) und der neue bar-achsen-basierte Faktor duerfen fuer
    dieselbe mtm_series unterschiedliche Werte liefern -- sie sind ab #1221 zwei separate Groessen
    (Annualisierung vs. Telemetrie), nicht mehr dieselbe."""
    s = _mtm_series(30 * 24)
    bar_axis_factor, _ = br._get_annualization_factor_with_source(s, symbol=None)
    # n_informative deutlich kleiner als die volle Bar-Zahl (selektive Strategie).
    informative_factor, _ = br._informative_annualization_factor_with_source(
        s, n_informative=30, symbol=None)
    assert bar_axis_factor != pytest.approx(informative_factor)
