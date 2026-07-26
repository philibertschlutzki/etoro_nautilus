"""Issue #775 (P1) — `_read_default_round_trip_cost_bps` nutzt den DEFAULT-Spread, obwohl die
Asset-Class bekannt ist.

Root-Cause: `commission_bps + spread_bps_by_asset_class['DEFAULT']` (5 bps), unabhängig vom Symbol
— für Krypto (16 bps real) 3,2× zu locker, für FOREX (2,5 bps) 2× zu streng. Die Auflösungskette
Symbol → Asset-Class → DEFAULT (`resolve_spread_bps`, #566) existierte bereits; dieser Reader nutzte
sie nicht.

Fix: `_read_default_round_trip_cost_bps(inst_id_str=None)` konsultiert dieselbe Kette wie der
Worker (`_resolve_asset_class_for_symbol` + `resolve_spread_bps`), pro Symbol gecached.
"""
import json

import automation.backtest_runner as br


def _write_backtest_json(tmp_path):
    (tmp_path / "backtest.json").write_text(json.dumps({
        "commission_bps": 1.0,
        "spread_bps_by_asset_class": {"CRYPTO": 15.0, "EQUITY": 3.0, "FOREX": 1.5, "DEFAULT": 4.0},
        "spread_bps_by_symbol": {"TSLA.ETORO": 2.0},
    }))
    (tmp_path / "instrument_map.json").write_text(json.dumps({
        "instruments": {
            "1": {"symbol": "BTC.ETORO", "asset_class": "crypto"},
            "2": {"symbol": "TSLA.ETORO", "asset_class": "equity"},
        }
    }))


def _reset_cache(monkeypatch):
    monkeypatch.setattr(br, "_default_round_trip_cost_bps_cache", {})


def test_crypto_symbol_resolves_to_asset_class_spread(tmp_path, monkeypatch):
    _write_backtest_json(tmp_path)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    _reset_cache(monkeypatch)
    assert br._read_default_round_trip_cost_bps("BTC.ETORO") == 16.0  # 1 + 15


def test_symbol_override_takes_precedence_over_asset_class(tmp_path, monkeypatch):
    _write_backtest_json(tmp_path)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    _reset_cache(monkeypatch)
    assert br._read_default_round_trip_cost_bps("TSLA.ETORO") == 3.0  # 1 + 2 (Symbol-Override)


def test_unknown_symbol_falls_back_to_default_asset_class(tmp_path, monkeypatch):
    _write_backtest_json(tmp_path)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    _reset_cache(monkeypatch)
    assert br._read_default_round_trip_cost_bps("UNKNOWN.ETORO") == 5.0  # 1 + 4 (DEFAULT)


def test_call_without_symbol_is_bit_identical_to_head(tmp_path, monkeypatch):
    """Akzeptanzkriterium #775/2: Aufruf ohne Symbol ⇒ bit-identisch zu HEAD (DEFAULT-Asset-Class)."""
    _write_backtest_json(tmp_path)
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    _reset_cache(monkeypatch)
    assert br._read_default_round_trip_cost_bps() == 5.0  # 1 + 4 (DEFAULT), wie Pre-#775


def test_per_symbol_caching_avoids_repeated_file_io(tmp_path, monkeypatch):
    """Akzeptanzkriterium #775/3: kein zusaetzliches File-I/O pro Trial (Cache-Trefferquote)."""
    _write_backtest_json(tmp_path)
    read_count = {"n": 0}
    real_open = open

    def _counting_open(path, *a, **k):
        if str(path).endswith("backtest.json"):
            read_count["n"] += 1
        return real_open(path, *a, **k)

    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    _reset_cache(monkeypatch)
    monkeypatch.setattr("builtins.open", _counting_open)

    for _ in range(5):
        assert br._read_default_round_trip_cost_bps("BTC.ETORO") == 16.0
    # instrument_map.json + backtest.json je einmal gelesen (nur beim ERSTEN Aufruf, danach Cache-Hit).
    assert read_count["n"] == 1
