"""Issue #831 (P1, Katalog #828-#835, GitHub-Issue #751) — propose_bounds_widening ist nur aus dem
STRUCTURAL-Zweig erreichbar: 37 Randlösungen ohne Vorschlag.

Root-Cause: der #763/#777-Bounds-Vorschlagspfad (``confirm.py``: ``propose_bounds_from_boundary_
hits`` + ``record_diagnosed_pair(binding_cause='boundary_solution')``) feuert erst INNERHALB
``confirm_per_symbol_promotion`` — die Funktion kehrt aber sofort zurück (``if not eligible_trials:
return``), BEVOR dieser Code je erreicht wird, wenn eine Study NIE einen eligiblen Trial produziert
(STRUCTURAL_ALL_UNEVALUABLE/ZERO_ELIGIBLE_PLATEAU). Eine solche Study kann trotzdem eine
[#597]-Randlösungs-Warnung tragen (``_boundary_hit_fraction``/``_emit_study_summary`` ranken nach
REWARD, nicht nach Eligibility) — für genau diese Studies entstand nie ein maschinenlesbarer
Bounds-Vorschlag.

Fix Punkt 1: derselbe Vorschlags-/Cache-Schreibpfad läuft jetzt AUCH direkt in
``run_optimization._emit_study_summary`` (demselben Post-Study-Pfad, in dem die [#597]-Warnung
entsteht), aber NUR wenn die Study 0 eligible Trials hat (``confirm.py``s eigener Pfad bleibt für
JEDE andere Study die einzige Schreibquelle — beide Pfade sind exklusiv, kein doppeltes
``record_diagnosed_pair`` für dasselbe Paar im selben Lauf).

Fix Punkt 2: ``WIRED_OVERRIDE_STRATEGIES`` (eine seit #681 eingefrorene 3-Strategien-Allowlist von
14 aktiven Strategien) ist ERSATZLOS ENTFERNT, ersetzt durch die ABGELEITETE Prüfung
``_strategy_supports_search_space_override`` (``bounds.extract_numeric_bounds`` nicht-leer) — siehe
test_issue_681_diagnosis_closed_loop.py/test_issue_699_adxatr_trendpullback_closed_loop.py für die
aktualisierten Regressionstests.

Fix Punkt 3 (Richtungssicherheit + ökonomische Obergrenze) war bereits über #763/#777
(``_widen_bounds_toward``, ``max_bars_in_trade``-Deckelung auf die #714-Zeitbox) vollständig
implementiert — keine Änderung nötig, siehe test_issue_777_boundary_writeback.py.

Fix Punkt 4: ``report.py`` weist Randlösungen jetzt als eigene ``cross_study['boundary_solutions']``-
Sektion aus (``{strategy, symbol, fraction, params, proposed_bounds}``).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config
from automation.optimizer import report as _report
from automation.optimizer.sweep_diagnostics import _strategy_supports_search_space_override


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
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path))


def _zero_eligible_payload():
    return {"fully_eligible_pairs": 0, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": False, "win_count": 0,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [0.1],
        "oos_metrics": {"sortino_ratio": 0.1, "max_drawdown": 0.05,
                       "total_return": 0.02, "total_trades": 50,
                       "sortino_period": 0.1, "n_periods": 200,
                       "ret_skew": 0.0, "ret_kurtosis": 3.0}}}


def _write_result(trial_dir, payload):
    out = Path(trial_dir) / "tournament_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), "utf-8")
    return out


def _fake_zero_eligible(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
    return _write_result(trial_dir, _zero_eligible_payload())


# ── Fix Punkt 1: boundary_hit_fraction > 0.3 auf einer 0-eligible Study schreibt jetzt ─────────────
def test_zero_eligible_study_with_boundary_hit_writes_diagnosed_pair(tmp_path, monkeypatch):
    """Akzeptanzkriterium #831: eine durchlaufende Study mit boundary_hit_fraction=0.5 (0 eligible
    Trials -- erreicht confirm() nie) erzeugt jetzt einen Vorschlag."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.5)
    monkeypatch.setattr(ro, "_boundary_hit_directions", lambda *a, **k: {"cooldown_bars": "high"})
    monkeypatch.setattr(ro, "run_backtest", _fake_zero_eligible)

    recorded = []
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sd, "record_diagnosed_pair", lambda rec, **k: recorded.append(rec))

    ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    assert len(recorded) == 1
    assert recorded[0]["binding_cause"] == "boundary_solution"
    assert recorded[0]["action"] == "search_space_override"
    assert "cooldown_bars" in recorded[0]["proposed_bounds"]
    assert recorded[0]["boundary_hit_fraction"] == 0.5


def test_zero_eligible_study_below_threshold_writes_nothing(tmp_path, monkeypatch):
    """Akzeptanzkriterium #831: mit boundary_hit_fraction=0.2 (< 0.3) entsteht KEIN Vorschlag."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.2)
    monkeypatch.setattr(ro, "_boundary_hit_directions", lambda *a, **k: {"cooldown_bars": "high"})
    monkeypatch.setattr(ro, "run_backtest", _fake_zero_eligible)

    recorded = []
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sd, "record_diagnosed_pair", lambda rec, **k: recorded.append(rec))

    ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    assert recorded == []


def test_eligible_study_does_not_double_write_via_the_new_path(tmp_path, monkeypatch):
    """Eine Study MIT >= 1 eligiblem Trial erreicht confirm.py's eigenen Boundary-Pfad -- die neue
    _emit_study_summary-Ergaenzung darf hierfuer NICHT ein zweites Mal schreiben (sonst wuerde
    n_runs_confirmed faelschlich zweimal statt einmal pro Lauf inkrementiert)."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.5)
    monkeypatch.setattr(ro, "_boundary_hit_directions", lambda *a, **k: {"cooldown_bars": "high"})

    def _fake_eligible(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        payload = _zero_eligible_payload()
        payload["fully_eligible_pairs"] = 1
        payload["aggregate_winner"]["oos_eligible"] = True
        return _write_result(trial_dir, payload)
    monkeypatch.setattr(ro, "run_backtest", _fake_eligible)

    recorded = []
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sd, "record_diagnosed_pair", lambda rec, **k: recorded.append(rec))

    ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    # _emit_study_summary selbst schreibt hier NICHTS (confirm() wurde in diesem Test gar nicht
    # aufgerufen -- der Nachweis ist, dass der neue Pfad bei >=1 eligiblem Trial inaktiv bleibt).
    assert recorded == []


# ── Fix Punkt 2: WIRED_OVERRIDE_STRATEGIES entfernt, abgeleitete Pruefung ersetzt sie ──────────────
def test_wired_override_strategies_constant_no_longer_exists():
    import automation.optimizer.sweep_diagnostics as sd
    assert not hasattr(sd, "WIRED_OVERRIDE_STRATEGIES")


def test_derived_check_covers_all_active_strategies_with_a_search_space():
    """Akzeptanzkriterium #831: die Ableitung deckt alle aktiven Strategien ab, nicht nur die alten
    3 von 14 -- exemplarisch eine, die vorher NICHT verdrahtet war."""
    assert _strategy_supports_search_space_override("ComboTrendVwapStrategy") is True
    assert _strategy_supports_search_space_override("SmaCrossoverStrategy") is True


def test_derived_check_is_false_for_an_unknown_strategy():
    assert _strategy_supports_search_space_override("NoSuchStrategy") is False


# ── Fix Punkt 4: report.boundary_solutions ──────────────────────────────────────────────────────────
def test_report_boundary_solutions_section_projects_expected_fields():
    fake_cache = {
        ("DynamicBreakoutStrategy", "ADBE.ETORO"): {
            "strategy": "DynamicBreakoutStrategy", "symbol": "ADBE.ETORO",
            "action": "search_space_override", "binding_cause": "boundary_solution",
            "proposed_bounds": {"cooldown_bars": [1.0, 50.0]},
            "boundary_hit_fraction": 0.75,
            "boundary_params": {"cooldown_bars": 36.0},
        },
        ("AdxAtrMomentumStrategy", "TSLA.ETORO"): {
            "strategy": "AdxAtrMomentumStrategy", "symbol": "TSLA.ETORO",
            "action": "denylist", "binding_cause": "signal_absent",
        },
    }
    from unittest.mock import patch
    with patch("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
              return_value=fake_cache):
        section = _report._boundary_solutions_section()
    assert len(section) == 1
    assert section[0]["strategy"] == "DynamicBreakoutStrategy"
    assert section[0]["fraction"] == 0.75
    assert section[0]["params"] == {"cooldown_bars": 36.0}
    assert section[0]["proposed_bounds"] == {"cooldown_bars": [1.0, 50.0]}


def test_report_boundary_solutions_is_wired_into_cross_study(monkeypatch, tmp_path):
    monkeypatch.setattr("automation.optimizer.sweep_diagnostics.load_diagnosed_pairs_cache",
                        lambda *a, **k: {})
    report_dict = _report._build_report(
        [], run_id="testrun", started_at_utc=None, wallclock_s=None, cli_args=None,
        reports_dir=tmp_path)
    assert "boundary_solutions" in report_dict["cross_study"]
    assert report_dict["cross_study"]["boundary_solutions"] == []
