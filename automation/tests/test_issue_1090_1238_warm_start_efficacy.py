"""Issue #1090/#1238 (P1, Katalog #1247+) — Warm-Start-Wirksamkeit messen statt annehmen.

Symptom. Es gibt keine Grösse im System, die beantwortet, ob ein Wiederholungslauf etwas gebracht
hat. Die Antwort aus diesem Batch ist negativ (B-11) und war nur durch externe Paarbildung über elf
Artefakte zu gewinnen.

Fix.
1. Bei ``store_reuse.reused = true`` je Study die Vorlauf-Referenzwerte aus dem Store lesen und
   stempeln: ``prior_best_eligible_reward``, ``prior_holdout_total_return``, ``prior_run_id``.
2. Daraus ``warm_start_reward_delta`` und ``warm_start_holdout_delta`` ableiten.
3. Neue Invariante ``check_warm_start_efficacy`` (severity ``high``): FAIL, wenn über die Studies
   des Laufs der Median ``warm_start_reward_delta > 0`` UND der Median
   ``warm_start_holdout_delta < 0`` ist — die Überanpassungs-Signatur.
4. §3.1 weist beide Mediane aus.
"""
import json
import time

import optuna
import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import report
from automation.optimizer import summary_de as sde


def _make_trials(storage_url, study_name, *, run_id, values, eligible_count):
    study = optuna.create_study(
        study_name=study_name, storage=storage_url, direction="maximize", load_if_exists=True)
    for i, v in enumerate(values):
        trial = study.ask()
        eligible = i < eligible_count
        trial.set_user_attr("run_id", run_id)
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", eligible)
        trial.set_user_attr("oos_coherence_violation", False)
        trial.set_user_attr("reward_terms", {
            "branch": "eligible" if eligible else "unevaluable",
            "base": v, "divergence": 0.1, "dd_penalty": 0.1, "param_pen": 0.1,
            "turnover": 0.1, "fold_dispersion": 0.1, "tie_breaker": 0.1,
        })
        study.tell(trial, float(v))


def _proposal(strategy, symbol):
    return {
        "strategy": strategy, "symbol": symbol,
        "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": {
            "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
            "deflation_n_eligible": 3, "deflation_n_family_effective": 3, "deflation_n_effective": 3,
        }},
    }


# --- report._prior_holdout_total_return: liest das Vorlauf-Report-Artefakt ----------------------

def test_prior_holdout_total_return_reads_the_matching_study_from_the_prior_report(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "run_run_1.json").write_text(json.dumps({
        "studies": [
            {"strategy": "A", "symbol": "X.ETORO", "holdout_total_return": -0.0546},
            {"strategy": "B", "symbol": "Y.ETORO", "holdout_total_return": 0.02},
        ],
    }), "utf-8")
    value = report._prior_holdout_total_return("run_1", "A", "X.ETORO", reports_dir=reports_dir)
    assert value == pytest.approx(-0.0546)


def test_prior_holdout_total_return_none_without_a_run_id():
    assert report._prior_holdout_total_return(None, "A", "X.ETORO") is None


def test_prior_holdout_total_return_fail_open_on_missing_report(tmp_path):
    assert report._prior_holdout_total_return(
        "run_missing", "A", "X.ETORO", reports_dir=tmp_path / "reports") is None


def test_prior_holdout_total_return_fail_open_when_study_not_found(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "run_run_1.json").write_text(json.dumps({
        "studies": [{"strategy": "OTHER", "symbol": "Z.ETORO", "holdout_total_return": 0.1}],
    }), "utf-8")
    assert report._prior_holdout_total_return(
        "run_1", "A", "X.ETORO", reports_dir=reports_dir) is None


def test_prior_holdout_total_return_fail_open_on_malformed_json(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "run_run_1.json").write_text("{not valid json", "utf-8")
    assert report._prior_holdout_total_return(
        "run_1", "A", "X.ETORO", reports_dir=reports_dir) is None


# --- Integration: sequenzieller Warm-Start stempelt die Vorlauf-Referenzwerte --------------------

def test_warm_started_study_stamps_prior_reward_and_holdout_deltas(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    study_name = "study_TestStrat_TSLA_ETORO"
    storage_url = f"sqlite:///{sweep_dir / 'shared.db'}"

    # Lauf 1 ("Vorlauf"): 5 Trials, beste eligible Reward = 2.0 (i=2, eligible fuer i<3).
    _make_trials(storage_url, study_name, run_id="run_1", values=[0.0, 1.0, 2.0, 3.0, 4.0],
                 eligible_count=3)
    time.sleep(0.2)  # echte Wanduhr-Luecke, keine Zeitfenster-Ueberlappung (wie #1021/#1196)
    # Lauf 2 (dieser Lauf): 5 weitere Trials, beste eligible Reward = 12.0 (i=2 -> 10+2).
    _make_trials(storage_url, study_name, run_id="run_2", values=[10.0, 11.0, 12.0, 13.0, 14.0],
                 eligible_count=3)

    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)

    # Das Vorlauf-Report-Artefakt (run_1) traegt den Holdout-Referenzwert dieser Study.
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "run_run_1.json").write_text(json.dumps({
        "studies": [{"strategy": "TestStrat", "symbol": "TSLA.ETORO", "holdout_total_return": -0.0546}],
    }), "utf-8")

    # Nur das Holdout-Ergebnis DIESES Laufs faelschen (kein echter Backtest in diesem Test) --
    # der Rest von _study_record (best_eligible_reward etc.) bleibt UNVERAENDERT/real.
    _real_study_record = report._study_record

    def _study_record_with_fake_holdout(*args, **kwargs):
        record, checks = _real_study_record(*args, **kwargs)
        record["holdout_total_return"] = 0.01  # +1%
        return record, checks

    monkeypatch.setattr(report, "_study_record", _study_record_with_fake_holdout)

    out_path = report.generate_sweep_report(
        [_proposal("TestStrat", "TSLA.ETORO")], run_id="run_2",
        started_at_utc="2026-08-18T15:13:33.000+00:00",
        wallclock_s=2411, cli_args={"strategies": "TestStrat"}, reports_dir=reports_dir,
    )
    data = json.loads(out_path.read_text("utf-8"))
    study_record = data["studies"][0]

    assert study_record["prior_run_id"] == "run_1"
    assert study_record["prior_best_eligible_reward"] == pytest.approx(2.0)
    assert study_record["best_eligible_reward"] == pytest.approx(12.0)
    assert study_record["warm_start_reward_delta"] == pytest.approx(10.0)
    assert study_record["prior_holdout_total_return"] == pytest.approx(-0.0546)
    assert study_record["warm_start_holdout_delta"] == pytest.approx(0.01 - (-0.0546))

    # invariants.check_warm_start_efficacy: Reward-Delta > 0 UND Holdout-Delta > 0 -- KEINE
    # Ueberanpassungs-Signatur (hier absichtlich positiv, siehe FAIL-Test unten).
    result = inv.check_warm_start_efficacy(data["studies"])
    assert result.passed is True


def test_study_without_warm_start_carries_no_prior_fields(tmp_path, monkeypatch):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    study_name = "study_TestStrat_FRESH_ETORO"
    storage_url = f"sqlite:///{sweep_dir / 'fresh.db'}"
    _make_trials(storage_url, study_name, run_id="run_only", values=[0.0, 1.0, 2.0],
                 eligible_count=2)
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)

    out_path = report.generate_sweep_report(
        [_proposal("TestStrat", "FRESH.ETORO")], run_id="run_only",
        started_at_utc="2026-08-18T15:13:33.000+00:00",
        wallclock_s=100, cli_args={}, reports_dir=tmp_path / "reports",
    )
    data = json.loads(out_path.read_text("utf-8"))
    study_record = data["studies"][0]
    assert study_record["prior_run_id"] is None
    assert study_record["prior_best_eligible_reward"] is None
    assert study_record["warm_start_reward_delta"] is None
    assert study_record["prior_holdout_total_return"] is None
    assert study_record["warm_start_holdout_delta"] is None


# --- invariants.check_warm_start_efficacy --------------------------------------------------------

def _record(reward_delta, holdout_delta):
    return {"warm_start_reward_delta": reward_delta, "warm_start_holdout_delta": holdout_delta}


def test_reproduces_b11_reference_finding_fails_on_the_overfit_signature():
    """Akzeptanzkriterium — reproduziert B-11: Reward-Median +0,1663, Holdout-Median −5,46 bps."""
    records = [
        _record(0.1663, -0.000546),
        _record(0.15, -0.0005),
        _record(0.18, -0.0006),
    ]
    result = inv.check_warm_start_efficacy(records)
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual["warm_start_reward_delta_median"] == pytest.approx(0.1663, abs=1e-6)
    assert result.actual["warm_start_holdout_delta_median"] == pytest.approx(-0.000546, abs=1e-9)


def test_inconclusive_without_reuse_not_a_pass():
    """Akzeptanzkriterium — bei reused=false ist der Check INCONCLUSIVE, nicht PASS."""
    result = inv.check_warm_start_efficacy([{"strategy": "A", "symbol": "X.ETORO"}])
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_passes_when_reward_improves_and_holdout_also_improves():
    records = [_record(0.1, 0.02), _record(0.2, 0.01)]
    result = inv.check_warm_start_efficacy(records)
    assert result.passed is True


def test_passes_when_reward_declines_even_if_holdout_also_declines():
    """Nur die KOMBINATION (Reward besser, Holdout schlechter) ist die Ueberanpassungs-Signatur."""
    records = [_record(-0.1, -0.02), _record(-0.2, -0.01)]
    result = inv.check_warm_start_efficacy(records)
    assert result.passed is True


def test_studies_missing_either_delta_are_excluded_from_the_median():
    records = [
        _record(0.1663, -0.000546),
        {"warm_start_reward_delta": 999.0},  # kein Holdout-Delta -> ausgeschlossen
        {"warm_start_holdout_delta": -999.0},  # kein Reward-Delta -> ausgeschlossen
    ]
    result = inv.check_warm_start_efficacy(records)
    assert result.actual["n_studies_measured"] == 1


# --- summary_de.py §3.1: beide Mediane sichtbar --------------------------------------------------

def test_section_3_1_shows_both_warm_start_medians():
    report_dict = {
        "run_id": "r1", "run_status": "complete",
        "started_at_utc": "2026-08-21T05:00:00Z", "wallclock_s": 100.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "studies": [
            {"strategy": "A", "symbol": "X.ETORO", "warm_start_reward_delta": 0.1663,
             "warm_start_holdout_delta": -0.000546},
        ],
        "cross_study": {
            "store_reuse": {
                "reused": True, "prior_run_ids": ["run_0"], "n_trials_prior": 100,
                "n_trials_own": 100, "studies_affected": 1,
            },
        },
    }
    section = sde._section_3_duration(report_dict)
    assert "Median warm_start_reward_delta" in section
    assert "Median warm_start_holdout_delta" in section
    assert "0.1663" in section


def test_section_3_1_omits_the_median_line_without_reuse():
    report_dict = {
        "run_id": "r1", "run_status": "complete",
        "started_at_utc": "2026-08-21T05:00:00Z", "wallclock_s": 100.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "studies": [{"strategy": "A", "symbol": "X.ETORO"}],
        "cross_study": {"store_reuse": {"reused": False}},
    }
    section = sde._section_3_duration(report_dict)
    assert "Median warm_start_reward_delta" not in section
