"""Issue #1249 (Katalog #1352, P0) Regression Tests
=====================================================
``universe_fetcher.run_fetch()`` war die einzige Schreibstelle von ``instrument_map.json`` und
persistierte eToros rohen ``AssetClass``-Wert (bzw. den Fallback-Literal ``"Unknown"``) ungeprüft.
Das blockierte jeden nachfolgenden Sweep-Start mit ``INSTRUMENT_METADATA_INCOHERENT``, Stunden
oder Tage nach dem korrumpierenden Schreibvorgang im täglichen Orchestrator-Lauf.

Diese Tests prüfen:
  1. ``_normalize_asset_class`` bildet eToros Vokabular korrekt auf die kanonischen Buckets ab
     und liefert ``None`` für unbekannte Werte statt zu raten.
  2. ``run_fetch()`` schreibt für ein nicht klassifizierbares ``AssetClass`` ``asset_class: null``
     (nicht das Literal ``"Unknown"``) und protokolliert ``ERROR`` statt ``INFO``.
  3. ``run_fetch()`` ruft nach dem Schreiben ``invariants.check_instrument_metadata_coherence()``
     auf und meldet Verletzungen sofort (fail-loud) im Log, statt sie erst beim nächsten manuellen
     Sweep-Start (``sweep.py::assert_instrument_metadata_coherence``) sichtbar zu machen.
"""
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation.universe_fetcher import _normalize_asset_class, run_fetch


def test_normalize_asset_class_known_values():
    assert _normalize_asset_class("Stocks") == "equity"
    assert _normalize_asset_class("ETF") == "equity"
    assert _normalize_asset_class("Crypto") == "crypto"
    assert _normalize_asset_class("Cryptocurrencies") == "crypto"
    assert _normalize_asset_class("Currencies") == "forex"
    assert _normalize_asset_class("Commodities") == "commodity"
    # Case-insensitive, whitespace-tolerant
    assert _normalize_asset_class("  eQuiTy  ") == "equity"


def test_normalize_asset_class_unknown_returns_none():
    # Root-Cause #1249: ein unbekannter/fehlender Wert darf NICHT als "Unknown"-Literal
    # durchgereicht werden — er bleibt unklassifiziert (None), damit
    # check_instrument_metadata_coherence() ihn korrekt als FEHLEND (nicht FALSCH) behandelt.
    assert _normalize_asset_class("Indices") is None
    assert _normalize_asset_class("Unknown") is None
    assert _normalize_asset_class(None) is None
    assert _normalize_asset_class("") is None


def _mock_portfolio_response(positions):
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"clientPortfolio": {"positions": positions}})
    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_session_cm


@pytest.fixture
def instrument_map_fixture(tmp_path, monkeypatch):
    """Ein instrument_map.json mit einem bereits inkohärenten Alt-Eintrag (size_precision=8,
    asset_class='equity' — strukturell identisch zum #920-Defekt), damit der Post-Write-
    Kohärenz-Check in run_fetch() etwas zum Beanstanden hat."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    instrument_map_path = config_dir / "instrument_map.json"
    instrument_map_path.write_text(json.dumps({
        "instruments": {
            "9999": {
                "symbol": "LEGACY.ETORO",
                "asset_class": "equity",
                "price_precision": 2,
                "size_precision": 8,
            }
        }
    }), encoding="utf-8")

    backtest_path = config_dir / "backtest.json"
    backtest_path.write_text(json.dumps({
        "spread_bps_by_asset_class": {
            "CRYPTO": 15.0, "EQUITY": 3.0, "FOREX": 1.5, "COMMODITY": 5.0, "DEFAULT": 4.0,
        }
    }), encoding="utf-8")

    output_path = tmp_path / "universe.json"
    monkeypatch.setenv("MOMENTUM_LS_USERNAME", "testuser")
    return instrument_map_path, output_path


@pytest.mark.asyncio
async def test_run_fetch_writes_null_for_unclassifiable_asset_class(instrument_map_fixture, caplog):
    instrument_map_path, output_path = instrument_map_fixture
    caplog.set_level(logging.INFO, logger="automation.universe_fetcher")

    positions = [{"instrumentID": "42", "instrumentName": "Mystery Instrument"}]
    metadata = {
        "InstrumentDisplayDatas": [
            {"InstrumentID": 42, "SymbolFull": "MYST", "AssetClass": "SomeNewEtoroCategory"},
        ]
    }

    with patch("automation.universe_fetcher.aiohttp.ClientSession") as mock_session_cls, \
         patch("automation.universe_fetcher.get_etoro_metadata", return_value=metadata):
        mock_session_cls.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(get=MagicMock(return_value=_mock_portfolio_response(positions))))
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_fetch(
            api_key="key", user_key="user",
            output_path=output_path, instrument_map_path=instrument_map_path,
        )

    assert result is True

    written = json.loads(instrument_map_path.read_text(encoding="utf-8"))
    entry = written["instruments"]["42"]
    # asset_class must be null, never the literal "Unknown" string.
    assert entry["asset_class"] is None
    assert entry["symbol"] == "MYST.ETORO"

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("MYST.ETORO" in r.message and "SomeNewEtoroCategory" in r.message
                for r in error_records), "Unresolvable AssetClass must be logged at ERROR level"
    assert not any(r.levelno == logging.INFO and "Resolved 42 ->" in r.message
                   for r in caplog.records), "Unresolvable AssetClass must not be logged as a normal INFO resolution"


@pytest.mark.asyncio
async def test_run_fetch_reports_coherence_violation_after_write(instrument_map_fixture, caplog):
    """Der pre-existing inkohärente Alt-Eintrag (size_precision=8/'equity') muss vom
    Post-Write-Kohärenz-Check in run_fetch() sofort erkannt und fail-loud geloggt werden —
    nicht erst beim naechsten manuellen Sweep-Start."""
    instrument_map_path, output_path = instrument_map_fixture
    caplog.set_level(logging.INFO, logger="automation.universe_fetcher")

    positions = [{"instrumentID": "42", "instrumentName": "Apple"}]
    metadata = {
        "InstrumentDisplayDatas": [
            {"InstrumentID": 42, "SymbolFull": "AAPL", "AssetClass": "Stocks"},
        ]
    }

    with patch("automation.universe_fetcher.aiohttp.ClientSession") as mock_session_cls, \
         patch("automation.universe_fetcher.get_etoro_metadata", return_value=metadata):
        mock_session_cls.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(get=MagicMock(return_value=_mock_portfolio_response(positions))))
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await run_fetch(
            api_key="key", user_key="user",
            output_path=output_path, instrument_map_path=instrument_map_path,
        )

    incoherence_records = [
        r for r in caplog.records
        if r.levelno == logging.ERROR and "INSTRUMENT_METADATA_INCOHERENT" in r.message
    ]
    assert incoherence_records, (
        "run_fetch() must call check_instrument_metadata_coherence() after writing and "
        "report violations fail-loud in the orchestrator log"
    )
    assert "LEGACY.ETORO" in incoherence_records[0].message
