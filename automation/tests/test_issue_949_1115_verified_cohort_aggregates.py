"""Issue #949/#1115 (Katalog #960) — Aggregat-Kennzahlen wurden ueber die kontaminierte Kohorte
gebildet.

Symptom: ``median(budget_executed_fraction) = 0,000`` bei tatsaechlich 1,000 ueber die eigene
Kohorte; Σ ``n_trials_budgeted`` 4000 bzw. 5820 statt 1940; ``worker_utilisation`` 1,1251/1,1360 >
1,0 — die Zusammenfassung uebernahm alle drei ungeprueft (B-6). Zusaetzlich trugen ZWEI
verschiedene Groessen denselben Namen "Worker-Auslastung" im selben Dokument.

Fix:
  1. Jede Aggregation ueber ``studies`` laeuft ausschliesslich auf der ``run_id``-verifizierten
     Kohorte (folgt strukturell aus #940/#1106 — ``report._build_report`` filtert ``studies_out``
     bereits vor jeder Aggregation; dieser Test fuehrt es als eigenes Akzeptanzkriterium).
  2. Die beiden Auslastungsgroessen tragen seither getrennte Namen: ``worker_occupancy_wallclock``
     (Σ Study-Wallclock / (n_jobs × Wallclock), kann > 1.0) und ``cpu_utilisation_backtest``
     (Σ Backtest-CPU-Zeit / (n_jobs × Wallclock), physikalisch <= 1.0).
"""
from automation.optimizer.report import (
    _budget_execution_summary, _worker_occupancy_wallclock, _cpu_utilisation_backtest,
)


def test_budget_execution_summary_operates_only_on_the_records_it_is_given():
    """Aggregation ueber ``studies_out`` (bereits die verifizierte Kohorte, #940/#1106) — ein Symbol
    aus einem fremden Lauf, das NICHT in dieser Liste steht, kann den Median nicht mehr verzerren."""
    own_cohort = [
        {"strategy": "TestStrat", "symbol": "TSLA.ETORO", "budget_executed_fraction": 1.0},
        {"strategy": "TestStrat", "symbol": "NVDA.ETORO", "budget_executed_fraction": 1.0},
    ]
    summary = _budget_execution_summary(own_cohort)
    assert summary["median"] == 1.0
    assert summary["n"] == 2


def test_budget_execution_summary_ignores_records_without_the_field():
    """Ein Record ohne ``budget_executed_fraction`` (z. B. eine ausgeschlossene fremde Study, die
    versehentlich ohne dieses Feld durchgereicht wuerde) zaehlt NICHT in den Nenner."""
    records = [
        {"budget_executed_fraction": 1.0},
        {"budget_executed_fraction": None},
        {},
    ]
    summary = _budget_execution_summary(records)
    assert summary["n"] == 1
    assert summary["median"] == 1.0


def test_worker_occupancy_wallclock_and_cpu_utilisation_backtest_are_distinct_metrics():
    """Reproduziert B-6 im Kleinen: dieselbe Kohorte liefert zwei UNTERSCHIEDLICHE Werte fuer die
    beiden jetzt getrennt benannten Groessen -- kein gemeinsamer Name mehr, unter dem sie
    verwechselt werden koennten."""
    studies = [
        {"study_wallclock_s": 900.0, "backtest_ms_sum": 300_000.0},
        {"study_wallclock_s": 950.0, "backtest_ms_sum": 250_000.0},
    ]
    occupancy = _worker_occupancy_wallclock(studies, n_jobs=2, sweep_wallclock_s=600.0)
    cpu = _cpu_utilisation_backtest(studies, n_jobs=2, sweep_wallclock_s=600.0)
    # Study-Wallclock ueberlappt sich (verschachtelte Worker-Pools) -- kann > 1.0 liegen.
    assert occupancy == (900.0 + 950.0) / (2 * 600.0)
    assert occupancy > 1.0
    # Echte CPU-Zeit ist additiv ueber Trials, bleibt hier deutlich unter 1.0.
    assert cpu == (300.0 + 250.0) / (2 * 600.0)
    assert cpu < 1.0
    assert occupancy != cpu


def test_worker_occupancy_wallclock_reports_none_without_data_regardless_of_foreign_contamination():
    assert _worker_occupancy_wallclock([], n_jobs=4, sweep_wallclock_s=100.0) is None
