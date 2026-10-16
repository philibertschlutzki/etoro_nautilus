"""Post-Fill-Sizing-Deckel im Allocator-Pfad: Basis ist der bestellte Betrag, nicht das schrumpfende Nautilus-Guthaben."""
from __future__ import annotations

from automation.live_risk import compute_sizing_cap_correction, sizing_cap_base_equity


def test_allocator_path_uses_ordered_notional_as_base():
    base = sizing_cap_base_equity(allocated_usd=1900.0, target_fraction=0.10, balance=17435.0)
    assert base == 19000.0


def test_without_allocation_keeps_balance_base():
    assert sizing_cap_base_equity(allocated_usd=None, target_fraction=0.15, balance=10000.0) == 10000.0
    assert sizing_cap_base_equity(allocated_usd=0.0, target_fraction=0.15, balance=10000.0) == 10000.0


def test_regression_no_trim_when_balance_shrinks_between_sizing_and_fill():
    """Gemessen am 16.10.: bestellt 1897 USD, Zielbasis aus dem Guthaben nur 1743 ⇒ 154 USD Teilschliessung nach 10 s."""
    realized = 1897.64
    old = compute_sizing_cap_correction(realized_notional=realized, equity_at_entry=17435.386, target_fraction=0.10)
    assert old.correction_needed                                           # bisheriges Verhalten (Fehlalarm)
    base = sizing_cap_base_equity(allocated_usd=1897.0, target_fraction=0.10, balance=17435.386)
    new = compute_sizing_cap_correction(realized_notional=realized, equity_at_entry=base, target_fraction=0.10)
    assert not new.correction_needed


def test_real_slippage_overshoot_is_still_corrected():
    base = sizing_cap_base_equity(allocated_usd=1900.0, target_fraction=0.10, balance=17435.0)
    c = compute_sizing_cap_correction(realized_notional=1900.0 * 1.06, equity_at_entry=base, target_fraction=0.10)
    assert c.correction_needed and c.excess_notional > 0
