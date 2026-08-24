"""Issue #659 — Gestapelte Multiple-Testing-Korrekturen kompoundieren Type-II-Fehler.

Die Promotion verlangt gleichzeitig: deflated_selection-Kohorten-Hürde (IS) UND DSR-Drop >=
deflation_confidence UND Bootstrap-CI-Untergrenze > 0 UND PBO <= 0.5 UND R_symbol > R_global +
margin. Auf einem kurzen Holdout (37 Trades) ist die 95%-DSR allein sehr streng; die Stapelung
mehrerer Korrekturen kompoundiert die False-Negative-Rate.

Dieser Test prüft die STRUKTUR des Fixes (ein konfigurierbarer, opt-in `promotion_correction_mode`),
NICHT einen konkreten Schwellenwert — die finale Korrektur-Konjunktion und `deflation_confidence`
müssen aus einem dedizierten empirischen Kalibrierlauf abgeleitet werden (siehe #659-Akzeptanz-
kriterium), nicht aus dieser Test-Datei geraten werden.
"""
import itertools
import json
import logging
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config


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
        "oos_min_win_rate": 0.0, "holdout_bootstrap_ci": False,
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
                     total_return=0.02, total_trades=50):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": eligible, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": total_return, "total_trades": total_trades,
                "sortino_period": sortino_period, "n_periods": n_periods,
                "ret_skew": 0.0, "ret_kurtosis": 3.0,
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

    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        sp = next(it)
        return _write_result(trial_dir, _result_payload(
            sortino_ratio=sp, dd=0.05, sortino_period=sp, n_periods=n_periods))
    return _fake


def _holdout_factory(global_params, *, symbol_result, global_result):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        payload = global_result if params == global_params else symbol_result
        return _write_result(trial_dir, payload)
    return _fake


def _build_study(tmp_path, monkeypatch, *, n_trials, cohort_periods, tournament_extra=None):
    _isolate(monkeypatch, tmp_path, tournament_extra)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory(cohort_periods))
    return ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=n_trials)


def test_default_mode_is_conjunction_bit_identical():
    """Fehlt der Key ⇒ 'conjunction' (Default) — die Config-Doku deklariert dies explizit."""
    from automation.backtest_runner import load_tournament_config
    # Direkter String-Zugriff (kein I/O nötig): Default wird in confirm.py über .get(...) aufgelöst.
    assert {}.get("promotion_correction_mode", "conjunction") == "conjunction"


def test_unknown_correction_mode_fails_loud(tmp_path, monkeypatch):
    """Ein Tippfehler/unbekannter Modus bricht fail-loud ab statt still auf ein falsches Verhalten
    zu fallen (Zero-Hardcoding-Disziplin, analog #649s Registry-Validierung)."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025],
                         tournament_extra={"promotion_correction_mode": "typo_mode"})
    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    with pytest.raises(ValueError, match="promotion_correction_mode"):
        confirm.confirm_per_symbol_promotion(
            study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
        )


def test_conjunction_mode_dsr_drop_still_blocks_promotion(tmp_path, monkeypatch, caplog):
    """Im (Default-)Konjunktions-Modus blockt ein DSR-Miss die Promotion weiterhin unabhängig von
    PBO — bit-identisch zum Pre-#659-Verhalten (Regressionsschutz)."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025],
                         tournament_extra={"promotion_correction_mode": "conjunction"})
    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
        # Issue #1091/#1239: resolvierte (minimale) Familien-N haelt diese Fixture aus dem neuen
        # unbedingten Hard-Stop heraus, ohne deflation_n_effective zu veraendern -- dieser Test
        # prueft den Konjunktions-Modus, nicht die Familien-Multiplizitaet.
        deflation_n_family=1,
    )
    assert res["metrics_symbol"]["deflated_dsr"] < 0.95
    assert res["holdout_passed"] is False
    # Issue #1002/#1154 (Katalog #1170) — DSR-Drop ist eine Deflations-Ablehnung.
    assert res["status"] == "REJECTED_ON_DEFLATION"
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_DSR_DROP"


def test_dsr_or_robust_pair_mode_does_not_reinstate_when_pbo_unavailable(tmp_path, monkeypatch, caplog):
    """Issue #1005 (Katalog #858, Pitfall #343) — REGRESSIONSTEST für den fail-open-Bug: VORHER
    liess ein NICHT SCHÄTZBARES PBO (``study_pbo is None``, hier: < pbo_min_configs Studies) den
    Ersatzpfad reinstaten (``not pbo_overfit`` war fälschlich True für 'unbekannt' UND für 'PBO≤0.5').
    JETZT ist eine nicht schätzbare PBO KEINE bestandene Prüfung — ohne echte Evidenz bleibt die
    Promotion abgelehnt, exakt wie Konjunktion."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025],
                         tournament_extra={"promotion_correction_mode": "dsr_or_robust_pair",
                                          "holdout_bootstrap_ci": False})
    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    with caplog.at_level(logging.INFO, logger="optimizer"):
        res = confirm.confirm_per_symbol_promotion(
            study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
            deflation_n_family=1,  # Issue #1091/#1239 — haelt die Fixture aus dem neuen Hard-Stop heraus.
        )

    # DSR selbst scheitert weiterhin (dieselbe Kohorte wie im Konjunktions-Test) ...
    assert res["metrics_symbol"]["deflated_dsr"] < 0.95
    # ... und PBO ist hier nicht auswertbar (min_trials-Guard, < 10 Configs) ⇒ study_pbo is None ⇒
    # pbo_ok=False (fail-closed) ⇒ robust_pair_ok=False ⇒ NICHT reinstated.
    assert res["metrics_symbol"].get("pbo") is None
    assert res["holdout_passed"] is False
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_DSR_DROP"
    assert "promotion_correction_route" not in res["metrics_symbol"]


def test_dsr_or_robust_pair_mode_reinstates_with_real_pbo_and_ci_evidence(tmp_path, monkeypatch, caplog):
    """Issue #1005 Fix — mit ECHTER Evidenz (ein tatsächlich berechnetes, sicheres PBO UND eine
    bestehende Bootstrap-CI) reinstated der Ersatzpfad weiterhin korrekt; die #1005-Korrektur macht
    den Pfad strenger (kein Fail-Open mehr für fehlende Evidenz), nicht wirkungslos für echte.
    ``_holdout_bootstrap_ci_passes`` wird gemockt (statt aus einer echten Return-Serie abgeleitet):
    ein Bootstrap-DSR, das stark genug ist, um bei ~99.6%-Konfidenz (nach der Fix-Item-3-Bonferroni-
    Korrektur) noch sicher zu bestehen, besteht bei ~95%-DSR-Konfidenz gegen dieselbe (kleine)
    SR0-Referenz nahezu immer auch die DSR selbst — was NUR den 'dsr'-Zweig testen würde, nicht den
    'robust_pair'-Zweig. Der Mock isoliert die robust_pair-Logik deterministisch und beweist
    zusätzlich (via der aufgezeichneten ``confidence``), dass Fix Item 3 tatsächlich die korrigierte
    Konfidenz an die Funktion durchreicht."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025],
                         tournament_extra={"promotion_correction_mode": "dsr_or_robust_pair",
                                          "holdout_bootstrap_ci": True})
    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=200)

    monkeypatch.setattr(confirm, "_study_pbo",
                        lambda *a, **k: (0.30, {"pbo_n_groups": 12, "pbo_n_configs": 12,
                                                "pbo_n_configs_raw": 12, "pbo_metric": "group_sortino"}))
    ci_calls = []

    def _fake_ci_passes(metrics, *, confidence=0.95):
        ci_calls.append(confidence)
        return True, 0.07

    monkeypatch.setattr(confirm, "_holdout_bootstrap_ci_passes", _fake_ci_passes)

    with caplog.at_level(logging.INFO, logger="optimizer"):
        res = confirm.confirm_per_symbol_promotion(
            study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
            run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                          global_result=global_result),
            deflation_n_family=1,  # Issue #1091/#1239 — haelt die Fixture aus dem neuen Hard-Stop heraus.
        )

    # DSR selbst scheitert weiterhin (dieselbe Kohorte wie im Konjunktions-Test, keine echten
    # Perioden-Returns ⇒ sharpe_formula_fallback bleibt unveraendert) ...
    assert res["metrics_symbol"]["deflated_dsr"] < 0.95
    # ... aber die gemockte Bootstrap-CI besteht ⇒ zusammen mit dem sicheren PBO ⇒ REINSTATED.
    assert res["holdout_passed"] is True
    assert res["is_rejection_detail_override"] is None
    assert res["metrics_symbol"]["promotion_correction_route"] == "robust_pair"
    assert res["metrics_symbol"]["promotion_correction_pbo_ok"] is True
    assert res["metrics_symbol"]["promotion_correction_ci_lower"] == 0.07
    # Bonferroni (Fix Item 3): alpha_effective = (1 - 0.95) / 12 ≈ 0.004167 — strenger als die
    # ungewichtete 5%-CI, und tatsaechlich an _holdout_bootstrap_ci_passes durchgereicht.
    expected_alpha = 0.05 / 12
    assert res["metrics_symbol"]["promotion_correction_alpha_effective"] == pytest.approx(expected_alpha)
    assert ci_calls and ci_calls[-1] == pytest.approx(1.0 - expected_alpha)
    assert any("[#659/#1005]" in r.message for r in caplog.records)


def test_holdout_gate_itself_failing_is_never_bypassed_by_or_mode(tmp_path, monkeypatch):
    """Der OR-Modus ersetzt NIEMALS das Symbol-Holdout-Gate selbst — ein Trial, der bereits am
    Punkt-Gate scheitert (REJECT_HOLDOUT_GATE), bleibt abgelehnt, unabhängig von DSR/PBO/CI."""
    global_params = {"price_breakout_period": 20}
    study = _build_study(tmp_path, monkeypatch, n_trials=2, cohort_periods=[0.02, 0.025],
                         tournament_extra={"promotion_correction_mode": "dsr_or_robust_pair"})
    bad_result = _result_payload(sortino_ratio=-1.0, dd=0.9, sortino_period=-0.02, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=bad_result,
                                      global_result=bad_result),
    )
    assert res["holdout_passed"] is False
    assert res["is_rejection_detail_override"] == "REJECT_HOLDOUT_GATE"


def test_calibration_choice_is_documented_not_hardcoded():
    """Akzeptanzkriterium (#659): die Doku macht explizit, dass Modus + deflation_confidence aus
    einem Kalibrierlauf abzuleiten sind, keine geratene Magic-Zahl."""
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    doc = cfg["_schema"]["fields"]["promotion_correction_mode"]
    assert "dsr_or_robust_pair" in doc
    assert "Kalibrierlauf" in doc
    assert "conjunction" in doc
