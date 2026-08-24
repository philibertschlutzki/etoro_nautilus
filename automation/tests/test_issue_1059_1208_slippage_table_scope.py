"""Issue #1059/#1208 (P2, Katalog #1196-1221) — §2.4-Slippage-Tabelle hat OOS-Scope, steht aber
unter Holdout-Zahlen.

Symptom: `2eebaa56`: SqueezeBreakout/ASML zeigt 133,29 bps Slippage, hat aber 0 Holdout-Trades und
erscheint in §2.3 unter "Ohne Benchmark-Vergleich". `d018dab4`: Squeeze/KRYS 189,22 bps bei 0
Holdout-Trades.

Root-Cause: §2.4 heisst "Kostenbasis" und steht direkt unter den Holdout-Ertragszahlen, aggregiert
aber über alle OOS-evaluierten Trials. Zwei Grundgesamtheiten unter einer Überschrift.

Fix: zwei Spalten -- `Slippage (OOS, Median)` und `Slippage (Holdout, Median)`, letztere `k. A.`
bei 0 Holdout-Trades. Scope-Hinweis in der Sektionsüberschrift.

Akzeptanzkriterium: keine Study mit 0 Holdout-Trades trägt in der Holdout-Spalte einen Wert.
"""
from automation.optimizer import summary_de


def _report(studies):
    return {
        "run_id": "r", "run_status": "complete",
        "started_at_utc": "2026-08-19T00:00:00Z", "wallclock_s": 10.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "symbols_completed": 1, "symbols_planned": 1,
        "studies": studies,
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }


def test_study_with_zero_holdout_trades_never_carries_a_holdout_value():
    """Reproduziert exakt das Symptom: SqueezeBreakout/ASML mit 133,29 bps OOS-Slippage, aber 0
    Holdout-Trades -- die Holdout-Spalte MUSS 'k. A.' zeigen, nicht den OOS-Wert."""
    report = _report([{
        "strategy": "SqueezeBreakoutStrategy", "symbol": "ASML.ETORO",
        "round_trip_cost_bps": 5.0, "stop_exit_slippage_bps": 133.29,
        "holdout_stop_exit_slippage_bps": None, "holdout_total_trades": 0,
    }])
    text = summary_de.generate_german_summary(report)
    row = next(l for l in text.splitlines() if "133.29" in l)
    cells = [c.strip() for c in row.split("|")]
    # letzte inhaltstragende Zelle ist die Holdout-Spalte.
    assert cells[-2] == "k. A."


def test_study_with_holdout_trades_shows_both_columns():
    report = _report([{
        "strategy": "A", "symbol": "X.ETORO",
        "round_trip_cost_bps": 5.0, "stop_exit_slippage_bps": 10.0,
        "holdout_stop_exit_slippage_bps": 8.0, "holdout_total_trades": 20,
    }])
    text = summary_de.generate_german_summary(report)
    row = next(l for l in text.splitlines() if l.startswith("| A | X.ETORO |") and "5.00" in l)
    assert "10.00" in row
    assert "8.00" in row


def test_section_heading_carries_a_scope_hint():
    report = _report([{
        "strategy": "A", "symbol": "X.ETORO",
        "round_trip_cost_bps": 5.0, "stop_exit_slippage_bps": 10.0,
        "holdout_stop_exit_slippage_bps": None, "holdout_total_trades": 0,
    }])
    text = summary_de.generate_german_summary(report)
    section = text.split("### 2.4 Kostenbasis")[1].split("### 2.5")[0] if "### 2.5" in text else \
        text.split("### 2.4 Kostenbasis")[1]
    assert "Scope-Hinweis" in section
    assert "OOS" in section and "Holdout" in section


def test_column_headers_name_the_two_scopes_explicitly():
    report = _report([{
        "strategy": "A", "symbol": "X.ETORO",
        "round_trip_cost_bps": 5.0, "stop_exit_slippage_bps": 10.0,
        "holdout_stop_exit_slippage_bps": None, "holdout_total_trades": 0,
    }])
    text = summary_de.generate_german_summary(report)
    # Issue #1095/#1243 — die Spaltenueberschriften nennen seither zusaetzlich den Nenner
    # ("vs. Stop-Level"), damit sie nicht mit trigger_to_fill_gap_bps (Nenner: Ausloese-Anker,
    # naechste Tabelle) verwechselt werden koennen.
    assert "Slippage vs. Stop-Level (OOS, Median" in text
    assert "Slippage vs. Stop-Level (Holdout, Median" in text
