"""Issues #451 & #453 — Root-Cause der 100%-Rejection + granulare Rejection-Observability.

#451 (Symptom): Trials handeln im In-Sample reichlich (`is_total_trades` > 500), trotzdem ist
`evaluable_trials=0` / `oos_eligible=false` ⇒ alle Proposals `REJECTED_ON_HOLDOUT`. Die Forensik
(#455) zeigt: die Ursache ist NICHT ein zu strenges IS-Gate, sondern eine OOS-Abdeckungs-Blindstelle
(der Katalog erreicht das OOS-Fenster nicht ⇒ `oos_total_trades=0` strukturell). Die Gate-1-Boolean-
Logik selbst ist korrekt (separat in `test_symbol_tunable_gate.py` gepinnt).

#453 (Feature): Der Catch-All `oos_not_evaluated` wird in dezidierte, aggregierbare Kategorien
aufgelöst (`_classify_is_rejection_detail` / `_map_oos_reason`), über das Trial-Event und das
Proposal (`is_rejection_detail`) propagiert. Diese Tests pinnen genau diese Diagnose-Granularität —
sie ist die eigentliche Auflösung von #451: der 100%-Reject ist jetzt eindeutig als DATENseitig
(`REJECT_OOS_WINDOW_UNREACHABLE`) statt parameterseitig erkennbar.
"""
import json
from pathlib import Path

import pandas as pd

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config
from automation.optimizer import confirm
from automation.optimizer.parsing import TournamentMetrics, parse_tournament
from automation.backtest_runner import write_tournament_json

_DAY_NS = 86400 * 1_000_000_000
START_NS = int(pd.Timestamp("2025-05-16", tz="UTC").value)


def _metrics(**kw) -> TournamentMetrics:
    base = dict(oos_evaluated=False, oos_eligible=False, is_sortino_median=0.0,
                oos_sortino=None, oos_max_drawdown=0.0, oos_total_trades=0, win_count=0,
                fully_eligible_pairs=0, is_total_trades=502, hit_trade_cap=False)
    base.update(kw)
    return TournamentMetrics(**base)


# ===========================================================================
# #451 — die 100%-Rejection wird DATENseitig (nicht parameterseitig) diagnostiziert
# ===========================================================================
def test_high_is_activity_zero_oos_uncovered_is_data_side_diagnosis():
    """502 IS-Trades, aber OOS=0 + oos_covered=False ⇒ REJECT_OOS_WINDOW_UNREACHABLE (Katalog-H2),
    NICHT ein generischer Catch-All. Genau die Auflösung von #451."""
    m = _metrics(is_total_trades=502, oos_total_trades=0, oos_covered=False)
    assert ro._classify_is_rejection_detail(m) == "REJECT_OOS_WINDOW_UNREACHABLE"


def test_high_is_activity_zero_oos_but_covered_is_strategy_side():
    """Dieselbe IS-Aktivität, aber OOS abgedeckt und dennoch 0 Trades ⇒ strategieseitig
    (REJECT_OOS_INACTIVE) — die Diagnose trennt die beiden Ursachen, die #451 vermischt."""
    m = _metrics(is_total_trades=502, oos_total_trades=0, oos_covered=True)
    assert ro._classify_is_rejection_detail(m) == "REJECT_OOS_INACTIVE"


def test_zero_oos_unknown_coverage_falls_back_to_legacy_category():
    """Ohne #455-Telemetrie (oos_covered=None) bleibt die ehrliche Legacy-Kategorie erhalten."""
    m = _metrics(oos_total_trades=0, oos_covered=None)
    assert ro._classify_is_rejection_detail(m) == "REJECT_OOS_NOT_EVALUATED"


def test_full_chain_json_to_diagnosis(tmp_path):
    """End-to-End #451: ein TSLA-artiges tournament_result.json (502 IS-Trades, OOS=0, H2 nicht
    abgedeckt) ⇒ parse_tournament ⇒ Diagnose REJECT_OOS_WINDOW_UNREACHABLE."""
    all_results = [{
        "symbol": "TSLA.ETORO", "strategy": "SmaCrossoverStrategy",
        "metrics": {"total_trades": 502}, "oos_metrics": {"total_trades": 0},
        "_fill_ts_min": START_NS + 2 * _DAY_NS, "_fill_ts_max": START_NS + 170 * _DAY_NS,
        "_oos_window_start_ns": START_NS + 180 * _DAY_NS, "_oos_covered": False,
    }]
    out_path = tmp_path / "tournament_result.json"
    write_tournament_json(all_results, str(out_path), per_symbol_winners={},
                          aggregate_winner=None, warnings_list=[], is_eligible_count=0,
                          fully_eligible_count=0, tournament_cfg={"min_trades": 20},
                          start_ns=START_NS, end_ns=START_NS + 360 * _DAY_NS)
    m = parse_tournament(out_path)
    assert m.oos_covered is False
    assert ro._classify_is_rejection_detail(m) == "REJECT_OOS_WINDOW_UNREACHABLE"


# ===========================================================================
# #453 — granulare Klassifikation der konkreten OOS-Gate-Verletzungen
# ===========================================================================
def test_map_oos_reason_prefixes():
    assert ro._map_oos_reason("oos_max_drawdown: 0.5 > 0.3") == "REJECT_OOS_MAX_DRAWDOWN"
    assert ro._map_oos_reason("oos_min_trades: 19 < 20") == "REJECT_OOS_MIN_TRADES"
    assert ro._map_oos_reason("oos_min_total_return: 0.001 < 0.005") == "REJECT_OOS_MIN_TOTAL_RETURN"
    assert ro._map_oos_reason("oos_min_win_rate: 0.20 < 0.25") == "REJECT_OOS_MIN_WIN_RATE"
    assert ro._map_oos_reason("Micro-Sizing: Median notional < 10.0 (value: 3.0)") == "REJECT_OOS_MICRO_SIZING"
    assert ro._map_oos_reason("voll unbekannter grund") == "REJECT_OOS_OTHER"


def test_classify_eligible_is_none():
    m = _metrics(oos_evaluated=True, oos_eligible=True, oos_total_trades=22)
    assert ro._classify_is_rejection_detail(m) == "NONE"


def test_classify_oos_gate_uses_dominant_reason():
    m = _metrics(oos_evaluated=True, oos_eligible=False, oos_total_trades=10,
                 oos_rejection_reasons=("oos_max_drawdown: 0.5 > 0.3", "oos_min_win_rate: 0.2 < 0.25"))
    assert ro._classify_is_rejection_detail(m) == "REJECT_OOS_MAX_DRAWDOWN"


def test_parse_tournament_reads_oos_rejection_reasons(tmp_path):
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": None,
               "single_symbol_oos": {"oos_evaluated": True, "oos_eligible": False,
                                     "oos_rejection_reasons": ["oos_min_trades: 5 < 20"],
                                     "oos_metrics": {"sortino_ratio": 0.4, "max_drawdown": 0.1,
                                                     "total_trades": 5, "total_return": 0.02,
                                                     "win_rate": 0.4, "profit_factor": 1.2}}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(payload), "utf-8")
    m = parse_tournament(p)
    assert m.oos_rejection_reasons == ("oos_min_trades: 5 < 20",)
    assert ro._classify_is_rejection_detail(m) == "REJECT_OOS_MIN_TRADES"


# ===========================================================================
# #453 — End-to-End: Trial-User-Attr + Proposal-Persistierung
# ===========================================================================
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


def test_make_symbol_objective_sets_granular_is_rejection_detail(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    payload = {"fully_eligible_pairs": 0, "aggregate_winner": None,
               "single_symbol_oos": {"oos_evaluated": True, "oos_eligible": False,
                                     "oos_rejection_reasons": ["oos_max_drawdown: 0.55 > 0.30"],
                                     "oos_metrics": {"sortino_ratio": 0.5, "max_drawdown": 0.55,
                                                     "total_trades": 12, "total_return": 0.08,
                                                     "win_rate": 0.45, "profit_factor": 1.2}}}
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest(payload))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=2)
    details = [t.user_attrs.get("is_rejection_detail") for t in study.trials]
    assert details and all(d == "REJECT_OOS_MAX_DRAWDOWN" for d in details)


class _T:
    def __init__(self, detail):
        self.user_attrs = {"is_rejection_detail": detail} if detail is not None else {}


class _Study:
    study_name = "study_SmaCrossoverStrategy_TSLA_ETORO"
    best_value = -9.9

    def __init__(self, details):
        self.trials = [_T(d) for d in details]


def test_dominant_is_rejection_detail_picks_mode():
    study = _Study(["REJECT_OOS_WINDOW_UNREACHABLE", "REJECT_OOS_WINDOW_UNREACHABLE",
                    "REJECT_OOS_MAX_DRAWDOWN"])
    assert confirm._dominant_is_rejection_detail(study) == "REJECT_OOS_WINDOW_UNREACHABLE"


def test_dominant_is_rejection_detail_none_when_absent():
    assert confirm._dominant_is_rejection_detail(_Study([None, None])) is None


def test_export_proposal_persists_dominant_is_rejection_detail(tmp_path, monkeypatch):
    """Issue #654 — der modale IS-Study-Trial-Grund wird SEPARAT als ``dominant_is_rejection_detail``
    persistiert (Study-Diagnose), NICHT mehr unter ``is_rejection_detail`` (das ist seit #654
    AUSSCHLIESSLICH die tatsächliche Promotion-Ursache — ``is_rejection_detail_override`` — und
    fällt NICHT MEHR auf den modalen IS-Grund zurück, wenn kein Override gesetzt ist: genau dieser
    OR-Fallback war die #654-Root-Cause, die einer bestandenen Holdout-Promotion fälschlich den
    modalen IS-Study-Grund als Ablehnungsursache zuschrieb)."""
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    study = _Study(["REJECT_OOS_WINDOW_UNREACHABLE", "REJECT_OOS_WINDOW_UNREACHABLE",
                    "REJECT_OOS_INACTIVE"])
    promotion = {
        "status": "REJECTED_ON_HOLDOUT", "symbol_params": {"sma_period": 10},
        "R_symbol": -9.9, "R_global": -9.0, "promotion_margin": 0.10,
        "metrics_symbol": {}, "metrics_global": {},
        "is_rejection_detail_override": "REJECT_HOLDOUT_GATE",
    }
    path = confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion)
    data = json.loads(Path(path).read_text("utf-8"))
    assert data["is_rejection_detail"] == "REJECT_HOLDOUT_GATE"
    assert data["dominant_is_rejection_detail"] == "REJECT_OOS_WINDOW_UNREACHABLE"


def test_export_proposal_is_rejection_detail_none_without_override(tmp_path, monkeypatch):
    """Issue #654 — fehlt ``is_rejection_detail_override`` (z. B. READY_FOR_PR, keine Ablehnung),
    ist ``is_rejection_detail`` explizit ``None`` — KEIN stiller Fallback auf den modalen IS-Grund
    mehr (der #654-Root-Cause-Fehler)."""
    monkeypatch.setattr(confirm, "WORK", tmp_path)
    study = _Study(["REJECT_OOS_WINDOW_UNREACHABLE", "REJECT_OOS_WINDOW_UNREACHABLE",
                    "REJECT_OOS_INACTIVE"])
    promotion = {
        "status": "READY_FOR_PR", "symbol_params": {"sma_period": 10},
        "R_symbol": 1.2, "R_global": 0.5, "promotion_margin": 0.10,
        "metrics_symbol": {}, "metrics_global": {},
    }
    path = confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion)
    data = json.loads(Path(path).read_text("utf-8"))
    assert data["is_rejection_detail"] is None
    assert data["dominant_is_rejection_detail"] == "REJECT_OOS_WINDOW_UNREACHABLE"
