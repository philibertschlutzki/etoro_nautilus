"""Issue #1101 (Katalog #934) — das Randlösungs-Veto (#622/#763) ist inhaltlich korrekt, aber
``HOLD_BOUNDARY_UNRESOLVED`` blieb bislang ein UNBESTIMMTER Zustand: die Bounds-Weitung
(``sweep_diagnostics.propose_bounds_from_boundary_hits``/``record_diagnosed_pair``, #761/#763/#1066)
schreibt bereits einen konkreten, gedeckelten Bounds-Vorschlag in den #761-Cache, den der NÄCHSTE
Sweep-Lauf desselben Paars automatisch aufgreift (``spaces._load_auto_proposed_bounds``) — das IST
bereits der "gezielte Nachlauf mit geweitetem Band". Was fehlte: das SCHLIESSEN der Schleife, wenn
dieser Nachlauf erneut auf demselben Parameter landet.

Fix: ``confirm.confirm_per_symbol_promotion`` exportiert jetzt ``boundary_parameter``/
``boundary_side`` (Akzeptanzkriterium 1) und erkennt, wenn der aktuelle Gewinner bereits unter
einem für ``boundary_parameter`` bis zur Weitungs-Sperre (``sweep_diagnostics.
_MAX_WIDEN_APPLICATIONS``) ausgereizten Suchraum evaluiert wurde — dann eskaliert der Ausgang von
``HOLD_BOUNDARY_UNRESOLVED`` zum terminalen ``REJECT_BOUNDARY_SOLUTION_PERSISTENT`` statt erneut
unbestimmt zu bleiben (Akzeptanzkriterium 2).
"""
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import sweep_diagnostics as sd
from automation.optimizer import trial_config


def _cfg_dir(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    opt_cfg = {
        "promotion_margin": 0.10, "penalty_unevaluable_oos": -10,
        "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0,
        "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
        "bonus_coverage_weight": 1.0, "evaluable_floor_epsilon": 0.001,
        "lambda_reg": 0.25,
        # Issue #1090 (Katalog #923) — diagnostic_writeback_enabled ist seit diesem Fix in der
        # ausgelieferten optimizer.json standardmaessig false; dieser Test uebt den
        # Rueckschrieb-Pfad selbst, daher bewusst wieder aktiviert.
        "diagnostic_writeback_enabled": True,
    }
    with open(cfg_dir / "optimizer.json", "w", encoding="utf-8") as f:
        json.dump(opt_cfg, f)
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
    # Issue #1101 — dieselbe Cache-Datei-Isolation wie test_issue_777s
    # test_bounds_for_uses_cache_entry_written_via_boundary_writeback: ohne diese Umleitung liest
    # load_diagnosed_pairs_cache() das REALE data/optimizer/diagnostics/diagnosed_pairs_cache.json
    # (dieselbe Lazy-Import-Konvention wie run_optimization.py's eigene Aufrufstellen, siehe
    # sweep_diagnostics._diagnosed_pairs_cache_path-Docstring) — deterministisch statt vom
    # Repo-Zustand abhaengig.
    monkeypatch.setattr(sd, "_diagnosed_pairs_cache_path",
                        lambda work_dir=None: tmp_path / "diagnosed_pairs_cache.json")


def _seed_prior_boundary_entry(tmp_path, *, widen_applications, first_seen_run_id="run-widen-1"):
    path = tmp_path / "diagnosed_pairs_cache.json"
    path.write_text(json.dumps({"pairs": [{
        "strategy": "DynamicBreakoutStrategy", "symbol": "TSLA.ETORO",
        "action": "search_space_override", "binding_cause": "boundary_solution",
        "widen_applications": widen_applications,
        "first_seen_run_id": first_seen_run_id,
        "n_runs_confirmed": 1, "expires_after_runs": 10, "runs_since_recorded": 0,
    }]}), "utf-8")


def _result_payload(*, sortino_ratio, dd, sortino_period, n_periods):
    return {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino_ratio],
            "oos_metrics": {
                "sortino_ratio": sortino_ratio, "max_drawdown": dd,
                "total_return": 0.02, "total_trades": 50,
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
    import itertools
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


def _run_confirm(tmp_path, monkeypatch, *, boundary_fraction, boundary_directions, run_id=None):
    global_params = {"price_breakout_period": 20}
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: boundary_fraction)
    monkeypatch.setattr(ro, "_boundary_hit_directions", lambda *a, **k: boundary_directions)
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([0.02, 0.025]))
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=400)
    return confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
        run_id=run_id,
        # Issue #1091/#1239: resolvierte (minimale) Familien-N haelt die Fixture aus dem neuen
        # unbedingten Hard-Stop heraus, ohne deflation_n_effective zu veraendern (siehe
        # confirm.py) -- diese Tests pruefen die Boundary-Route, nicht die Familien-Multiplizitaet.
        deflation_n_family=1,
    )


# ── Akzeptanzkriterium 1: boundary_parameter/boundary_side im Report ────────────────────────────
def test_boundary_parameter_and_side_exported_for_hold_unresolved(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    res = _run_confirm(tmp_path, monkeypatch, boundary_fraction=0.5,
                       boundary_directions={"cooldown_bars": "high"})
    assert res["status"] == "HOLD_BOUNDARY_UNRESOLVED"
    assert res["metrics_symbol"]["boundary_parameter"] == "cooldown_bars"
    assert res["metrics_symbol"]["boundary_side"] == "high"
    assert res["metrics_symbol"]["boundary_directions"] == {"cooldown_bars": "high"}


def test_boundary_parameter_picks_deterministic_first_of_multiple(tmp_path, monkeypatch):
    """Mehrere klemmende Parameter -- alphabetisch erster gewinnt, deterministisch."""
    _isolate(monkeypatch, tmp_path)
    res = _run_confirm(tmp_path, monkeypatch, boundary_fraction=0.6,
                       boundary_directions={"cooldown_bars": "high", "adx_period": "low"})
    assert res["metrics_symbol"]["boundary_parameter"] == "adx_period"
    assert res["metrics_symbol"]["boundary_side"] == "low"


def test_boundary_parameter_none_without_any_boundary_hit(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    res = _run_confirm(tmp_path, monkeypatch, boundary_fraction=0.0, boundary_directions={})
    assert res["metrics_symbol"]["boundary_parameter"] is None
    assert res["metrics_symbol"]["boundary_side"] is None


# ── Akzeptanzkriterium 2: nie unbestimmt -- PROMOTED/REJECTED_*/REJECT_BOUNDARY_SOLUTION_PERSISTENT
def test_stays_hold_when_no_prior_widening_attempt_exists(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)   # frischer Cache, keine Vorgeschichte.
    res = _run_confirm(tmp_path, monkeypatch, boundary_fraction=0.5,
                       boundary_directions={"cooldown_bars": "high"})
    assert res["status"] == "HOLD_BOUNDARY_UNRESOLVED"
    assert res["metrics_symbol"]["boundary_resolution_exhausted"] is False
    assert res["metrics_symbol"]["boundary_resolution_run_id"] is None


def test_stays_hold_when_widen_count_below_the_cap(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _seed_prior_boundary_entry(tmp_path, widen_applications={"cooldown_bars": 1})
    res = _run_confirm(tmp_path, monkeypatch, boundary_fraction=0.5,
                       boundary_directions={"cooldown_bars": "high"})
    assert res["status"] == "HOLD_BOUNDARY_UNRESOLVED"
    assert res["metrics_symbol"]["boundary_resolution_exhausted"] is False


def test_escalates_to_persistent_when_widen_cap_already_reached(tmp_path, monkeypatch):
    """Referenzsymptom #934: derselbe Parameter klebt erneut am Rand, NACHDEM die Weitungs-Sperre
    (sweep_diagnostics._MAX_WIDEN_APPLICATIONS) bereits erreicht wurde -- kein Suchraum-Artefakt
    mehr, sondern ein reproduziertes Optimum. Terminaler Ausgang statt eines erneuten HOLD."""
    _isolate(monkeypatch, tmp_path)
    assert sd._MAX_WIDEN_APPLICATIONS == 2
    _seed_prior_boundary_entry(tmp_path, widen_applications={"cooldown_bars": 2},
                               first_seen_run_id="run-widen-1")
    res = _run_confirm(tmp_path, monkeypatch, boundary_fraction=0.5,
                       boundary_directions={"cooldown_bars": "high"}, run_id="run-current")
    assert res["status"] == "REJECT_BOUNDARY_SOLUTION_PERSISTENT"
    assert res["is_rejection_detail_override"] == "REJECT_BOUNDARY_SOLUTION_PERSISTENT"
    assert res["promote"] is False
    assert res["metrics_symbol"]["boundary_resolution_exhausted"] is True
    assert res["metrics_symbol"]["boundary_resolution_run_id"] == "run-widen-1"


def test_escalation_is_scoped_to_the_specific_boundary_parameter(tmp_path, monkeypatch):
    """Eine bereits ausgereizte Weitung an EINEM Parameter darf einen Kandidaten, der an einem
    ANDEREN Parameter klemmt, nicht faelschlich eskalieren."""
    _isolate(monkeypatch, tmp_path)
    _seed_prior_boundary_entry(tmp_path, widen_applications={"ema_period": 2})
    res = _run_confirm(tmp_path, monkeypatch, boundary_fraction=0.5,
                       boundary_directions={"cooldown_bars": "high"})
    assert res["status"] == "HOLD_BOUNDARY_UNRESOLVED"
    assert res["metrics_symbol"]["boundary_resolution_exhausted"] is False


def test_below_hold_threshold_boundary_overfit_is_unaffected_by_resolution_logic(tmp_path, monkeypatch):
    """REJECTED_BOUNDARY_SOLUTION (0.3 < frac < 0.5) bleibt terminal wie bisher -- die
    Eskalationslogik betrifft ausschliesslich den vorher UNBESTIMMTEN HOLD-Zustand."""
    _isolate(monkeypatch, tmp_path)
    _seed_prior_boundary_entry(tmp_path, widen_applications={"cooldown_bars": 2})
    res = _run_confirm(tmp_path, monkeypatch, boundary_fraction=0.4,
                       boundary_directions={"cooldown_bars": "high"})
    assert res["status"] == "REJECTED_BOUNDARY_SOLUTION"


# ── report.py-Verdrahtung ─────────────────────────────────────────────────────────────────────────
def test_stage_for_reject_detail_covers_the_new_terminal_status():
    from automation.optimizer.report import _STAGE_FOR_REJECT_DETAIL
    assert _STAGE_FOR_REJECT_DETAIL["REJECT_BOUNDARY_SOLUTION_PERSISTENT"] == "boundary"


def test_confirm_stage_rejections_includes_the_new_terminal_status():
    from automation.optimizer.confirm import _CONFIRM_STAGE_REJECTIONS
    assert "REJECT_BOUNDARY_SOLUTION_PERSISTENT" in _CONFIRM_STAGE_REJECTIONS


def test_boundary_solutions_section_surfaces_parameter_and_widen_applications(tmp_path, monkeypatch):
    import automation.optimizer.report as report_mod

    monkeypatch.setattr(report_mod, "_diagnosed_pairs_all", lambda: [{
        "strategy": "S", "symbol": "X.ETORO", "binding_cause": "boundary_solution",
        "boundary_hit_fraction": 0.55, "boundary_params": {"cooldown_bars": 40},
        "proposed_bounds": {"cooldown_bars": [1, 60]},
        "boundary_parameter": "cooldown_bars", "boundary_side": "high",
        "widen_applications": {"cooldown_bars": 2},
    }])
    section = report_mod._boundary_solutions_section()
    assert len(section) == 1
    assert section[0]["boundary_parameter"] == "cooldown_bars"
    assert section[0]["boundary_side"] == "high"
    assert section[0]["widen_applications"] == {"cooldown_bars": 2}
