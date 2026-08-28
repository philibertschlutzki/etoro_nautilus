"""Issue #1302 (GH #1179) — Single Source of Truth für die Auflösung von Quote-Tick-Parquet-
Dateien und ihrer Spaltennamen im NautilusTrader-Katalog-Layout.

Vor diesem Modul konstruierten fünf unabhängige Call-Sites (``backtest_runner.
normalize_parquet_metadata``, ``backtest_runner._quick_median_price_from_catalog``,
``backtest_runner.read_precisions_from_parquet``, ``sweep.count_available_bars``,
``sweep._load_symbol_bar_quality_sample``) den Pfad ``.../data/quote_tick/<symbol>/data.parquet``
jeweils selbst — fiel eine Annahme (Dateiname, Spaltenname) aus, fielen nicht alle aus, was einen
Katalog-Layout-Wechsel (z. B. ``data.parquet`` → ``part-*.parquet``, siehe Issue #1301/GH #1178)
unauflösbar an einer der fünf Stellen scheitern liess, während die anderen vier weiterliefen.

BEWUSST ohne ``nautilus_trader``-, ``optuna``- oder ``pandas``-Abhängigkeit (analog
``automation/optimizer/_contracts.py``) — importierbar aus ``sweep.py`` UND ``backtest_runner.py``,
beide mit unterschiedlichen, teils schweren Import-Graphen."""

from __future__ import annotations

from pathlib import Path

# Reihenfolge ist Präferenzreihenfolge: der klassische Einzeldatei-Name zuerst, dann die
# NautilusTrader-typischen partitionierten Layouts. ``*.parquet`` als letzter, weitester Fallback.
_QUOTE_TICK_GLOB_PATTERNS: tuple[str, ...] = ("data.parquet", "part-*.parquet", "*.parquet")

# Spalten-Alias-Tabelle (Issue #1301/GH #1178 Fix Punkt 2) — kanonischer Name -> akzeptierte
# Alias-Reihenfolge (erster Treffer im Schema gewinnt).
_QUOTE_TICK_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "bid_price": ("bid_price", "bid"),
    "ask_price": ("ask_price", "ask"),
    "ts_event": ("ts_event", "ts_init", "timestamp"),
}


def resolve_quote_tick_files(catalog_path: str | Path, symbol: str) -> list[Path]:
    """Löst die Quote-Tick-Parquet-Datei(en) für ``symbol`` im Katalog unter ``catalog_path`` auf.

    Probiert die Glob-Muster in ``_QUOTE_TICK_GLOB_PATTERNS`` der Reihe nach im Instrument-
    Verzeichnis (``.../data/quote_tick/<symbol>/``); das erste Muster mit mindestens einem Treffer
    gewinnt (kein Vermischen der Muster). Innerhalb eines Musters werden die Treffer sortiert
    zurückgegeben (deterministische Row-Group-Reihenfolge für ``part-*.parquet``-Layouts).

    Leere Liste, wenn das Instrument-Verzeichnis fehlt oder kein Muster einen Treffer liefert —
    kein Fehler, der Aufrufer entscheidet über Fail-open/Fail-loud."""
    inst_dir = Path(catalog_path) / "data" / "quote_tick" / str(symbol)
    if not inst_dir.is_dir():
        return []
    for pattern in _QUOTE_TICK_GLOB_PATTERNS:
        matches = sorted(inst_dir.glob(pattern))
        if matches:
            return matches
    return []


def resolve_quote_tick_columns(schema_names) -> dict[str, str] | None:
    """Löst die kanonischen Spaltennamen (``bid_price``, ``ask_price``, ``ts_event``) gegen die
    tatsächlichen Spalten eines Parquet-Schemas (``schema_names``, iterable von str) auf.

    Rückgabe ``dict[kanonischer_name, tatsächlicher_name]`` nur, wenn ALLE drei kanonischen Namen
    einen Treffer haben — ``None``, wenn mindestens einer fehlt (der Aufrufer entscheidet über den
    Fehlerpfad, siehe Issue #1301/GH #1178 Fix Punkt 3: ``COLUMNS_MISSING``)."""
    available = set(schema_names)
    resolved: dict[str, str] = {}
    for canonical, aliases in _QUOTE_TICK_COLUMN_ALIASES.items():
        hit = next((alias for alias in aliases if alias in available), None)
        if hit is None:
            return None
        resolved[canonical] = hit
    return resolved
