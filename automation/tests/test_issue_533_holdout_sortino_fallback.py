"""Issue #533 — Holdout-Gate blockiert bei ``oos_sortino=None`` (Zero-Loss-Fold) valide Strategien.

Der Sortino ist per Definition ``None``, wenn ein OOS-Fold keine Verluste enthaelt
(``losses_count == 0``). Vor dem Fix blockierte das Holdout-Gate eine verlustfreie, profitable
OOS-Periode fälschlich (``None → 0.0``, ``0.0 > 0.0`` = False). Der ``oos_sortino_fallback``-
Mechanismus aus ``reward.py`` (``total_return > 0``) wird jetzt paritätisch auf das Holdout-Gate
ausgedehnt (``automation/optimizer/confirm.py::_holdout_gate_passed``).
"""
from types import SimpleNamespace

from automation.optimizer.confirm import _holdout_gate_passed

RISK_DD_CAP = 0.30


def _m(**kw):
    base = dict(
        oos_evaluated=True,
        oos_eligible=True,
        oos_max_drawdown=0.10,
        oos_sortino=1.0,
        oos_total_return=0.05,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_lossless_profitable_fold_passes_with_fallback():
    # Akzeptanzkriterium 1: sortino=None + oos_total_return>0 + max_dd<=cap ⇒ PASS.
    m = _m(oos_sortino=None, oos_total_return=0.02)
    assert _holdout_gate_passed(m, RISK_DD_CAP, sortino_fallback_enabled=True) is True


def test_lossless_but_unprofitable_fold_fails():
    # Akzeptanzkriterium 2: oos_total_return<=0 ⇒ FAIL (kein Gate-Gaming).
    assert _holdout_gate_passed(
        _m(oos_sortino=None, oos_total_return=0.0), RISK_DD_CAP, sortino_fallback_enabled=True
    ) is False
    assert _holdout_gate_passed(
        _m(oos_sortino=None, oos_total_return=-0.05), RISK_DD_CAP, sortino_fallback_enabled=True
    ) is False


def test_fallback_disabled_keeps_legacy_block():
    # Parität zu reward.py: fehlt/deaktiviert der oos_sortino_fallback, bleibt das Legacy-Verhalten
    # (undefinierter Sortino blockiert) — Micro-Sizing-/Risiko-Gates bleiben wirksam.
    m = _m(oos_sortino=None, oos_total_return=0.02)
    assert _holdout_gate_passed(m, RISK_DD_CAP, sortino_fallback_enabled=False) is False


def test_defined_positive_sortino_passes_independent_of_fallback():
    m = _m(oos_sortino=0.5, oos_total_return=-0.01)  # Sortino definiert & positiv
    assert _holdout_gate_passed(m, RISK_DD_CAP, sortino_fallback_enabled=False) is True
    assert _holdout_gate_passed(m, RISK_DD_CAP, sortino_fallback_enabled=True) is True


def test_defined_nonpositive_sortino_fails():
    # Ein DEFINIERTER, nicht-positiver Sortino faellt weiterhin (Fallback greift nur bei None).
    assert _holdout_gate_passed(
        _m(oos_sortino=0.0, oos_total_return=0.10), RISK_DD_CAP, sortino_fallback_enabled=True
    ) is False
    assert _holdout_gate_passed(
        _m(oos_sortino=-1.0, oos_total_return=0.10), RISK_DD_CAP, sortino_fallback_enabled=True
    ) is False


def test_drawdown_cap_blocks_even_with_fallback():
    # Der harte Risk-DD-Cap bleibt auch im Fallback-Pfad wirksam.
    m = _m(oos_sortino=None, oos_total_return=0.10, oos_max_drawdown=0.50)
    assert _holdout_gate_passed(m, RISK_DD_CAP, sortino_fallback_enabled=True) is False


def test_not_evaluated_or_not_eligible_fails():
    assert _holdout_gate_passed(
        _m(oos_eligible=False, oos_sortino=None, oos_total_return=0.5),
        RISK_DD_CAP, sortino_fallback_enabled=True
    ) is False
    assert _holdout_gate_passed(
        _m(oos_evaluated=False, oos_sortino=None, oos_total_return=0.5),
        RISK_DD_CAP, sortino_fallback_enabled=True
    ) is False
