"""Issue #405 (P0) — Evaluierbarkeit von Gewinner-Status entkoppeln.

Pitfall #75, Defekt 1: Im Single-Symbol-Sweep (universe_size==1) leitete `parse_tournament` die
Evaluierbarkeit (`oos_evaluated`) aus `aggregate_winner` ab — der aber `null` bleibt, solange das
Symbol das volle Tournament-Gate (IS-eligible ∧ OOS-eligible) fuer KEINE Parametrisierung klaert.
Die Per-Symbol-OOS-Resultate (`_oos_eval`, `oos_metrics`) existieren dennoch und werden im Reward
verworfen ⇒ jeder Trial faellt auf den Unevaluable-Floor.

Fix:
  - `backtest_runner.write_tournament_json` schreibt im Single-Symbol-Pfad einen
    `single_symbol_oos`-Block aus `r["_oos_eval"]`/`r["oos_metrics"]` — UNGEACHTET des
    Gewinner-Status. Multi-Symbol-Laeufe bleiben bit-identisch (kein Block).
  - `parse_tournament` nutzt diesen Block als Quelle, WENN `aggregate_winner` fehlt.

Parsing-Tests sind reines Python; die write_tournament_json-Tests benoetigen `nautilus_trader`
(importorskip — in CI real, lokal via Stub).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.parsing import parse_tournament


# ---------------------------------------------------------------------------
# parsing.parse_tournament — single_symbol_oos-Fallback (pure Python)
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_parse_uses_single_symbol_oos_when_no_winner(tmp_path):
    """aggregate_winner null + single_symbol_oos vorhanden ⇒ OOS-Metriken aus dem Block."""
    p = _write(tmp_path, {
        "fully_eligible_pairs": 0,
        "aggregate_winner": None,
        "single_symbol_oos": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 0,
            "median_is_sortino": 1.3, "oos_fold_sortinos": [0.8, 1.2],
            "oos_metrics": {"sortino_ratio": 1.0, "max_drawdown": 0.1,
                            "total_trades": 25, "total_return": 0.3}},
    })
    m = parse_tournament(p)
    assert m.oos_evaluated is True
    assert m.oos_eligible is True
    assert m.oos_sortino == pytest.approx(1.0)         # median([0.8, 1.2]) == 1.0
    assert m.oos_total_trades == 25
    assert m.oos_total_return == pytest.approx(0.3)
    assert m.is_sortino_median == pytest.approx(1.3)


def test_single_symbol_oos_can_be_unevaluated(tmp_path):
    """Der Block bildet auch den ehrlichen Unevaluable-Fall ab (kein OOS-Material)."""
    p = _write(tmp_path, {
        "aggregate_winner": None,
        "single_symbol_oos": {"oos_evaluated": False, "oos_eligible": False,
                              "oos_metrics": None, "median_is_sortino": 0.5},
    })
    m = parse_tournament(p)
    assert m.oos_evaluated is False
    assert m.oos_sortino is None


def test_aggregate_winner_takes_precedence_over_single_symbol(tmp_path):
    """Liegt ein echter aggregate_winner vor, wird single_symbol_oos ignoriert (Praezedenz)."""
    p = _write(tmp_path, {
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 3,
            "median_is_sortino": 2.0,
            "oos_metrics": {"sortino_ratio": 2.5, "max_drawdown": 0.05, "total_trades": 40}},
        "single_symbol_oos": {
            "oos_evaluated": True, "oos_eligible": False,
            "oos_metrics": {"sortino_ratio": 0.1, "total_trades": 5}},
    })
    m = parse_tournament(p)
    assert m.oos_sortino == pytest.approx(2.5)
    assert m.win_count == 3


def test_no_single_symbol_block_still_unevaluable(tmp_path):
    """Regression: ohne single_symbol_oos bleibt null-Winner strikt unevaluable (Pitfall #64)."""
    p = _write(tmp_path, {"fully_eligible_pairs": 0, "aggregate_winner": None})
    m = parse_tournament(p)
    assert m.oos_evaluated is False
    assert m.oos_eligible is False


# ---------------------------------------------------------------------------
# backtest_runner.write_tournament_json — single_symbol_oos-Block
# ---------------------------------------------------------------------------

def _wt():
    return pytest.importorskip("automation.backtest_runner")  # nautilus_trader (real/Stub)


def test_write_adds_single_symbol_oos_regardless_of_winner(tmp_path):
    """Single-Symbol-Lauf, KEIN Gewinner (aggregate_winner None): Block wird dennoch geschrieben."""
    wt = _wt()
    oos_metrics = {"sortino_ratio": 1.1, "max_drawdown": 0.08, "total_trades": 18,
                   "total_return": 0.22, "win_rate": 0.6, "_oos_trade_records": [(1, 2, 3, 4, 5)]}
    all_results = [{
        "symbol": "CPRT.ETORO", "strategy": "SmaCrossoverStrategy",
        "metrics": {"sortino_ratio": 1.4, "total_trades": 120},
        "oos_metrics": oos_metrics, "_score": 0.9,
        "_oos_eval": {"oos_evaluated": True, "oos_eligible": False, "oos_metrics": oos_metrics,
                      "oos_rejection_reasons": ["oos_max_drawdown: 0.08 > 0.05"]},
    }]
    out = tmp_path / "t.json"
    wt.write_tournament_json(all_results, str(out), per_symbol_winners={}, aggregate_winner=None,
                             warnings_list=[], is_eligible_count=1, fully_eligible_count=0,
                             tournament_cfg={})
    data = json.loads(out.read_text("utf-8"))
    assert data["aggregate_winner"] is None
    block = data["single_symbol_oos"]
    assert block["oos_evaluated"] is True
    assert block["oos_eligible"] is False
    assert block["oos_metrics"]["total_trades"] == 18
    assert block["median_is_sortino"] == pytest.approx(1.4)
    # JSON-Hygiene: interne Trade-Records werden nicht in den Block durchgereicht.
    assert "_oos_trade_records" not in block["oos_metrics"]


def test_multi_symbol_has_no_single_symbol_block(tmp_path):
    """Bit-Identitaet: bei >1 Symbol wird KEIN single_symbol_oos-Key erzeugt."""
    wt = _wt()
    all_results = [
        {"symbol": "A.ETORO", "strategy": "S", "metrics": {"total_trades": 5},
         "oos_metrics": {}, "_oos_eval": {"oos_evaluated": False, "oos_eligible": False}},
        {"symbol": "B.ETORO", "strategy": "S", "metrics": {"total_trades": 7},
         "oos_metrics": {}, "_oos_eval": {"oos_evaluated": False, "oos_eligible": False}},
    ]
    out = tmp_path / "t.json"
    wt.write_tournament_json(all_results, str(out), {}, None, [], 2, 0, tournament_cfg={})
    data = json.loads(out.read_text("utf-8"))
    assert "single_symbol_oos" not in data


def test_pitfall75_roundtrip_decouples_evaluability(tmp_path):
    """End-to-end: write → parse. aggregate_winner null, Symbol dennoch evaluable (der Fix)."""
    wt = _wt()
    oos_metrics = {"sortino_ratio": 0.9, "max_drawdown": 0.08, "total_trades": 18,
                   "total_return": 0.2, "win_rate": 0.55}
    all_results = [{
        "symbol": "CPRT.ETORO", "strategy": "SmaCrossoverStrategy",
        "metrics": {"sortino_ratio": 1.4, "total_trades": 120},
        "oos_metrics": oos_metrics, "_score": 0.9,
        "_oos_eval": {"oos_evaluated": True, "oos_eligible": True, "oos_metrics": oos_metrics,
                      "oos_rejection_reasons": []},
    }]
    out = tmp_path / "t.json"
    wt.write_tournament_json(all_results, str(out), {}, None, [], 1, 1, tournament_cfg={})
    m = parse_tournament(out)
    assert m.oos_evaluated is True            # entkoppelt vom (fehlenden) Gewinner-Status
    assert m.oos_sortino == pytest.approx(0.9)
    assert m.oos_total_trades == 18


def test_single_symbol_without_oos_eval_writes_no_block(tmp_path):
    """Hat das eine Symbol gar kein _oos_eval (nie IS-eligible), wird kein Block geschrieben —
    der Lauf bleibt ehrlich unevaluable (kein erfundener OOS-Record)."""
    wt = _wt()
    all_results = [{"symbol": "X.ETORO", "strategy": "S", "metrics": {"total_trades": 0},
                    "oos_metrics": {}}]
    out = tmp_path / "t.json"
    wt.write_tournament_json(all_results, str(out), {}, None, [], 0, 0, tournament_cfg={})
    data = json.loads(out.read_text("utf-8"))
    assert "single_symbol_oos" not in data
