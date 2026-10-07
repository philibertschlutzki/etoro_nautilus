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

import os
import shutil
import tempfile
from pathlib import Path

from automation import bar_axis

# Issue #1364 (GH #1260) — Archiv-Verzeichnis des Katalogs. ``--rebuild-catalog`` VERSCHIEBT einen
# Instrument-Katalog hierher (``<catalog>/archive/<UTC-ts>/<symbol>/``), statt ihn zu löschen: Historie
# jenseits der API-Tiefe und Echt-Ticks sind nach einem ``rmtree`` unwiederbringlich verloren.
# Jede automatische Bereinigung (``optimizer/retention.py``, ``optimizer/disk_guard.py``) schliesst
# diesen Pfad über ``is_catalog_archive_path`` aus.
ARCHIVE_DIRNAME = "archive"

# Issue #1354/#1366 (GH #1251/#1263) — Auflösungs-Unterordner der ECHT-Ticks des 24/7-Collectors
# (``catalog_service`` → ``daily_orchestrator._merge_symbol``): ``<symbol>/RealTick/data.parquet`` mit
# ``bar_interval_ns = 0`` und Metadatum ``catalog_interval = "RealTick"``. Die Engine liest diesen Strom
# NIE (``engine_catalog_view`` enthält ausschliesslich die Kerzen-Auflösung); er speist die
# Spread-Kalibrierung (#1366).
REALTICK_INTERVAL = "RealTick"


def catalog_archive_root(catalog_path: str | Path) -> Path:
    """``<catalog_path>/archive`` — Wurzel aller Rebuild-Archive (siehe ``ARCHIVE_DIRNAME``)."""
    return Path(catalog_path) / ARCHIVE_DIRNAME


def is_catalog_archive_path(path: str | Path) -> bool:
    """True, wenn ``path`` im Katalog-Archiv liegt (Komponentenfolge ``nautilus/archive``).

    Reine Pfad-Prüfung ohne Dateisystemzugriff — die einzige Quelle für „darf eine automatische
    Bereinigung diesen Pfad anfassen?" (Antwort bei True: nie)."""
    parts = Path(path).parts
    return any(
        parts[i] == "nautilus" and parts[i + 1] == ARCHIVE_DIRNAME
        for i in range(len(parts) - 1)
    )


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


def resolve_quote_tick_files(
    catalog_path: str | Path, symbol: str, interval: str | None = None
) -> list[Path]:
    """Löst die Quote-Tick-Parquet-Datei(en) für ``symbol`` im Katalog unter ``catalog_path`` auf.

    Seit Issue #1331 (GH #1225) trennt der Backfiller Auflösungen in eigene Unterverzeichnisse
    (``.../data/quote_tick/<symbol>/<interval>/``), damit ``OneHour``- und ``OneDay``-Kerzen nicht
    mehr in einem ununterscheidbaren Tick-Strom landen. Diese Funktion sucht zuerst dort; existiert
    kein solches Unterverzeichnis (Alt-Katalog vor #1331, oder eine Auflösung ohne eigenes
    Unterverzeichnis), fällt sie auf das klassische flache Layout (``.../<symbol>/``) zurück — der
    Optimizer konsumiert per Default ausschliesslich ``OneHour`` (#1331 Fix Punkt 4).

    Probiert die Glob-Muster in ``_QUOTE_TICK_GLOB_PATTERNS`` der Reihe nach; das erste Muster mit
    mindestens einem Treffer gewinnt (kein Vermischen der Muster). Innerhalb eines Musters werden
    die Treffer sortiert zurückgegeben (deterministische Row-Group-Reihenfolge für
    ``part-*.parquet``-Layouts).

    Leere Liste, wenn das Instrument-Verzeichnis fehlt oder kein Muster einen Treffer liefert —
    kein Fehler, der Aufrufer entscheidet über Fail-open/Fail-loud."""
    interval = interval or bar_axis.active_axis().catalog_interval
    inst_dir = Path(catalog_path) / "data" / "quote_tick" / str(symbol)
    if not inst_dir.is_dir():
        return []

    interval_dir = inst_dir / str(interval)
    if interval_dir.is_dir():
        for pattern in _QUOTE_TICK_GLOB_PATTERNS:
            matches = sorted(interval_dir.glob(pattern))
            if matches:
                return matches

    for pattern in _QUOTE_TICK_GLOB_PATTERNS:
        matches = sorted(inst_dir.glob(pattern))
        if matches:
            return matches
    return []


# Issue #1354 (GH #1251, P0) — die Sicht, die die Backtest-ENGINE liest.
#
# NautilusTraders ``ParquetDataCatalog.get_file_list_from_data_cls`` globbt rekursiv ``data/quote_tick/
# **/*.parquet``; ``filter_files`` vergleicht danach ``file_path.split("/")[-2]`` EXAKT mit der
# Instrument-ID. Für ``…/TSLA.ETORO/OneHour/data.parquet`` ist dieser Wert ``"OneHour"`` ⇒ die Datei
# fällt heraus: seit dem #1331-Layout lud die Engine 0 Stunden-Ticks, während jeder Preflight (der über
# ``resolve_quote_tick_files`` liest) „Daten vorhanden" meldete (Pitfall #483: Preflight und Engine
# müssen denselben Leser benutzen). Die Sicht ist ein temporäres Katalog-Wurzelverzeichnis, in dem
# ``data/quote_tick/<symbol>/data.parquet`` ein Hardlink (Fallback Symlink, Fallback Kopie) auf die von
# ``resolve_quote_tick_files`` gewählte Datei ist — genau das einzige Layout, das NautilusTrader liest.
# ``query(..., files=[...])`` scheidet aus (PyArrow-Pfad, dessen ``Wrangler.from_schema`` jedes
# Schema-Metadatum als Konstruktor-Argument übergibt und an ``catalog_schema_version``/
# ``catalog_interval``/... bricht).

class EngineCatalogViewError(RuntimeError):
    """Für ``symbol``/``interval`` existiert keine Quote-Tick-Datei, aus der eine Sicht gebaut werden kann."""


class EngineCatalogView:
    """Temporäres, von der Engine lesbares Katalog-Wurzelverzeichnis (siehe oben). Kontextmanager;
    ``close()`` räumt auf (idempotent). ``link_kind`` ∈ ``{"hardlink", "symlink", "copy"}``."""

    def __init__(self, root: Path, symbol: str, source: Path, link_kind: str) -> None:
        self.root = Path(root)
        self.symbol = symbol
        self.source = Path(source)
        self.link_kind = link_kind

    @property
    def data_file(self) -> Path:
        return self.root / "data" / "quote_tick" / self.symbol / "data.parquet"

    def replace_data_file(self, writer) -> None:
        """Schreibt die Sicht-Datei NEU (``writer(path)``), ohne je den Originalkatalog zu berühren:
        ein Hardlink teilt den Inode mit dem Original — ein direktes Überschreiben würde das Original
        mitverändern, daher wird der Link vorher gelöst. Die Precision-Normalisierung
        (``backtest_runner``) läuft ausschliesslich über diesen Weg."""
        target = self.data_file
        target.unlink()
        self.link_kind = "copy"
        writer(target)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "EngineCatalogView":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def engine_catalog_view(
    catalog_path: str | Path, symbol: str, interval: str | None = None,
) -> EngineCatalogView:
    """Baut die Engine-Sicht für ``symbol`` (Hardlink → Symlink → Kopie). Wirft
    ``EngineCatalogViewError`` ohne Quelldatei. Als ``with``-Block oder mit explizitem ``close()``
    verwenden. Die Sicht enthält GENAU EINE Datei: die Auflösung ``interval`` (Default: die Katalog-
    Auflösung der aktiven Bar-Achse, ``bar_axis``; OneHour) — nie Zeilen einer anderen Auflösung oder Echt-Ticks
    (``RealTick/``, #1366)."""
    interval = interval or bar_axis.active_axis().catalog_interval
    files = resolve_quote_tick_files(catalog_path, symbol, interval=interval)
    if not files:
        raise EngineCatalogViewError(
            f"Keine Quote-Tick-Datei für {symbol}/{interval} unter {catalog_path} — keine Engine-Sicht.")
    source = files[0]
    root = Path(tempfile.mkdtemp(prefix="nautilus_engine_view_"))
    dst_dir = root / "data" / "quote_tick" / str(symbol)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "data.parquet"
    try:
        try:
            os.link(source, dst)
            kind = "hardlink"
        except OSError:
            try:
                os.symlink(source.resolve(), dst)
                kind = "symlink"
            except OSError:
                shutil.copy2(source, dst)
                kind = "copy"
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return EngineCatalogView(root, str(symbol), source, kind)


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


# Issue #1213 (Katalog-Nummern #1209-#1211) — dieselbe Skala wie ``automation._serde._RAW_SCALE``
# (Build-Guard dort: ``Price.from_str("1").raw == 10**16``, High-Precision-i128-Build). Hier
# EIGENSTAENDIG dupliziert statt ``automation._serde`` importiert: dieses Modul bleibt bewusst frei
# von ``nautilus_trader`` (siehe Moduldocstring), ``_serde.py`` importiert
# ``nautilus_trader.model.objects`` fuer seinen Build-Guard-Assert. Die Skala ist ein globales
# Build-Merkmal des Katalogs, NICHT von der je Instrument gespeicherten ``price_precision``
# abhaengig (dieselbe Konstante fuer jeden Aufrufer von ``_encode_fsb16``/``_to_fsb16``,
# unabhaengig vom uebergebenen, nur fuer die Rundung vor der Skalierung relevanten
# ``precision``-Parameter jener Funktionen) — ``read_precisions_from_parquet`` ist fuer DIESE
# Dekodierung deshalb nicht erforderlich.
_FSB16_SCALE = 10 ** 16


def decode_fsb16_price(raw: bytes) -> float:
    """Dekodiert einen rohen ``pa.binary(16)``-Preiswert (Nautilus FixedSizeBinary(16),
    High-Precision-i128-Build) in den dezimalen Preis: 16-Byte little-endian signed int, Skala
    ``_FSB16_SCALE``. Root-Cause #1213 — ``bid_price``/``ask_price`` aus einem rohen
    ``pyarrow``-Zugriff (OHNE die volle ``ParquetDataCatalog``-Materialisierung, siehe
    ``resolve_quote_tick_files``-Docstring) sind dieser rohe Byte-Wert, kein float/decimal —
    ``.astype(float)`` darauf wirft ``ValueError: could not convert string to float``."""
    return int.from_bytes(raw[:16], "little", signed=True) / _FSB16_SCALE
