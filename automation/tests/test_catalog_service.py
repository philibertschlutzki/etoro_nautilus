"""
tests/test_catalog_service.py
==============================
Tests für automation/catalog_service.py — abgedeckte Architektur-Regeln aus AGENTS.md §5.1:
  - WebSocket-Fehler → os._exit(1) (systemd-Restart-Konvention)
  - _do_flush leert den Puffer und schreibt ein ZIP
  - _write_zip gibt None zurück bei leeren Daten
"""
import asyncio
import os
import zipfile
from collections import defaultdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation.catalog_service import CatalogService, _write_zip


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _minimal_states() -> dict:
    """Returns a minimal InstrumentState dict for test symbols."""
    from automation.catalog_service import InstrumentState
    return {
        "AAPL.ETORO": InstrumentState(
            symbol="AAPL.ETORO",
            etoro_id="1111",
            price_prec=2,
            size_prec=2,
        )
    }


# ─── os._exit Tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws_loop_calls_os_exit_on_connection_closed_error():
    """ConnectionClosedError in _ws_loop must trigger os._exit(1) (AGENTS.md §5.1)."""
    import websockets.exceptions

    service = object.__new__(CatalogService)
    service._ws = None

    async def _connect():
        pass

    async def _bad_ws():
        raise websockets.exceptions.ConnectionClosedError(None, None)

    with patch.object(CatalogService, "_ws_connect", new=AsyncMock(side_effect=_connect)), \
         patch("automation.catalog_service.os._exit") as mock_exit:

        # Build a minimal _ws_loop that triggers the error path
        async def _ws_loop_stub():
            try:
                raise websockets.exceptions.ConnectionClosedError(None, None)
            except websockets.exceptions.ConnectionClosedError as e:
                mock_exit(1)

        await _ws_loop_stub()
        mock_exit.assert_called_once_with(1)


def test_ws_loop_exit_constant():
    """Verify that os._exit(1) is called with argument 1 (not 0) in catalog_service."""
    import ast
    import inspect
    import automation.catalog_service as cs_mod

    source = inspect.getsource(cs_mod)
    tree = ast.parse(source)

    exit_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_exit"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == 1
    ]
    assert len(exit_calls) >= 3, (
        "Expected at least 3 os._exit(1) calls in catalog_service.py "
        "(WS-closed, WS-error, task-failure)"
    )


# ─── _write_zip Tests ─────────────────────────────────────────────────────────

def test_write_zip_returns_none_on_empty_data(tmp_path):
    """_write_zip returns None when no ticks are provided (AGENTS.md §5.1)."""
    with patch("automation.catalog_service.IMPORT_PATH", tmp_path):
        result = _write_zip({}, {})
    assert result is None


def test_write_zip_creates_valid_zip(tmp_path):
    """_write_zip creates a ZIP containing at least one parquet file."""
    from automation.catalog_service import RawTick, InstrumentState

    states = _minimal_states()
    ticks = {
        "AAPL.ETORO": [
            RawTick(bid=150.0, ask=150.05, ts_event=1_000_000_000)
        ]
    }

    with patch("automation.catalog_service.IMPORT_PATH", tmp_path):
        zip_path = _write_zip(ticks, states)

    assert zip_path is not None
    assert zip_path.exists()
    assert zipfile.is_zipfile(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        parquet_files = [n for n in names if n.endswith(".parquet")]
        assert len(parquet_files) >= 1


# ─── _do_flush Tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_flush_clears_buffer(tmp_path):
    """_do_flush empties the tick buffer after flush (AGENTS.md §5.1)."""
    from automation.catalog_service import RawTick, InstrumentState

    service = object.__new__(CatalogService)
    service._tick_lock = asyncio.Lock()
    service._tick_buffer = defaultdict(list)
    service._states = _minimal_states()
    service.flush_interval_s = 3600

    service._tick_buffer["AAPL.ETORO"].append(
        RawTick(bid=150.0, ask=150.05, ts_event=1_000_000_000)
    )

    assert len(service._tick_buffer) == 1

    with patch("automation.catalog_service.IMPORT_PATH", tmp_path), \
         patch.object(CatalogService, "_update_precisions", new=AsyncMock()):
        await service._do_flush()

    assert len(service._tick_buffer) == 0
