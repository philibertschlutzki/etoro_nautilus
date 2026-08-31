"""Issue #1213 (Katalog-Nummern #1209-#1211, P1) — ``.astype(float)`` auf undekodierten
Preis-Spalten, fail-open in zwei Pfaden.

Symptom. ``sweep._load_symbol_bar_quality_sample`` und ``backtest_runner.
_quick_median_price_from_catalog`` lesen ``bid_price``/``ask_price`` per rohem
``pyarrow.parquet``-Zugriff und casten direkt mit ``.astype(float)`` — der reale Katalog speichert
diese Spalten jedoch als Nautilus ``FixedSizeBinary(16)`` (High-Precision-i128-Build, siehe
``automation._serde``/``automation.catalog_service``/``automation.api_backfiller``), kein
float/decimal. ``.astype(float)`` wirft ``ValueError: could not convert string to float``, beide
Pfade fangen das breit und geben fail-open ``None`` zurück.

Root-Cause. Beide Lesepfade sind bewusst OHNE die volle NautilusTrader-``ParquetDataCatalog``-
Materialisierung gebaut (Performance), haben dabei aber nie die Dekodierung des rohen
``pa.binary(16)``-Werts nachgebaut.

Fix. ``automation.catalog_paths.decode_fsb16_price`` (neue, gemeinsame Decode-Hilfsfunktion,
16-Byte little-endian signed int / 10**16 — dieselbe Skala wie ``automation._serde.decode_fsb16``,
hier ohne die ``nautilus_trader``-Abhängigkeit jenes Moduls dupliziert) ersetzt ``.astype(float)``
an BEIDEN Call-Sites (``sweep.py:808``, ``backtest_runner.py:~1194``).
"""
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from automation._serde import decode_fsb16 as _serde_decode_fsb16
from automation._serde import encode_price_fsb16
from automation.catalog_paths import decode_fsb16_price

# Byte-Payloads exakt aus den Rohissues #1209 (TSLA.ETORO) und #1210 (NVDA.ETORO) — der erste
# Trial, der beim allerersten ``.astype(float)``-Aufruf abbrach.
_TSLA_RAW = b"\x00\xa0h\xbc\xddi\xeb%\x00\x00\x00\x00\x00\x00\x00\x00"
_NVDA_RAW = b"\x00\x90e?]\x1bB\x06\x00\x00\x00\x00\x00\x00\x00\x00"


# ---------------------------------------------------------------------------------------------
# decode_fsb16_price — reine Dekodierformel gegen die Rohissue-Payloads (Akzeptanzkriterium 2).
# ---------------------------------------------------------------------------------------------

def test_astype_float_on_the_raw_issue_payload_reproduces_the_reported_crash():
    with pytest.raises(ValueError):
        float(_TSLA_RAW)


def test_decodes_tsla_payload_to_a_plausible_price():
    price = decode_fsb16_price(_TSLA_RAW)
    assert price == pytest.approx(273.2394, abs=1e-9)
    assert 1.0 < price < 10_000.0  # Sanity-Check, kein fehlerfrei laufender Fehl-Skalierer.


def test_decodes_nvda_payload_to_a_plausible_price():
    price = decode_fsb16_price(_NVDA_RAW)
    assert price == pytest.approx(45.0953, abs=1e-9)
    assert 1.0 < price < 10_000.0


def test_decode_fsb16_price_matches_automation_serde_decode_fsb16():
    """Regressionsschutz: die hier (nautilus_trader-frei) duplizierte Formel MUSS bit-identisch
    zu automation._serde.decode_fsb16 bleiben (siehe dortigen Build-Guard-Assert)."""
    for raw in (_TSLA_RAW, _NVDA_RAW):
        assert decode_fsb16_price(raw) == _serde_decode_fsb16(raw, precision=2)


def test_decode_is_roundtrip_correct_against_encode_price_fsb16():
    encoded = encode_price_fsb16(123.45, 2)
    assert decode_fsb16_price(encoded) == pytest.approx(123.45, abs=1e-9)


# ---------------------------------------------------------------------------------------------
# _load_symbol_bar_quality_sample / _quick_median_price_from_catalog — end-to-end gegen echte
# FixedSizeBinary(16)-Parquet-Stichproben (Akzeptanzkriterien 1, 3).
# ---------------------------------------------------------------------------------------------

_FSB16 = pa.binary(16)
_NS_PER_HOUR = 3_600_000_000_000


def _write_fsb16_quote_tick_parquet(path, ts_ns_list, price=100.0, price_precision=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(ts_ns_list)
    table = pa.table({
        "bid_price": pa.array([encode_price_fsb16(price, price_precision)] * n, type=_FSB16),
        "ask_price": pa.array([encode_price_fsb16(price + 0.02, price_precision)] * n, type=_FSB16),
        "ts_event": pa.array(ts_ns_list, type=pa.int64()),
    })
    meta = {b"price_precision": str(price_precision).encode(), b"size_precision": b"2"}
    pq.write_table(table.replace_schema_metadata(meta), str(path))


def test_load_symbol_bar_quality_sample_decodes_real_fsb16_catalog_data(tmp_path):
    from automation.optimizer import sweep

    ts_list = [i * _NS_PER_HOUR for i in range(48)]
    _write_fsb16_quote_tick_parquet(
        tmp_path / "data" / "quote_tick" / "TSLA.ETORO" / "data.parquet", ts_list, price=273.24,
    )
    sample = sweep._load_symbol_bar_quality_sample("TSLA.ETORO", catalog_path=tmp_path)
    assert sample is not None
    # mid = (bid + ask) / 2, bid=273.24, ask=273.26 -> 273.25, plausibel gegen den TSLA-Referenzpreis.
    assert all(pytest.approx(273.25, abs=0.01) == h for h in sample["highs"])


def test_quick_median_price_from_catalog_decodes_real_fsb16_catalog_data(tmp_path):
    from automation.backtest_runner import _quick_median_price_from_catalog

    ts_list = [i * _NS_PER_HOUR for i in range(10)]
    _write_fsb16_quote_tick_parquet(
        tmp_path / "data" / "quote_tick" / "NVDA.ETORO" / "data.parquet", ts_list, price=45.10,
    )
    median_price = _quick_median_price_from_catalog(tmp_path, "NVDA.ETORO")
    assert median_price is not None
    assert median_price > 0
    assert median_price == pytest.approx(45.11, abs=0.01)


def test_quick_median_price_from_catalog_returns_none_on_missing_file(tmp_path):
    from automation.backtest_runner import _quick_median_price_from_catalog

    assert _quick_median_price_from_catalog(tmp_path, "GHOST.ETORO") is None
