"""Issue #1282 (GH #1155, Katalog #1272-1297, P0) — ``oos_min_alpha_tstat`` als alleiniges
Qualitätsgate kalibrieren oder begründen.

Symptom. Nach der Entfernung von ``oos_min_psr`` aus ``eligible_requires_all`` (#1248) und bei
leerem ``eligible_requires_any`` trägt ``t(α) >= 2.0`` die gesamte Qualitätsentscheidung — 7
eligible Trials in zwei von 56 Studies. Der dokumentierte Modus ``'multiplicity_adjusted'`` fiel
ohne Kalibrier-Fixture fail-open auf ``'static'`` zurück; ``calibration.calibrate_alpha_tstat_gate``
existierte, war aber unbenutzt.

Fix.
1. ``sweep.calibrate_and_write_alpha_tstat_gate_cache`` + ``--calibrate-alpha-tstat-gate``-CLI-
   Flag: Monte-Carlo-Kalibrierung aus den TATSAECHLICH persistierten Studies, geschrieben nach
   ``PERSISTENT_CACHE_ROOT/alpha_tstat_gate_calibration.json`` (analog ``calibrated_slippage.json``).
2. ``oos_min_alpha_tstat_mode`` bleibt bewusst 'static' in dieser PR (Zero-Guessing — die Umstellung
   auf 'multiplicity_adjusted' erfordert einen echten Kalibrierlauf gegen die reale Trial-Budget-/
   Holdout-Laengen-Verteilung, siehe tournament.json-Schema).
3. ``reward.resolve_alpha_tstat_gate_threshold`` unterscheidet seither ``source='static_fallback'``
   (Modus konfiguriert, aber unwirksam) von ``source='static'`` (Modus nie verlassen); neue
   Invariante ``invariants.check_alpha_tstat_gate_calibrated`` meldet FAIL 'high' bei
   ``'static_fallback'``.
4. ``eligible_requires_any=[]`` ist seither ein EXPLIZITER, dokumentierter Beschluss
   (``tournament.json['eligible_requires_any_empty_accepted']``), fail-loud erzwungen von
   ``reward.assert_eligible_requires_any_not_silently_empty`` (Sweep-Start-Preflight).
"""
import inspect
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv, reward, sweep


def _load_production_tournament_cfg() -> dict:
    return json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ---------------------------------------------------------------------------------------------
# reward.resolve_alpha_tstat_gate_threshold — static vs. static_fallback
# ---------------------------------------------------------------------------------------------

def test_static_mode_never_reports_static_fallback():
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "static"}
    _, source = reward.resolve_alpha_tstat_gate_threshold(tcfg)
    assert source == "static"


def test_multiplicity_adjusted_without_fixture_reports_static_fallback_not_static():
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "multiplicity_adjusted"}
    _, source = reward.resolve_alpha_tstat_gate_threshold(tcfg)
    assert source == "static_fallback"


def test_multiplicity_adjusted_with_full_fixture_reports_calibrated():
    tcfg = {"oos_min_alpha_tstat": 2.0, "oos_min_alpha_tstat_mode": "multiplicity_adjusted"}
    _, source = reward.resolve_alpha_tstat_gate_threshold(
        tcfg, n_family_stage1=280, oos_n_periods_median=1079,
        calibration_fixture=[{"n_configs": 280, "n_periods": 1079, "threshold": 3.5666}],
    )
    assert source == "calibrated"


# ---------------------------------------------------------------------------------------------
# invariants.check_alpha_tstat_gate_calibrated
# ---------------------------------------------------------------------------------------------

def test_static_mode_is_inconclusive_not_a_failure():
    r = inv.check_alpha_tstat_gate_calibrated("static", [{"strategy": "S", "symbol": "X",
                                                           "alpha_tstat_gate_threshold_source": "static_fallback"}])
    assert r.passed is True
    assert r.inconclusive is True


def test_multiplicity_adjusted_with_all_studies_calibrated_passes():
    records = [{"strategy": "S", "symbol": "X", "alpha_tstat_gate_threshold_source": "calibrated"}]
    r = inv.check_alpha_tstat_gate_calibrated("multiplicity_adjusted", records)
    assert r.passed is True
    assert r.severity == "high"


def test_multiplicity_adjusted_with_a_static_fallback_study_fails_high():
    records = [
        {"strategy": "S1", "symbol": "X", "alpha_tstat_gate_threshold_source": "calibrated"},
        {"strategy": "S2", "symbol": "Y", "alpha_tstat_gate_threshold_source": "static_fallback"},
    ]
    r = inv.check_alpha_tstat_gate_calibrated("multiplicity_adjusted", records)
    assert r.passed is False
    assert r.severity == "high"
    assert "S2/Y" in r.actual["static_fallback_studies"]
    assert "S1/X" not in r.actual["static_fallback_studies"]


def test_none_mode_defaults_to_static_semantics_inconclusive():
    r = inv.check_alpha_tstat_gate_calibrated(None, [])
    assert r.passed is True
    assert r.inconclusive is True


def test_wired_in_build_report():
    from automation.optimizer import report as rpt
    source = inspect.getsource(rpt._build_report)
    assert "check_alpha_tstat_gate_calibrated" in source


# ---------------------------------------------------------------------------------------------
# reward.assert_eligible_requires_any_not_silently_empty
# ---------------------------------------------------------------------------------------------

def test_non_empty_eligible_requires_any_always_passes():
    reward.assert_eligible_requires_any_not_silently_empty({"eligible_requires_any": ["min_win_rate"]})


def test_empty_without_documented_decision_fails_loud():
    with pytest.raises(ValueError, match="eligible_requires_any_empty_accepted"):
        reward.assert_eligible_requires_any_not_silently_empty({"eligible_requires_any": []})


def test_empty_with_incomplete_decision_still_fails_loud():
    """accepted=True allein genuegt nicht -- rationale/decided_in_issue muessen ebenfalls vorliegen
    (kein Freifahrtschein ohne Begruendung)."""
    with pytest.raises(ValueError):
        reward.assert_eligible_requires_any_not_silently_empty({
            "eligible_requires_any": [],
            "eligible_requires_any_empty_accepted": {"accepted": True},
        })


def test_empty_with_full_documented_decision_passes():
    reward.assert_eligible_requires_any_not_silently_empty({
        "eligible_requires_any": [],
        "eligible_requires_any_empty_accepted": {
            "accepted": True, "rationale": "...", "decided_in_issue": "#1282",
        },
    })


def test_production_config_documents_the_empty_decision():
    """Die Produktions-Config hat eligible_requires_any=[] -- der Preflight darf den Sweep-Start
    nicht sprengen."""
    cfg = _load_production_tournament_cfg()
    reward.assert_eligible_requires_any_not_silently_empty(cfg)


def test_assert_gate_reward_parity_calls_the_new_guard():
    source = inspect.getsource(sweep._assert_gate_reward_parity)
    assert "assert_eligible_requires_any_not_silently_empty" in source


# ---------------------------------------------------------------------------------------------
# sweep.write_alpha_tstat_gate_calibration_cache / read_alpha_tstat_gate_calibration_cache
# ---------------------------------------------------------------------------------------------

def test_write_and_read_alpha_tstat_gate_calibration_cache_roundtrip(tmp_path):
    points = [{"n_configs": 280, "n_periods": 1079, "threshold": 3.5666}]
    sweep.write_alpha_tstat_gate_calibration_cache(tmp_path, points, run_id="run_abc")
    data = sweep.read_alpha_tstat_gate_calibration_cache(tmp_path)
    assert data == points


def test_read_alpha_tstat_gate_calibration_cache_missing_file_returns_empty_list(tmp_path):
    assert sweep.read_alpha_tstat_gate_calibration_cache(tmp_path) == []


# ---------------------------------------------------------------------------------------------
# sweep.calibrate_and_write_alpha_tstat_gate_cache
# ---------------------------------------------------------------------------------------------

class _FakeTrial:
    def __init__(self, **user_attrs):
        self.user_attrs = user_attrs


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials


def _eligible_trial(oos_n_periods=1000.0, run_id="run_xyz"):
    return _FakeTrial(oos_selection_statistic_available=True, oos_n_periods=oos_n_periods,
                      run_id=run_id)


def test_calibrate_and_write_alpha_tstat_gate_cache_writes_a_file(tmp_path):
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    studies = [_FakeStudy([_eligible_trial(), _eligible_trial(), _eligible_trial()])]

    result = sweep.calibrate_and_write_alpha_tstat_gate_cache(
        pairs, studies, work_dir=tmp_path, run_id="run_xyz")

    assert len(result) == 1
    assert result[0]["n_configs"] == 3
    assert result[0]["n_periods"] == 1000
    assert result[0]["threshold"] > 2.0
    cached = sweep.read_alpha_tstat_gate_calibration_cache(tmp_path)
    assert cached == result


def test_calibrate_and_write_alpha_tstat_gate_cache_skips_studies_with_too_few_configs(tmp_path):
    """n_configs < 2 -- keine sinnvolle Maximum-ueber-Kandidaten-Verteilung, kein Gitterpunkt."""
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    studies = [_FakeStudy([_eligible_trial()])]

    result = sweep.calibrate_and_write_alpha_tstat_gate_cache(
        pairs, studies, work_dir=tmp_path, run_id="run_xyz")
    assert result == []
    assert sweep.read_alpha_tstat_gate_calibration_cache(tmp_path) == []


def test_calibrate_and_write_alpha_tstat_gate_cache_deduplicates_identical_grid_points(tmp_path):
    pairs = [("StratA", "TSLA.ETORO", "OK"), ("StratB", "NVDA.ETORO", "OK")]
    studies = [
        _FakeStudy([_eligible_trial(), _eligible_trial(), _eligible_trial()]),
        _FakeStudy([_eligible_trial(), _eligible_trial(), _eligible_trial()]),
    ]
    result = sweep.calibrate_and_write_alpha_tstat_gate_cache(
        pairs, studies, work_dir=tmp_path, run_id="run_xyz")
    assert len(result) == 1


def test_calibrate_and_write_alpha_tstat_gate_cache_none_study_is_skipped(tmp_path):
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    studies = [None]
    result = sweep.calibrate_and_write_alpha_tstat_gate_cache(
        pairs, studies, work_dir=tmp_path, run_id="run_xyz")
    assert result == []


# ---------------------------------------------------------------------------------------------
# CLI-Einstiegspunkt
# ---------------------------------------------------------------------------------------------

def test_cli_flag_exists_and_calls_calibrate_and_write():
    source = inspect.getsource(sweep.main)
    assert "--calibrate-alpha-tstat-gate" in source
    assert "calibrate_and_write_alpha_tstat_gate_cache" in source


def test_sweep_loop_auto_refreshes_the_cache_after_confirm_export():
    source = inspect.getsource(sweep)
    idx = source.index("calibrate_and_write_alpha_tstat_gate_cache(\n                    symbol_pairs")
    assert idx > 0
