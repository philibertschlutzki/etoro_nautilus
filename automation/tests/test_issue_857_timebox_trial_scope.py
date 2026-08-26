"""Issue #857 (P0, Katalog #856-#861, GitHub-Issue #758) — die Zeitbox-Konsequenz verwarf die
STUDY statt des TRIALS und kostete 52 % der handelnden Suche in einem Referenzlauf (206 von 462
Studies via ``REJECT_INVALID_TIMEBOX`` verworfen, obwohl der Exit-Pfad nach #836/#837 überwiegend
intakt war — Median-Verletzungsanteil je Study nur 5,77 %).

Root-Cause (a): die Messung liegt JE TRIAL vor (``oos_max_holding_time_s``), die Konsequenz griff
aber je STUDY — ein einzelner verletzender Trial kontaminierte 159 saubere Geschwister
(Pitfall #272). Root-Cause (b): die Invarianten-Toleranz (0.01 Bars) war strenger als die vom
#836-Watchdog selbst vorgesehene Ausführungslatenz (``exit_close_max_bars`` + 1 Fill-Bar) — #858.

Fix: ``run_optimization.make_symbol_objective`` stempelt einen einzelnen zeitbox-verletzenden
Trial jetzt VOR ``compute_reward`` auf den Unevaluable-Pfad um (``oos_evaluated=False``,
``oos_invalid_reason='TIMEBOX_VIOLATION'``) — er zählt weder in Eligibility noch in Reward noch
(via #822) in ``n_family``, kontaminiert aber keine sauberen Geschwister mehr. Die Study-Ebene
(confirm.py, ``timebox_violation_study_tolerance``, Default 0.25) bleibt NUR noch als Bug-Detektor
für einen strukturell defekten Exit-Pfad (siehe test_issue_839_timebox_consequence.py für die
confirm.py-Tests)."""
import json
from pathlib import Path

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_result_factory(*, max_holding_time_s: float, max_bars_in_trade: int = 24):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 5,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [0.5],
            "oos_metrics": {
                "sortino_ratio": 0.5, "max_drawdown": 0.05, "total_return": 0.02,
                "total_trades": 30, "sortino_period": 0.05, "n_periods": 200,
                "max_holding_time_s": max_holding_time_s,
            },
        }}), "utf-8")
        return out
    return _fake


def test_trial_beyond_slack_tolerance_is_downgraded_to_unevaluated(tmp_path, monkeypatch):
    """Ein Trial, dessen Haltedauer den globalen 24-Bar-Deckel + die #858-Slack-Toleranz (3 Bars,
    27 Bars gesamt) überschreitet, wird auf oos_evaluated=False umgestempelt -- unabhängig von
    jeder Study-weiten Aggregation."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_result_factory(max_holding_time_s=30 * 3600.0))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    trial = study.trials[0]

    assert trial.user_attrs["oos_timebox_violated"] is True
    assert trial.user_attrs["oos_evaluated"] is False
    assert trial.user_attrs["oos_eligible"] is False
    assert trial.user_attrs["oos_invalid_reason"] == "TIMEBOX_VIOLATION"
    # Root-Cause-Fix: die Evidenz bleibt trotz der Ueberschreibung erhalten (sonst koennte
    # confirm.py/report.py die Verletzung nicht mehr studienweit nachvollziehen).
    assert trial.user_attrs["oos_max_holding_time_s"] == 30 * 3600.0


def test_trial_within_watchdog_slack_is_not_a_violation(tmp_path, monkeypatch):
    """Issue #858 — die Haltedauer liegt INNERHALB der Toleranz (sampled max_bars_in_trade + 3
    Bars Slack): das ist exakt die vom #836-Watchdog selbst vorgesehene Ausfuehrungslatenz, kein
    Vertragsbruch mehr (vorher, mit der 0.01-Bar-Toleranz, waere dieser Trial faelschlich als
    Verletzung gezaehlt worden). Issue #1275 (GH #1148, Katalog #1272-1297, P0) Fix Punkt 3 — der
    max_bars_in_trade-Suchraum ist auf [3, 6] umkalibriert (Faktor 0.24, vormals 24-Bar-Deckel);
    4h liegt sicher unterhalb des kleinstmoeglichen Schwellenwerts ((3 + 3) Bars = 6h) UNABHAENGIG
    davon, welcher Wert in diesem Lauf tatsaechlich gesampelt wird (kein fixer Seed hier)."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_result_factory(max_holding_time_s=4 * 3600.0))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    trial = study.trials[0]

    assert trial.user_attrs["oos_timebox_violated"] is False
    assert trial.user_attrs["oos_evaluated"] is True
    assert trial.user_attrs["oos_eligible"] is True
    assert "oos_invalid_reason" not in trial.user_attrs


def test_clean_trial_is_unaffected_and_carries_cap_source(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_result_factory(max_holding_time_s=5 * 3600.0))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    trial = study.trials[0]

    assert trial.user_attrs["oos_timebox_violated"] is False
    assert trial.user_attrs["oos_evaluated"] is True
    assert trial.user_attrs["timebox_cap_source"] in ("sampled", "default", "global")


def test_violating_trial_does_not_contaminate_reward_of_the_run(tmp_path, monkeypatch):
    """Pitfall #272 — die Konsequenz einer Zeitbox-Verletzung darf NICHT auf andere Trials
    ausstrahlen: ein zeitbox-verletzender Trial faellt auf den Unevaluable-Reward-Floor, ein
    sauberer Trial im selben Study-Objekt bleibt vollstaendig unberuehrt (hier ueber zwei separate
    Studies nachgestellt, da optimize_symbol je Aufruf eine frische Study anlegt -- die Kern-
    Aussage ist die GLEICHE Behandlung ungeachtet anderer Trials, siehe test_issue_839 fuer den
    Study-Kohorten-Fall mit einer echten Multi-Trial-Study)."""
    _isolate(monkeypatch, tmp_path)

    monkeypatch.setattr(ro, "run_backtest", _fake_result_factory(max_holding_time_s=100 * 3600.0))
    violating_study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    violating_reward = violating_study.trials[0].value

    monkeypatch.setattr(ro, "run_backtest", _fake_result_factory(max_holding_time_s=5 * 3600.0))
    clean_study = ro.optimize_symbol("SmaCrossoverStrategy", "AAPL.ETORO", n_trials=1)
    clean_reward = clean_study.trials[0].value

    assert violating_reward < clean_reward
