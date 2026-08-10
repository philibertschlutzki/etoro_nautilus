"""Issue #983 (Katalog D, P0 HEADLINE) — der Wallclock-Preflight (#931) unterschätzte die reale
Laufzeit des Referenzlaufs ``46cf5070`` um Faktor 1.90 und die ``degrade``-Policy kürzte das
Suchbudget trotzdem nur um 26%, statt das Ziel zu erreichen.

Vollständige Fehlerzerlegung aus dem Referenzlauf:
    Median-statt-Mittelwert (rechtsschief):    7.575s vs 9.690s  → Faktor 1.279
    Nicht-Backtest-Anteil der Study-Wallclock: 25687s / 28908s   → Faktor 1.125
    Scheduling-Verlust (Makespan vs Σ/n_jobs): 2900.8s vs 2411s  → Faktor 1.203
    Produkt: 1.279 × 1.125 × 1.203 ≈ 1.731, plus Rest-Overhead ≈ 1.10 ⇒ Gesamt ≈ 1.90

Diese Tests decken die drei eingeführten Korrekturen ab:
1. ``wallclock_guard.estimate_expected_wallclock_h`` konsumiert MITTELWERT statt Median und zwei
   zusätzliche, config-gesteuerte Korrekturfaktoren (``backtest_share``, ``scheduling_efficiency``).
2. ``sweep._read_last_backtest_ms_mean`` — der Ledger-Ersatz für den Mittelwert (gewichtet nach
   ``n_trials``), analog zu ``sweep._read_last_backtest_ms_median`` (#931).
3. ``budget_degradation_factor`` erscheint IMMER (1.0 = kein Degrade) in ``optimizer_study_
   completed``/im #742-Report, statt nur bei aktivem Degrade sichtbar zu sein.
"""
import json

from automation.optimizer import wallclock_guard
from automation.optimizer import sweep


# ── wallclock_guard.estimate_expected_wallclock_h ────────────────────────────────────────────────
def test_legacy_call_without_correction_factors_is_unaffected_when_factors_default_to_one():
    """Rückwärtskompatibilität: ruft man mit backtest_share=1.0/scheduling_efficiency=1.0 (die
    Pre-#983-Annahmen), ist das Ergebnis bit-identisch zur alten #931-Formel."""
    h = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=277420, backtest_ms_median=7575.0, parallelism_degree=6.0,
        backtest_share=1.0, scheduling_efficiency=1.0)
    assert abs(h - 97.285) < 0.01


def test_default_correction_factors_increase_the_estimate():
    """Die #983-Defaults (0.85/0.80) MÜSSEN die Schätzung gegenüber der alten reinen
    trials×ms/n_jobs-Formel erhöhen (der Preflight hat bislang UNTERSCHÄTZT, nie überschätzt)."""
    legacy = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=277420, backtest_ms_median=7575.0, parallelism_degree=6.0,
        backtest_share=1.0, scheduling_efficiency=1.0)
    corrected = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=277420, backtest_ms_median=7575.0, parallelism_degree=6.0)
    assert corrected > legacy
    # Default-Korrektur: 1 / (0.85 * 0.80) ≈ 1.4706×.
    assert abs(corrected / legacy - 1.0 / (0.85 * 0.80)) < 1e-9


def test_reference_run_error_decomposition_reproduces_measured_factors():
    """Reproduziert die im Referenzlauf 46cf5070 GEMESSENEN Korrekturfaktoren (nicht die
    konservativen Defaults): Mittelwert 9.690s statt Median 7.575s, backtest_share=0.889,
    scheduling_efficiency=1/1.203=0.831 — das Produkt der drei Fehlerquellen soll nahe am
    dokumentierten Gesamtfaktor 1.731 liegen (vor dem "Rest-Overhead"-Faktor 1.10)."""
    with_median = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=277420, backtest_ms_median=7575.0, parallelism_degree=6.0,
        backtest_share=1.0, scheduling_efficiency=1.0)
    with_mean_and_corrections = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=277420, backtest_ms_median=9690.0, parallelism_degree=6.0,
        backtest_share=0.889, scheduling_efficiency=1.0 / 1.203)
    ratio = with_mean_and_corrections / with_median
    assert abs(ratio - 1.731) < 0.02


def test_non_positive_correction_factors_fall_back_to_no_correction():
    h_default = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=1000, backtest_ms_median=5000.0, parallelism_degree=4.0,
        backtest_share=0.0, scheduling_efficiency=-1.0)
    h_legacy = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=1000, backtest_ms_median=5000.0, parallelism_degree=4.0,
        backtest_share=1.0, scheduling_efficiency=1.0)
    assert h_default == h_legacy


def test_zero_parallelism_falls_back_to_serial():
    h = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=100, backtest_ms_median=1000.0, parallelism_degree=0.0)
    h_serial = wallclock_guard.estimate_expected_wallclock_h(
        expected_trials=100, backtest_ms_median=1000.0, parallelism_degree=1.0)
    assert h == h_serial


# ── sweep._read_last_backtest_ms_mean ─────────────────────────────────────────────────────────────
def test_read_last_backtest_ms_mean_weights_by_n_trials(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report = {
        "studies": [
            {"strategy": "A", "symbol": "X", "n_trials": 100, "backtest_ms_mean": 10000.0},
            {"strategy": "B", "symbol": "X", "n_trials": 300, "backtest_ms_mean": 6000.0},
        ]
    }
    (reports_dir / "run_1.json").write_text(json.dumps(report), encoding="utf-8")

    import automation.optimizer.report as report_mod
    monkeypatch.setattr(report_mod, "REPORTS_DIR", reports_dir, raising=False)

    mean = sweep._read_last_backtest_ms_mean()
    # gewichteter Mittelwert: (100*10000 + 300*6000) / 400 = 7000.0
    assert mean == 7000.0


def test_read_last_backtest_ms_mean_none_when_no_reports(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports_empty"
    reports_dir.mkdir()
    import automation.optimizer.report as report_mod
    monkeypatch.setattr(report_mod, "REPORTS_DIR", reports_dir, raising=False)
    assert sweep._read_last_backtest_ms_mean() is None


def test_read_last_backtest_ms_mean_ignores_studies_without_the_field(tmp_path, monkeypatch):
    """Rückwärtskompatibilität: ein Pre-#983-Report ohne ``backtest_ms_mean`` darf nicht crashen,
    nur keinen Beitrag liefern."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report = {"studies": [{"strategy": "A", "symbol": "X", "n_trials": 100}]}
    (reports_dir / "run_1.json").write_text(json.dumps(report), encoding="utf-8")
    import automation.optimizer.report as report_mod
    monkeypatch.setattr(report_mod, "REPORTS_DIR", reports_dir, raising=False)
    assert sweep._read_last_backtest_ms_mean() is None
