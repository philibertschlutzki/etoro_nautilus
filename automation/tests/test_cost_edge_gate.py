"""Kosten-Edge-Gate: Krypto-Entries nur bei ATR, die die Round-Trip-Kosten tragen kann."""
from __future__ import annotations

import pytest

from automation.live_params import resolve_live_entry_cost_gate


def test_crypto_needs_far_higher_atr_than_equity_or_fx():
    crypto = resolve_live_entry_cost_gate("BTC.ETORO")
    equity = resolve_live_entry_cost_gate("AAPL.ETORO")
    fx = resolve_live_entry_cost_gate("EURUSD.ETORO")
    need = lambda g: g["entry_min_atr_to_cost_ratio"] * g["entry_round_trip_cost_bps"]
    assert need(crypto) > 80.0            # nur bei echten Volatilitaetsphasen
    assert need(equity) < 15.0 and need(fx) < 5.0


def test_gate_fail_open_and_disable(monkeypatch):
    from pathlib import Path
    from automation import backtest_runner as br
    assert resolve_live_entry_cost_gate("BTC.ETORO", ratio=0.0) == {}
    monkeypatch.setattr(br, "_read_default_round_trip_cost_bps", lambda s=None: (_ for _ in ()).throw(OSError()))
    assert resolve_live_entry_cost_gate("BTC.ETORO") == {}
