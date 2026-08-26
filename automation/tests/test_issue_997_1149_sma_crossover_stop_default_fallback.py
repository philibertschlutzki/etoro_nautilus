"""Issue #997/#1149 (Katalog #1170) — ``SmaCrossoverStrategy`` sampelt keinen Stop-Parameter ⇒
Kalibrierkette strukturell nicht messbar.

Symptom: ``atr_trailing_multiplier_median = None`` in beiden SmaCrossover-Studies (die einzigen 2
von 28), obwohl die Basisklasse (``HourlyStrategyBase``) trotzdem einen ATR-Trailing-Stop mit dem
DEFAULT-Multiplikator (1.5) anwendet — ``spaces.py`` sampelt fuer ``SmaCrossoverStrategy`` nur
``sma_period``/``cooldown_bars``.

Fix: ``report._median_of_sampled_param`` bekommt einen ``default_from``-Fallback und liefert ein
``(value, source)``-Tupel; ``_study_record`` reicht den passenden ``strategy_defaults.json``-Eintrag
durch. Kein stiller Ratewert: fehlt der Parameter in BEIDEN Quellen, bleibt ``value=None`` und
``source="unavailable"``.
"""
import json
from pathlib import Path

from automation.optimizer import report as rpt

_STRATEGY_DEFAULTS_PATH = (
    Path(__file__).resolve().parents[2] / "automation" / "config" / "strategy_defaults.json")


def test_sampled_value_wins_over_default():
    trial_attrs = [
        {"sampled_params": {"atr_trailing_multiplier": 2.0}},
        {"sampled_params": {"atr_trailing_multiplier": 3.0}},
    ]
    value, source = rpt._median_of_sampled_param(
        trial_attrs, "atr_trailing_multiplier", default_from={"atr_trailing_multiplier": 1.5})
    assert value == 2.5
    assert source == "sampled"


def test_falls_back_to_strategy_default_when_never_sampled():
    trial_attrs = [{"sampled_params": {"sma_period": 20}}, {"sampled_params": {"sma_period": 10}}]
    value, source = rpt._median_of_sampled_param(
        trial_attrs, "atr_trailing_multiplier", default_from={"atr_trailing_multiplier": 1.5})
    assert value == 1.5
    assert source == "strategy_default"


def test_no_silent_guess_when_absent_from_both_sources():
    value, source = rpt._median_of_sampled_param(
        [{"sampled_params": {}}], "atr_trailing_multiplier", default_from={})
    assert value is None
    assert source == "unavailable"


def test_no_silent_guess_when_default_from_is_none():
    value, source = rpt._median_of_sampled_param(
        [{"sampled_params": {}}], "atr_trailing_multiplier", default_from=None)
    assert value is None
    assert source == "unavailable"


def test_empty_trial_attrs_with_default_still_yields_strategy_default():
    value, source = rpt._median_of_sampled_param(
        [], "max_bars_in_trade", default_from={"max_bars_in_trade": 24})
    assert value == 24.0
    assert source == "strategy_default"


def test_strategy_defaults_json_declares_the_true_runtime_default_for_sma_crossover():
    """Regressionswaechter: SmaCrossoverStrategy fehlten diese Keys komplett (das eigentliche
    Symptom des Issues) — der Wert MUSS mit HourlyStrategyConfig's tatsaechlichem Klassendefault
    (1.5 / 6, seit Issue #1275 GH #1148 Katalog #1272-1297 P0 Fix Punkt 3 -- vormals 24)
    uebereinstimmen, sonst waere die Telemetrie eine erfundene Zahl, keine Dokumentation des
    realen Verhaltens."""
    defaults = json.loads(_STRATEGY_DEFAULTS_PATH.read_text("utf-8"))
    sma_defaults = defaults["SmaCrossoverStrategy"]
    assert sma_defaults["atr_trailing_multiplier"] == 1.5
    assert sma_defaults["max_bars_in_trade"] == 6


def test_study_record_stamps_source_field_for_a_strategy_that_never_samples_the_stop():
    """End-to-End durch _study_record: eine SmaCrossover-Study ohne atr_trailing_multiplier in
    sampled_params bekommt trotzdem einen nicht-None Median UND source='strategy_default'."""

    class _FakeTrial:
        def __init__(self, user_attrs):
            self.user_attrs = user_attrs
            self.number = 0
            self.state = None
            self.params = {}
            self.value = 0.0
            self.values = None

    class _FakeStudy:
        def __init__(self, trials):
            self.trials = trials
            self.user_attrs = {}
            self.best_trials = []

        @property
        def best_trial(self):
            raise ValueError("no best trial in this fixture")

    trial_attrs = {
        "sampled_params": {"sma_period": 20, "cooldown_bars": 12},
        "oos_evaluated": True, "oos_eligible": True,
    }
    study = _FakeStudy([_FakeTrial(trial_attrs)])
    proposal = {"strategy": "SmaCrossoverStrategy", "symbol": "X.ETORO", "status": "REJECTED_ON_HOLDOUT"}

    record, _invariant_results = rpt._study_record(proposal, study, tournament_cfg={})
    assert record["atr_trailing_multiplier_median"] == 1.5
    assert record["atr_trailing_multiplier_median_source"] == "strategy_default"
    # Issue #1275 (GH #1148, Katalog #1272-1297, P0) Fix Punkt 3 — 24.0 -> 6.0 (Faktor 0.24).
    assert record["max_bars_in_trade_median"] == 6.0
    assert record["max_bars_in_trade_median_source"] == "strategy_default"
