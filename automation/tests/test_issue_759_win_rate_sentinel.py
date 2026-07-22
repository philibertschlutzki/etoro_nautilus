"""Issue #759 (P1) — `win_rate`-Sentinel: fehlende Werte werden als beobachtete Nullen gezählt.

Root-Cause: `TournamentMetrics.oos_win_rate` war `float = 0.0` — ein fehlender Wert (kein Trial je
evaluiert, kein `win_rate`-Key im Metrics-Dict) kollabierte auf denselben Wert wie eine ECHT
BEOBACHTETE Null. `reward.check_any_arm_reachability_live`/`resolve_any_arm_policy` konnten die
beiden Fälle nicht mehr trennen und rekalibrierten Schwellen aus einer Verteilung, die teils/
ausschliesslich aus Missing-Data-Sentinels bestand (empirisch: 305/459 `[#660]`-Warnungen mit
p99=0.0000, davon 255 aus Studies mit 0 evaluierten Trials).

Fix: `oos_win_rate: float | None = None`, None durchgereicht statt kollabiert; Konsumenten an der
Konsumstelle None-fest gemacht; `any_arm_min_observations` (Default 10) + `n_evaluated`-Suppression
in `check_any_arm_reachability_live`/`resolve_any_arm_policy`; siebter `invariants.py`-Check
`check_metric_sentinel_absence`.
"""
import json
import tempfile
from pathlib import Path

import pytest

from automation.optimizer.parsing import parse_tournament
from automation.optimizer.reward import check_any_arm_reachability_live, resolve_any_arm_policy
from automation.optimizer.invariants import check_metric_sentinel_absence


# ── parse_tournament: fehlender win_rate-Key ⇒ None, nicht 0.0 ────────────────────────────────────
def test_parse_tournament_missing_win_rate_yields_none_not_zero():
    payload = {
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": False,
            "oos_metrics": {
                "total_trades": 25, "max_drawdown": 0.05,
                # KEIN "win_rate"-Key — simuliert eine Study, in der die Kennzahl nicht berechnet wurde.
            },
        },
        "full_results": [],
    }
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "tournament_result.json"
        p.write_text(json.dumps(payload), "utf-8")
        m = parse_tournament(p)
    assert m.oos_win_rate is None


def test_parse_tournament_real_zero_win_rate_stays_zero():
    """Gegenprobe: eine ECHT beobachtete Null bleibt 0.0 (unterscheidbar von None)."""
    payload = {
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": False,
            "oos_metrics": {"total_trades": 25, "max_drawdown": 0.05, "win_rate": 0.0},
        },
        "full_results": [],
    }
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "tournament_result.json"
        p.write_text(json.dumps(payload), "utf-8")
        m = parse_tournament(p)
    assert m.oos_win_rate == 0.0
    assert m.oos_win_rate is not None


# ── resolve_any_arm_policy: ausschliesslich None-Beobachtungen ⇒ insufficient_data, keine Aenderung ─
def test_resolve_any_arm_policy_with_only_none_observations_reports_insufficient_data():
    tcfg = {
        "eligible_requires_any": ["min_profit_factor", "min_win_rate"],
        "oos_min_win_rate": 0.15,
        "any_arm_unreachable_policy": "recalibrate",
    }
    # Simuliert 12 Trials, VON DENEN KEINER eine echte win_rate-Beobachtung beitrug (alles None) —
    # z. B. eine Study mit 0 evaluierten Trials, VOR der #759-Parsing-Korrektur waeren das 12
    # Missing-Data-Sentinel-Nullen gewesen.
    observed = {"min_win_rate": [None] * 12}
    decision = resolve_any_arm_policy(tcfg, observed, n_evaluated=0)
    assert decision["any_arm_decision"] == "insufficient_data"
    assert decision["recalibrated_thresholds"] == {}
    assert decision["dropped_clauses"] == []


def test_resolve_any_arm_policy_below_min_observations_reports_insufficient_data():
    tcfg = {
        "eligible_requires_any": ["min_profit_factor", "min_win_rate"],
        "oos_min_win_rate": 0.15,
        "any_arm_unreachable_policy": "recalibrate",
        "any_arm_min_observations": 10,
    }
    # Nur 4 ECHTE Beobachtungen — unter dem Default-Minimum von 10.
    decision = resolve_any_arm_policy(tcfg, {"min_win_rate": [0.01, 0.02, 0.03, 0.04]},
                                      n_evaluated=20)
    assert decision["any_arm_decision"] == "insufficient_data"
    assert decision["recalibrated_thresholds"] == {}


def test_resolve_any_arm_policy_with_enough_real_observations_still_recalibrates():
    tcfg = {
        "eligible_requires_any": ["min_profit_factor", "min_win_rate"],
        "oos_min_win_rate": 0.15,
        "any_arm_unreachable_policy": "recalibrate",
        "any_arm_min_observations": 10,
    }
    low = [0.0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.0968]
    decision = resolve_any_arm_policy(tcfg, {"min_win_rate": low}, n_evaluated=20)
    assert decision["any_arm_decision"] == "recalibrated"
    assert "oos_min_win_rate" in decision["recalibrated_thresholds"]


def test_zero_evaluated_trials_suppresses_reachability_check_entirely():
    """Akzeptanzkriterium: check_any_arm_reachability_live wird unterdrückt, solange die Study
    oos_evaluated-Trials = 0 hat — selbst wenn (hypothetisch, z. B. Alt-Daten) genug Samples
    vorlägen."""
    tcfg = {"eligible_requires_any": ["min_win_rate"], "oos_min_win_rate": 0.15}
    many_low_samples = [0.01] * 20
    result = check_any_arm_reachability_live(tcfg, {"min_win_rate": many_low_samples}, n_evaluated=0)
    assert result == []


# ── invariants.check_metric_sentinel_absence ───────────────────────────────────────────────────────
def test_check_metric_sentinel_absence_passes_when_win_rate_absent_on_unevaluated_trials():
    trials = [{"oos_evaluated": False, "oos_win_rate": None},
             {"oos_evaluated": True, "oos_win_rate": 0.3}]
    result = check_metric_sentinel_absence(trials)
    assert result.passed is True


def test_check_metric_sentinel_absence_fails_on_sentinel_collapse_regression():
    """Simuliert die #759-Regression: ein NICHT evaluierter Trial traegt trotzdem eine
    win_rate-Beobachtung (der genaue Bug-Signatur-Fall)."""
    trials = [{"oos_evaluated": False, "oos_win_rate": 0.0}]
    result = check_metric_sentinel_absence(trials)
    assert result.passed is False
    assert result.actual == 1


def test_report_wires_metric_sentinel_absence_check():
    from automation.optimizer.report import _study_record

    class _T:
        def __init__(self, evaluated, win_rate):
            self.value = 1.0
            self.user_attrs = {"oos_evaluated": evaluated, "oos_eligible": False,
                               "oos_win_rate": win_rate}

    class _S:
        trials = [_T(False, 0.0)]
        best_value = 1.0
        user_attrs = {}

    record, checks = _study_record({"symbol": "AAA.ETORO", "strategy": "SmaCrossoverStrategy"}, _S())
    names = [c.name for c in checks]
    assert "check_metric_sentinel_absence" in names
    check = next(c for c in checks if c.name == "check_metric_sentinel_absence")
    assert check.passed is False


# ── Produktions-Config hat any_arm_min_observations deklariert ─────────────────────────────────────
def test_production_config_declares_any_arm_min_observations():
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    assert "any_arm_min_observations" in cfg
    assert cfg["any_arm_min_observations"] >= 1
