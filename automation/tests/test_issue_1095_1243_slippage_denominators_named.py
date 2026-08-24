"""Issue #1095/#1243 (P2, Katalog #1247+) — zwei Slippage-Nenner stehen unmarkiert nebeneinander.

Symptom: §2.4 zeigt "Slippage (OOS, Median)" und "Absetzen-zu-Fill-Gap" in benachbarten Tabellen
mit fast identischen Werten (AdxAtr/GOOGL 20,62 gegen 20,63; Combo 31,79 gegen 31,92). Es sind zwei
verschiedene Grössen: ``stop_exit_slippage_bps`` hat den Stop-Level als Nenner,
``trigger_to_fill_gap_bps`` den Auslöse-Anker.

Fix (docs-only, keine Berechnung geändert): beide Spaltenüberschriften nennen jetzt explizit ihren
Nenner ("vs. Stop-Level" bzw. "vs. Auslöse-Anker") und ein einzeiliger Lesehinweis vor der ersten
Tabelle stellt die Unterscheidung zusätzlich in Prosa dar.

Akzeptanzkriterium: ein Leser kann aus dem Artefakt allein die Nenner unterscheiden.
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


def _study(**overrides):
    r = {
        "strategy": "AdxAtrMomentumStrategy", "symbol": "GOOGL.ETORO",
        "round_trip_cost_bps": 5.0, "stop_exit_slippage_bps": 20.62,
        "holdout_stop_exit_slippage_bps": None, "holdout_total_trades": 0,
        "stop_distance_bps_measured": 100.0, "trigger_to_fill_gap_bps": 20.63,
        "realized_loss_bps": 120.63, "stop_distance_share_median": 0.83,
        "trigger_to_fill_gap_share_median": 0.17,
    }
    r.update(overrides)
    return r


def test_slippage_column_header_names_the_stop_level_denominator():
    text = summary_de.generate_german_summary(_report([_study()]))
    assert "Slippage vs. Stop-Level (OOS, Median" in text
    assert "Slippage vs. Stop-Level (Holdout, Median" in text


def test_trigger_to_fill_gap_column_header_names_the_trigger_anchor_denominator():
    text = summary_de.generate_german_summary(_report([_study()]))
    assert "Absetzen-zu-Fill-Gap vs. Auslöse-Anker (bps)" in text


def test_reading_hint_distinguishes_the_two_denominators_in_prose():
    text = summary_de.generate_german_summary(_report([_study()]))
    assert "Lesehinweis" in text
    assert "Stop-Level" in text and "Auslöse-Anker" in text


def test_near_identical_values_remain_distinguishable_by_column_alone():
    """Reproduziert das konkrete Symptom (20,62 vs. 20,63) — beide Zahlen erscheinen im Text, aber
    unter zwei UNTERSCHIEDLICH benannten Spaltenueberschriften, nicht nur nebeneinander."""
    text = summary_de.generate_german_summary(_report([_study()]))
    idx_stop_level_header = text.index("Slippage vs. Stop-Level")
    idx_trigger_anchor_header = text.index("Absetzen-zu-Fill-Gap vs. Auslöse-Anker")
    assert idx_stop_level_header != -1 and idx_trigger_anchor_header != -1
    assert "20.62" in text or "20,62" in text
    assert "20.63" in text or "20,63" in text
