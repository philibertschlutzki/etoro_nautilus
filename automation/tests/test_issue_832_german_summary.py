"""Issue #832 (P1, Katalog #828-#835, GitHub-Issue #751) — deutschsprachiger Abschlussbericht:
monetäres Ergebnis, Zeitdauer, längste Trades.

Direkte Nutzeranforderung (siehe Katalog #751): nach Abschluss des Sweeps soll eine Zusammenfassung
in deutscher Sprache entstehen. Diese Tests decken die drei Bausteine ab:

1. ``backtest_runner._calculate_stats`` liefert seit diesem Fix ``max_holding_time_s``/
   ``min_holding_time_s``/``p95_holding_time_s`` (Sekunden) zusätzlich zu den bereits vorhandenen
   ``median_bars_held``/``p95_bars_held`` (#710, Bars).
2. ``automation.optimizer.summary_de.generate_german_summary`` baut den vollständigen Bericht
   AUSSCHLIESSLICH aus einem #742-Report-Dict (kein Zugriff auf ``data/optimizer/``).
3. ``invariants.check_holding_time_cap`` — Plausibilitätswächter gegen die #714/GR-01-Zeitbox.

Scope-Entscheidung (dokumentiert im summary_de.py-Modul-Docstring): Abschnitt 4 listet Top-K
STUDIES nach Haltedauer, nicht individuelle Trades mit Entry-/Exit-Zeitstempel — das würde eine
neue State-Verfolgung in der FIFO-Match-Schleife von ``backtest_runner.extract_metrics``
voraussetzen, ohne einen realen Marktdaten-Lauf zur Verifikation in diesem Environment.
"""
import pandas as pd
import pytest

from automation.backtest_runner import _calculate_stats
from automation.optimizer import invariants as inv
from automation.optimizer import summary_de


# ── backtest_runner._calculate_stats: max/min/p95_holding_time_s ───────────────────────────────────
def _mtm_series(n_bars=50):
    idx = pd.date_range("2025-01-01", periods=n_bars, freq="1h", tz="UTC")
    return pd.Series([1000.0 + i for i in range(n_bars)], index=idx)


def test_holding_time_seconds_fields_present_and_consistent_with_bars():
    # hold_list: (holding_ns, qty) -- drei Trades mit 1h/2h/5h Haltedauer.
    hold_list = [(3600 * 10**9, 1.0), (7200 * 10**9, 1.0), (18000 * 10**9, 1.0)]
    stats = _calculate_stats([1.0, -1.0, 2.0], hold_list, 1000.0, mtm_series=_mtm_series(),
                             min_trades_for_sortino=10)
    assert stats["max_holding_time_s"] == pytest.approx(18000.0)
    assert stats["min_holding_time_s"] == pytest.approx(3600.0)
    assert stats["p95_holding_time_s"] is not None
    # Bars-Aequivalent (#710) bleibt konsistent: max/3600 == p95_bars_held-Groessenordnung.
    assert stats["max_holding_time_s"] / 3600.0 == pytest.approx(5.0)


def test_holding_time_seconds_fields_are_zero_for_empty_hold_list():
    stats = _calculate_stats([], [], 1000.0, mtm_series=_mtm_series(), min_trades_for_sortino=10)
    assert stats["max_holding_time_s"] == 0.0
    assert stats["min_holding_time_s"] == 0.0
    assert stats["p95_holding_time_s"] == 0.0


# ── invariants.check_holding_time_cap (Issue #861 — unified per-trial-aware contract) ───────────
def test_holding_time_cap_passes_within_the_714_boundary():
    studies_out = [{"strategy": "S", "symbol": "A.ETORO",
                     "timebox_evaluated_trades": 10, "timebox_violation_fraction": 0.0}]
    result = inv.check_holding_time_cap(studies_out)
    assert result.passed is True


def test_holding_time_cap_fails_beyond_the_714_boundary():
    studies_out = [{"strategy": "S", "symbol": "A.ETORO",
                     "timebox_evaluated_trades": 10, "timebox_violation_fraction": 0.30}]
    result = inv.check_holding_time_cap(studies_out)
    assert result.passed is False
    assert "S/A.ETORO" in result.detail


def test_holding_time_cap_is_a_noop_without_telemetry():
    result = inv.check_holding_time_cap([{"strategy": "S", "symbol": "A.ETORO"}])
    assert result.passed is True


def test_holding_time_cap_respects_the_study_tolerance():
    studies_out = [{"strategy": "S", "symbol": "A.ETORO",
                     "timebox_evaluated_trades": 10, "timebox_violation_fraction": 0.25}]
    result = inv.check_holding_time_cap(studies_out, study_tolerance=0.25)
    assert result.passed is True


# ── summary_de.generate_german_summary: liest AUSSCHLIESSLICH das Report-Dict ──────────────────────
def _minimal_report(**overrides):
    base = {
        "run_id": "run-abc",
        "run_status": "complete",
        "started_at_utc": "2026-07-30T00:00:00Z",
        "wallclock_s": 7200.0,
        "cli_args": {"n_jobs": 8, "n_jobs_source": "CLI"},
        "symbols_completed": None,
        "symbols_planned": None,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {},
            "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [],
            "boundary_solutions": [],
            "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_summary_contains_all_five_sections():
    text = summary_de.generate_german_summary(_minimal_report())
    for heading in ["## 1. Ergebnis in einem Satz", "## 2. Monetäres Ergebnis", "## 3. Zeitdauer",
                    "## 4. Trades mit der längsten Haltedauer", "## 5. Auffälligkeiten"]:
        assert heading in text


def test_zero_promotions_states_no_deployable_result_verbatim():
    """Akzeptanzkriterium #832 — bei 0 Promotionen enthält Abschnitt 2.1 wörtlich die Aussage, dass
    kein deploybares Ergebnis vorliegt."""
    report = _minimal_report(studies=[
        {"strategy": "S", "symbol": "A.ETORO", "promotion_outcome": "REJECTED",
         "holdout_total_return": -0.01},
    ])
    report["cross_study"]["promotion_outcome_counts"] = {"REJECTED": 1}
    text = summary_de.generate_german_summary(report)
    assert "kein deploybares Ergebnis" in text
    assert "NICHT deploybar" in text


def test_deployable_candidate_appears_in_section_2_1_table():
    report = _minimal_report(studies=[
        {"strategy": "S", "symbol": "A.ETORO", "promotion_outcome": "READY_FOR_PR",
         "holdout_total_return": 0.05, "holdout_expectancy": 0.002, "holdout_win_rate": 0.6,
         "holdout_profit_factor": 1.4, "holdout_total_trades": 40},
    ])
    report["cross_study"]["promotion_outcome_counts"] = {"READY_FOR_PR": 1}
    text = summary_de.generate_german_summary(report)
    assert "kein deploybares Ergebnis aus diesem Lauf" not in text.split("## 2")[0].lower()
    assert "S | A.ETORO" in text
    assert "5.0 %" in text


def test_promote_global_default_counts_as_deployable():
    """#783 — PROMOTE_GLOBAL_DEFAULT ist eine echte Promotion, keine Ablehnung."""
    report = _minimal_report(studies=[
        {"strategy": "S", "symbol": "A.ETORO", "promotion_outcome": "PROMOTE_GLOBAL_DEFAULT",
         "holdout_total_return": 0.01},
    ])
    report["cross_study"]["promotion_outcome_counts"] = {"PROMOTE_GLOBAL_DEFAULT": 1}
    text = summary_de.generate_german_summary(report)
    assert "1 Promotion(en)" in text


def test_longest_trades_section_lists_the_longest_holding_studies():
    report = _minimal_report()
    report["cross_study"]["longest_holding_studies"] = [
        {"strategy": "S", "symbol": "A.ETORO", "max_holding_time_s": 20 * 3600.0,
         "p95_holding_time_s": 15 * 3600.0},
    ]
    text = summary_de.generate_german_summary(report)
    assert "20.00 h" in text


def test_anomalies_section_surfaces_failing_invariants():
    report = _minimal_report(invariant_checks=[
        {"check": "check_holding_time_cap", "passed": False, "scope": "global",
         "detail": "S/A.ETORO ueberschreitet die Zeitbox"},
    ])
    text = summary_de.generate_german_summary(report)
    assert "check_holding_time_cap" in text
    assert "Invarianten-FAILs (1)" in text


def test_aborted_run_status_is_surfaced_in_section_1_and_3():
    report = _minimal_report(run_status="aborted_wallclock", symbols_completed=40, symbols_planned=122)
    text = summary_de.generate_german_summary(report)
    assert "NICHT vollständig" in text
    assert "40/122" in text
    assert "Laufzeit-Budget" in text


def test_generate_german_summary_never_touches_the_filesystem(monkeypatch, tmp_path):
    """Akzeptanzkriterium #832 — Unit-Test mit einem synthetischen Report erzeugt die vollstaendige
    Datei ohne Zugriff auf data/optimizer/."""
    import os
    monkeypatch.chdir(tmp_path)  # kein automation/config/ o.ae. im CWD erreichbar
    text = summary_de.generate_german_summary(_minimal_report())
    assert len(text) > 0
    assert os.listdir(tmp_path) == []  # generate_german_summary selbst schreibt nichts


# ── write_german_summary: schreibt logs/zusammenfassung_<run_id>.md ────────────────────────────────
def test_write_german_summary_creates_the_expected_file(tmp_path):
    out_path = summary_de.write_german_summary(_minimal_report(run_id="run-xyz"), logs_dir=tmp_path)
    assert out_path == tmp_path / "zusammenfassung_run-xyz.md"
    assert out_path.exists()
    assert "run-xyz" in out_path.read_text("utf-8")


def test_write_german_summary_for_report_path_reads_json_from_disk(tmp_path):
    import json
    report_path = tmp_path / "run_report.json"
    report_path.write_text(json.dumps(_minimal_report(run_id="run-fromfile")), "utf-8")
    out_path = summary_de.write_german_summary_for_report_path(report_path, logs_dir=tmp_path / "logs")
    assert out_path is not None
    assert out_path.exists()
    assert "run-fromfile" in out_path.read_text("utf-8")


def test_write_german_summary_for_report_path_fails_open_on_bad_json(tmp_path):
    bad_path = tmp_path / "not_json.json"
    bad_path.write_text("{not valid json", "utf-8")
    assert summary_de.write_german_summary_for_report_path(bad_path, logs_dir=tmp_path) is None
