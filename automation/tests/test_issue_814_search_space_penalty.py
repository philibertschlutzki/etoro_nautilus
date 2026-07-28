"""Issue #814 (P1, abhängig von #813) — ``deflation_family_floor_mode='budgeted'`` zählt nie
gezogene Kandidaten und koppelt die Multiplizitätskorrektur erneut an die Budgetausführung.

Root-Cause: Ein Trial, der nie gezogen wurde, hat keinen Sharpe-Schätzer und kann das Maximum
unter H0 nicht beeinflusst haben — ihn über ``n_trials_budget`` mitzuzählen ist keine konservative
Wahl, sondern eine Fehlspezifikation der Nullverteilung. Bei 14 Strategien mit Budgets 100–280
ergab 'budgeted' ``n_family≈2100`` je Symbol UNABHÄNGIG von der tatsächlichen Ziehungszahl (+29%
auf SR₀). Zusätzlich reproduzierte 'budgeted' denselben Kopplungsfehler, den #784 beseitigen
wollte, nur mit umgekehrtem Vorzeichen (eine früh abgebrochene Study wird HÄRTER deflatiert als
eine vollständig durchlaufene).

Fix: Default ``deflation_family_floor_mode`` von 'budgeted' auf 'attempted' (N = tatsächlich
ausgewertete Kandidaten). Die Suchraum-Kapazität wandert in einen separaten, expliziten,
additiven SR₀-Term (``deflation_search_space_penalty``, Default ``None`` = aus) statt N selbst zu
verzerren. 'budgeted' bleibt als Option für eine konservative Sensitivitätsanalyse erhalten.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.deflation import sr0_multiple_testing_robust
from automation.optimizer import sweep

CFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


# ── Config: Default-Wechsel + neuer Key ─────────────────────────────────────────────────────────
def test_real_config_defaults_to_attempted():
    assert CFG["deflation_family_floor_mode"] == "attempted"


def test_real_config_declares_search_space_penalty_off_by_default():
    assert CFG["deflation_search_space_penalty"] is None


def test_schema_documents_814_for_both_keys():
    assert "#814" in CFG["_schema"]["fields"]["deflation_family_floor_mode"]
    assert "#814" in CFG["_schema"]["fields"]["deflation_search_space_penalty"]


def test_budgeted_mode_still_declared_as_a_valid_option_in_the_schema_text():
    doc = CFG["_schema"]["fields"]["deflation_family_floor_mode"]
    assert "budgeted" in doc
    assert "sensitivitaetsanalyse" in doc.lower() or "sensitivitätsanalyse" in doc.lower()


# ── sweep._family_n_from_studies: Code-Fallback folgt dem neuen Default ────────────────────────────
class _T:
    def __init__(self, evaluated=True):
        self.user_attrs = {"oos_evaluated": evaluated}


class _Study:
    def __init__(self, trials, n_trials_budget=None):
        self.trials = trials
        self.user_attrs = {} if n_trials_budget is None else {"n_trials_budget": n_trials_budget}


def test_missing_floor_mode_key_no_longer_inflates_to_budget():
    early_stopped = _Study([_T()] * 5, n_trials_budget=200)
    pairs = [("StratA", "NKE.ETORO", "OK")]
    family_n = sweep._family_n_from_studies(pairs, [early_stopped], tournament_cfg={})
    assert family_n["NKE.ETORO"] == 5  # NICHT 200


def test_real_config_yields_attempted_behavior():
    early_stopped = _Study([_T()] * 5, n_trials_budget=200)
    pairs = [("StratA", "NKE.ETORO", "OK")]
    family_n = sweep._family_n_from_studies(pairs, [early_stopped], tournament_cfg=CFG)
    assert family_n["NKE.ETORO"] == 5


def test_budgeted_still_works_when_explicitly_selected():
    early_stopped = _Study([_T()] * 5, n_trials_budget=200)
    pairs = [("StratA", "NKE.ETORO", "OK")]
    family_n = sweep._family_n_from_studies(
        pairs, [early_stopped], tournament_cfg={"deflation_family_floor_mode": "budgeted"})
    assert family_n["NKE.ETORO"] == 200


# ── deflation.sr0_multiple_testing_robust: search_space_penalty ────────────────────────────────────
def _base_kwargs():
    return dict(var_sr_trials=0.0002, n_trials=100, min_cohort=10, n_periods=250,
               sr_estimate=0.0, variance_n_trials=100)


def test_none_penalty_is_bit_identical_to_no_penalty():
    sr0_none, *_ = sr0_multiple_testing_robust(**_base_kwargs(), search_space_penalty=None)
    sr0_omitted, *_ = sr0_multiple_testing_robust(**_base_kwargs())
    assert sr0_none == sr0_omitted


def test_zero_penalty_is_a_noop():
    sr0_zero, *_ = sr0_multiple_testing_robust(**_base_kwargs(), search_space_penalty=0.0)
    sr0_none, *_ = sr0_multiple_testing_robust(**_base_kwargs(), search_space_penalty=None)
    assert sr0_zero == sr0_none


def test_positive_penalty_adds_exactly_itself_to_sr0():
    sr0_base, *_ = sr0_multiple_testing_robust(**_base_kwargs())
    sr0_penalized, *_ = sr0_multiple_testing_robust(**_base_kwargs(), search_space_penalty=0.05)
    assert sr0_penalized == pytest.approx(sr0_base + 0.05)


def test_penalty_is_additive_regardless_of_n_trials():
    """Der Term wirkt AUSSCHLIESSLICH additiv auf SR0, unabhaengig von N (im Unterschied zu
    'budgeted', das N selbst verzerrte) -- dieselbe Additivitaet gilt bei kleinem wie bei grossem N."""
    for n in (10, 1000):
        sr0_bare, *_ = sr0_multiple_testing_robust(
            var_sr_trials=0.0002, n_trials=n, n_periods=250, variance_n_trials=n)
        sr0_penalized, *_ = sr0_multiple_testing_robust(
            var_sr_trials=0.0002, n_trials=n, n_periods=250, variance_n_trials=n,
            search_space_penalty=0.1)
        assert sr0_penalized == pytest.approx(sr0_bare + 0.1)


# ── confirm.py: deflation_search_space_penalty wird gelesen + angewendet + exportiert ──────────────
def test_confirm_reads_and_applies_search_space_penalty(monkeypatch, tmp_path):
    from automation.optimizer import confirm, run_optimization as ro, trial_config
    import itertools

    def _cfg_dir():
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        json.dump({
            "promotion_margin": 0.10, "penalty_unevaluable_oos": -10,
            "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0,
            "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
            "bonus_coverage_weight": 1.0, "evaluable_floor_epsilon": 0.001,
            "lambda_reg": 0.25,
        }, open(cfg_dir / "optimizer.json", "w", encoding="utf-8"))
        json.dump({
            "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
            "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
            "oos_min_win_rate": 0.0, "pbo_cluster_threshold": 0.99,
            "deflation_search_space_penalty": 0.07,
        }, open(cfg_dir / "tournament.json", "w", encoding="utf-8"))
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}},
                  open(cfg_dir / "backtest.json", "w", encoding="utf-8"))
        return cfg_dir

    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", _cfg_dir)
    monkeypatch.setattr(confirm, "config_dir", _cfg_dir)
    monkeypatch.setattr(trial_config, "config_dir", _cfg_dir)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)

    def _payload(sortino_ratio, dd, sortino_period, n_periods, eligible=True):
        return {"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": eligible, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {"sortino_ratio": sortino_ratio, "max_drawdown": dd,
                           "total_return": 0.02, "total_trades": 50,
                           "sortino_period": sortino_period, "n_periods": n_periods,
                           "ret_skew": 0.0, "ret_kurtosis": 3.0}}}

    def _write(trial_dir, payload):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), "utf-8")
        return out

    it = itertools.cycle([0.02 + 0.001 * i for i in range(20)])

    def _cohort_fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp = next(it)
        return _write(trial_dir, _payload(sp, 0.05, sp, 200))

    monkeypatch.setattr(ro, "run_backtest", _cohort_fake)
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=20)

    global_params = {"price_breakout_period": 20}
    fixed_result = _payload(2.0, 0.05, 0.03, 200)

    def _holdout_fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        return _write(trial_dir, fixed_result)

    res_with_penalty = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_fake,
    )
    assert res_with_penalty["metrics_symbol"]["deflation_search_space_penalty"] == 0.07

    # Ohne den Key (Default) ist der Term aus -- SR0 muss um genau 0.07 NIEDRIGER liegen.
    def _cfg_dir_no_penalty():
        d = _cfg_dir()
        tcfg = json.loads((d / "tournament.json").read_text("utf-8"))
        tcfg.pop("deflation_search_space_penalty")
        (d / "tournament.json").write_text(json.dumps(tcfg), "utf-8")
        return d

    monkeypatch.setattr(ro, "config_dir", _cfg_dir_no_penalty)
    monkeypatch.setattr(confirm, "config_dir", _cfg_dir_no_penalty)
    monkeypatch.setattr(trial_config, "config_dir", _cfg_dir_no_penalty)
    it2 = itertools.cycle([0.02 + 0.001 * i for i in range(20)])

    def _cohort_fake2(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp = next(it2)
        return _write(trial_dir, _payload(sp, 0.05, sp, 200))
    monkeypatch.setattr(ro, "run_backtest", _cohort_fake2)
    study2 = ro.optimize_symbol("DynamicBreakoutStrategy", "AAPL.ETORO", n_trials=20)
    res_without_penalty = confirm.confirm_per_symbol_promotion(
        study2, "DynamicBreakoutStrategy", "AAPL.ETORO", global_params=global_params,
        run_backtest=_holdout_fake,
    )
    assert res_without_penalty["metrics_symbol"]["deflation_search_space_penalty"] is None
    assert (res_with_penalty["metrics_symbol"]["deflated_sr0"]
            == pytest.approx(res_without_penalty["metrics_symbol"]["deflated_sr0"] + 0.07))
