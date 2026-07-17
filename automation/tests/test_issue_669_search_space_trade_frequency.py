"""Issue #669 — 7 von 10 Strategien tragen nichts bei: 0 evaluable / 0 eligible auf TSLA-1h —
Suchraum-/Sizing-Bounds.

Fix: (1) ein Diagnose-Artefakt (``sweep_diagnostics.diagnose_trade_frequency``) trennt die
bindende Ursache eines 0-evaluable/0-eligible-Kollapses (Signal-Frequenz vs. Haltedauer vs.
Signal-Qualität); (2) symbol-spezifische Suchraum-Bounds-Überschreibungen
(``spaces._bounds_for``/``search_space_overrides.json``, opt-in, leer per Default) für die drei im
Issue explizit benannten trade-armen Strategien; (3) eine deklarative (Symbol, Strategie)-
Deaktivierungsliste (``symbol_strategy_denylist.json``), die ein bereits diagnostiziertes,
nicht-viables Paar VOR dem Sweep überspringt.
"""
import json

import pytest

from automation.optimizer.sweep_diagnostics import (
    diagnose_trade_frequency,
    load_symbol_strategy_denylist,
)
from automation.optimizer import spaces, sweep
from automation.optimizer.bounds import _RecordingTrial


_GATE_CFG = {
    "walk_forward": {"is_window_days": 120, "oos_window_days": 30, "splits": 4, "holdout_days": 45},
    "gate1_buffer_days": 30, "min_bars_per_param": 200, "min_oos_bars_per_fold": 500,
}


def test_enumerate_tunable_pairs_skips_denylisted_pair(monkeypatch):
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    monkeypatch.setattr(sweep, "load_symbol_strategy_denylist",
                        lambda: {("TrendPullbackStrategy", "A.ETORO"): "signal_frequency"})
    bars = {"A.ETORO": 10_000, "B.ETORO": 10_000}
    pairs = sweep.enumerate_tunable_pairs(
        ["TrendPullbackStrategy"], ["A.ETORO", "B.ETORO"],
        tier="all", available_bars=bars, config=_GATE_CFG)
    assert pairs == [("TrendPullbackStrategy", "B.ETORO", "OK")]


# ── Diagnose-Artefakt: bindende Ursache ──────────────────────────────────────────────────────────
def _trial(oos_evaluated, oos_eligible, oos_total_trades=0, is_total_trades=0, hit_trade_cap=False):
    return {
        "oos_evaluated": oos_evaluated, "oos_eligible": oos_eligible,
        "oos_total_trades": oos_total_trades, "is_total_trades": is_total_trades,
        "hit_trade_cap": hit_trade_cap,
    }


def test_signal_frequency_binding_cause_when_is_activity_too_low():
    """0 evaluable, IS-Aktivität selbst bereits < oos_min_trades ⇒ Signal-Frequenz-Problem
    (Bounds-Kalibrierung ist der richtige Hebel, TrendPullback/AdxAtr-Symptom)."""
    trials = [_trial(False, False, is_total_trades=3) for _ in range(16)]
    diag = diagnose_trade_frequency(trials, oos_min_trades=20)
    assert diag["binding_cause"] == "signal_frequency"
    assert diag["n_evaluable"] == 0


def test_hold_duration_binding_cause_when_trade_cap_dominates():
    """0 evaluable, AUSREICHENDE IS-Aktivität, aber Mehrheit trifft die Haltedauer-/Trade-Cap-
    Grenze ⇒ Haltedauer-Problem, nicht Signal-Frequenz."""
    trials = [_trial(False, False, is_total_trades=100, hit_trade_cap=(i < 12)) for i in range(16)]
    diag = diagnose_trade_frequency(trials, oos_min_trades=20)
    assert diag["binding_cause"] == "hold_duration"


def test_signal_quality_binding_cause_when_all_evaluated_but_none_eligible():
    """Alle Trials evaluiert (echte OOS-Backtests, genug Trades), aber KEINER eligible ⇒
    Signal-QUALITÄT — Bounds-Kalibrierung würde hier nichts beheben (HourlyMeanReversion-Symptom)."""
    trials = [_trial(True, False, oos_total_trades=214) for _ in range(16)]
    diag = diagnose_trade_frequency(trials, oos_min_trades=20)
    assert diag["binding_cause"] == "signal_quality"
    assert diag["n_evaluable"] == 16
    assert diag["n_eligible"] == 0
    assert diag["median_oos_trades"] == 214


def test_none_binding_cause_when_at_least_one_eligible():
    trials = [_trial(True, i == 0, oos_total_trades=30) for i in range(16)]
    diag = diagnose_trade_frequency(trials, oos_min_trades=20)
    assert diag["binding_cause"] == "none"


def test_no_data_binding_cause_for_empty_cohort():
    diag = diagnose_trade_frequency([], oos_min_trades=20)
    assert diag["binding_cause"] == "no_data"
    assert diag["n_trials"] == 0


# ── Deklarative Deaktivierungsliste ──────────────────────────────────────────────────────────────
def test_denylist_empty_by_default(tmp_path):
    assert load_symbol_strategy_denylist(tmp_path) == {}


def test_denylist_loads_configured_pairs(tmp_path):
    (tmp_path / "symbol_strategy_denylist.json").write_text(json.dumps({
        "pairs": [
            {"strategy": "TrendPullbackStrategy", "symbol": "TSLA.ETORO", "reason": "signal_frequency"},
        ]
    }), encoding="utf-8")
    denylist = load_symbol_strategy_denylist(tmp_path)
    assert denylist == {("TrendPullbackStrategy", "TSLA.ETORO"): "signal_frequency"}


def test_denylist_malformed_json_is_fail_open(tmp_path):
    (tmp_path / "symbol_strategy_denylist.json").write_text("{not valid json", encoding="utf-8")
    assert load_symbol_strategy_denylist(tmp_path) == {}


# ── Suchraum-Bounds-Überschreibung (spaces.py) ───────────────────────────────────────────────────
def test_sample_params_bit_identical_without_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", None)
    monkeypatch.setattr("automation.optimizer.trial_config.config_dir", lambda: tmp_path)
    t = _RecordingTrial()
    spaces.sample_params("TrendPullbackStrategy", t)
    assert t.numeric["ema_period"] == (50, 300)


def test_sample_params_bit_identical_with_symbol_but_no_override_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", None)
    monkeypatch.setattr("automation.optimizer.trial_config.config_dir", lambda: tmp_path)
    t = _RecordingTrial()
    spaces.sample_params("TrendPullbackStrategy", t, symbol="TSLA.ETORO")
    assert t.numeric["ema_period"] == (50, 300)


def test_sample_params_applies_configured_symbol_override(monkeypatch, tmp_path):
    (tmp_path / "search_space_overrides.json").write_text(json.dumps({
        "overrides": {
            "TrendPullbackStrategy": {
                "TSLA.ETORO": {"ema_period": [20, 120], "cooldown_bars": [1, 12]}
            }
        }
    }), encoding="utf-8")
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", None)
    monkeypatch.setattr("automation.optimizer.trial_config.config_dir", lambda: tmp_path)
    t = _RecordingTrial()
    spaces.sample_params("TrendPullbackStrategy", t, symbol="TSLA.ETORO")
    assert t.numeric["ema_period"] == (20, 120)
    assert t.numeric["cooldown_bars"] == (1, 12)
    # unveränderte Parameter bleiben bei den Default-Bounds.
    assert t.numeric["rsi_period"] == (5, 21)


def test_sample_params_override_is_symbol_scoped():
    """Ein Override für TSLA.ETORO wirkt NICHT auf ein anderes Symbol (kein globaler Bleed)."""
    import automation.optimizer.spaces as spaces_mod
    spaces_mod._search_space_overrides_cache = {
        "TrendPullbackStrategy": {"TSLA.ETORO": {"ema_period": [20, 120]}}
    }
    try:
        t_other = _RecordingTrial()
        spaces.sample_params("TrendPullbackStrategy", t_other, symbol="AAPL.ETORO")
        assert t_other.numeric["ema_period"] == (50, 300)
    finally:
        spaces_mod._search_space_overrides_cache = None


def test_unwired_strategy_ignores_override_fail_open(monkeypatch, tmp_path):
    """Ein Override für eine NICHT über _bounds_for verdrahtete Strategie/Parameter hat keine
    Wirkung (fail-open, kein Fehler)."""
    (tmp_path / "search_space_overrides.json").write_text(json.dumps({
        "overrides": {"SmaCrossoverStrategy": {"TSLA.ETORO": {"sma_period": [1, 5]}}}
    }), encoding="utf-8")
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", None)
    monkeypatch.setattr("automation.optimizer.trial_config.config_dir", lambda: tmp_path)
    t = _RecordingTrial()
    result = spaces.sample_params("SmaCrossoverStrategy", t, symbol="TSLA.ETORO")
    assert result["sma_period"] == 5  # Default-Range (5, 60) bleibt unverändert wirksam


# ── Akzeptanzkriterium: die Bounds-Kalibrierung erhöht die Trade-Frequenz messbar ────────────────
def _synthetic_trade_count(ema_period: int) -> int:
    """Deklaratives Kalibrier-Fixture (analog reward._CALIBRATION_FIXTURE_*, #631/#633): je LÄNGER
    das EMA-Trend-Fenster, desto SELTENER ein Pullback-Signal auf einem festen 180-Tage-1h-IS-Fenster
    — eine monoton fallende, deterministische Trade-Frequenz-Kurve als Ersatz für einen echten
    Backtest (kein Netzwerk-/Marktdatenzugriff in diesem Kontext möglich)."""
    return max(0, 260 - ema_period)


def test_bounds_calibration_increases_trade_frequency_share_above_min_trades(monkeypatch, tmp_path):
    oos_min_trades = 20
    default_lo, default_hi = 50, 300
    override_lo, override_hi = 20, 120

    default_samples = list(range(default_lo, default_hi + 1, 5))
    override_samples = list(range(override_lo, override_hi + 1, 5))

    default_share = sum(
        1 for v in default_samples if _synthetic_trade_count(v) >= oos_min_trades
    ) / len(default_samples)
    override_share = sum(
        1 for v in override_samples if _synthetic_trade_count(v) >= oos_min_trades
    ) / len(override_samples)

    assert override_share > default_share
    assert override_share >= 0.5  # die kalibrierten Bounds erreichen das Gate in >= 50% der Trials

    # Und: der Override-Mechanismus selbst liefert exakt diesen engeren Bereich an trial.suggest_int.
    (tmp_path / "search_space_overrides.json").write_text(json.dumps({
        "overrides": {"TrendPullbackStrategy": {"TSLA.ETORO": {"ema_period": [override_lo, override_hi]}}}
    }), encoding="utf-8")
    monkeypatch.setattr(spaces, "_search_space_overrides_cache", None)
    monkeypatch.setattr("automation.optimizer.trial_config.config_dir", lambda: tmp_path)
    t = _RecordingTrial()
    spaces.sample_params("TrendPullbackStrategy", t, symbol="TSLA.ETORO")
    assert t.numeric["ema_period"] == (override_lo, override_hi)
