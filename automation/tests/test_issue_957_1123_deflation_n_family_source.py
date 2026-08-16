"""Issue #957/#1123 (Katalog #960) — Multiplizität driftet zwischen gleichzeitigen Reports und
widerspricht ihrer eigenen Zerlegung.

Symptom (B-14). ``n_family(TSLA) = 197`` (Report A) gegen ``469`` (Reports B/C) — Faktor 2,38
⇒ Φ⁻¹(1−1/N) 2,600 gegen 2,860, SR* um ~10 % verschoben. ``n_family`` gegen
``Σ n_family_stage1`` unverändert Faktor 3,48–4,16, in promotionsfreundlicher Richtung.

Status. Die Drift ist bereits adressiert (#1091 friert n_family vor dem ersten Trial ein) UND die
Partitionslücke ist bereits eine Tautologie (#1102: ``report._n_family_by_symbol`` wird DIREKT aus
``Σ n_family_stage1`` abgeleitet; ``invariants.check_n_family_partition`` steht bereits auf
``severity='blocking'``, siehe Tests unten, die das als Regressionsschutz verankern).

Verbleibender Fix (#957/#1123): "Die Promotion konsumiert genau eine der beiden Zahlen; die Wahl
wird im Report-Schema begründet und im Artefakt als ``deflation_n_family_source`` mitgeführt."
``confirm.confirm_per_symbol_promotion`` erhält ein neues, vom Aufrufer (``sweep._run_confirm_and_
export``) gesetztes Label, das explizit im Proposal-Artefakt (und im Sweep-Report,
``report._study_record``) dokumentiert, welche Quelle die Deflationsschwelle tatsächlich gespeist
hat — vorher nur aus dem Aufrufer-Quelltext erschliessbar, nicht aus dem Artefakt selbst.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro, confirm, sweep
from automation.optimizer import trial_config
from automation.optimizer import invariants as inv


# ── check_n_family_partition severity regression guard ──────────────────────────────────────────
def test_check_n_family_partition_severity_is_blocking():
    """Akzeptanzkriterium #957 (Fix-Teil 1) — bereits umgesetzt (#1102); dieser Test verankert es
    als Regressionsschutz gegen ein kuenftiges versehentliches Downgrade."""
    result = inv.check_n_family_partition({"X.ETORO": 10}, {"X.ETORO": {"A": 4, "B": 6}})
    assert result.severity == "blocking"


def test_check_n_family_partition_fails_blocking_on_a_real_gap():
    result = inv.check_n_family_partition({"X.ETORO": 100}, {"X.ETORO": {"A": 40, "B": 60}})
    assert result.passed is True  # 100 == 40+60
    gap_result = inv.check_n_family_partition({"X.ETORO": 100}, {"X.ETORO": {"A": 20, "B": 30}})
    assert gap_result.passed is False
    assert gap_result.severity == "blocking"


# ── deflation_n_family_source: fixtures/helpers (mirrors test_issue_652) ────────────────────────
def _cfg_dir(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    with open(cfg_dir / "optimizer.json", "w", encoding="utf-8") as f:
        json.dump({
            "promotion_margin": 0.10, "penalty_unevaluable_oos": -10,
            "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0,
            "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
            "bonus_coverage_weight": 1.0, "evaluable_floor_epsilon": 0.001,
            "lambda_reg": 0.25,
        }, f)
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump({
            "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
            "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
            "oos_min_win_rate": 0.0,
        }, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)


def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods, eligible=True,
                     total_return=0.02, total_trades=50, skew=0.0, kurtosis=3.0):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": eligible, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": total_return, "total_trades": total_trades,
                "sortino_period": sortino_period, "n_periods": n_periods,
                "ret_skew": skew, "ret_kurtosis": kurtosis,
            },
        },
    }


def _write_result(trial_dir, payload):
    out = Path(trial_dir) / "tournament_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), "utf-8")
    return out


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_sortino=0.02):
    _isolate(monkeypatch, tmp_path)

    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=cohort_sortino, dd=0.05, sortino_period=cohort_sortino, n_periods=200))
    monkeypatch.setattr(ro, "run_backtest", _fake)
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


def test_deflation_n_family_source_is_absent_without_the_new_argument(tmp_path, monkeypatch):
    """Rueckwaertskompatibilitaet: Legacy-Aufrufer (kein deflation_n_family_source uebergeben)
    erhalten kein erfundenes Feld im Artefakt."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=5)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=100,
    )
    assert "deflation_n_family_source" not in res["metrics_symbol"]


def test_deflation_n_family_source_is_stamped_verbatim_when_provided(tmp_path, monkeypatch):
    """Akzeptanzkriterium #957 — genau eine Quelle, benannt im Artefakt."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=5)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=100, deflation_n_family_source="n_family_stage1_per_strategy",
    )
    assert res["metrics_symbol"]["deflation_n_family_source"] == "n_family_stage1_per_strategy"
    assert res["metrics_symbol"]["deflation_n_family"] == 100


def test_sweep_passes_the_per_strategy_stage1_source_label():
    """sweep._run_confirm_and_export benennt explizit die per-Study-Stage1-Quelle (#826) — nicht
    die symbolweite Summe — als deflation_n_family_source (statischer Vertragstest ueber den
    Quelltext, da _run_confirm_and_export eine lokale Closure innerhalb von run_per_symbol_sweep
    ist und keinen eigenstaendigen Unit-Test-Einstiegspunkt bietet)."""
    import inspect
    src = inspect.getsource(sweep)
    assert 'deflation_n_family_source="n_family_stage1_per_strategy"' in src


def test_report_study_record_surfaces_the_source_field():
    """report._study_record liest deflation_n_family_source aus holdout_metrics (statischer
    Vertragstest, analog test_sweep_passes_the_per_strategy_stage1_source_label — _study_record
    braucht ein reales Optuna-Study-Objekt fuer einen vollen Integrationstest)."""
    import inspect
    from automation.optimizer import report
    src = inspect.getsource(report._study_record)
    assert '"deflation_n_family_source": holdout_metrics.get("deflation_n_family_source")' in src
