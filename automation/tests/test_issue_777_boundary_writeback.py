"""Issue #777 (P1) — 32 Randlösungen an genau den Frequenz-Parametern, die die `#769`-Abbrüche
verursachen; der Bounds-Vorschlag entsteht nur im falschen Zweig.

Root-Cause: `[#597]` meldete 32 Studies mit `boundary_hit_fraction` 0,33–0,50 — GENAU der Bereich
zwischen der `#622`-Warnschwelle (> 0,3, reine Prosa) und der `#763`-Cache-Schreib-Schwelle (>= 0,5,
`HOLD_BOUNDARY_UNRESOLVED`). Für keine dieser 32 Studies entstand ein maschinenlesbarer Bounds-
Vorschlag; ``[#597]`` blieb reine Prosa.

Fix: der `#761`-Diagnose-Cache-Write (``propose_bounds_from_boundary_hits``/``record_diagnosed_pair``)
feuert jetzt bei JEDER Randlösung (``boundary_overfit``, frac > 0,3 — dieselbe Schwelle wie die
`#622`-Warnung), nicht mehr erst ab `boundary_unresolved` (>= 0,5); der Weitungsfaktor liest
deklarativ ``optimizer.json['bounds_widening_factor']`` (Default 0.3, bit-identisch); die
Vorschlags-Arithmetik deckelt ``max_bars_in_trade`` hart auf die `#714`-Zeitbox-Obergrenze (24).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import run_optimization as ro, confirm
from automation.optimizer import trial_config
from automation.optimizer.sweep_diagnostics import propose_bounds_from_boundary_hits
from automation.optimizer.spaces import _bounds_for, _auto_proposed_bounds_cache


def _cfg_dir(tmp_path, optimizer_extra=None):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    opt_cfg = {
        "promotion_margin": 0.10, "penalty_unevaluable_oos": -10,
        "unevaluable_shaping_span": 0.25, "sortino_clip_abs": 5.0,
        "penalty_overfit_weight": 0.5, "penalty_dd_weight": 8.0,
        "bonus_coverage_weight": 1.0, "evaluable_floor_epsilon": 0.001,
        "lambda_reg": 0.25,
        # Issue #1090 (Katalog #923) — diagnostic_writeback_enabled ist seit diesem Fix in der
        # ausgelieferten optimizer.json standardmaessig false; dieses Modul testet den
        # Rueckschrieb-Pfad selbst, daher hier bewusst wieder aktiviert.
        "diagnostic_writeback_enabled": True,
    }
    if optimizer_extra:
        opt_cfg.update(optimizer_extra)
    with open(cfg_dir / "optimizer.json", "w", encoding="utf-8") as f:
        json.dump(opt_cfg, f)
    tcfg = {
        "max_drawdown": 0.30, "deflated_selection": True, "deflation_confidence": 0.95,
        "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
        "oos_min_win_rate": 0.0,
    }
    with open(cfg_dir / "tournament.json", "w", encoding="utf-8") as f:
        json.dump(tcfg, f)
    with open(cfg_dir / "backtest.json", "w", encoding="utf-8") as f:
        json.dump({"walk_forward": {"holdout_days": 45, "is_window_days": 120,
                                     "oos_window_days": 45, "splits": 1,
                                     "embargo_period_days": 0}}, f)
    return cfg_dir


def _isolate(monkeypatch, tmp_path, optimizer_extra=None):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: _cfg_dir(tmp_path, optimizer_extra))
    monkeypatch.setattr(confirm, "config_dir", lambda: _cfg_dir(tmp_path, optimizer_extra))
    monkeypatch.setattr(trial_config, "config_dir", lambda: _cfg_dir(tmp_path, optimizer_extra))
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


# ── Akzeptanzkriterium #777/1: eine Randlösung im 0,33-0,50-Graubereich schreibt jetzt einen
# Cache-Eintrag (vorher: nur die #622-Warnung, kein Vorschlag) ────────────────────────────────────
def test_boundary_hit_fraction_0_4_now_writes_cache_entry(tmp_path, monkeypatch):
    """frac=0.4 liegt UNTER der #763-Schwelle (0.5, HOLD_BOUNDARY_UNRESOLVED) aber UEBER der
    #622-Warnschwelle (0.3) — genau der vor #777 nicht abgedeckte Graubereich."""
    global_params = {"price_breakout_period": 20}
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.4)
    monkeypatch.setattr(ro, "_boundary_hit_directions",
                        lambda *a, **k: {"cooldown_bars": "high"})
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([0.02, 0.025]))
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    recorded = []
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sd, "record_diagnosed_pair", lambda rec, **k: recorded.append(rec))

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=400)
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
        deflation_n_family=1,  # Issue #1091/#1239 — haelt die Fixture aus dem neuen Hard-Stop heraus.
    )
    assert res["status"] == "REJECTED_BOUNDARY_SOLUTION"    # #622-Tier bleibt unveraendert
    assert len(recorded) == 1                                # ABER jetzt EIN Cache-Eintrag
    assert recorded[0]["binding_cause"] == "boundary_solution"
    assert "cooldown_bars" in recorded[0]["proposed_bounds"]
    # Obergrenze angehoben (high-Richtung).
    assert recorded[0]["proposed_bounds"]["cooldown_bars"][1] > 36.0


def test_boundary_hit_fraction_0_5_still_writes_cache_entry_via_hold_path(tmp_path, monkeypatch):
    """Regressionsschutz: die staerkere #763-Schwelle (>= 0.5) schreibt weiterhin (jetzt ueber
    denselben vereinheitlichten boundary_overfit-Zweig)."""
    global_params = {"price_breakout_period": 20}
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.5)
    monkeypatch.setattr(ro, "_boundary_hit_directions",
                        lambda *a, **k: {"cooldown_bars": "high"})
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([0.02, 0.025]))
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    recorded = []
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sd, "record_diagnosed_pair", lambda rec, **k: recorded.append(rec))

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=400)
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
        deflation_n_family=1,  # Issue #1091/#1239 — haelt die Fixture aus dem neuen Hard-Stop heraus.
    )
    assert res["status"] == "HOLD_BOUNDARY_UNRESOLVED"
    assert len(recorded) == 1


# ── deklarativer Weitungsfaktor (optimizer.json.bounds_widening_factor) ────────────────────────────
def test_confirm_reads_bounds_widening_factor_from_config(tmp_path, monkeypatch):
    global_params = {"price_breakout_period": 20}
    _isolate(monkeypatch, tmp_path, optimizer_extra={"bounds_widening_factor": 1.5})
    monkeypatch.setattr(ro, "_boundary_hit_fraction", lambda *a, **k: 0.4)
    monkeypatch.setattr(ro, "_boundary_hit_directions",
                        lambda *a, **k: {"cooldown_bars": "high"})
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([0.02, 0.025]))
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    recorded = []
    from automation.optimizer import sweep_diagnostics as sd
    monkeypatch.setattr(sd, "record_diagnosed_pair", lambda rec, **k: recorded.append(rec))

    symbol_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.5, n_periods=400)
    global_result = _result_payload(sortino_ratio=0.5, dd=0.05, sortino_period=0.01, n_periods=400)
    confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    from automation.optimizer.bounds import extract_numeric_bounds
    lo, hi = extract_numeric_bounds("DynamicBreakoutStrategy")["cooldown_bars"]
    expected_hi = round(hi + 1.5 * (hi - lo), 6)
    assert recorded[0]["proposed_bounds"]["cooldown_bars"] == [lo, expected_hi]


# ── Akzeptanzkriterium #777/2: max_bars_in_trade wird auf 24 gekappt (#714/GR-01 bindend) ────────
def test_max_bars_in_trade_proposal_is_capped_at_714_hard_limit():
    proposals = propose_bounds_from_boundary_hits(
        {"max_bars_in_trade": "high"}, "TrendPullbackStrategy",
        current_bounds={"max_bars_in_trade": (12.0, 20.0)}, widen_fraction=1.5)
    # Ohne Kappung waere 20 + 1.5*8 = 32.0 — die #714-Zeitbox-Obergrenze ist 24.
    assert proposals["max_bars_in_trade"] == [12.0, 24.0]


def test_max_bars_in_trade_proposal_below_cap_is_unaffected():
    proposals = propose_bounds_from_boundary_hits(
        {"max_bars_in_trade": "high"}, "TrendPullbackStrategy",
        current_bounds={"max_bars_in_trade": (10.0, 16.0)}, widen_fraction=0.3)
    assert proposals["max_bars_in_trade"] == [10.0, round(16.0 + 0.3 * 6.0, 6)]
    assert proposals["max_bars_in_trade"][1] < 24.0


def test_other_params_unaffected_by_the_714_cap():
    proposals = propose_bounds_from_boundary_hits(
        {"cooldown_bars": "high"}, "TrendPullbackStrategy",
        current_bounds={"cooldown_bars": (2.0, 36.0)}, widen_fraction=1.5)
    assert proposals["cooldown_bars"] == [2.0, round(36.0 + 1.5 * 34.0, 6)]


# ── Akzeptanzkriterium #777/3: leerer Cache ⇒ _bounds_for bit-identisch zu HEAD ────────────────────
def test_bounds_for_unchanged_when_cache_empty(monkeypatch):
    import automation.optimizer.spaces as spaces_mod
    monkeypatch.setattr(spaces_mod, "_auto_proposed_bounds_cache", None)
    monkeypatch.setattr(spaces_mod, "_search_space_overrides_cache", None)
    monkeypatch.setattr(spaces_mod, "_load_auto_proposed_bounds", lambda: {})
    monkeypatch.setattr(spaces_mod, "_load_search_space_overrides", lambda: {})
    lo, hi = spaces_mod._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "cooldown_bars", 2.0, 36.0)
    assert (lo, hi) == (2.0, 36.0)


def test_bounds_for_uses_cache_entry_written_via_boundary_writeback(tmp_path, monkeypatch):
    """End-to-end: der ueber confirm.py geschriebene Cache-Eintrag wird von _bounds_for tatsaechlich
    im NAECHSTEN Lauf gelesen (derselbe Mechanismus wie ein 'signal_frequency'-Kollaps-Vorschlag)."""
    import automation.optimizer.spaces as spaces_mod
    from automation.optimizer import sweep_diagnostics as sd

    monkeypatch.setattr(sd, "_diagnosed_pairs_cache_path",
                        lambda work_dir=None: tmp_path / "diagnosed_pairs_cache.json")
    # Issue #1090 (Katalog #923) — diagnostic_writeback_enabled ist seit diesem Fix in der
    # ausgelieferten optimizer.json standardmaessig false; dieser Test prueft den Rueckschrieb-
    # Pfad selbst, daher hier bewusst wieder aktiviert.
    monkeypatch.setattr(sd, "_read_diagnostic_writeback_enabled", lambda: True)
    sd.record_diagnosed_pair({
        "strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO",
        "action": "search_space_override", "binding_cause": "boundary_solution",
        "proposed_bounds": {"cooldown_bars": [1.0, 50.0]},
    })
    monkeypatch.setattr(spaces_mod, "_auto_proposed_bounds_cache", None)
    monkeypatch.setattr(spaces_mod, "_search_space_overrides_cache", {})
    lo, hi = spaces_mod._bounds_for("TrendPullbackStrategy", "TSLA.ETORO", "cooldown_bars", 2.0, 36.0)
    assert (lo, hi) == (1.0, 50.0)
