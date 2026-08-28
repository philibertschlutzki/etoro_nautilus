"""Issue #1299 (GH #1176, P0) — ``backtest_runner._empty_result`` markiert beide Datenpfade
(Tick-Ladefehler, leere Tick-Menge) nicht mehr stumm ohne ``error``-Schlüssel.

Symptom. Ein Backtest ohne Ticks war stromabwärts nicht von einem Backtest ohne Signale
unterscheidbar (B-5) — die Brücke, über die #1303/B-4 (search_space_override auf einem Lauf ohne
Trades) entstand.

Fix.
1. ``_empty_result`` erhält ein PFLICHT-Keyword ``error: str | None`` (kein Default) — jeder
   Aufrufer muss explizit benennen, warum der Trial leer ist.
2. Beide zuvor stummen Pfade setzen jetzt ``error`` ("tick_load_failed"/"no_ticks_in_window") UND
   reichen ``start_capital`` durch.
3. ``parsing.parse_tournament`` liest den Wert in ``TournamentMetrics.worker_error``.
4. ``sweep_diagnostics``s ``_BINDING_CAUSES`` trägt ``"data_unavailable"``.
"""
import inspect
import json

import pytest

from automation import backtest_runner as br
from automation.optimizer import parsing
from automation.optimizer.sweep_diagnostics import _BINDING_CAUSES


# ---------------------------------------------------------------------------------------------
# _empty_result — Pflicht-Keyword error, kein Default
# ---------------------------------------------------------------------------------------------

def test_empty_result_without_error_argument_is_a_type_error():
    with pytest.raises(TypeError):
        br._empty_result("TSLA.ETORO", "SmaCrossoverStrategy", {})


def test_empty_result_sets_error_key():
    res = br._empty_result("TSLA.ETORO", "SmaCrossoverStrategy", {}, 100000.0,
                           error="no_ticks_in_window")
    assert res["error"] == "no_ticks_in_window"
    assert res["start_capital"] == 100000.0


def test_empty_result_error_none_omits_the_key():
    res = br._empty_result("TSLA.ETORO", "SmaCrossoverStrategy", {}, error=None)
    assert "error" not in res


def test_all_empty_result_call_sites_pass_an_explicit_error_argument():
    """Akzeptanzkriterium: grep -n 'return _empty_result' automation/backtest_runner.py zeigt für
    jeden Aufruf ein explizites error-Argument — hier als AST-Scan statt eines Shell-Greps, robust
    gegen Zeilenumbrüche im Aufruf."""
    import ast
    source = inspect.getsource(br)
    tree = ast.parse(source)
    call_sites = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_empty_result"
    ]
    assert call_sites, "keine _empty_result-Aufrufe gefunden"
    for call in call_sites:
        kwarg_names = {kw.arg for kw in call.keywords}
        assert "error" in kwarg_names, (
            f"_empty_result-Aufruf in Zeile {call.lineno} ohne explizites error-Argument")


# ---------------------------------------------------------------------------------------------
# parsing.parse_tournament — worker_error aus full_results[0]["error"]
# ---------------------------------------------------------------------------------------------

def _write_tournament(tmp_path, full_results):
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps({"full_results": full_results}), "utf-8")
    return p


def test_parse_tournament_reads_worker_error_from_first_result(tmp_path):
    p = _write_tournament(tmp_path, [
        {"metrics": {"total_trades": 0}, "error": "no_ticks_in_window", "strat_params": {}},
    ])
    metrics = parsing.parse_tournament(p)
    assert metrics.worker_error == "no_ticks_in_window"


def test_parse_tournament_worker_error_none_without_error_key(tmp_path):
    p = _write_tournament(tmp_path, [
        {"metrics": {"total_trades": 12}, "strat_params": {}},
    ])
    metrics = parsing.parse_tournament(p)
    assert metrics.worker_error is None


def test_parse_tournament_worker_error_none_with_empty_full_results(tmp_path):
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps({}), "utf-8")
    metrics = parsing.parse_tournament(p)
    assert metrics.worker_error is None


# ---------------------------------------------------------------------------------------------
# sweep_diagnostics — data_unavailable ist ein gültiger binding_cause.
# ---------------------------------------------------------------------------------------------

def test_data_unavailable_is_a_valid_binding_cause():
    assert "data_unavailable" in _BINDING_CAUSES
