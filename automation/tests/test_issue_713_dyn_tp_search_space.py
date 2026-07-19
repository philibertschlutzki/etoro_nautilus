"""Issue #713 (P2) — λ/γ als TPE-Suchdimensionen (Req-01-Realgehalt, gated).

`dyn_tp_enabled` wird für ALLE 15 Strategien einheitlich in `spaces.sample_params` als
(searchbare) kategoriale Dimension gesampelt; `dyn_tp_lambda`/`dyn_tp_gamma` werden KONDITIONAL
nur im `True`-Ast gesampelt (TPE-nativ). Akzeptanzkriterien:
- Sweep enumeriert `dyn_tp_enabled` für alle 15 Strategien; λ/γ erscheinen nur in `True`-Trials,
  ohne `ValueError`/`Unknown strategy`.
- Trials mit `dyn_tp_enabled=False` sind bit-identisch zum heutigen Pfad.
- Alle drei `dyn_tp_*`-Keys sind in jeder `*Config` als Feld vorhanden (kein stilles Verwerfen).
"""
import json
from pathlib import Path

import optuna
import pytest

from automation.optimizer import spaces
from automation.optimizer import bounds

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _active_strategies() -> list[str]:
    data = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    return [s["strategy_class"] for s in data["strategies"] if s.get("active", True) is not False]


@pytest.mark.parametrize("strategy", _active_strategies())
def test_dyn_tp_enabled_is_sampled_for_every_strategy(strategy):
    study = optuna.create_study()
    trial = study.ask()
    params = spaces.sample_params(strategy, trial)
    assert "dyn_tp_enabled" in params
    assert isinstance(params["dyn_tp_enabled"], bool)


@pytest.mark.parametrize("strategy", _active_strategies())
def test_no_value_error_across_many_trials(strategy):
    """Kein ValueError/Unknown-strategy über viele Trials (deckt beide dyn_tp_enabled-Äste ab)."""
    study = optuna.create_study()
    for _ in range(20):
        trial = study.ask()
        params = spaces.sample_params(strategy, trial)
        if params["dyn_tp_enabled"]:
            assert "dyn_tp_lambda" in params
            assert "dyn_tp_gamma" in params
            assert 0.1 <= params["dyn_tp_lambda"] <= 3.0
            assert 0.5 <= params["dyn_tp_gamma"] <= 4.0
        else:
            assert "dyn_tp_lambda" not in params
            assert "dyn_tp_gamma" not in params
        study.tell(trial, 0.0)


def test_lambda_gamma_only_appear_in_true_branch_fixed_trial():
    ft_off = optuna.trial.FixedTrial({"sma_period": 20, "cooldown_bars": 10, "dyn_tp_enabled": False})
    p_off = spaces.sample_params("SmaCrossoverStrategy", ft_off)
    assert p_off["dyn_tp_enabled"] is False
    assert "dyn_tp_lambda" not in p_off and "dyn_tp_gamma" not in p_off

    ft_on = optuna.trial.FixedTrial({
        "sma_period": 20, "cooldown_bars": 10, "dyn_tp_enabled": True,
        "dyn_tp_lambda": 0.5, "dyn_tp_gamma": 2.0,
    })
    p_on = spaces.sample_params("SmaCrossoverStrategy", ft_on)
    assert p_on["dyn_tp_enabled"] is True
    assert p_on["dyn_tp_lambda"] == 0.5
    assert p_on["dyn_tp_gamma"] == 2.0


def test_dyn_tp_enabled_false_bit_identical_to_pre_713_param_set():
    """Ein dyn_tp_enabled=False-Trial fügt exakt EINEN neuen Key hinzu (dyn_tp_enabled selbst) —
    keine weiteren Parameter, bit-identisch zum Pre-#713-Suchraum ausserhalb dieses einen Flags."""
    ft = optuna.trial.FixedTrial({"sma_period": 20, "cooldown_bars": 10, "dyn_tp_enabled": False})
    params = spaces.sample_params("SmaCrossoverStrategy", ft)
    assert set(params.keys()) == {"sma_period", "cooldown_bars", "dyn_tp_enabled"}


@pytest.mark.parametrize("strategy", _active_strategies())
def test_extract_numeric_bounds_excludes_categorical_dyn_tp_enabled(strategy):
    """bounds.extract_numeric_bounds (A4.3 param_pen Shrinkage) darf dyn_tp_enabled NICHT als
    numerische Bound führen (kategorial, keine Metrik-Distanz) — funktioniert bereits generisch
    über die RecordingTrial (choices[0]=False ⇒ lambda/gamma werden erst gar nicht besucht)."""
    b = bounds.extract_numeric_bounds(strategy)
    assert "dyn_tp_enabled" not in b
    assert "dyn_tp_lambda" not in b
    assert "dyn_tp_gamma" not in b
