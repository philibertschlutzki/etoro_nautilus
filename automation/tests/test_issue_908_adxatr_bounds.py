"""Issue #908 (P1) — AdxAtrMomentum handelt nach der #870-Bounds-Öffnung alle ~5,7 Bars (754
OOS-Trades / 180 d bei einer 24-Bar-Zeitbox) — Durchsatzkosten ohne Informationsgewinn.

Fix: ``cooldown_bars``-Untergrenze 2 → 6, UND ``min_holding_time`` neu im Suchraum (vorher an
KEINER Strategie gesampelt), um das Handelsregime zu begrenzen.

Issue #1275 (GH #1148, Katalog #1272-1297, P0) Fix Punkt 3 — ``min_holding_time``-Obergrenze auf
den rekalibrierten ``max_bars_in_trade``-Suchraum ([3, 6], Faktor 0.24) abgestimmt: 8 → 2.
"""
import optuna

from automation.optimizer.spaces import sample_params


def _trial():
    study = optuna.create_study()
    return study.ask()


def test_adxatr_cooldown_bars_lower_bound_raised_to_six():
    trial = _trial()
    params = sample_params("AdxAtrMomentumStrategy", trial)
    dist = trial.distributions["cooldown_bars"]
    assert dist.low == 6
    assert dist.high == 36


def test_adxatr_min_holding_time_is_now_sampled():
    trial = _trial()
    params = sample_params("AdxAtrMomentumStrategy", trial)
    assert "min_holding_time" in params
    dist = trial.distributions["min_holding_time"]
    assert dist.low == 0
    assert dist.high == 2


def test_adxatr_sampled_params_stay_within_bounds_across_many_trials():
    study = optuna.create_study()
    for _ in range(30):
        trial = study.ask()
        params = sample_params("AdxAtrMomentumStrategy", trial)
        assert 6 <= params["cooldown_bars"] <= 36
        assert 0 <= params["min_holding_time"] <= 2
        study.tell(trial, 0.0)
