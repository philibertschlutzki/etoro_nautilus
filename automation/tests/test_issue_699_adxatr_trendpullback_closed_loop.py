"""Issue #699 (P2) — `AdxAtrMomentum` & `TrendPullback`: `STRUCTURAL_ALL_UNEVALUABLE` persistiert;
Bounds/Denylist + Closed-Loop-Writeback.

Zwei unabhängige Root-Causes behoben:

1. `AdxAtrMomentumStrategy` — `DirectionalMovement.value` bleibt in NautilusTrader 1.230.0
   konstant 0.0 (dasselbe #691-Trockenlauf-Ergebnis, das DonchianRegimeBreakout bereits umging).
   Das Entry-Gate `adx_value > 25` war daher IMMER False (0 Trades, `strategies.json`s vormalige
   "ADX-Initialisierungsproblem"-Note). Fix: derselbe Weg wie Donchian — EMA-Steigungs-Bestätigung
   (Option B) statt des toten ADX-Gates; `adx_period` wird nicht mehr gesampelt (spaces.py).

2. `TrendPullbackStrategy` — `on_bar()` rief nie `_check_exits_and_update()` auf (Verstoss gegen
   den `HourlyStrategyBase`-Vertrag) ⇒ kein ATR-Trailing-Stop, kein Time-Exit ⇒ 0 realisierte
   Round-Trips (`strategies.json`s vormalige "0 FIFO-Schliessungen"-Note). Fix: der fehlende Aufruf
   nachgerüstet, inkl. `on_position_closed`-Reset von `current_signal` (verhindert Flat-Lock).

3. Closed-Loop-Lücke (`recommend_diagnosis_action`): eine `'search_space_override'`-Empfehlung
   ohne Eskalationspfad wiederholte sich identisch bei jedem Lauf, solange niemand tatsächlich
   einen Override einträgt — genau das #699-Symptom ("jeder Lauf verbrennt 3×16 Trials für
   dieselben nicht-viablen Paare"). Fix: `previously_recommended_override` eskaliert auf
   `'denylist'`, sobald die Override-Empfehlung EINMAL ignoriert wurde und das Paar weiterhin
   scheitert.
"""
import json
import logging

import optuna
import pytest

from automation.optimizer import run_optimization as ro
from automation.optimizer.sweep_diagnostics import (
    recommend_diagnosis_action, record_diagnosed_pair, load_diagnosed_pairs_cache,
    WIRED_OVERRIDE_STRATEGIES,
)
from automation.optimizer.bounds import extract_numeric_bounds


# ── recommend_diagnosis_action: Eskalation nach einem ignorierten Override-Vorschlag ─────────────
def test_first_occurrence_recommends_override_not_denylist():
    assert "AdxAtrMomentumStrategy" in WIRED_OVERRIDE_STRATEGIES
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_frequency", "median_oos_trades": 0, "median_is_trades": 5},
        has_existing_override=False, previously_recommended_override=False,
    )
    assert rec["action"] == "search_space_override"


def test_repeated_occurrence_without_override_escalates_to_denylist():
    """Kernkriterium #699: dieselbe Ursache, KEIN Override wurde inzwischen eingetragen, UND die
    Empfehlung wurde bereits einmal ausgesprochen ⇒ Eskalation auf 'denylist' (kein endloses
    Wiederholen derselben folgenlosen Empfehlung)."""
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_frequency", "median_oos_trades": 0, "median_is_trades": 5},
        has_existing_override=False, previously_recommended_override=True,
    )
    assert rec["action"] == "denylist"


def test_escalation_does_not_apply_once_override_actually_exists():
    """Existiert inzwischen ein ECHTER Override (Bounds-Kalibrierung wurde umgesetzt), greift die
    reguläre #681-Eskalation (has_existing_override), nicht die #699-previously_recommended-
    Eskalation — beide Wege landen bei 'denylist', aber aus unterschiedlichen, unabhängigen
    Gründen (keine Doppel-Zählung/Verwechslung)."""
    rec = recommend_diagnosis_action(
        "AdxAtrMomentumStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_frequency", "median_oos_trades": 0, "median_is_trades": 5},
        has_existing_override=True, previously_recommended_override=False,
    )
    assert rec["action"] == "denylist"


def test_escalation_only_applies_to_wired_strategies():
    """Eine NICHT verdrahtete Strategie empfiehlt ohnehin immer 'denylist' — previously_recommended_
    override ist dort ein No-Op (kein Zustand, der jemals 'search_space_override' war)."""
    rec_first = recommend_diagnosis_action(
        "SmaCrossoverStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_frequency", "median_oos_trades": 0, "median_is_trades": 5},
        previously_recommended_override=False,
    )
    rec_second = recommend_diagnosis_action(
        "SmaCrossoverStrategy", "TSLA.ETORO",
        {"binding_cause": "signal_frequency", "median_oos_trades": 0, "median_is_trades": 5},
        previously_recommended_override=True,
    )
    assert rec_first["action"] == rec_second["action"] == "denylist"


def test_default_previously_recommended_override_is_bit_identical():
    """Fehlt der neue Kwarg (Legacy-Aufrufer) ⇒ bit-identisch zum Pre-#699-Verhalten (erste
    Empfehlung bleibt 'search_space_override', keine ungewollte sofortige Eskalation)."""
    rec = recommend_diagnosis_action(
        "TrendPullbackStrategy", "TSLA.ETORO",
        {"binding_cause": "hold_duration", "median_oos_trades": 3, "median_is_trades": 40},
        has_existing_override=False,
    )
    assert rec["action"] == "search_space_override"


# ── floor_plateau_callback: End-to-End-Wiring der Eskalation über zwei simulierte Läufe ──────────
class _FakeTrial:
    def __init__(self, value, *, oos_evaluated=None, oos_eligible=None,
                 oos_total_trades=None, is_total_trades=None, hit_trade_cap=None,
                 state=optuna.trial.TrialState.COMPLETE):
        self.value = value
        self.state = state
        self.user_attrs = {}
        if oos_evaluated is not None:
            self.user_attrs["oos_evaluated"] = oos_evaluated
        if oos_eligible is not None:
            self.user_attrs["oos_eligible"] = oos_eligible
        if oos_total_trades is not None:
            self.user_attrs["oos_total_trades"] = oos_total_trades
        if is_total_trades is not None:
            self.user_attrs["is_total_trades"] = is_total_trades
        if hit_trade_cap is not None:
            self.user_attrs["hit_trade_cap"] = hit_trade_cap


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials
        self._attrs = {}
        self.stopped = False

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v

    def stop(self):
        self.stopped = True


W = {"penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25, "n_startup_trials": 16}


def _quiet_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    return lg


def test_repeated_structural_collapse_escalates_to_denylist_across_two_runs(tmp_path, monkeypatch):
    """Simuliert #699 wörtlich: Lauf 1 (STRUCTURAL_ALL_UNEVALUABLE, 0 evaluable, IS-Aktivität
    unter oos_min_trades) schreibt 'search_space_override' in den Auto-Cache. Lauf 2 (derselbe
    Kollaps, kein Override wurde eingetragen) MUSS jetzt 'denylist' schreiben — der frühere Bug
    hätte hier erneut 'search_space_override' geschrieben (Endlosschleife)."""
    monkeypatch.setattr("automation.optimizer.manifest.WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path)

    trials = [_FakeTrial(-9.8, oos_evaluated=False, is_total_trades=3) for _ in range(20)]
    study1 = _FakeStudy(trials)
    lg = _quiet_logger("test699_run1")
    ro.floor_plateau_callback(study1, trials[-1], weights=W, n_startup_trials=16, logger=lg,
                              strategy="AdxAtrMomentumStrategy", symbol="TSLA.ETORO")

    cache_after_run1 = load_diagnosed_pairs_cache(tmp_path)
    rec1 = cache_after_run1.get(("AdxAtrMomentumStrategy", "TSLA.ETORO"))
    assert rec1 is not None
    assert rec1["action"] == "search_space_override"

    # Lauf 2 — fabrikneue Study (frischer Sweep), IDENTISCHER Kollaps, KEIN Override eingetragen.
    study2 = _FakeStudy([_FakeTrial(-9.8, oos_evaluated=False, is_total_trades=3) for _ in range(20)])
    lg2 = _quiet_logger("test699_run2")
    ro.floor_plateau_callback(study2, study2.trials[-1], weights=W, n_startup_trials=16, logger=lg2,
                              strategy="AdxAtrMomentumStrategy", symbol="TSLA.ETORO")

    cache_after_run2 = load_diagnosed_pairs_cache(tmp_path)
    rec2 = cache_after_run2[("AdxAtrMomentumStrategy", "TSLA.ETORO")]
    assert rec2["action"] == "denylist"


def test_adding_a_real_override_prevents_escalation(tmp_path, monkeypatch):
    """Wird zwischen Lauf 1 und Lauf 2 tatsächlich ein Override in search_space_overrides.json
    eingetragen, greift NICHT die #699-Eskalation (die Bounds-Kalibrierung wurde ja umgesetzt) —
    die reguläre #681-has_existing_override-Logik übernimmt (weiterhin 'denylist', aber aus dem
    richtigen, unterscheidbaren Grund: der Override existiert UND wirkte nicht)."""
    monkeypatch.setattr("automation.optimizer.manifest.WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: tmp_path)

    trials = [_FakeTrial(-9.8, oos_evaluated=False, is_total_trades=3) for _ in range(20)]
    study1 = _FakeStudy(trials)
    ro.floor_plateau_callback(study1, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("test699b_run1"),
                              strategy="TrendPullbackStrategy", symbol="TSLA.ETORO")
    assert load_diagnosed_pairs_cache(tmp_path)[("TrendPullbackStrategy", "TSLA.ETORO")]["action"] == "search_space_override"

    (tmp_path / "search_space_overrides.json").write_text(json.dumps({
        "overrides": {"TrendPullbackStrategy": {"TSLA.ETORO": {"ema_period": [50, 150]}}}
    }), "utf-8")

    study2 = _FakeStudy([_FakeTrial(-9.8, oos_evaluated=False, is_total_trades=3) for _ in range(20)])
    ro.floor_plateau_callback(study2, study2.trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("test699b_run2"),
                              strategy="TrendPullbackStrategy", symbol="TSLA.ETORO")
    assert load_diagnosed_pairs_cache(tmp_path)[("TrendPullbackStrategy", "TSLA.ETORO")]["action"] == "denylist"


# ── spaces.py: adx_period nicht mehr gesampelt (tote Konfiguration, Phantom-Tuning-Vermeidung) ───
def test_adx_period_no_longer_sampled_for_adx_atr_momentum():
    bounds = extract_numeric_bounds("AdxAtrMomentumStrategy")
    assert "adx_period" not in bounds
    # ema_period/atr_multiplier/atr_period/cooldown_bars/atr_trailing_multiplier/max_bars_in_trade
    # bleiben unveraendert gesampelt.
    for expected in ("ema_period", "atr_multiplier", "atr_period", "cooldown_bars",
                     "atr_trailing_multiplier", "max_bars_in_trade"):
        assert expected in bounds


# ── strategies.json: Notes reflektieren den tatsaechlichen Fix, nicht mehr "Deaktiviert" ─────────
def test_strategies_json_notes_no_longer_claim_deactivated():
    data = json.loads(open("automation/config/strategies.json", encoding="utf-8").read())
    by_class = {s["strategy_class"]: s for s in data["strategies"]}
    for cls in ("TrendPullbackStrategy", "AdxAtrMomentumStrategy"):
        entry = by_class[cls]
        assert entry["active"] is True
        assert "#699" in entry.get("_note", "")
        assert "Deaktiviert" not in entry.get("_note", "")
