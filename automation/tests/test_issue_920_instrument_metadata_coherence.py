"""Issue #920 (Pitfall #298) — 12 Krypto-Symbole trugen asset_class='equity' seit dem #898-Backfill
(size_precision=8 widerlegt das ohne externe Quelle). Fix: die 12 Werte korrigiert + eine
Kohärenz-Invariante, die diese Klasse künftig VOR dem Sweep-Start fängt.
"""
import json

from automation.optimizer import invariants as inv
from automation.optimizer.trial_config import config_dir


def test_check_fails_on_the_920_defect():
    instruments = {
        "100000": {"symbol": "BTC.ETORO", "asset_class": "equity",
                   "price_precision": 2, "size_precision": 8},
    }
    result = inv.check_instrument_metadata_coherence(instruments)
    assert result.passed is False
    assert result.severity == "blocking"
    assert "BTC.ETORO" in result.actual


def test_check_passes_once_corrected():
    instruments = {
        "100000": {"symbol": "BTC.ETORO", "asset_class": "crypto",
                   "price_precision": 2, "size_precision": 8},
    }
    result = inv.check_instrument_metadata_coherence(instruments)
    assert result.passed is True


def test_forex_requires_at_least_four_price_precision():
    instruments = {
        "42": {"symbol": "USDZAR.ETORO", "asset_class": "forex",
              "price_precision": 2, "size_precision": 2},
    }
    result = inv.check_instrument_metadata_coherence(instruments)
    assert result.passed is False
    assert "USDZAR.ETORO" in result.actual


def test_asset_class_without_a_cost_entry_fails():
    instruments = {
        "1": {"symbol": "X.ETORO", "asset_class": "bond",
             "price_precision": 2, "size_precision": 2},
    }
    result = inv.check_instrument_metadata_coherence(
        instruments, spread_bps_by_asset_class={"EQUITY": 3.0, "CRYPTO": 15.0})
    assert result.passed is False
    assert "X.ETORO" in result.actual


def test_empty_instruments_passes_trivially():
    assert inv.check_instrument_metadata_coherence({}).passed is True


# ── real shipped config ─────────────────────────────────────────────────────────────────────────
def test_real_shipped_instrument_map_has_no_crypto_symbols_mislabeled_equity():
    data = json.loads((config_dir() / "instrument_map.json").read_text("utf-8"))
    instruments = data["instruments"]
    backtest_cfg = json.loads((config_dir() / "backtest.json").read_text("utf-8"))
    spread_by_asset_class = backtest_cfg.get("spread_bps_by_asset_class")
    result = inv.check_instrument_metadata_coherence(
        instruments, spread_bps_by_asset_class=spread_by_asset_class)
    assert result.passed is True, result.detail


def test_real_shipped_instrument_map_has_twelve_crypto_symbols():
    from collections import Counter
    data = json.loads((config_dir() / "instrument_map.json").read_text("utf-8"))
    counts = Counter(v.get("asset_class") for v in data["instruments"].values())
    assert counts["crypto"] == 12
    assert counts["equity"] == 130
    assert counts["commodity"] == 2
    assert counts["forex"] == 2


def test_btc_resolves_to_the_crypto_spread(monkeypatch, tmp_path):
    """Akzeptanzkriterium #920: round_trip_cost_bps für BTC.ETORO ist 16.0 (15.0 Spread + 1.0
    Kommission), nicht mehr 4.0 (3.0 Equity-Spread + 1.0)."""
    import automation.backtest_runner as br
    (tmp_path / "instrument_map.json").write_text(
        json.dumps({"instruments": {"100000": {"symbol": "BTC.ETORO", "asset_class": "crypto",
                                                "price_precision": 2, "size_precision": 8}}}),
        encoding="utf-8")
    monkeypatch.setattr(br, "config_dir", lambda: tmp_path)
    asset_class = br._resolve_asset_class_for_symbol("BTC.ETORO")
    assert asset_class == "CRYPTO"
    spread = br.resolve_spread_bps(
        "BTC.ETORO", {"CRYPTO": 15.0, "EQUITY": 3.0}, None, asset_class)
    assert spread == 15.0
