"""Katalog #1353 (GH #1271, Erstfassung GH #1248) — Nachzieh-Pflicht für die bewusst unabhängige
Zweitimplementierung (Pitfall #482).

``verify_symbol_data.resolve_quote_tick_files`` ist absichtlich eine eigenständige Kopie von
``automation.catalog_paths.resolve_quote_tick_files`` (keine ``automation.*``-Imports). Der Preis
dafür war #1353: nach dem Layout-Wechsel #1331 meldete das Werkzeug 162/162 Symbole als datenlos,
ohne dass irgendein Import-Fehler die Divergenz anzeigte. Dieser Test macht die Divergenz zu einem
Testfehler: beide Auflöser müssen auf jedem bekannten Katalog-Layout dieselben Dateien liefern,
und das Werkzeug bleibt importfrei.
"""
import ast
from pathlib import Path

import pytest

from automation import catalog_paths
from automation.tests import verify_symbol_data

_LAYOUTS = {
    "interval_only": ["TSLA.ETORO/OneHour/data.parquet"],
    "flat_legacy": ["TSLA.ETORO/data.parquet"],
    "interval_and_flat": ["TSLA.ETORO/OneHour/data.parquet", "TSLA.ETORO/data.parquet"],
    "all_resolutions_and_realtick": ["TSLA.ETORO/OneHour/data.parquet", "TSLA.ETORO/OneDay/data.parquet",
                                     "TSLA.ETORO/RealTick/data.parquet"],
    "other_resolution_only": ["TSLA.ETORO/OneDay/data.parquet"],
    "partitioned_parts": ["TSLA.ETORO/OneHour/part-0.parquet", "TSLA.ETORO/OneHour/part-1.parquet"],
    "empty_interval_dir_with_flat": ["TSLA.ETORO/OneHour/", "TSLA.ETORO/data.parquet"],
    "missing_symbol": [],
}


def _build(root: Path, entries: list[str]) -> Path:
    base = root / "data" / "quote_tick"
    base.mkdir(parents=True)
    for rel in entries:
        target = base / rel
        if rel.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
    return root


@pytest.mark.parametrize("layout", sorted(_LAYOUTS))
@pytest.mark.parametrize("interval", ["OneHour", "OneDay", "RealTick"])
def test_verify_tool_resolves_the_same_files_as_the_pipeline(tmp_path, layout, interval):
    catalog = _build(tmp_path, _LAYOUTS[layout])
    expected = catalog_paths.resolve_quote_tick_files(catalog, "TSLA.ETORO", interval=interval)
    actual = verify_symbol_data.resolve_quote_tick_files(catalog, "TSLA.ETORO", interval)
    assert actual == expected


def test_default_interval_matches_the_pipeline_default():
    import inspect

    from automation import bar_axis

    # Issue #1382: der Pipeline-Default ist die Katalog-Auflösung der AKTIVEN Bar-Achse (``None`` ⇒ bar_axis);
    # das Stand-alone-Tool (keine automation.*-Imports) behält den Literal-Default der Produktionsachse.
    assert inspect.signature(catalog_paths.resolve_quote_tick_files).parameters["interval"].default is None
    pipeline = bar_axis.active_axis().catalog_interval
    tool = inspect.signature(verify_symbol_data.resolve_quote_tick_files).parameters["interval"].default
    assert tool == pipeline == bar_axis.DEFAULT_AXIS


def test_verify_tool_stays_independent_of_the_pipeline_modules():
    """Die Design-Prämisse aus dem Moduldocstring: keine automation.*-Imports."""
    tree = ast.parse(Path(verify_symbol_data.__file__).read_text("utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not {m for m in imported if m == "automation" or m.startswith("automation.")}
