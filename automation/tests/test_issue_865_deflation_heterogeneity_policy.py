"""Issue #865 (P2, Katalog #862-#865, GitHub-Issue #759, Pitfall #277) — eine nicht berechenbare
DSR-Korrektur ist kein bestandener Test.

Root-Cause: die #845-Heterogenitäts-Prüfung (``deflation_n_periods_ratio > deflation_max_n_periods_
ratio``) griff bislang ERST an der Export-Kohärenzgrenze in ``confirm.py`` — WEIT NACH der
``promote``-Entscheidung. Ein Kandidat konnte mit einer aus einer inkommensurablen Kohorte
berechneten DSR promotet werden; die Heterogenität wurde erst NACHTRÄGLICH in der Telemetrie
(``deflation_skipped_reason``) sichtbar, ohne die bereits gefallene Entscheidung zu beeinflussen —
eine nicht mehr aussagekräftige Korrektur bestand stillschweigend als bestandener Test.

Fix: ``tournament.json['deflation_heterogeneity_policy']`` ∈ {suppress_dsr, reject, per_stratum},
Default ``per_stratum``, wird VOR der DSR-Berechnung angewendet:
- ``suppress_dsr``: SR0/DSR werden None VOR der Entscheidung — das bestehende
  ``deflation_dsr is None ⇒ REJECT_HOLDOUT_DSR_DROP``-Gate greift dann korrekt (kann aber über
  ``promotion_correction_mode='dsr_or_robust_pair'`` unterlaufen werden).
- ``reject``: behandelt die Heterogenität selbst als UNBEDINGTEN, eigenständigen Ablehnungsgrund
  (``REJECT_DEFLATION_HETEROGENEOUS``), der NICHT über ``dsr_or_robust_pair`` unterlaufen werden kann.
- ``per_stratum``: SR0/DSR werden aus dem Quartils-Stratum von ``oos_n_periods`` berechnet, dem der
  promotete Trial selbst angehört (``confirm._stratify_cohort_by_n_periods``) — bleibt das gewählte
  Stratum < 2 Mitglieder, fällt die Politik auf ``suppress_dsr``-Verhalten zurück.
"""
import itertools
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config
from automation.optimizer.confirm import _stratify_cohort_by_n_periods


# ── Unit-Ebene: _stratify_cohort_by_n_periods ────────────────────────────────────────────────────
def test_stratify_two_wildly_different_trials_yields_singleton_strata():
    """AK — eine Zwei-Trial-Kohorte mit Ratio 50 darf NICHT als EIN Stratum zurückfallen (das wäre
    bit-identisch zur vollen inkommensurablen Kohorte und würde den #845/#865-Bug reproduzieren):
    jedes Trial landet in seinem eigenen Singleton-Stratum."""
    pairs = [(0.05, 50), (0.06, 2500)]
    sr_a, id_a, np_a = _stratify_cohort_by_n_periods(pairs, 50)
    sr_b, id_b, np_b = _stratify_cohort_by_n_periods(pairs, 2500)
    assert sr_a == [0.05] and len(sr_a) < 2
    assert sr_b == [0.06] and len(sr_b) < 2
    assert id_a != id_b


def test_stratify_larger_cohort_isolates_commensurable_cluster():
    """AK — bei genug Kohorten-Mitgliedern liefert die Stratifizierung eine ECHTE Teil-Kohorte
    (>= 2 Mitglieder) um den Anker, statt in Singletons zu zerfallen."""
    pairs = [(0.1, 200), (0.12, 210), (0.09, 190), (0.15, 220), (0.5, 9000), (0.4, 9500)]
    stratum_sr, stratum_id, stratum_np = _stratify_cohort_by_n_periods(pairs, 205)
    assert len(stratum_sr) >= 2
    assert all(150 <= n <= 300 for n in stratum_np)


def test_stratify_single_member_cohort_is_its_own_stratum():
    """Randfall — eine Ein-Trial-Kohorte kann per Definition nicht in mehrere Strata zerfallen (kein
    ``n_strata >= 2`` möglich); der Aufrufer erkennt das Singleton-Stratum und fällt auf
    'suppress_dsr' zurück (dieselbe Fallback-Logik wie bei zwei wildly-different Trials)."""
    pairs = [(0.1, 200)]
    stratum_sr, stratum_id, stratum_np = _stratify_cohort_by_n_periods(pairs, 200)
    assert stratum_sr == [0.1]
    assert len(stratum_sr) < 2


# ── End-to-End über confirm.confirm_per_symbol_promotion (dieselbe Infrastruktur wie #845) ───────
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
        "oos_min_win_rate": 0.0,
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


def _cohort_factory(specs):
    """specs: Liste von (sortino_period, n_periods)-Paaren, je Trial zyklisch zugewiesen."""
    it = itertools.cycle(specs)

    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp, np_ = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=np_))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _assert_coherent(res):
    result = inv.check_sr0_coherence(res["metrics_symbol"])
    assert result.passed, result.detail


def test_default_policy_is_per_stratum():
    """AK — tournament.json['deflation_heterogeneity_policy'] fehlt ⇒ Default 'per_stratum' (Issue-
    Text-konform), FAIL-LOUD auf einen unbekannten Wert."""
    from automation.optimizer.confirm import _VALID_DEFLATION_HETEROGENEITY_POLICIES
    assert _VALID_DEFLATION_HETEROGENEITY_POLICIES == {"suppress_dsr", "reject", "per_stratum"}


def test_unknown_policy_is_fail_loud(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path, tournament_extra={"deflation_heterogeneity_policy": "bogus"})
    global_params = {"sma_period": 20}
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([(0.05, 200), (0.06, 220)]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    with pytest.raises(ValueError, match="deflation_heterogeneity_policy"):
        confirm.confirm_per_symbol_promotion(
            study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
        )


def test_small_heterogeneous_cohort_falls_back_to_suppress_under_per_stratum(tmp_path, monkeypatch):
    """AK — der #845-Kernfall (2 Trials, n_periods=[50, 2500], Ratio=50) hat unter dem NEUEN Default
    ('per_stratum') je nur ein Singleton-Stratum je Trial ⇒ fällt auf 'suppress_dsr'-Verhalten
    zurück: SR0/DSR werden weiterhin None, bit-identisches Aussenverhalten zum alten Default."""
    _isolate(monkeypatch, tmp_path)
    global_params = {"sma_period": 20}
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([(0.05, 50), (0.06, 2500)]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflated_sr0") is None
    assert res["metrics_symbol"].get("deflated_dsr") is None
    assert res["metrics_symbol"].get("deflation_skipped_reason") == "N_PERIODS_HETEROGENEOUS"
    assert res["metrics_symbol"].get("deflation_heterogeneity_policy") == "per_stratum"
    _assert_coherent(res)


def test_large_heterogeneous_cohort_recovers_dsr_via_per_stratum(tmp_path, monkeypatch):
    """AK — eine GROSSE, in zwei klare n_periods-Cluster zerfallende Kohorte (Ratio weit über der
    Schwelle) verliert unter 'per_stratum' NICHT die gesamte DSR: das Stratum um den promoteten Trial
    (n_periods=200 aus dem Holdout-Ergebnis) hat >= 2 Mitglieder und liefert eine gültige,
    kommensurable SR0/DSR — der eigentliche Fortschritt gegenüber 'suppress_dsr'."""
    _isolate(monkeypatch, tmp_path)
    global_params = {"sma_period": 20}
    # 6 Trials: 3 im n_periods~200-Cluster (kommensurabel zum promoteten Holdout-n_periods=200),
    # 3 im n_periods~9000-Cluster (weit entfernt) -- Ratio weit ueber dem Default 4.0.
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([
        (0.10, 190), (0.12, 200), (0.09, 210), (0.5, 8900), (0.4, 9000), (0.6, 9100),
    ]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=6)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflated_sr0") is not None
    assert res["metrics_symbol"].get("deflation_skipped_reason") is None
    assert res["metrics_symbol"].get("deflation_stratum_n") >= 2
    _assert_coherent(res)
    # Issue #1013 (Katalog #858, Fix Punkt 2) — ohne dieses Feld ist die 'per_stratum'-Politik nicht
    # prüfbar; das Stratum selbst (n_periods 190/200/210) ist kommensurabel (ratio nahe 1.0).
    stratum_ratio = res["metrics_symbol"].get("deflation_stratum_n_periods_ratio")
    assert stratum_ratio is not None and stratum_ratio < 4.0
    # Issue #1013 — der frueher hart-invertierte Waechter muss diese korrekte per_stratum-Erholung
    # jetzt als PASS erkennen (vorher: garantierter False Positive genau in diesem Fall).
    from automation.optimizer import invariants as inv
    coherence = inv.check_family_n_periods_homogeneity(res["metrics_symbol"])
    assert coherence.passed is True, coherence.detail


def test_suppress_dsr_policy_nulls_before_promotion_decision(tmp_path, monkeypatch):
    """AK (Fix-Item 2 des Katalogs) — unter 'suppress_dsr' ist eine heterogene Kohorte NICHT mehr
    stillschweigend promotionsfähig: SR0/DSR werden None VOR der Promotion-Entscheidung, das
    bestehende REJECT_HOLDOUT_DSR_DROP-Gate greift (holdout_passed wird False, sofern es zuvor True
    war)."""
    _isolate(monkeypatch, tmp_path,
             tournament_extra={"deflation_heterogeneity_policy": "suppress_dsr"})
    global_params = {"sma_period": 20}
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([
        (0.10, 190), (0.12, 200), (0.09, 210), (0.5, 8900), (0.4, 9000), (0.6, 9100),
    ]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=6)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflated_sr0") is None
    assert res["metrics_symbol"].get("deflated_dsr") is None
    assert res["metrics_symbol"].get("deflation_skipped_reason") == "N_PERIODS_HETEROGENEOUS"
    assert res["promote"] is False
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_DSR_DROP"
    _assert_coherent(res)


def test_reject_policy_forces_unconditional_veto_not_bypassable_via_robust_pair(tmp_path, monkeypatch):
    """AK (Fix-Item 2, verschärft) — unter 'reject' ist eine heterogene Kohorte ein UNBEDINGTER,
    eigenständiger Ablehnungsgrund (REJECT_DEFLATION_HETEROGENEOUS), der selbst bei
    promotion_correction_mode='dsr_or_robust_pair' NICHT über den PBO/Bootstrap-CI-Ersatzpfad
    umgangen werden kann -- eine undefinierte Korrektur ist kein bestandener Test, unabhängig davon,
    ob die anderen Bestätigungen sonst passen würden."""
    _isolate(monkeypatch, tmp_path, tournament_extra={
        "deflation_heterogeneity_policy": "reject",
        "promotion_correction_mode": "dsr_or_robust_pair",
    })
    global_params = {"sma_period": 20}
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([
        (0.10, 190), (0.12, 200), (0.09, 210), (0.5, 8900), (0.4, 9000), (0.6, 9100),
    ]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=6)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["promote"] is False
    # Issue #1002/#1154 (Katalog #1170) — die Heterogenitaets-Ablehnung ist eine Deflations-,
    # keine Holdout-Gate-Ablehnung.
    assert res["status"] == "REJECTED_ON_DEFLATION"
    assert res["is_rejection_detail_override"] == "REJECT_DEFLATION_HETEROGENEOUS"
    _assert_coherent(res)


def test_homogeneous_cohort_unaffected_by_heterogeneity_policy(tmp_path, monkeypatch):
    """Regressionswächter — eine bereits homogene Kohorte (Ratio unter der Schwelle) bleibt für ALLE
    drei Politiken bit-identisch unverändert (die Politik greift NUR bei tatsächlich überschrittener
    Ratio)."""
    for policy in ("suppress_dsr", "reject", "per_stratum"):
        _isolate(monkeypatch, tmp_path,
                 tournament_extra={"deflation_heterogeneity_policy": policy})
        global_params = {"sma_period": 20}
        monkeypatch.setattr(ro, "run_backtest", _cohort_factory([(0.05, 200), (0.06, 240)]))
        study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)

        symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02,
                                        n_periods=200)
        global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01,
                                        n_periods=200)

        res = confirm.confirm_per_symbol_promotion(
            study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
        )
        assert res["metrics_symbol"].get("deflated_sr0") is not None, policy
        assert res["metrics_symbol"].get("deflation_skipped_reason") is None, policy
        _assert_coherent(res)


def test_deflation_heterogeneity_policy_always_telemetered(tmp_path, monkeypatch):
    """AK — das Feld ist IMMER im Winner-Eintrag vorhanden, auch wenn deflated_selection nie
    heterogen war (macht den Konfigurationszustand selbst auditierbar)."""
    _isolate(monkeypatch, tmp_path)
    global_params = {"sma_period": 20}
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([(0.05, 200), (0.06, 210)]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    assert res["metrics_symbol"].get("deflation_heterogeneity_policy") == "per_stratum"
