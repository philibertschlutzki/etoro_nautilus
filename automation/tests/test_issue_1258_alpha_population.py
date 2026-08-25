"""Issue #1258 (GH #1128) — die Regressions-Grundgesamtheit auditierbar machen.

Symptom. holdout_alpha_n_periods = 1079 in 13/13 Studies, unabhängig von Exposure (5,4-85,6 %) und
Trade-Zahl (32-121). Aus dem Artefakt ist nicht ablesbar, wie viele Zeilen der Regression überhaupt
Information tragen.

Root-Cause. Nur die Gesamtzahl wird gestempelt.

Fix. ``backtest_runner._alpha_regression_diagnostics`` (geteilt mit #1255/GH #1125) liefert
zusätzlich: ``n_total``, ``n_informative`` (y != 0 ODER x != 0), ``n_y_nonzero``, ``n_x_nonzero``,
``n_both_zero``. Alle fünf gebrückt bis ``report._study_record`` als ``holdout_alpha_n_*``.
"""
import json
from pathlib import Path

import pytest

from automation.backtest_runner import _alpha_regression_diagnostics
from automation.optimizer import confirm, run_optimization as ro


# ---------------------------------------------------------------------------------------------
# Population counts — exact partition
# ---------------------------------------------------------------------------------------------

def test_all_rows_informative_when_no_zeros():
    x = [0.01, -0.02, 0.015, -0.005, 0.03, -0.01, 0.02, -0.015, 0.005, 0.001]
    y = [0.2 * xi + 0.0003 for xi in x]
    diag = _alpha_regression_diagnostics(y, x)
    assert diag["n_total"] == 10
    assert diag["n_informative"] == 10
    assert diag["n_both_zero"] == 0
    assert diag["n_both_zero"] + diag["n_informative"] == diag["n_total"]


def test_partition_is_exact_with_mixed_zero_rows():
    """Konstruiert eine Serie mit bekannten Zero-Kategorien: manche Zeilen y==0 (nicht im Markt),
    manche x==0 (Benchmark-Bar ohne Bewegung), manche beides, manche keines."""
    x = [0.01, 0.0, 0.02, 0.0, 0.03, -0.01, 0.0, 0.015, -0.02, 0.005]
    y = [0.0, 0.0, 0.01, 0.0, 0.0, -0.005, 0.02, 0.0, -0.01, 0.002]
    # Zeilenweise Klassifikation von Hand zur Kontrolle:
    # i=0: x!=0,y=0 -> informativ (x)
    # i=1: x=0,y=0 -> both_zero
    # i=2: x!=0,y!=0 -> informativ
    # i=3: x=0,y=0 -> both_zero
    # i=4: x!=0,y=0 -> informativ (x)
    # i=5: x!=0,y!=0 -> informativ
    # i=6: x=0,y!=0 -> informativ (y)
    # i=7: x!=0,y=0 -> informativ (x)
    # i=8: x!=0,y!=0 -> informativ
    # i=9: x!=0,y!=0 -> informativ
    expected_both_zero = 2
    expected_informative = 8
    diag = _alpha_regression_diagnostics(y, x)
    assert diag["n_total"] == 10
    assert diag["n_both_zero"] == expected_both_zero
    assert diag["n_informative"] == expected_informative
    assert diag["n_both_zero"] + diag["n_informative"] == diag["n_total"]
    assert diag["n_y_nonzero"] == sum(1 for v in y if v != 0.0)
    assert diag["n_x_nonzero"] == sum(1 for v in x if v != 0.0)


def test_n_total_matches_input_length():
    import random
    rng = random.Random(3)
    n = 137
    x = [rng.gauss(0, 0.01) for _ in range(n)]
    y = [0.1 * xi + rng.gauss(0, 0.001) for xi in x]
    diag = _alpha_regression_diagnostics(y, x)
    assert diag["n_total"] == n


def test_partition_exact_on_large_random_fixture():
    """Akzeptanzkriterium: n_both_zero + n_informative == n_total, generisch (nicht nur an einem
    handkonstruierten Beispiel)."""
    import random
    rng = random.Random(11)
    for trial in range(20):
        n = rng.randint(5, 200)
        x = [rng.choice([0.0, rng.gauss(0, 0.01)]) for _ in range(n)]
        y = [rng.choice([0.0, rng.gauss(0, 0.001)]) for _ in range(n)]
        if all(xi == 0.0 for xi in x):
            continue  # sxx==0 -> None, kein gueltiges Fixture
        diag = _alpha_regression_diagnostics(y, x)
        if diag is None:
            continue
        assert diag["n_both_zero"] + diag["n_informative"] == diag["n_total"] == n


# ---------------------------------------------------------------------------------------------
# Bridging: run_optimization allowlist / confirm._metrics_dict (die fuenf Zaehl-Felder)
# ---------------------------------------------------------------------------------------------

def test_five_population_fields_are_allowlisted_holdout_only():
    fields = ("oos_alpha_n_total", "oos_alpha_n_informative", "oos_alpha_n_y_nonzero",
             "oos_alpha_n_x_nonzero", "oos_alpha_n_both_zero")
    assert len(fields) == 5
    for field in fields:
        assert field in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
        assert "1258" in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS[field]


class _M:
    def __getattr__(self, name):
        return None


def test_metrics_dict_carries_all_five_population_fields():
    m = _M()
    m.oos_alpha_n_total = 500
    m.oos_alpha_n_informative = 480
    m.oos_alpha_n_y_nonzero = 470
    m.oos_alpha_n_x_nonzero = 495
    m.oos_alpha_n_both_zero = 20
    d = confirm._metrics_dict(m)
    assert d["oos_alpha_n_total"] == 500
    assert d["oos_alpha_n_informative"] == 480
    assert d["oos_alpha_n_y_nonzero"] == 470
    assert d["oos_alpha_n_x_nonzero"] == 495
    assert d["oos_alpha_n_both_zero"] == 20


# ---------------------------------------------------------------------------------------------
# Full report bridging
# ---------------------------------------------------------------------------------------------

def _make_study(storage_url: str, study_name: str, n: int = 3):
    import optuna
    study = optuna.create_study(study_name=study_name, storage=storage_url, direction="maximize",
                                 load_if_exists=True)
    for i in range(n):
        trial = study.ask()
        trial.set_user_attr("oos_evaluated", True)
        trial.set_user_attr("oos_eligible", True)
        trial.set_user_attr("oos_coherence_violation", False)
        study.tell(trial, float(i))
    return study


def _proposal_with_population(*, n_total, n_informative, n_y_nonzero, n_x_nonzero, n_both_zero):
    holdout = {
        "deflated_sr0": 0.1, "deflated_dsr": 0.8, "deflation_dsr_z": 1.2,
        "deflation_n_eligible": 3, "deflation_n_family_effective": 3, "deflation_n_effective": 3,
        "oos_excess_return": 0.11, "oos_exposure_fraction": 0.1,
        "oos_alpha": 0.0002, "oos_beta": 0.15, "oos_alpha_tstat": 1.9,
        "oos_alpha_n_periods": n_total,
        "oos_alpha_n_total": n_total, "oos_alpha_n_informative": n_informative,
        "oos_alpha_n_y_nonzero": n_y_nonzero, "oos_alpha_n_x_nonzero": n_x_nonzero,
        "oos_alpha_n_both_zero": n_both_zero,
    }
    return {
        "strategy": "TestStrat", "symbol": "A.ETORO", "status": "REJECTED_ON_HOLDOUT",
        "dominant_is_rejection_detail": "REJECT_OOS_MIN_TRADES",
        "holdout_reject_detail": "REJECT_HOLDOUT_DSR_DROP",
        "is_rejection_detail": "REJECT_HOLDOUT_DSR_DROP",
        "holdout": {"symbol": holdout},
    }


@pytest.fixture
def wired_storage(tmp_path, monkeypatch):
    from automation.optimizer import report
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    storage_url = f"sqlite:///{sweep_dir / 'study.db'}"
    _make_study(storage_url, "study_TestStrat_A_ETORO")
    monkeypatch.setattr(report, "resolve_storage", lambda *, study_name, base_cfg=None: storage_url)
    return tmp_path


def test_study_record_carries_all_five_population_fields(wired_storage):
    from automation.optimizer import report
    out_path = report.generate_sweep_report(
        [_proposal_with_population(
            n_total=1079, n_informative=847, n_y_nonzero=612, n_x_nonzero=845, n_both_zero=232)],
        run_id="alphaPop1", reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_alpha_n_total"] == 1079
    assert rec["holdout_alpha_n_informative"] == 847
    assert rec["holdout_alpha_n_y_nonzero"] == 612
    assert rec["holdout_alpha_n_x_nonzero"] == 845
    assert rec["holdout_alpha_n_both_zero"] == 232
    # Akzeptanzkriterium: n_both_zero + n_informative == n_total, auch im Report-JSON.
    assert rec["holdout_alpha_n_both_zero"] + rec["holdout_alpha_n_informative"] == rec["holdout_alpha_n_total"]
    # n_total ist ein expliziter Alias von holdout_alpha_n_periods (dieselbe Zahl).
    assert rec["holdout_alpha_n_total"] == rec["holdout_alpha_n_periods"]


def test_study_record_population_fields_none_without_legacy_data(wired_storage):
    from automation.optimizer import report
    proposal = _proposal_with_population(
        n_total=None, n_informative=None, n_y_nonzero=None, n_x_nonzero=None, n_both_zero=None)
    proposal["holdout"]["symbol"]["oos_alpha_n_periods"] = 1079  # aeltere JSONs traegen dieses Feld
    out_path = report.generate_sweep_report(
        [proposal], run_id="alphaPop2", reports_dir=wired_storage / "reports",
    )
    rec = json.loads(out_path.read_text("utf-8"))["studies"][0]
    assert rec["holdout_alpha_n_periods"] == 1079
    assert rec["holdout_alpha_n_total"] is None
    assert rec["holdout_alpha_n_informative"] is None


def test_production_config_reward_semantics_version_at_least_27():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["reward_semantics_version"] >= 27
