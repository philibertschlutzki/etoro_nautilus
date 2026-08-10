"""Issue #921 — SqueezeBreakoutStrategy: 178 Trials, nur 19 auswertbar (Median 1 OOS-Trade).

squeeze_on (squeeze_breakout.py:70) verlangt, dass die Bollinger-Bänder VOLLSTÄNDIG innerhalb
des Keltner-Kanals liegen -- strukturell nur erreichbar, wenn bb_std_dev/keltner_multiplier
unterhalb eines datengetriebenen Schwellwerts nahe 1 bleibt. Vorher wurden beide unabhängig
gesampelt (Band bb_std_dev [1.5;2.5], keltner_multiplier [1.0;2.5] -> Verhältnis [0.6;2.5],
trifft die enge Zone selten). Fix: das Verhältnis selbst wird gesampelt (squeeze_ratio, Band
[0.70;1.05], fast+gap-Muster wie ComboTrendVwaps macd_slow), bb_std_dev wird daraus abgeleitet.

Die andere Hälfte des Issues (binding_cause muss bei median_oos_total_trades<=2 IMMER
signal_sparse statt signal_quality sein) ist bereits durch
sweep_diagnostics.resolve_ineligible_binding_cause (#926) abgedeckt und in
test_issue_769_signal_absent_vs_sparse.py getestet -- hier nicht dupliziert.
"""
import optuna

from automation.optimizer.spaces import sample_params

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _sample_many(n: int, symbol: str | None = None) -> list[dict]:
    study = optuna.create_study()
    out = []
    for _ in range(n):
        trial = study.ask()
        params = sample_params("SqueezeBreakoutStrategy", trial, symbol=symbol)
        out.append(params)
        study.tell(trial, 0.0)
    return out


def test_squeeze_ratio_never_leaks_into_the_strategy_params():
    """squeeze_ratio ist ausschliesslich eine Optuna-Suchraum-Achse -- kein Feld von
    SqueezeBreakoutConfig. Eine Leckage würde beim Config-Aufbau in backtest_runner.py als
    'unbekannter Parameter' verworfen und stillschweigend ignoriert (kein Fehler, aber toter
    Suchraum-Freiheitsgrad)."""
    for params in _sample_many(30):
        assert "squeeze_ratio" not in params


def test_bb_std_dev_to_keltner_multiplier_ratio_stays_in_the_squeeze_feasible_band():
    for params in _sample_many(300):
        ratio = params["bb_std_dev"] / params["keltner_multiplier"]
        assert 0.70 - 1e-9 <= ratio <= 1.05 + 1e-9


def test_all_expected_config_keys_still_present():
    params = _sample_many(1)[0]
    for key in ("bb_period", "bb_std_dev", "keltner_period", "keltner_multiplier",
               "min_squeeze_bars", "cooldown_bars", "atr_period",
               "atr_trailing_multiplier", "max_bars_in_trade"):
        assert key in params


def test_search_space_overrides_are_wired_for_squeeze_breakout(monkeypatch):
    """Issue #921 Fix 3 — search_space_overrides.json muss jetzt für SqueezeBreakoutStrategy
    wirksam sein (vorher nur TrendPullback/AdxAtr/HourlyMeanReversion)."""
    import automation.optimizer.spaces as sp

    monkeypatch.setattr(sp, "_search_space_overrides_cache", {
        "SqueezeBreakoutStrategy": {"XOM.ETORO": {"squeeze_ratio": [0.90, 0.95]}}
    })
    for params in _sample_many(30, symbol="XOM.ETORO"):
        ratio = params["bb_std_dev"] / params["keltner_multiplier"]
        assert 0.90 - 1e-9 <= ratio <= 0.95 + 1e-9


def test_search_space_overrides_do_not_affect_other_symbols(monkeypatch):
    import automation.optimizer.spaces as sp

    monkeypatch.setattr(sp, "_search_space_overrides_cache", {
        "SqueezeBreakoutStrategy": {"XOM.ETORO": {"squeeze_ratio": [0.90, 0.95]}}
    })
    for params in _sample_many(30, symbol="NKE.ETORO"):
        ratio = params["bb_std_dev"] / params["keltner_multiplier"]
        assert 0.70 - 1e-9 <= ratio <= 1.05 + 1e-9
