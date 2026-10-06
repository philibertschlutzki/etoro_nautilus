"""Issue #1367 (GH #1264, P1) — PSR-Erreichbarkeit wurde gegen einen Ausreisser-Kandidaten geprüft: der Preflight
meldete "erreichbar" (``max_attainable_psr = 0.9751`` bei T = 300), obwohl ein 60-Tage-Holdout bei 0,95 nur eine
annualisierte Sharpe ≥ 4,0 zertifizieren kann; ``required_t`` war ein Echo von ``t_holdout``.

Abnahme: T = 300 ⇒ ``mds_annual ∈ [3.99, 4.01]``, ``passed = False`` bei Ziel 1,5; ``reference_sr`` ⇒
``required_t = 212``; keine Literale "45 Tage"/"45-d-Holdout" in ``automation/optimizer/*.py``.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from automation.optimizer import deflation, invariants

_REPO = Path(__file__).resolve().parents[2]
_A = math.sqrt(252 * 7)


def test_t300_mds_is_four_and_fails_the_target():
    res = invariants.check_promotion_confidence_reachability(300, 0.95, target_annual_sharpe=1.5)
    assert 3.99 <= res.actual["mds_annual"] <= 4.01
    assert res.passed is False and res.severity == "blocking"
    assert res.actual["required_t"] == 212
    assert res.actual["reference_sr_historical_outlier"] == pytest.approx(0.11386)
    assert res.actual["required_t_for_target"] == 2124
    assert res.actual["required_holdout_days_for_target"] == pytest.approx(424.8, abs=0.1)


@pytest.mark.parametrize("t,mds_bar,mds_annual", [(300, 0.0953, 4.00), (420, 0.0805, 3.38), (600, 0.0673, 2.83)])
def test_mds_table(t, mds_bar, mds_annual):
    m = deflation.min_detectable_sharpe(t, 0.95)
    assert m == pytest.approx(mds_bar, abs=5e-5)
    assert m * _A == pytest.approx(mds_annual, abs=6e-3)
    # Definition: PSR genau an der Schwelle.
    assert deflation.probabilistic_sharpe_ratio(m, t) >= 0.95
    assert deflation.probabilistic_sharpe_ratio(m * 0.999, t) < 0.95


@pytest.mark.parametrize("sr_annual,bars,days", [(3.0, 533, 107), (2.0, 1196, 239), (1.5, 2124, 425),
                                                 (1.0, 4775, 955)])
def test_required_holdout_for_target_sharpe(sr_annual, bars, days):
    t = deflation.required_periods_for_sharpe(sr_annual / _A, 0.95)
    assert t == bars
    assert round(t / 7 * 7 / 5) == days


def test_reference_sr_minimum_is_212_not_an_echo():
    assert deflation.required_periods_for_sharpe(0.11386, 0.95) == 212
    assert deflation.probabilistic_sharpe_ratio(0.11386, 211) < 0.95


def test_target_comes_from_tournament_json():
    cfg = json.loads((_REPO / "automation/config/tournament.json").read_text("utf-8"))
    assert cfg["promotion_target_annual_sharpe"] == 1.5
    assert cfg["deflation_confidence"] == 0.95                 # Lock aus #1246 bleibt


def test_no_45_day_literals_in_optimizer_modules():
    offenders = []
    for path in sorted((_REPO / "automation/optimizer").glob("*.py")):
        for i, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if re.search(r"45 Tage|45-d-Holdout", line):
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, offenders


def test_report_section_and_log_are_built_from_the_same_values():
    from automation.optimizer import summary_de

    det = {"holdout_days": 60, "t_holdout": 288, "promotion_confidence": 0.95, "mds_bar": 0.0974,
           "mds_annual": 4.09, "promotion_target_annual_sharpe": 1.5, "required_t_for_target": 2124,
           "required_holdout_days_for_target": 424.8, "passed": False}
    text = summary_de._section_detectability({"detectability": det})
    assert "Nachweisbarkeit" in text and "4.09" in text and "2124" in text and "424.8" in text
    sweep_src = (_REPO / "automation/optimizer/sweep.py").read_text("utf-8")
    assert "Mindest-nachweisbare Sharpe" in sweep_src and "_stamp_detectability_section(report_path)" in sweep_src
