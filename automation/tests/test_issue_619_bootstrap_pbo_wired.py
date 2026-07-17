"""Issue #619 — bootstrap.py (#599) und cpcv.py (#600) sind jetzt VERDRAHTET.

Vor #619: 0 Referenzen ausserhalb der Tests (toter Code mit Robustheits-Docstring). Fix: der
Stationary-Bootstrap-CI prüft die untere Sortino-CI-Grenze im Holdout-Gate; die CPCV/PBO ist
Sweep-Level-Diagnose mit Hard-Stop (PBO > 0.5 ⇒ REJECTED_SELECTION_OVERFIT).
"""
import types

import numpy as np
import optuna

from automation.optimizer import confirm as cmod


# ── Bootstrap-CI-Gate: untere CI-Grenze statt Punktschätzer ──────────────────────────────────────
def test_bootstrap_ci_gate_blocks_wide_ci():
    # Rauschige Returns (Mittel ~0, breite Streuung) ⇒ ci_lower(sortino) ≤ 0 ⇒ Gate blockt.
    rng = np.random.default_rng(1)
    m = types.SimpleNamespace(oos_period_returns=tuple(rng.normal(0.0, 0.02, 200)))
    ok, lo = cmod._holdout_bootstrap_ci_passes(m, confidence=0.95)
    assert ok is False
    assert lo is not None and lo <= 0.0


def test_bootstrap_ci_gate_passes_strong_edge():
    # Starker, konsistenter Edge mit MESSBARER (kleiner) Downside ⇒ Sortino hoch positiv ⇒ ci_lower > 0.
    m = types.SimpleNamespace(oos_period_returns=tuple([0.02, 0.018, -0.002, 0.021] * 50))
    ok, lo = cmod._holdout_bootstrap_ci_passes(m, confidence=0.95)
    assert ok is True and lo > 0.0


def test_bootstrap_ci_gate_too_few_returns_no_veto():
    m = types.SimpleNamespace(oos_period_returns=(0.01, 0.02))
    ok, lo = cmod._holdout_bootstrap_ci_passes(m)
    assert ok is True and lo is None   # < 5 Returns ⇒ kein Zusatz-Veto


# ── PBO über die Study (CSCV über der gepoolten OOS-Perioden-Serie, #663) ───────────────────────
def _study_with_period_returns(config_rows):
    """``config_rows``: je Config eine Liste gepoolter OOS-Per-Perioden-Returns (#663 — die CSCV
    partitioniert diese Serie in Gruppen, NICHT mehr die 4 Walk-Forward-Folds)."""
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    for i, rets in enumerate(config_rows):
        t = study.ask()
        study._storage.set_trial_user_attr(t._trial_id, "oos_eligible", True)
        study._storage.set_trial_user_attr(t._trial_id, "oos_period_returns", list(rets))
        study.tell(t, float(i))
    return study


def test_pbo_low_for_consistent_is_oos():
    # IS-Rangordnung stimmt mit OOS überein (die gepoolte Serie ist über die Zeit konsistent
    # niveauverschoben je Config) ⇒ niedrige PBO. >= 10 Configs, >= 8 Gruppen (n_obs=96 ⇒ 12 Gruppen).
    rng = np.random.default_rng(3)
    rows = []
    for base in np.linspace(0.0, 0.02, 12):
        rows.append((base + rng.normal(0, 0.001, 96)).tolist())
    pbo, telemetry = cmod._study_pbo(_study_with_period_returns(rows))
    assert pbo is not None and 0.0 <= pbo <= 1.0
    assert pbo < 0.5
    assert telemetry["pbo_n_configs"] == 12
    assert telemetry["pbo_n_groups"] == 12
    assert telemetry["pbo_metric"] == "period_return"


def test_pbo_none_when_too_few_trials():
    pbo, telemetry = cmod._study_pbo(_study_with_period_returns([[1.0, 2.0, 3.0, 4.0] * 30]))
    assert pbo is None
    assert telemetry["pbo_n_configs"] == 1


def test_pbo_none_when_too_few_groups():
    # >= 10 Configs, aber zu wenig gepoolte Perioden-Beobachtungen für >= 8 Gruppen.
    rows = [[float(i), float(i) + 1.0, float(i) + 2.0] for i in range(10)]
    pbo, telemetry = cmod._study_pbo(_study_with_period_returns(rows))
    assert pbo is None
    assert telemetry["pbo_n_groups"] < 8


def test_pbo_high_for_anticorrelated_is_oos():
    # IS-Gewinner (hohe frühe Perioden-Returns) ist in der zweiten Hälfte der gepoolten Serie
    # systematisch schlecht (Trend kehrt sich um) ⇒ hohe PBO.
    rows = []
    for i in range(12):
        a = i / 11.0
        rows.append(([a] * 48) + ([1.0 - a] * 48))   # erste Hälfte hoch/a, zweite Hälfte invertiert
    pbo, telemetry = cmod._study_pbo(_study_with_period_returns(rows))
    assert pbo is not None and pbo > 0.5


def test_pbo_group_granularity_default_config():
    """Issue #663 — Default-Konfiguration (kein tournament_cfg) nutzt pbo_min_configs=10,
    pbo_n_groups=12 (Modul-Defaults)."""
    rows = [[float(i)] * 200 for i in range(10)]
    _, telemetry = cmod._study_pbo(_study_with_period_returns(rows))
    assert telemetry["pbo_n_groups"] == 12
