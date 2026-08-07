"""Issue #958 (Katalog D, P0) — ein Trial mit ``oos_n_periods == 0`` (oder ohne verwertbare
Selektions-Teststatistik) darf nicht als Study-Bester/promoteter Trial nominiert werden.

Root-Cause: ``oos_eligible`` allein (total_trades > 0, kein EXIT_CLOSE_UNRECOVERABLE) sagt nichts
über die Verfuegbarkeit von Sortino/PSR aus. Ein Trial ohne eine einzige OOS-Periode hat weder
Sortino noch PSR noch Profit-Faktor noch Expectancy — er kann nicht "am besten" sein, konnte aber
vor diesem Fix trotzdem in die Top-k-Holdout-Kohorte gelangen und zum ``promoted_trial`` werden
(nachgelagert sichtbar als "Stratum None" in der Deflations-Stratifizierung, #865).

Der Fix erweitert ``confirm.confirm_per_symbol_promotion``s ``eligible_trials``-Filter um
``oos_n_periods >= 1`` und ``oos_selection_statistic_available is True`` — ein Symbol ohne
zulaessigen Trial durchlaeuft danach den bereits bestehenden ``not eligible_trials``-Pfad
(HOLDOUT_NO_ELIGIBLE_TRIALS / PROMOTE_GLOBAL_DEFAULT), statt einen degenerierten Trial zu
nominieren.
"""
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro, confirm
from automation.tests.test_per_symbol_promotion import _cfg_dir, _isolate


def _factory_with_n_periods(tuned_keyvals, sortino, n_periods, dd=0.05, psr=0.9):
    """Wie ``test_per_symbol_promotion._factory_by_params``, aber mit steuerbarem
    ``oos_metrics.n_periods``/``psr`` — genau die Felder, die
    ``oos_n_periods``/``oos_selection_statistic_available`` speisen (parsing.py:236/240)."""
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino],
            "oos_metrics": {"sortino_ratio": sortino, "max_drawdown": dd,
                           "n_periods": n_periods, "psr": psr}}}), "utf-8")
        return out
    return _fake


def test_zero_n_periods_trial_is_never_promoted(tmp_path, monkeypatch):
    tuned = {"sma_period": 60}
    _isolate(monkeypatch, tmp_path)
    # der EINZIGE Trial hat n_periods=0 -> darf nicht als eligible_trial zaehlen, egal wie hoch
    # sein (an sich fragwuerdiger) Sortino-Wert ist.
    monkeypatch.setattr(ro, "run_backtest", _factory_with_n_periods(tuned, 5.0, n_periods=0))
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: dict(tuned))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)
    assert study.best_trial.user_attrs["oos_n_periods"] == 0

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO",
        global_params={"sma_period": 20},
        run_backtest=_factory_with_n_periods(tuned, 5.0, n_periods=0),
    )
    # Die einzige zulaessige Konsequenz: der degenerierte Trial darf NIE zum promoted_trial werden.
    # Je nach globalem Default-Pfad landet das Ergebnis bei REJECTED (kein eligibler Trial) oder
    # PROMOTE_GLOBAL_DEFAULT (der GLOBALE, ungetunte Vektor) — niemals READY_FOR_PR ueber den
    # degenerierten Symbol-Trial.
    assert res["status"] != "READY_FOR_PR"


def test_positive_n_periods_trial_is_still_promotable(tmp_path, monkeypatch):
    """Regressionsschutz: die neue Bedingung darf einen REGULAEREN Trial (n_periods > 0, PSR
    gesetzt) nicht faelschlich ausschliessen."""
    tuned = {"sma_period": 60}
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _factory_with_n_periods(tuned, 2.0, n_periods=200))
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: dict(tuned))
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.0)
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)
    assert study.best_trial.user_attrs["oos_n_periods"] == 200
    assert study.best_trial.user_attrs["oos_selection_statistic_available"] is True

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO",
        global_params={"sma_period": 20},
        run_backtest=_factory_by_params_for_global(tuned, 2.0, 0.5),
    )
    assert res["promote"] is True
    assert res["status"] == "READY_FOR_PR"


def _factory_by_params_for_global(tuned_keyvals, sortino_tuned, sortino_global, dd=0.05):
    """Holdout-Vergleich braucht unterschiedliche Werte fuer getunte vs. globale Params (wie
    test_per_symbol_promotion._factory_by_params), plus n_periods/psr fuer #958."""
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        is_tuned = all(params.get(k) == v for k, v in tuned_keyvals.items())
        s = sortino_tuned if is_tuned else sortino_global
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [s],
            "oos_metrics": {"sortino_ratio": s, "max_drawdown": dd,
                           "n_periods": 200, "psr": 0.9}}}), "utf-8")
        return out
    return _fake
