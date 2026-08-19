"""Issue #762 (P1) — Suchraum-Bounds für die Null-Ertrag-Strategien kalibrieren.

Ist: SqueezeBreakoutStrategy (dim=9), AdxAtrMomentumStrategy (dim=6) und TrendPullbackStrategy
(dim=7) liefern über 124 Symbole keinen einzigen eligiblen Trial (121-124× 'signal_quality'/
'signal_frequency'). Squeeze ist der Sonderfall: dim=9 >= 8, k=2 (18 Zufalls-Startpunkte) ist knapp
für die multivariate=True,group=True-Kovarianzschätzung.

Fix: (1) `bounds.theoretical_max_oos_trades` rechnet je Strategie die an der SCHNELLSTEN im
Suchraum zulässigen Zyklus-Konfiguration (cooldown_bars/max_bars_in_trade-Untergrenze) erreichbare
obere Schranke der gepoolten OOS-Trade-Zahl aus — ein Ergebnis darunter `oos_min_trades` wäre
mechanisch ein Bounds-Bug, kein Signalqualitäts-Befund (Akzeptanzkriterium #1). (2) für dim >=
`n_startup_trials_high_dim_threshold` (Default 8) hebt `derive_n_startup_trials`
`n_startup_trials_per_dim` von 2 auf 3 (Squeeze: 18 → 27, ComboTrendVwap: 28 → 42).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.bounds import theoretical_max_oos_trades
from automation.optimizer.run_optimization import derive_n_startup_trials

OPT = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
BT = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))

_ZERO_YIELD_STRATEGIES = ("SqueezeBreakoutStrategy", "AdxAtrMomentumStrategy", "TrendPullbackStrategy")


# ── Akzeptanzkriterium #1: theoretische Trade-Obergrenze >= oos_min_trades ─────────────────────────
@pytest.mark.parametrize("strategy", _ZERO_YIELD_STRATEGIES)
def test_theoretical_max_oos_trades_reaches_oos_min_trades_at_default_bounds(strategy):
    """Regression-Guard: an der schnellstmoeglichen Zyklus-Konfiguration MUSS die gepoolte
    OOS-Trade-Obergrenze oos_min_trades erreichen — sonst ist es ein Bounds-, kein Strategie-Bug."""
    max_trades = theoretical_max_oos_trades(strategy, walk_forward=BT["walk_forward"])
    assert max_trades is not None
    assert max_trades >= TCFG["oos_min_trades"]


def test_theoretical_max_oos_trades_is_none_for_strategy_without_cycle_model():
    """GapContinuationStrategy hat kein cooldown_bars/max_bars_in_trade-Zyklusmodell im Suchraum
    (nur atr_trailing_multiplier/max_bars_in_trade, kein cooldown_bars) — die Schranke ist dafuer
    nicht definierbar (kein falsch-positiver Bounds-Alarm).

    Issue #1043/#1192 — vormals war SmaCrossoverStrategy das Beispiel hier: sie sampelte KEINEN
    Risikoparameter (weder cooldown_bars noch max_bars_in_trade waren ihr Fehlen relevant, siehe
    dortiger Root-Cause), seither sampelt sie beide (spaces._sample_risk_layer) UND besass
    cooldown_bars bereits vorher — sie hat jetzt sehr wohl ein Zyklusmodell und ist daher kein
    gueltiges Beispiel fuer diesen Test mehr."""
    assert theoretical_max_oos_trades("GapContinuationStrategy", walk_forward=BT["walk_forward"]) is None


def test_theoretical_max_oos_trades_uses_the_fastest_cycle_bound():
    """cooldown_bars=[2,36], max_bars_in_trade=[12,24] fuer TrendPullback ⇒ schnellster Zyklus
    2+12=14 Bars; ueber splits*oos_window_days*bars_per_day gepoolte Bars // 14."""
    wf = {"splits": 4, "oos_window_days": 45}
    result = theoretical_max_oos_trades("TrendPullbackStrategy", walk_forward=wf, bars_per_day=24)
    total_bars = 4 * 45 * 24
    assert result == total_bars // 14


def test_theoretical_max_oos_trades_shrinks_with_narrower_oos_window():
    wf_wide = {"splits": 4, "oos_window_days": 45}
    wf_narrow = {"splits": 4, "oos_window_days": 10}
    wide = theoretical_max_oos_trades("AdxAtrMomentumStrategy", walk_forward=wf_wide)
    narrow = theoretical_max_oos_trades("AdxAtrMomentumStrategy", walk_forward=wf_narrow)
    assert narrow < wide


# ── Akzeptanzkriterium #3: n_startup_trials_per_dim ist dimensionsabhaengig belegt ──────────────────
def test_squeeze_and_combo_get_the_raised_per_dim_rate_above_the_threshold():
    """dim >= n_startup_trials_high_dim_threshold (Default 8, Config) ⇒ k=3 statt k=2."""
    squeeze = derive_n_startup_trials("SqueezeBreakoutStrategy", 16, OPT)
    combo = derive_n_startup_trials("ComboTrendVwapStrategy", 16, OPT)
    assert squeeze == 27  # 9 dim * 3
    assert combo == 42    # 14 dim * 3


def test_sub_threshold_strategies_keep_the_base_per_dim_rate():
    """dim < threshold (AdxAtr=6, TrendPullback=7) ⇒ unveraendert k=2."""
    assert derive_n_startup_trials("AdxAtrMomentumStrategy", 16, OPT) == 16   # max(16, ceil(2*6)=12)
    assert derive_n_startup_trials("TrendPullbackStrategy", 16, OPT) == 16    # max(16, ceil(2*7)=14)


def test_missing_high_dim_keys_falls_back_to_flat_rate_legacy():
    """Fehlt n_startup_trials_high_dim_threshold/n_startup_trials_per_dim_high_dim ⇒ bit-identisch
    zum Vor-#762-Verhalten (flaches k fuer alle dim)."""
    opt_data = {"n_startup_trials_per_dim": 2}
    assert derive_n_startup_trials("SqueezeBreakoutStrategy", 16, opt_data) == 18  # 9 * 2, Legacy


def test_invalid_high_dim_keys_fall_back_to_flat_rate():
    opt_data = {"n_startup_trials_per_dim": 2, "n_startup_trials_high_dim_threshold": 0,
               "n_startup_trials_per_dim_high_dim": 3}
    assert derive_n_startup_trials("SqueezeBreakoutStrategy", 16, opt_data) == 18  # threshold<=0 ⇒ Legacy
