"""Issue #695 (P0) — DSR-Familien-Multiplizität korrelations-declustern.

Root-Cause: ``confirm.py`` zählte die familienweite Multiple-Testing-Multiplizität
(``deflation_n_family``, Σ eligibler Trials über ALLE Strategien-Studies eines Symbols) ROH — ohne
jede Korrelations-Dekorrelation — und speiste diese rohe Zahl direkt in ``E[max_N]``
(``sr0_multiple_testing_robust``). Der PBO-Pfad DERSELBEN Datei (``_study_pbo``) declustert
dieselben Configs bereits via ``cpcv.cluster_effective_configs`` (Pearson ρ > ``pbo_cluster_
threshold``) und telemetriert ``pbo_n_configs`` (effektiv) vs. ``pbo_n_configs_raw`` (roh). Zwei
Multiple-Testing-Korrekturen im selben Confirm-Pfad nutzten dadurch INKONSISTENTE Config-Zählungen.

Referenzfall (#703): ``OpeningRangeBreakout`` — 117 near-identische Configs (ρ>0.99), deklustert auf
effektiv 1 (PBO-Pfad) — wird über den DSR-Pfad trotzdem als 117 unabhängige Configs in die familienweite
N=803 gezählt. Fix: ``confirm_per_symbol_promotion(..., deflation_family_period_returns=...)``
declustert die familienweite Return-Matrix VOR der SR₀-Berechnung; ``deflation_n_family_effective``
(nicht die rohe Summe) treibt jetzt ``E[max_N]``.
"""
import itertools
import json
import random
import statistics
from pathlib import Path

import numpy as np
import pytest

from automation.optimizer import confirm, run_optimization as ro, sweep
from automation.optimizer import trial_config
from automation.optimizer.cpcv import cluster_effective_configs
from automation.optimizer.deflation import (
    deflated_sharpe_ratio, deflated_threshold, sr0_multiple_testing,
)


# ── cluster_effective_configs: k unabhängige Cluster-Zentren × m near-Duplikate ─────────────────
def test_decluster_recovers_k_centers_not_k_times_m():
    """Synthetische Familie: k=8 unabhängige Cluster-Zentren, je m=15 near-Duplikaten (ρ≈0.999,
    wie ein dichtes Optuna-Suchraum-Grid um dieselbe Parametrisierung). Die deklusterte Zahl muss
    bei ≈k liegen (NICHT k·m=120) — das wörtliche Akzeptanzkriterium #695."""
    rng = np.random.default_rng(11)
    k, m, n_periods = 8, 15, 250
    rows = []
    for center_idx in range(k):
        base = rng.normal(0.0005 * center_idx, 0.002, n_periods)
        for _ in range(m):
            rows.append((base + rng.normal(0.0, 1e-9, n_periods)).tolist())
    assert len(rows) == k * m

    keep = cluster_effective_configs(rows, threshold=0.99)
    # Sehr enge Near-Duplikate (Rauschen 1e-9) kollabieren fast perfekt auf die k Zentren.
    assert k <= len(keep) <= k + 1


# ── confirm.confirm_per_symbol_promotion: declusterte statt rohe Familien-N treibt SR₀ ──────────
def _cfg_dir(tmp_path, tournament_extra=None):
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
    tcfg = {
        "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
        "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
        "oos_min_win_rate": 0.0, "pbo_cluster_threshold": 0.99,
    }
    if tournament_extra:
        tcfg.update(tournament_extra)
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump(tcfg, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate(monkeypatch, tmp_path, tournament_extra=None):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path, tournament_extra))
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


def _cohort_factory(sortino_periods, n_periods=200):
    it = itertools.cycle(sortino_periods)

    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        sp = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=n_periods))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_periods, tournament_extra=None):
    _isolate(monkeypatch, tmp_path, tournament_extra)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory(cohort_periods))
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


def _family_rows(k, m, *, n_periods=250, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for center_idx in range(k):
        base = rng.normal(0.0005 * center_idx, 0.002, n_periods)
        for _ in range(m):
            rows.append((base + rng.normal(0.0, 1e-9, n_periods)).tolist())
    return rows


def test_confirm_declusters_family_matrix_instead_of_counting_raw(tmp_path, monkeypatch):
    """Integrationstest: übergibt man ``deflation_family_period_returns`` (k=25 Zentren × m=10
    near-Duplikate, ρ≈0.999), muss ``deflation_n_family_effective`` ≈ k tragen (NICHT k·m=250) und
    ``deflation_n_effective`` (das E[max_N]-treibende N) diese declusterte Zahl verwenden — die
    rohe Zahl bleibt separat als ``deflation_n_family_raw`` sichtbar (Telemetrie-Parität #696).
    k=25 > die per-Study-Kohorte (n_trials=20), damit die familienweite (nicht die per-Study-)
    Zahl tatsächlich das ``max(...)`` in ``deflation_n_effective`` treibt (#652-Invariante)."""
    global_params = {"price_breakout_period": 20}
    cohort_periods = [0.02 + 0.001 * i for i in range(20)]
    study = _build_study(tmp_path, monkeypatch, n_trials=20, cohort_periods=cohort_periods)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    k, m = 25, 10
    family_rows = _family_rows(k, m, n_periods=150)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=k * m,
        deflation_family_period_returns=family_rows,
    )

    assert res["metrics_symbol"]["deflation_n_family_raw"] == k * m
    assert k <= res["metrics_symbol"]["deflation_n_family_effective"] <= k + 1
    assert res["metrics_symbol"]["deflation_n_effective"] == res["metrics_symbol"]["deflation_n_family_effective"]
    # Die declusterte (kleinere) N muss ein NIEDRIGERES SR₀ (laxere Huerde) ergeben als die rohe.
    sr0_raw = sr0_multiple_testing(0.0002, k * m)
    sr0_effective = sr0_multiple_testing(0.0002, res["metrics_symbol"]["deflation_n_family_effective"])
    assert sr0_effective < sr0_raw


def test_confirm_without_family_matrix_stays_bit_identical(tmp_path, monkeypatch):
    """Fehlt ``deflation_family_period_returns`` (Legacy-Aufrufer, nur der Skalar ``deflation_n_
    family``), MUSS ``deflation_n_family_effective == deflation_n_family_raw`` gelten — bit-identisch
    zum Pre-#695-Verhalten (kein Clustering ohne Daten möglich)."""
    global_params = {"price_breakout_period": 20}
    cohort_periods = [0.02 + 0.001 * i for i in range(20)]
    study = _build_study(tmp_path, monkeypatch, n_trials=20, cohort_periods=cohort_periods)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=496,
    )
    assert res["metrics_symbol"]["deflation_n_family_raw"] == 496
    assert res["metrics_symbol"]["deflation_n_family_effective"] == 496
    assert res["metrics_symbol"]["deflation_n_effective"] == 496


def test_family_period_returns_from_studies_groups_by_symbol():
    """``sweep._family_period_returns_from_studies`` sammelt die ``oos_period_returns`` ALLER
    evaluierten Trials (#784 — dieselbe erweiterte Menge wie ``_family_n_from_studies``, seit #784
    NICHT mehr nur eligible) ALLER Strategien-Studies DESSELBEN Symbols (Matrix statt Zähler)."""
    class _T:
        def __init__(self, evaluated, rets):
            self.user_attrs = {"oos_evaluated": evaluated, "oos_period_returns": rets}

    class _Study:
        def __init__(self, trials):
            self.trials = trials

    pairs = [
        ("StratA", "TSLA.ETORO", "OK"), ("StratB", "TSLA.ETORO", "OK"),
        ("StratA", "AAPL.ETORO", "OK"),
    ]
    studies = [
        _Study([_T(True, [0.01, 0.02]), _T(True, [0.03, 0.04]), _T(False, [0.05, 0.06])]),
        _Study([_T(True, [0.07, 0.08])]),
        _Study([_T(True, [0.09, 0.10])]),
    ]
    family_returns = sweep._family_period_returns_from_studies(pairs, studies)
    assert family_returns["TSLA.ETORO"] == [[0.01, 0.02], [0.03, 0.04], [0.07, 0.08]]
    assert family_returns["AAPL.ETORO"] == [[0.09, 0.10]]


# ── Referenzfall #703: OpeningRangeBreakout bleibt HOLD unter BEIDEN N (roh und deklustert) ──────
def test_opening_range_reference_case_stays_hold_under_declustered_n():
    """Regressionstest auf den Referenzfall aus Issue #703: ein Kandidat mit ``ŜR≈0.0308``,
    ``T≈4319`` (per-Perioden-Sortino/Perioden des Holdouts) scheitert bei der ROHEN Familien-N=803
    (``deflated_dsr≈0.31``) UND bleibt bei der DECLUSTERTEN Familien-N≈312 (``deflated_dsr≈0.40``)
    unter der 0.95-Promotionsschwelle — der Fix darf den einzigen vollständig eligiblen Kandidaten
    des Laufs NICHT promoten (Beleg: er korrigiert nur die Statistik, weicht die Type-I-Kontrolle
    nicht auf). Gleichzeitig muss DSR(312) > DSR(803) gelten (declustern macht die Hürde niedriger,
    nicht höher — die erwartete Wirkungsrichtung des Fixes)."""
    sr_estimate = 0.0308
    n_periods = 4319
    var_sr_trials = 0.011966 ** 2  # sqrt(V) aus #703 rückgerechnet (SR0(803)=0.0382=sqrt(V)*E[max_803])

    sr0_raw = sr0_multiple_testing(var_sr_trials, 803)
    sr0_effective = sr0_multiple_testing(var_sr_trials, 312)
    assert sr0_effective < sr0_raw
    assert sr0_raw == pytest.approx(0.0382, abs=2e-3)
    assert sr0_effective == pytest.approx(0.0348, abs=2e-3)

    dsr_raw = deflated_sharpe_ratio(sr_estimate, n_periods, sr0=sr0_raw)
    dsr_effective = deflated_sharpe_ratio(sr_estimate, n_periods, sr0=sr0_effective)

    assert dsr_raw == pytest.approx(0.312, abs=0.02)
    assert dsr_effective == pytest.approx(0.40, abs=0.03)
    # Kernkriterium: der Fix hebt die DSR an (weniger konservativ), aber NIE über die
    # Promotionsschwelle — OpeningRange bleibt in BEIDEN Fällen ein HOLD.
    assert dsr_effective > dsr_raw
    assert dsr_raw < 0.95
    assert dsr_effective < 0.95


# ── Monte-Carlo: declusterte N kontrolliert die False-Positive-Rate weiterhin am nominellen Niveau ──
def test_family_decluster_monte_carlo_false_positive_rate():
    """Unter H0 (kein echter Edge): k unabhängige Cluster-Zentren, je m near-Duplikate (dieselbe
    Konfiguration effektiv m-fach im Suchraum-Grid dupliziert). Die empirische False-Positive-
    Winner-Rate MUSS bei Verwendung der DECLUSTERTEN N=k weiterhin ≤ 1−confidence (+ Sampling-
    Toleranz) bleiben — der Fix senkt die Hürde, ohne die Typ-I-Garantie zu brechen. Die ROHE
    N=k·m ist per Konstruktion NIE weniger konservativ als die deklusterte N=k (E[max] waechst
    monoton in N) — sie kontrolliert also a fortiori mindestens genauso gut, nur unnoetig strenger."""
    random.seed(20260718)
    k, m = 8, 15
    confidence = 0.95
    sweeps = 3000

    fp_effective = 0
    fp_raw = 0
    for _ in range(sweeps):
        centers = [random.gauss(0.0, 1.0) for _ in range(k)]
        all_vals = []
        for c in centers:
            for _ in range(m):
                all_vals.append(c + random.gauss(0.0, 1e-6))
        best = max(all_vals)
        disp = statistics.pstdev(all_vals)
        thr_effective = deflated_threshold(k, disp, confidence=confidence, baseline=0.0)
        thr_raw = deflated_threshold(k * m, disp, confidence=confidence, baseline=0.0)
        if best > thr_effective:
            fp_effective += 1
        if best > thr_raw:
            fp_raw += 1

    rate_effective = fp_effective / sweeps
    rate_raw = fp_raw / sweeps

    # Nominelles Niveau 1-confidence=0.05, Toleranz fuer Sampling-Rauschen (wie test_issue_553).
    assert rate_effective <= 0.09, f"Deklusterte FP-Rate {rate_effective:.3f} ueber nominellem Niveau"
    # Die rohe (ueberzaehlte) N ist NIE weniger konservativ als die deklusterte N.
    assert rate_raw <= rate_effective + 0.02
