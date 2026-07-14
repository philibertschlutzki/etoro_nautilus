"""Issue #596 — ``required_span_days`` widersprach ``compute_walk_forward_window`` um exakt das Embargo.

``required_span_days`` (Gate/Fail-Loud-Guard) ließ das Embargo weg, ``compute_walk_forward_window``
reserviert es (Issue #548). Der Guard ``assert_walk_forward_geometry`` prüfte damit gegen eine um
``embargo_period_days`` ZU KLEINE Zahl ⇒ ein Symbol mit 405–425 d Historie passierte, obwohl ``start``
vor den Datenanfang fiel und das IS-Fenster still verkürzt wurde (die No-Clamping-Verletzung, die #531
ausschliessen sollte). Fix: das Embargo GEHÖRT in beide Formeln.
"""
import datetime as dt

import pytest

from automation.optimizer.gate import (
    required_span_days, assert_walk_forward_geometry, InsufficientGeometryError,
)
from automation.optimizer.trial_config import compute_walk_forward_window


def test_geometry_source_parity_all_combinations():
    """(end − start).days + holdout_days == required_span_days(wf) für ALLE Kombinationen aus
    embargo ∈ {0, 7, 21} und splits ∈ {1, 2, 4}."""
    now = dt.datetime(2026, 6, 25, tzinfo=dt.timezone.utc)
    for embargo in (0, 7, 21):
        for splits in (1, 2, 4):
            wf = {
                "is_window_days": 180, "oos_window_days": 45, "splits": splits,
                "holdout_days": 45, "embargo_period_days": embargo,
            }
            start, end = compute_walk_forward_window(
                now=now, holdout_days=wf["holdout_days"], is_window_days=wf["is_window_days"],
                oos_window_days=wf["oos_window_days"], n_folds=wf["splits"],
                embargo_period_days=wf["embargo_period_days"],
            )
            assert (end - start).days + wf["holdout_days"] == required_span_days(wf), (
                f"Geometrie-Divergenz bei embargo={embargo}, splits={splits}")


def test_production_geometry_is_426_days():
    """Produktions-Geometrie: 180 + 21 + 4·45 + 45 = 426 (vorher fälschlich 405 ohne Embargo)."""
    wf = {"is_window_days": 180, "oos_window_days": 45, "splits": 4,
          "holdout_days": 45, "embargo_period_days": 21}
    assert required_span_days(wf) == 426


def test_guard_rejects_insufficient_span_with_embargo():
    """assert_walk_forward_geometry wirft bei catalog_span_days=420, embargo=21 (426 nötig)."""
    wf = {"is_window_days": 180, "oos_window_days": 45, "splits": 4,
          "holdout_days": 45, "embargo_period_days": 21}
    with pytest.raises(InsufficientGeometryError):
        assert_walk_forward_geometry(actual_span_days=420, walk_forward_dict=wf)
    # 426 d reichen exakt (>= required).
    assert assert_walk_forward_geometry(actual_span_days=426, walk_forward_dict=wf) == 426
