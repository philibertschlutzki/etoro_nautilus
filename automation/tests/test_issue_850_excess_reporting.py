"""Issue #850 (P1) — Abschnitt 2.3 misst das Symbol, nicht die Strategie.

Root-Cause: ``holdout_excess_return`` ist im Bärenmarkt näherungsweise ``−Buy&Hold``, also eine
SYMBOL-Konstante — 14 strukturell verschiedene Strategien lieferten auf demselben Symbol Excess-
Returns innerhalb weniger Prozentpunkte, bei einem absoluten Niveau von über 20 (Varianzanteil
Symbol 99,1 % auf den Katalog-Daten). Die Spalte "positiv (schlägt Buy & Hold)" belohnte damit
Strategien, die überwiegend in Cash standen (vermiedener Kursverlust), nicht echtes Alpha.

Fix:
1. ``backtest_runner._calculate_stats`` exportiert ``exposure_fraction`` (Anteil der Fenster-Zeit
   mit offener Position), durchgereicht als ``oos_exposure_fraction``
   (parsing.py/confirm.py/report.py) bis zu ``holdout_exposure_fraction`` im #742-Report.
2. ``report._excess_variance_decomposition`` zerlegt die Streuung von ``holdout_excess_return``
   nach Symbol (Ein-Weg-ANOVA-Quadratsummen-Zerlegung).
3. ``summary_de.py`` Abschnitt 2.3 zeigt Strategie-/B&H-Return, Excess, Zeit im Markt und
   Excess/Exposure; sortiert nach Strategie-Return; ein negativer B&H-Return unterdrückt die
   "schlägt Buy & Hold"-Wertung.

Akzeptanzkriterien:
- AK-1: Abschnitt 2.3 nennt für jede Zeile Strategie-Return, B&H-Return, Excess und Zeit im Markt.
- AK-2: Zeilen mit buyhold_return < 0 tragen die Kennzeichnung und keine "schlägt Buy & Hold"-Wertung.
- AK-3: Der Varianzanteil wird berechnet und ausgewiesen.
- AK-4: Ein synthetischer Testfall mit echtem Alpha erhält die Wertung "schlägt Buy & Hold".
"""
import pandas as pd
import pytest

from automation.backtest_runner import _calculate_stats
from automation.optimizer.report import _excess_variance_decomposition
from automation.optimizer import summary_de


# ── exposure_fraction in _calculate_stats ────────────────────────────────────────────────────────
def _dt_mtm(n_bars: int, held_ratio: float | None = None):
    idx = pd.date_range("2025-01-01", periods=n_bars, freq="1h", tz="UTC")
    return pd.Series([1000.0 + i for i in range(n_bars)], index=idx)


def test_exposure_fraction_matches_manual_ratio_single_series():
    series = _dt_mtm(101)  # Spanne: 100h
    # 2 Round-Trips à 10h Haltezeit -> 20h / 100h = 0.2
    hold_list = [(10 * 3600 * 10**9, 1.0), (10 * 3600 * 10**9, 1.0)]
    stats = _calculate_stats(
        pnl_list=[1.0, 1.0], hold_list=hold_list, starting_capital=1000.0, mtm_series=series)
    assert stats["exposure_fraction"] == pytest.approx(0.2, abs=1e-9)


def test_exposure_fraction_is_none_without_datetime_index():
    """RangeIndex (kein DatetimeIndex, z. B. in vielen aelteren Test-Fixtures) -> nicht
    beurteilbar, exposure_fraction bleibt None (kein Crash, kein falscher Wert)."""
    series = pd.Series([1000.0, 1010.0, 1005.0])
    stats = _calculate_stats(
        pnl_list=[1.0], hold_list=[(3600 * 10**9, 1.0)], starting_capital=1000.0, mtm_series=series)
    assert stats.get("exposure_fraction") is None


def test_exposure_fraction_is_none_without_hold_list():
    series = _dt_mtm(51)
    stats = _calculate_stats(pnl_list=[], hold_list=[], starting_capital=1000.0, mtm_series=series)
    assert stats.get("exposure_fraction") is None


def test_exposure_fraction_clips_to_one_for_overlapping_positions():
    """Schutz-Klemmung: eine (hier konstruierte) Haltezeit-Summe > Fenster-Spanne darf nicht > 1.0
    ergeben."""
    series = _dt_mtm(11)  # Spanne: 10h
    hold_list = [(20 * 3600 * 10**9, 1.0)]  # 20h Haltezeit > 10h Fenster
    stats = _calculate_stats(
        pnl_list=[1.0], hold_list=hold_list, starting_capital=1000.0, mtm_series=series)
    assert stats["exposure_fraction"] == 1.0


def test_exposure_fraction_uses_sum_of_fold_spans_excluding_embargo_gaps():
    """mtm_frames (mehrere Folds mit Embargo-Luecke dazwischen): die Fenster-Spanne ist die SUMME
    der Einzel-Fold-Spannen, NICHT first-to-last des konkatenierten Frames (das wuerde die
    Embargo-Luecke faelschlich als Fenster-Zeit mitzaehlen)."""
    fold_a = _dt_mtm(11)  # 10h Spanne, endet 2025-01-01 10:00
    idx_b = pd.date_range("2025-01-05 00:00", periods=11, freq="1h", tz="UTC")  # 10h Spanne, Luecke davor
    fold_b = pd.Series([1000.0] * 11, index=idx_b)
    # 10h Haltezeit gesamt / (10h + 10h) Fold-Spannen = 0.5 (NICHT / grosse Gesamt-Spanne inkl. Luecke)
    hold_list = [(10 * 3600 * 10**9, 1.0)]
    concat = pd.concat([fold_a, fold_b])
    stats = _calculate_stats(
        pnl_list=[1.0], hold_list=hold_list, starting_capital=1000.0,
        mtm_series=concat, mtm_frames=[fold_a, fold_b])
    assert stats["exposure_fraction"] == pytest.approx(0.5, abs=1e-9)


# ── report._excess_variance_decomposition (reine Funktion) ──────────────────────────────────────
def test_variance_decomposition_none_with_fewer_than_two_symbols():
    studies = [
        {"symbol": "A.ETORO", "holdout_excess_return": 0.1},
        {"symbol": "A.ETORO", "holdout_excess_return": 0.12},
    ]
    assert _excess_variance_decomposition(studies) is None


def test_variance_decomposition_dominant_symbol_share():
    """Nachbildung der Katalog-Situation: zwei Symbole mit stark unterschiedlichem Excess-Niveau,
    aber geringer Innersymbol-Streuung -> symbol_share nahe 1."""
    studies = (
        [{"symbol": "MSTR.ETORO", "holdout_excess_return": v}
         for v in (0.219, 0.223, 0.226, 0.230, 0.233, 0.238, 0.241, 0.257)]
        + [{"symbol": "COIN.ETORO", "holdout_excess_return": v}
           for v in (0.064, 0.067, 0.070, 0.073, 0.076, 0.076)]
    )
    result = _excess_variance_decomposition(studies)
    assert result is not None
    assert result["symbol_share"] > 0.9
    assert result["strategy_share"] == pytest.approx(1.0 - result["symbol_share"])
    assert result["n_rows"] == 14


def test_variance_decomposition_zero_variance_returns_none_shares():
    studies = [
        {"symbol": "A.ETORO", "holdout_excess_return": 0.05},
        {"symbol": "B.ETORO", "holdout_excess_return": 0.05},
    ]
    result = _excess_variance_decomposition(studies)
    assert result == {"symbol_share": None, "strategy_share": None, "n_rows": 2}


def test_variance_decomposition_ignores_rows_without_excess_return():
    studies = [
        {"symbol": "A.ETORO", "holdout_excess_return": 0.1},
        {"symbol": "A.ETORO", "holdout_excess_return": None},
        {"symbol": "B.ETORO", "holdout_excess_return": 0.2},
    ]
    result = _excess_variance_decomposition(studies)
    assert result["n_rows"] == 2


# ── summary_de.py Abschnitt 2.3 ──────────────────────────────────────────────────────────────────
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


def test_ak1_section_2_3_lists_strategy_buyhold_excess_and_exposure():
    report = _minimal_report(studies=[
        {"strategy": "S", "symbol": "A.ETORO", "holdout_total_return": 0.05,
         "holdout_buyhold_return": 0.03, "holdout_excess_return": 0.02,
         "holdout_exposure_fraction": 0.6},
    ])
    report["cross_study"]["excess_variance_decomposition"] = None
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "Strategie-Return" in section_2_3
    assert "Buy&Hold-Return" in section_2_3
    assert "Excess" in section_2_3
    assert "Zeit im Markt" in section_2_3
    assert "5.0 %" in section_2_3  # holdout_total_return
    assert "3.0 %" in section_2_3  # holdout_buyhold_return
    assert "60.0 %" in section_2_3  # exposure_fraction


def test_ak2_negative_buyhold_suppresses_beats_buyhold_wording():
    report = _minimal_report(studies=[
        {"strategy": "VolatilityBreakoutPump", "symbol": "MSTR.ETORO",
         "holdout_total_return": -0.02, "holdout_buyhold_return": -0.257,
         "holdout_excess_return": 0.237, "holdout_exposure_fraction": 0.1},
    ])
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "B&H negativ" in section_2_3
    assert "schlägt Buy & Hold" not in section_2_3


def test_ak4_real_alpha_case_gets_beats_buyhold_wording():
    """Akzeptanzkriterium #850-AK-4: Strategie +10 %, B&H +8 %, exposure_fraction 0.8 -> echtes
    Alpha, die Wertung 'schlägt Buy & Hold' bleibt erhalten."""
    report = _minimal_report(studies=[
        {"strategy": "RealAlpha", "symbol": "X.ETORO", "holdout_total_return": 0.10,
         "holdout_buyhold_return": 0.08, "holdout_excess_return": 0.02,
         "holdout_exposure_fraction": 0.8},
    ])
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "schlägt Buy & Hold" in section_2_3


def test_ak3_variance_share_line_rendered():
    report = _minimal_report(studies=[
        {"strategy": "S1", "symbol": "MSTR.ETORO", "holdout_total_return": 0.2,
         "holdout_buyhold_return": 0.21, "holdout_excess_return": 0.22,
         "holdout_exposure_fraction": 0.1},
        {"strategy": "S2", "symbol": "COIN.ETORO", "holdout_total_return": 0.05,
         "holdout_buyhold_return": 0.06, "holdout_excess_return": 0.07,
         "holdout_exposure_fraction": 0.1},
    ])
    report["cross_study"]["excess_variance_decomposition"] = {
        "symbol_share": 0.991, "strategy_share": 0.009, "n_rows": 2,
    }
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "99.1 %" in section_2_3
    assert "Symbol" in section_2_3


def test_section_2_3_absent_when_no_benchmark_data():
    report = _minimal_report(studies=[
        {"strategy": "S", "symbol": "A.ETORO", "holdout_total_return": 0.05},
    ])
    text = summary_de.generate_german_summary(report)
    section_2_3 = text.split("### 2.3")[1].split("### 2.4")[0]
    assert "Keine Benchmark-Vergleichsdaten" in section_2_3
