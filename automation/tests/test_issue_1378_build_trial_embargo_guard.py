"""Issue #1378 (GH #1280, Pitfall #499) — der No-Clamping-Guard in ``build_trial`` prueft die Spanne
MIT Embargo (Produktionsgeometrie: required 444 d)."""
import datetime as dt

import pytest

from automation.optimizer import trial_config as tc
from automation.optimizer.gate import InsufficientGeometryError, required_span_days

_NOW = dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc)


def _build(span, tmp_path):
    return tc.build_trial(
        "SmaCrossoverStrategy", {}, study_name="issue1378", trial_number=0, seed=1, now=_NOW,
        catalog_span_days=span,
    )


def test_span_430_raises_with_required_444(tmp_path):
    wf = tc.resolve_wf_settings()
    assert required_span_days(wf) == 444
    with pytest.raises(InsufficientGeometryError) as exc:
        _build(430, tmp_path)
    assert exc.value.required == 444


def test_span_444_does_not_raise(tmp_path):
    _build(444, tmp_path)
