"""Issue #1100 (Katalog #933) — ``holdout_buyhold_return`` kollabiert bei ``holdout_total_trades
== 0`` auf den Sentinel ``0.0`` statt ``None`` durchzureichen (siebte Instanz der #759/#788/#966-
Fehlerklasse). Symptom: ``SqueezeBreakoutStrategy`` meldet in ASML/PLTR/NVDA
``holdout_buyhold_return = 0.00 %``, während Schwester-Studies DESSELBEN Symbols (u. a.
``TrendPullbackStrategy`` — ebenfalls 0 Holdout-Trades) den korrekten, von 0 verschiedenen Wert
tragen.

Fix: ``oos_buyhold_return`` ist jetzt Teil von ``invariants._SENTINEL_GUARDED_METRIC_KEYS``
(``run_optimization.py`` stempelt den Trial-User-Attr nur bei ``oos_evaluated=True``, derselbe
Torwaechter wie die fünf Geschwister-Metriken). Neue, symbolweite Invariante
``invariants.check_holdout_buyhold_return_coherence`` bewacht zusätzlich genau das im Katalog
beschriebene Kohärenz-Muster: ein Nullwert bei 0 Holdout-Trades ist nur zulässig, wenn KEINE
Schwester-Study desselben Symbols einen echten Marktwert trägt (der Buy&Hold-Benchmark ist eine
reine Preisserie DES SYMBOLS, unabhängig von der Strategie).
"""
from automation.optimizer.invariants import (
    _SENTINEL_GUARDED_METRIC_KEYS,
    check_holdout_buyhold_return_coherence,
    check_metric_sentinel_absence,
)


# ── check_metric_sentinel_absence — oos_buyhold_return ist jetzt eine geschützte Metrik ─────────
def test_oos_buyhold_return_is_a_sentinel_guarded_key():
    assert "oos_buyhold_return" in _SENTINEL_GUARDED_METRIC_KEYS


def test_sentinel_absence_fails_when_unevaluated_trial_carries_a_buyhold_observation():
    trials = [{"oos_evaluated": False, "oos_buyhold_return": 0.0}]
    result = check_metric_sentinel_absence(trials)
    assert result.passed is False
    assert result.actual == 1


def test_sentinel_absence_passes_when_evaluated_trial_carries_a_real_buyhold_value():
    trials = [{"oos_evaluated": True, "oos_buyhold_return": 0.0093}]
    result = check_metric_sentinel_absence(trials)
    assert result.passed is True


# ── invariants.check_holdout_buyhold_return_coherence ────────────────────────────────────────────
def _rec(strategy, symbol, *, total_trades, buyhold):
    return {"strategy": strategy, "symbol": symbol,
            "holdout_total_trades": total_trades, "holdout_buyhold_return": buyhold}


def test_fails_matching_933_reference_symptom():
    """SqueezeBreakout (0 Trades, buyhold=0.0) neben TrendPullback (ebenfalls 0 Trades, aber
    echtem Marktwert) auf demselben Symbol -- exakt das #933-Referenzmuster."""
    records = [
        _rec("SqueezeBreakoutStrategy", "ASML.ETORO", total_trades=0, buyhold=0.0),
        _rec("TrendPullbackStrategy", "ASML.ETORO", total_trades=0, buyhold=0.0093),
        _rec("MeanReversionStrategy", "ASML.ETORO", total_trades=140, buyhold=0.0093),
    ]
    result = check_holdout_buyhold_return_coherence(records)
    assert result.passed is False
    assert result.severity == "high"
    assert "SqueezeBreakoutStrategy/ASML.ETORO" in result.actual
    assert "TrendPullbackStrategy/ASML.ETORO" not in result.actual
    assert result.actual["SqueezeBreakoutStrategy/ASML.ETORO"]["sibling_holdout_buyhold_returns"] == [0.0093]


def test_passes_when_all_sibling_studies_agree_on_zero():
    """Ein Symbol, dessen Markt ueber das gesamte Holdout-Fenster tatsaechlich exakt seitwaerts
    lief -- alle Studies tragen konsistent 0.0, kein Abweichler, kein Fehler."""
    records = [
        _rec("SqueezeBreakoutStrategy", "FLAT.ETORO", total_trades=0, buyhold=0.0),
        _rec("TrendPullbackStrategy", "FLAT.ETORO", total_trades=12, buyhold=0.0),
    ]
    result = check_holdout_buyhold_return_coherence(records)
    assert result.passed is True


def test_passes_when_no_sibling_has_any_measured_value():
    """Kein Schwester-Record mit von 0 verschiedenem Wert -- keine Vergleichsgrundlage, kein
    Urteil (z. B. eine Legacy-Study ohne #552-Benchmark-Telemetrie ueberhaupt)."""
    records = [
        _rec("SqueezeBreakoutStrategy", "X.ETORO", total_trades=0, buyhold=None),
        _rec("TrendPullbackStrategy", "X.ETORO", total_trades=0, buyhold=None),
    ]
    result = check_holdout_buyhold_return_coherence(records)
    assert result.passed is True
    assert result.actual is None


def test_a_zero_trade_study_with_nonzero_buyhold_is_not_an_offender():
    """0 Trades UND ein tatsaechlich gemessener, von 0 verschiedener Wert -- das ist der KORREKTE
    Zustand (siehe TrendPullback im Referenzsymptom), nicht der Fehler."""
    records = [
        _rec("SqueezeBreakoutStrategy", "Y.ETORO", total_trades=0, buyhold=0.4974),
        _rec("TrendPullbackStrategy", "Y.ETORO", total_trades=0, buyhold=0.4974),
    ]
    result = check_holdout_buyhold_return_coherence(records)
    assert result.passed is True


def test_multiple_symbols_are_evaluated_independently():
    records = [
        _rec("SqueezeBreakoutStrategy", "ASML.ETORO", total_trades=0, buyhold=0.0),
        _rec("TrendPullbackStrategy", "ASML.ETORO", total_trades=0, buyhold=0.0093),
        _rec("SqueezeBreakoutStrategy", "PLTR.ETORO", total_trades=0, buyhold=0.0),
        _rec("TrendPullbackStrategy", "PLTR.ETORO", total_trades=0, buyhold=0.4974),
    ]
    result = check_holdout_buyhold_return_coherence(records)
    assert result.passed is False
    assert len(result.actual) == 2
    assert "SqueezeBreakoutStrategy/ASML.ETORO" in result.actual
    assert "SqueezeBreakoutStrategy/PLTR.ETORO" in result.actual


def test_skips_records_without_a_symbol():
    result = check_holdout_buyhold_return_coherence([{"strategy": "S", "holdout_total_trades": 0}])
    assert result.passed is True
