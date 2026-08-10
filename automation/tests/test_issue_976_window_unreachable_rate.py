"""Issue #976 (Katalog B, P2) — REJECT_OOS_WINDOW_UNREACHABLE (Warmup-Bedarf des Indikators
überschreitet die verfügbare IS-Historie) ist ein Suchraum-/Bounds-Defekt, kein Laufzeitzustand.
``check_window_unreachable_rate`` ist die Detektions-Hälfte des Fixes.
"""
from automation.optimizer import invariants as inv


def test_passes_within_tolerance():
    result = inv.check_window_unreachable_rate([
        {"strategy": "A", "symbol": "X", "n_trials": 100,
         "is_rejection_detail_counts": {"REJECT_OOS_WINDOW_UNREACHABLE": 2}},
    ], max_fraction=0.05)
    assert result.passed is True


def test_fails_beyond_tolerance():
    result = inv.check_window_unreachable_rate([
        {"strategy": "SqueezeBreakout", "symbol": "GSAT.ETORO", "n_trials": 67,
         "is_rejection_detail_counts": {"REJECT_OOS_WINDOW_UNREACHABLE": 20}},
    ], max_fraction=0.05)
    assert result.passed is False
    assert "SqueezeBreakout/GSAT.ETORO" in result.actual


def test_noop_without_telemetry():
    result = inv.check_window_unreachable_rate([{"strategy": "A", "symbol": "X"}])
    assert result.passed is True
    assert result.actual is None
