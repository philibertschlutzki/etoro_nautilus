"""Katalog #1352 (GH #1270, Erstfassung GH #1249) — Folge-Fix zu ``universe_fetcher.run_fetch()``.

Der erste Fix (PR #1250) normalisierte nur das Metadaten-Feld ``AssetClass``. Im Produktionslauf
vom 2026-10-04 blieben damit 24 von 24 neu aufgelösten IDs ``asset_class: null`` und wurden im
Backtest per ``unknown_asset_class_policy='reject'`` abgewiesen; bereits geschriebene Alt-Einträge
(``"Unknown"``) wurden nie wieder angefasst.

Diese Tests prüfen:
  1. ``_classify_instrument_metadata`` wertet eToros ``InstrumentTypeID`` aus (PascalCase und
     camelCase), ``AssetClass`` hat Vorrang, Indizes bleiben unklassifiziert.
  2. Ein neu als 'forex'/'crypto' klassifiziertes Symbol bekommt klassenkonsistente Precisions —
     sonst bliebe die blockierende Kohärenzregel ``forex ⇒ price_precision >= 4`` verletzt.
  3. ``run_fetch()`` klassifiziert Bestandseinträge ohne kanonische Klasse nach, lässt manuell
     gesetzte Klassen stehen und meldet verbleibende ``null``-Einträge auf ERROR.
  4. Ohne Handlungsbedarf wird weder die Metadaten-Quelle befragt noch die Map neu geschrieben.
  5. Die committete ``instrument_map.json`` ist bereinigt (Fix Punkt 4) und besteht
     ``assert_instrument_metadata_coherence()``.
"""
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation.universe_fetcher import (
    _CANONICAL_ASSET_CLASSES,
    _classify_instrument_metadata,
    run_fetch,
)

_REPO = Path(__file__).resolve().parents[2]
_SPREADS = {"CRYPTO": 15.0, "EQUITY": 3.0, "FOREX": 1.5, "COMMODITY": 5.0, "DEFAULT": 4.0}


def test_instrument_type_id_classifies_when_asset_class_is_absent():
    assert _classify_instrument_metadata({"InstrumentTypeID": 5}) == "equity"
    assert _classify_instrument_metadata({"InstrumentTypeID": 6}) == "equity"
    assert _classify_instrument_metadata({"InstrumentTypeID": 10}) == "crypto"
    assert _classify_instrument_metadata({"InstrumentTypeID": 1}) == "forex"
    assert _classify_instrument_metadata({"InstrumentTypeID": 2}) == "commodity"
    # camelCase der Public API, Zahl als String
    assert _classify_instrument_metadata({"instrumentTypeID": "5"}) == "equity"


def test_asset_class_takes_precedence_and_unmappable_stays_none():
    assert _classify_instrument_metadata({"AssetClass": "Crypto", "InstrumentTypeID": 5}) == "crypto"
    # unbekannter AssetClass-Wert fällt auf die InstrumentTypeID zurück
    assert _classify_instrument_metadata({"AssetClass": "Unknown", "InstrumentTypeID": 5}) == "equity"
    # Indizes haben keinen Kosten-Bucket
    assert _classify_instrument_metadata({"InstrumentTypeID": 4}) is None
    assert _classify_instrument_metadata({"InstrumentTypeID": "n/a"}) is None
    assert _classify_instrument_metadata({}) is None


def _mock_portfolio_response(positions):
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"clientPortfolio": {"positions": positions}})
    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_session_cm


async def _run(tmp_path, instruments, positions, metadata):
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    instrument_map_path = config_dir / "instrument_map.json"
    instrument_map_path.write_text(json.dumps({"instruments": instruments}), encoding="utf-8")
    (config_dir / "backtest.json").write_text(
        json.dumps({"spread_bps_by_asset_class": _SPREADS}), encoding="utf-8")

    with patch("automation.universe_fetcher.aiohttp.ClientSession") as mock_session_cls, \
         patch("automation.universe_fetcher.get_etoro_metadata", return_value=metadata) as mock_meta:
        mock_session_cls.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(get=MagicMock(return_value=_mock_portfolio_response(positions))))
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        assert await run_fetch(api_key="key", user_key="user", output_path=tmp_path / "universe.json",
                               instrument_map_path=instrument_map_path) is True
    return instrument_map_path, mock_meta


@pytest.fixture(autouse=True)
def _username(monkeypatch):
    monkeypatch.setenv("MOMENTUM_LS_USERNAME", "testuser")


@pytest.mark.asyncio
async def test_new_id_without_asset_class_field_is_classified_from_instrument_type(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="automation.universe_fetcher")
    path, _ = await _run(
        tmp_path, instruments={},
        positions=[{"instrumentID": "1027", "instrumentName": "Merck"}],
        metadata={"InstrumentDisplayDatas": [{"InstrumentID": 1027, "SymbolFull": "MRK", "InstrumentTypeID": 5}]},
    )
    entry = json.loads(path.read_text("utf-8"))["instruments"]["1027"]
    assert entry == {"symbol": "MRK.ETORO", "asset_class": "equity", "price_precision": 2, "size_precision": 2}
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_new_forex_and_crypto_get_class_consistent_precisions(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="automation.universe_fetcher")
    path, _ = await _run(
        tmp_path, instruments={},
        positions=[{"instrumentID": "7", "instrumentName": "EUR/CHF"},
                   {"instrumentID": "100500", "instrumentName": "Newcoin"}],
        metadata={"InstrumentDisplayDatas": [
            {"InstrumentID": 7, "SymbolFull": "EURCHF", "InstrumentTypeID": 1},
            {"InstrumentID": 100500, "SymbolFull": "NEWC", "InstrumentTypeID": 10},
        ]},
    )
    instruments = json.loads(path.read_text("utf-8"))["instruments"]
    assert instruments["7"] == {"symbol": "EURCHF.ETORO", "asset_class": "forex",
                                "price_precision": 5, "size_precision": 5}
    assert instruments["100500"] == {"symbol": "NEWC.ETORO", "asset_class": "crypto",
                                     "price_precision": 2, "size_precision": 8}
    assert not [r for r in caplog.records if "INSTRUMENT_METADATA_INCOHERENT" in r.message]


@pytest.mark.asyncio
async def test_existing_unclassified_entries_are_reclassified_at_the_write_site(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="automation.universe_fetcher")
    instruments = {
        "1021": {"symbol": "INTC.ETORO", "asset_class": "Unknown", "price_precision": 2, "size_precision": 2},
        "1135": {"symbol": "ORCL.ETORO", "asset_class": None, "price_precision": 2, "size_precision": 2},
        "1001": {"symbol": "AAPL.ETORO", "asset_class": "Stocks", "price_precision": 2, "size_precision": 2},
        "27": {"symbol": "SPX500.ETORO", "asset_class": "unknown", "price_precision": 2, "size_precision": 2},
        "5555": {"symbol": "MANUAL.ETORO", "asset_class": "commodity", "price_precision": 5, "size_precision": 5},
    }
    metadata = {"InstrumentDisplayDatas": [
        {"InstrumentID": 1021, "SymbolFull": "INTC", "InstrumentTypeID": 5},
        {"InstrumentID": 1135, "SymbolFull": "ORCL", "InstrumentTypeID": 5},
        {"InstrumentID": 27, "SymbolFull": "SPX500", "InstrumentTypeID": 4},
        {"InstrumentID": 5555, "SymbolFull": "MANUAL", "InstrumentTypeID": 5},
    ]}
    path, mock_meta = await _run(tmp_path, instruments, positions=[], metadata=metadata)

    mock_meta.assert_called_once()
    written = json.loads(path.read_text("utf-8"))["instruments"]
    assert written["1021"]["asset_class"] == "equity"
    assert written["1135"]["asset_class"] == "equity"
    assert written["1001"]["asset_class"] == "equity"       # Rohwert, ohne Metadaten normalisierbar
    assert written["5555"]["asset_class"] == "commodity"    # manuelle Klasse bleibt stehen
    # Index: unklassifizierbar — FEHLEND statt FALSCH, nie wieder das Literal
    assert written["27"]["asset_class"] is None

    errors = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert not [m for m in errors if "INSTRUMENT_METADATA_INCOHERENT" in m]
    assert [m for m in errors if "ohne asset_class" in m and "SPX500.ETORO" in m]
    assert not [m for m in errors if "ohne asset_class" in m and "INTC.ETORO" in m]


@pytest.mark.asyncio
async def test_reclassification_without_metadata_still_removes_the_literal(tmp_path):
    instruments = {
        "1021": {"symbol": "INTC.ETORO", "asset_class": "Unknown", "price_precision": 2, "size_precision": 2},
        "1001": {"symbol": "AAPL.ETORO", "asset_class": "Stocks", "price_precision": 2, "size_precision": 2},
    }
    path, _ = await _run(tmp_path, instruments, positions=[], metadata=None)
    written = json.loads(path.read_text("utf-8"))["instruments"]
    assert written["1021"]["asset_class"] is None
    assert written["1001"]["asset_class"] == "equity"


@pytest.mark.asyncio
async def test_no_metadata_fetch_and_no_rewrite_when_everything_is_classified(tmp_path):
    instruments = {
        "1001": {"symbol": "AAPL.ETORO", "asset_class": "equity", "price_precision": 2, "size_precision": 2},
    }
    raw_before = json.dumps({"instruments": instruments})
    path, mock_meta = await _run(
        tmp_path, instruments, positions=[{"instrumentID": "1001", "instrumentName": "Apple"}], metadata=None)
    mock_meta.assert_not_called()
    assert path.read_text("utf-8") == raw_before


def test_committed_instrument_map_is_cleaned_and_coherent(monkeypatch):
    """Fix Punkt 4: kein Eintrag ohne kanonische Klasse; Akzeptanz: der Sweep-Preflight besteht."""
    instruments = json.loads((_REPO / "automation" / "config" / "instrument_map.json").read_text("utf-8"))["instruments"]
    offenders = {e.get("symbol"): e.get("asset_class") for e in instruments.values()
                 if e.get("asset_class") not in _CANONICAL_ASSET_CLASSES}
    assert not offenders, offenders

    from automation.optimizer import sweep
    monkeypatch.setattr(sweep, "config_dir", lambda: _REPO / "automation" / "config")
    result = sweep.assert_instrument_metadata_coherence()
    assert result is not None and result.passed, result.detail
