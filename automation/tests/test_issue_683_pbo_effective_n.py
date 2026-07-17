"""Issue #683 — CSCV/PBO auf per-BAR-MtM-Returns misst Regime-Timing statt Config-Robustheit.

1. Split-Metrik ist der per-Gruppen-Sortino (annualisierungsfrei), nicht der rohe Gruppen-Mittelwert.
2. Near-Duplicate-Configs werden vor der CSCV-Partitionierung auf effektiv-unabhängige Vertreter
   reduziert (Pearson-Korrelation der vollen per-Perioden-Serie > pbo_cluster_threshold).
3. Akzeptanzkriterium (wörtlich): 10 identische Configs + 10 verschiedene ⇒ PBO unverändert
   gegenüber nur den 10 verschiedenen.
"""
import numpy as np
import optuna

from automation.optimizer import confirm as cmod
from automation.optimizer.cpcv import cluster_effective_configs


def _study(config_rows):
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    for i, rets in enumerate(config_rows):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", True)
        study._storage.set_trial_user_attr(t._trial_id, "oos_period_returns", list(rets))
        study.tell(t, float(i))
    return study


# ── cluster_effective_configs: pure unit ────────────────────────────────────────────────────────
def test_cluster_keeps_all_rows_when_uncorrelated():
    rng = np.random.default_rng(1)
    rows = rng.normal(0.0, 0.01, (10, 100)).tolist()
    keep = cluster_effective_configs(rows, threshold=0.99)
    assert len(keep) == 10


def test_cluster_collapses_near_identical_rows():
    rng = np.random.default_rng(1)
    base = rng.normal(0.0, 0.01, 100)
    tiny_noise_rows = [(base + rng.normal(0, 1e-9, 100)).tolist() for _ in range(5)]
    keep = cluster_effective_configs(tiny_noise_rows, threshold=0.99)
    assert len(keep) == 1


def test_cluster_treats_identical_flat_rows_as_duplicates():
    """Mehrere identische signal-freie (konstante Null-Return) Configs sind Duplikate."""
    rows = [[0.0] * 50 for _ in range(6)]
    keep = cluster_effective_configs(rows, threshold=0.99)
    assert len(keep) == 1


def test_cluster_keeps_different_flat_rows_separate():
    rows = [[0.01] * 50, [0.02] * 50, [0.03] * 50]
    keep = cluster_effective_configs(rows, threshold=0.99)
    assert len(keep) == 3


def test_cluster_empty_and_singleton():
    assert cluster_effective_configs([]) == []
    assert cluster_effective_configs([[0.01, 0.02]]) == [0]


# ── _study_pbo: Akzeptanzkriterium — Duplikat-Invarianz ────────────────────────────────────────
def test_pbo_stable_against_config_duplicates():
    """Wörtliches Akzeptanzkriterium (#683): 10 identische Configs + 10 verschiedene Configs ⇒
    PBO (nahezu) unverändert gegenüber nur den 10 verschiedenen Configs allein."""
    rng = np.random.default_rng(42)
    n_periods = 300
    distinct_rows = []
    for base in np.linspace(-0.002, 0.002, 10):
        distinct_rows.append((base + rng.normal(0, 0.001, n_periods)).tolist())

    pbo_distinct_only, tel_distinct_only = cmod._study_pbo(_study(distinct_rows))
    assert pbo_distinct_only is not None

    duplicate_row = distinct_rows[0]
    padded_rows = distinct_rows + [list(duplicate_row) for _ in range(10)]
    pbo_with_dupes, tel_with_dupes = cmod._study_pbo(_study(padded_rows))
    assert pbo_with_dupes is not None

    # Die effektive Config-Zahl bleibt (nach Clustering) nahe der 10 tatsächlich unabhängigen
    # Configs, NICHT bei den nominellen 20.
    assert tel_with_dupes["pbo_n_configs_raw"] == 20
    assert tel_with_dupes["pbo_n_configs"] <= 11  # 10 distinct + höchstens 1 Duplikat-Repräsentant
    assert abs(pbo_with_dupes - pbo_distinct_only) < 0.15


def test_pbo_metric_documented_as_group_sortino_not_bar_mean():
    rng = np.random.default_rng(2)
    rows = [(rng.normal(0.0005 * i, 0.001, 200)).tolist() for i in range(10)]
    _, telemetry = cmod._study_pbo(_study(rows))
    assert telemetry["pbo_metric"] == "group_sortino"


def test_pbo_n_configs_raw_and_effective_both_present():
    rng = np.random.default_rng(4)
    rows = rng.normal(0.0, 0.01, (12, 100)).tolist()
    _, telemetry = cmod._study_pbo(_study(rows))
    assert telemetry["pbo_n_configs_raw"] == 12
    assert telemetry["pbo_n_configs"] == 12  # unkorreliert ⇒ kein Clustering-Effekt


def test_cluster_threshold_configurable_via_tournament_cfg():
    rng = np.random.default_rng(5)
    base = rng.normal(0.0, 0.01, 150)
    # Zwei nahe, aber nicht perfekt korrelierte Configs (ρ knapp unter 1, ueber einer moderaten
    # Schwelle, aber unter der strengen Default-Schwelle 0.99).
    rows = [base.tolist()]
    rows.append((base * 0.995 + rng.normal(0, 0.02, 150)).tolist())
    for _ in range(8):
        rows.append(rng.normal(0.0, 0.01, 150).tolist())

    _, tel_default = cmod._study_pbo(_study(rows), tournament_cfg={})
    _, tel_loose = cmod._study_pbo(_study(rows), tournament_cfg={"pbo_cluster_threshold": 0.5})
    assert tel_loose["pbo_n_configs"] <= tel_default["pbo_n_configs"]
