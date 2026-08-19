"""Issue #1043/#1192 (P2) — SmaCrossover sampelt keinen einzigen Risikoparameter.

Symptom. ``spaces.py:228-232`` sampelte für ``SmaCrossoverStrategy`` ausschließlich ``sma_period``
und ``cooldown_bars``. ``atr_trailing_multiplier`` (Quelle ``strategy_default``, k=1,5000) und
``max_bars_in_trade`` (Quelle ``strategy_default``, 24) waren UNTUNBAR. Die Study war mit
−0,918 % Holdout-Return und α·n = −1,450 % die zweitschlechteste des Laufs, bei einem
TRAILING_STOP-Anteil von 58,18 %.

Fix. Ein gemeinsamer Sampling-Block (``spaces._sample_risk_layer``) für die Risiko-Layer-
Parameter, den der SmaCrossover-Zweig jetzt aufruft. Neue Invariante
``check_risk_layer_parameter_parity`` bewacht die Vollständigkeit für ALLE Strategien dauerhaft.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import spaces


class _RecordingTrial:
    def __init__(self):
        self.numeric = {}
        self.categorical = {}

    def suggest_int(self, name, low, high, *a, **k):
        self.numeric[name] = (low, high)
        return low

    def suggest_float(self, name, low, high, *a, **k):
        self.numeric[name] = (low, high)
        return low

    def suggest_categorical(self, name, choices, *a, **k):
        self.categorical[name] = list(choices)
        return choices[0]


# ── Akzeptanzkriterium #1043/1: atr_trailing_multiplier_median_source == "sampled" in 14/14 ──────
def test_sma_crossover_now_samples_both_risk_layer_parameters():
    trial = _RecordingTrial()
    params = spaces.sample_params("SmaCrossoverStrategy", trial)
    assert "atr_trailing_multiplier" in params
    assert "max_bars_in_trade" in params
    assert params["atr_trailing_multiplier"] == 0.5  # low bound (RecordingTrial returns low)
    assert params["max_bars_in_trade"] == 12


def test_sma_crossover_symbol_override_still_applies_to_the_shared_risk_layer(monkeypatch):
    """_sample_risk_layer nutzt _bounds_for -- ein kuratierter/automatischer Override fuer
    max_bars_in_trade muss auch fuer SmaCrossover wirksam werden."""
    monkeypatch.setattr(spaces, "_load_search_space_overrides", lambda: {
        "SmaCrossoverStrategy": {"TSLA.ETORO": {"max_bars_in_trade": [4, 18]}},
    })
    trial = _RecordingTrial()
    params = spaces.sample_params("SmaCrossoverStrategy", trial, symbol="TSLA.ETORO")
    assert trial.numeric["max_bars_in_trade"] == (4, 18)
    assert params["max_bars_in_trade"] == 4


def test_all_other_strategies_are_unaffected_by_the_fix():
    """Regressionswaechter: die 13 bereits korrekt sampelnden Strategien behalten ihre exakten,
    strategiespezifischen Bandbreiten (kein Umstellen auf den gemeinsamen Default)."""
    trial = _RecordingTrial()
    spaces.sample_params("FlashCrashReversalStrategy", trial)
    assert trial.numeric["max_bars_in_trade"][0] == 6  # unveraendert die 6er-Untergrenze
    trial2 = _RecordingTrial()
    spaces.sample_params("AdxAtrMomentumStrategy", trial2)
    assert trial2.numeric["cooldown_bars"][0] == 6  # unveraendert die #908-Untergrenze


# ── invariants.check_risk_layer_parameter_parity ─────────────────────────────────────────────────
def test_check_passes_for_the_real_active_strategy_roster():
    import json
    from pathlib import Path
    cfg = json.loads(Path("automation/config/strategies.json").read_text("utf-8"))
    active = [e["strategy_class"] for e in cfg["strategies"] if e.get("active")]
    result = inv.check_risk_layer_parameter_parity(active)
    assert result.passed is True
    assert result.actual is None


def test_check_fails_for_a_strategy_missing_a_risk_layer_param(monkeypatch):
    def _fake_sample_params(strategy, trial, *, symbol=None):
        if strategy == "BrokenStrategy":
            return {"some_param": 1}  # keine Risiko-Layer-Parameter
        return {"atr_trailing_multiplier": 1.0, "max_bars_in_trade": 12}

    from automation.optimizer import spaces as sp
    monkeypatch.setattr(sp, "sample_params", _fake_sample_params)
    result = inv.check_risk_layer_parameter_parity(["BrokenStrategy", "SmaCrossoverStrategy"])
    assert result.passed is False
    assert result.severity == "medium"
    assert set(result.actual["BrokenStrategy"]) == {"atr_trailing_multiplier", "max_bars_in_trade"}
    assert "SmaCrossoverStrategy" not in result.actual


def test_check_respects_the_allowlist(monkeypatch):
    def _fake_sample_params(strategy, trial, *, symbol=None):
        return {"some_param": 1}

    from automation.optimizer import spaces as sp
    monkeypatch.setattr(sp, "sample_params", _fake_sample_params)
    result = inv.check_risk_layer_parameter_parity(
        ["ExemptStrategy"], allowlist={"ExemptStrategy": "kein ATR-Trailing-Stop, Begruendung X"})
    assert result.passed is True


def test_check_skips_unknown_strategy_names_without_crashing():
    result = inv.check_risk_layer_parameter_parity(["NoSuchStrategy"])
    assert result.passed is True
    assert result.actual is None
