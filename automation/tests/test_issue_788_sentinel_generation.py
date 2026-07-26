"""Issue #788 (P1) — `#759` ist halb behoben: Trials tragen eine `oos_win_rate`-Beobachtung TROTZ
`oos_evaluated=False` (und dieselbe Fehlerklasse gilt für weitere OOS-Metriken).

Root-Cause: `#759` behob den Sentinel-Kollaps in der PARSING-Schicht (`parsing.TournamentMetrics.
oos_win_rate` liefert `None` statt `0.0` durch) — die ERZEUGUNGSSEITE
(`run_optimization.make_symbol_objective`) stempelte den Wert aber weiterhin ALS Trial-User-Attr,
selbst wenn `oos_evaluated is False`. Das ist selektionswirksam: `any_arm_unreachable_policy=
'recalibrate'` rekalibriert `oos_min_win_rate` aus der beobachteten Verteilung DIESER Study; sind
16 von 64 Beobachtungen unechte 0.0/None-Fallbacks aus nie evaluierten Trials, wird die Schwelle zu
tief rekalibriert — der OR-Arm wird künstlich leichter passierbar.

Fix: `make_symbol_objective` setzt OOS-Metrik-User-Attrs (`oos_win_rate`, `oos_profit_factor`,
`oos_expectancy`, `oos_total_return`, `oos_sortino`, `oos_psr`, `oos_sortino_period`) NUR, wenn
`metrics.oos_evaluated is True` — kein Default-Wert, kein Key überhaupt für einen nicht evaluierten
Trial. `invariants.check_metric_sentinel_absence` prüft jetzt alle sieben Keys statt nur einem.
"""
import json

import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config
from automation.optimizer.invariants import check_metric_sentinel_absence, _SENTINEL_GUARDED_METRIC_KEYS
from pathlib import Path


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_backtest(payload):
    def _fake(trial_dir, manifest_path):
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), "utf-8")
        return out
    return _fake


# ── Akzeptanzkriterium #788/1: nicht evaluierter Trial hat KEINEN oos_win_rate-Key ─────────────────
def test_not_evaluated_trial_has_no_oos_win_rate_key(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    # oos_evaluated=False, aber die oos_metrics tragen dennoch (defekt/pre-#788) eine Win-Rate —
    # simuliert genau die im Katalog beschriebene Erzeugungsseiten-Situation.
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": {
        "oos_evaluated": False, "oos_eligible": False,
        "oos_metrics": {"win_rate": 0.0, "total_trades": 0, "max_drawdown": 0.0,
                        "profit_factor": 0.0, "expectancy": 0.0, "total_return": 0.0,
                        "sortino_ratio": 0.0, "psr": 0.0, "sortino_period": 0.0}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))

    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    attrs = study.trials[0].user_attrs
    assert attrs.get("oos_evaluated") is False
    for key in _SENTINEL_GUARDED_METRIC_KEYS:
        assert key not in attrs, f"{key} sollte fuer einen nicht evaluierten Trial NICHT gesetzt sein"


def test_evaluated_trial_still_carries_all_metrics(tmp_path, monkeypatch):
    """Gegenprobe: ein ECHT evaluierter Trial verliert keine der bisher gestempelten Metriken."""
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 1, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
        "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
        "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05, "total_trades": 22,
                        "total_return": 0.31, "win_rate": 0.55, "profit_factor": 1.4,
                        "expectancy": 0.02, "psr": 0.8, "sortino_period": 0.03}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))

    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    attrs = study.trials[0].user_attrs
    assert attrs.get("oos_evaluated") is True
    assert attrs.get("oos_win_rate") == 0.55
    assert attrs.get("oos_profit_factor") == 1.4
    assert attrs.get("oos_expectancy") == 0.02
    assert attrs.get("oos_total_return") == 0.31
    assert attrs.get("oos_sortino") == 1.2
    assert attrs.get("oos_psr") == 0.8
    assert attrs.get("oos_sortino_period") == 0.03


def test_real_zero_observation_on_evaluated_trial_is_preserved():
    """Eine ECHT beobachtete Null (evaluierter Trial, Metrik zufaellig 0.0) bleibt erhalten —
    die Unterscheidung betrifft ausschliesslich NICHT evaluierte Trials."""
    from automation.optimizer.parsing import TournamentMetrics
    m = TournamentMetrics(
        oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0, oos_sortino=0.0,
        oos_max_drawdown=0.05, oos_total_trades=25, win_count=0, fully_eligible_pairs=0,
        is_total_trades=25, oos_win_rate=0.0, oos_total_return=0.0, oos_expectancy=0.0,
    )
    assert m.oos_win_rate == 0.0
    assert m.oos_total_return == 0.0


# ── Akzeptanzkriterium #788/2: check_metric_sentinel_absence prüft ALLE OOS-Metriken ──────────────
def test_check_metric_sentinel_absence_covers_all_seven_metric_keys():
    assert set(_SENTINEL_GUARDED_METRIC_KEYS) == {
        "oos_win_rate", "oos_profit_factor", "oos_expectancy", "oos_total_return",
        "oos_sortino", "oos_psr", "oos_sortino_period",
    }


@pytest.mark.parametrize("key", list(_SENTINEL_GUARDED_METRIC_KEYS))
def test_check_metric_sentinel_absence_fails_on_any_metric_sentinel_collapse(key):
    trials = [{"oos_evaluated": False, key: 0.0}]
    result = check_metric_sentinel_absence(trials)
    assert result.passed is False
    assert result.actual == 1


def test_check_metric_sentinel_absence_passes_when_all_metrics_absent_on_unevaluated_trials():
    trials = [{"oos_evaluated": False}, {"oos_evaluated": True, "oos_win_rate": 0.6}]
    result = check_metric_sentinel_absence(trials)
    assert result.passed is True
    assert result.actual == 0
