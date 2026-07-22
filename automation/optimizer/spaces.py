import json as _json


_search_space_overrides_cache: dict | None = None
_auto_proposed_bounds_cache: dict | None = None


def _load_search_space_overrides() -> dict:
    """Issue #669 — symbol-spezifische Suchraum-Bounds-Überschreibungen aus
    ``search_space_overrides.json`` (Zero-Hardcoding, gecached). Fehlt die Datei/der Eintrag
    ⇒ ``{}`` (bit-identisch zum Pre-#669-Verhalten)."""
    global _search_space_overrides_cache
    if _search_space_overrides_cache is not None:
        return _search_space_overrides_cache
    try:
        from automation.optimizer.trial_config import config_dir
        path = config_dir() / "search_space_overrides.json"
        if path.exists():
            data = _json.loads(path.read_text("utf-8")) or {}
            _search_space_overrides_cache = data.get("overrides", {}) or {}
        else:
            _search_space_overrides_cache = {}
    except Exception:
        _search_space_overrides_cache = {}
    return _search_space_overrides_cache


def _load_auto_proposed_bounds() -> dict:
    """Issue #761 — AUTOMATISCH vorgeschlagene (NICHT kuratierte) Bounds aus dem #681-Diagnose-
    Cache (``sweep_diagnostics.propose_bounds_widening``, geschrieben von ``run_optimization.
    floor_plateau_callback`` bei einem ``'search_space_override'``-Befund). Gecached wie
    ``_load_search_space_overrides``. Fehlt der Cache/ein Eintrag ⇒ ``{}``."""
    global _auto_proposed_bounds_cache
    if _auto_proposed_bounds_cache is not None:
        return _auto_proposed_bounds_cache
    try:
        from automation.optimizer.sweep_diagnostics import load_diagnosed_pairs_cache
        out: dict = {}
        for (strategy, symbol), entry in load_diagnosed_pairs_cache().items():
            proposed = entry.get("proposed_bounds")
            if proposed:
                out.setdefault(strategy, {})[symbol] = proposed
        _auto_proposed_bounds_cache = out
    except Exception:
        _auto_proposed_bounds_cache = {}
    return _auto_proposed_bounds_cache


def _bounds_for(strategy: str, symbol: str | None, param: str, low, high):
    """Issue #669/#761 — löst die effektiven ``(low, high)``-Suchraumgrenzen für ``param`` auf, in
    Prioritätsreihenfolge: (1) eine kuratierte symbol-spezifische Überschreibung
    (``search_space_overrides.json``, menschliche PR-Entscheidung), (2) ein AUTOMATISCH
    vorgeschlagener Bounds-Wert aus dem #681-Diagnose-Cache (``SEARCH_SPACE_AUTO_OVERRIDE`` —
    die Brücke, damit der NÄCHSTE Lauf nicht identisch an denselben zu engen Bounds scheitert,
    bis ein Operator die permanente Kalibrierung per PR einträgt), (3) die universellen Default-
    Bounds. Fehlt ``symbol`` ODER ist weder ein kuratierter noch ein automatischer Override für
    (``strategy``, ``symbol``, ``param``) vorhanden ⇒ ``(low, high)`` UNVERÄNDERT (bit-identisch,
    Zero-Hardcoding)."""
    if not symbol:
        return low, high
    entry = (_load_search_space_overrides().get(strategy) or {}).get(symbol) or {}
    bound = entry.get(param)
    if bound and len(bound) == 2:
        return bound[0], bound[1]
    auto_entry = (_load_auto_proposed_bounds().get(strategy) or {}).get(symbol) or {}
    auto_bound = auto_entry.get(param)
    if auto_bound and len(auto_bound) == 2:
        import logging
        logging.getLogger("optimizer").info(
            "[JSON_EVENT] " + _json.dumps({
                "event_type": "SEARCH_SPACE_AUTO_OVERRIDE",
                "strategy": strategy, "symbol": symbol, "param": param,
                "bounds": [auto_bound[0], auto_bound[1]], "default_bounds": [low, high],
            }))
        return auto_bound[0], auto_bound[1]
    return low, high


# Issue #714 (GR-01) — 24-Bar-Zeitbox (1h-Bars). Harte Obergrenze für JEDE ``max_bars_in_trade``-
# Suchraum-Bound über alle 15 Strategien (Untergrenzen bleiben unverändert). Single Source of Truth,
# konsistent mit ``hourly_strategy_base.MAX_BARS_IN_TRADE_HARD_CAP``.
_MAX_BARS_IN_TRADE_CAP = 24


def _dyn_tp_params(trial) -> dict:
    """Issue #713 (Req-01-Realgehalt, gated) — dyn_tp_enabled als (searchbare) TPE-Dimension,
    einheitlich für ALLE 15 Strategien (der dyn-TP-Pfad ist via #712 in der Basisklasse verfügbar).
    λ/γ werden KONDITIONAL nur im ``True``-Ast gesampelt (TPE-nativ; Optuna behandelt die inaktiven
    Trials korrekt als fehlende Dimensionen). Alle drei Keys sind echte ``HourlyStrategyConfig``-
    Felder (#712) — kein stilles Verwerfen (Pitfall #446)."""
    dyn_tp_on = trial.suggest_categorical("dyn_tp_enabled", [False, True])
    params: dict = {"dyn_tp_enabled": dyn_tp_on}
    if dyn_tp_on:
        # λ∈[0.1, 3.0] (Halbwertszeit der Target-Kontraktion), log=True (multiplikative Wirkung);
        # γ∈[0.5, 4.0] (ATR-Vielfaches, konsistent mit den bestehenden atr_trailing_multiplier-Bounds).
        params["dyn_tp_lambda"] = trial.suggest_float("dyn_tp_lambda", 0.1, 3.0, log=True)
        params["dyn_tp_gamma"] = trial.suggest_float("dyn_tp_gamma", 0.5, 4.0)
    return params


def sample_params(strategy: str, trial, *, symbol: str | None = None) -> dict:
    """Issue #669 — ``symbol`` (optional, Default ``None``) aktiviert symbol-spezifische
    Suchraum-Bounds-Überschreibungen für die trade-armen Strategien (siehe ``_bounds_for``). Fehlt
    ``symbol`` (z. B. der globale Multi-Symbol-Pfad, ``bounds.extract_numeric_bounds``) ⇒
    bit-identisch zu den universellen Default-Bounds.

    Issue #713 — jede Strategie erhält zusätzlich die konditionalen ``dyn_tp_*``-Suchdimensionen
    (siehe ``_dyn_tp_params``), einheitlich am Ende dieser Funktion angehängt statt pro Strategie-
    Zweig dupliziert."""
    if strategy == "HourlyMeanReversionStrategy":
        kp_lo, kp_hi = _bounds_for(strategy, symbol, "keltner_period", 6, 40)
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "keltner_period": trial.suggest_int("keltner_period", kp_lo, kp_hi),
            "keltner_atr_period": trial.suggest_int("keltner_atr_period", 6, 40),
            "keltner_multiplier": trial.suggest_float("keltner_multiplier", 1.0, 3.5),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.3, 2.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "SmaCrossoverStrategy":
        params = {
            "sma_period": trial.suggest_int("sma_period", 5, 60),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
        }
    elif strategy == "ComboTrendVwapStrategy":
        # Konzept §4 alignment (ISSUE-OPT-377): macd_fast 3–14, macd_gap 4–26
        # ⇒ macd_slow = fast + gap (Gap garantiert fast < slow für den MACD-Indikator).
        fast = trial.suggest_int("macd_fast", 3, 14)
        gap = trial.suggest_int("macd_gap", 4, 26)
        params = {
            # Korrigierter Name für den Config-Empfänger
            "macd_signal_period": trial.suggest_int("macd_signal_period", 5, 15),
            "macd_fast": fast,
            "macd_slow": fast + gap,

            # WICHTIG: Die primären Entry-Konditionen für Optuna freigeben
            "sma_period": trial.suggest_int("sma_period", 20, 100),
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.0, 2.5),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_multiplier": trial.suggest_float("atr_multiplier", 0.1, 1.5),
            "vwap_period": trial.suggest_int("vwap_period", 10, 60),

            "trend_tolerance_pct": trial.suggest_float("trend_tolerance_pct", 0.0, 0.10),
            "bb_touch_window": trial.suggest_int("bb_touch_window", 6, 96),

            # Konjunktions-Schalter: erlauben dem Optimizer, einzelne Entry-Bedingungen abzuwählen
            "require_vwap_confirmation": trial.suggest_categorical("require_vwap_confirmation", [True, False]),
            "require_bb_touch": trial.suggest_categorical("require_bb_touch", [True, False]),

            # Trade-Management
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            # Issue #714 (GR-01) — Obergrenze 120 → 24 (24-Bar-Zeitbox über alle 15 Strategien).
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "FlashCrashReversalStrategy":
        # Issue #446 — `vol_surge_multiplier` ENTFERNT (Phantom-Tuning): kein Volumen-Pfad in der
        # Strategie und 1h-Bars haben `volume=1.0`. Stattdessen die ECHTEN Entry-Felder
        # `bb_period`/`bb_std_dev` (die BB-Crash-Schwelle) tunbar machen — dadurch beeinflusst das
        # Sampling die Round-Trip-Zahl nachweislich. `rsi_overbought` bleibt bewusst fix (Exit-Gate).
        params = {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.5, 3.0),
            "rsi_period": trial.suggest_int("rsi_period", 2, 14),
            "rsi_oversold": trial.suggest_int("rsi_oversold", 10, 30),
            "atr_period": trial.suggest_int("atr_period", 5, 20),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            # Issue #714 (GR-01) — Obergrenze 48 → 24.
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 6, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "VolatilityBreakoutPumpStrategy":
        # Issue #446 — `bb_std` → `bb_std_dev` (echter Config-Feldname). `vol_window`/`vol_threshold`
        # ENTFERNT (Phantom-Tuning): die Strategie hat keinen Volumen-Pfad, und synthetische 1h-Bars
        # tragen konstant `volume=1.0` (hourly_strategy_base.py:174) — ein Volumen-Filter feuert nie
        # (gleiche Architekturentscheidung wie dynamic_breakout/vwap_exhaustion). Getunt werden nur
        # die echten BB-Entry-Felder + Trade-Management.
        params = {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.5, 3.0),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            # Issue #714 (GR-01) — Obergrenze 72 → 24.
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "VwapExhaustionStrategy":
        # Issue #446 — `vwap_window` → `vwap_period` (echter Config-Feldname). `rsi_period`/
        # `rsi_extreme` ENTFERNT (Phantom-Tuning): VwapExhaustion ist bewusst „Price-Deviation only"
        # und besitzt KEINEN RSI-Indikator (siehe Modul-Docstring). Getunt werden nur die echten
        # Felder.
        params = {
            "vwap_period": trial.suggest_int("vwap_period", 10, 50),
            "deviation_threshold": trial.suggest_float("deviation_threshold", 0.005, 0.03),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            # Issue #714 (GR-01) — Obergrenze 48 → 24.
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 6, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "DynamicBreakoutStrategy":
        params = {
            "price_breakout_period": trial.suggest_int("price_breakout_period", 5, 60),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            # Issue #714 (GR-01) — Obergrenze 96 → 24.
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "TrendPullbackStrategy":
        # Issue #669 — TrendPullback erzeugte auf TSLA-1h STRUCTURAL_ALL_UNEVALUABLE (0/16 Trials
        # >= oos_min_trades): ema_period bis 300 Bars (~12.5 Tage bei 1h) verlangt ein sehr langes
        # Trend-Fenster, cooldown_bars/max_bars_in_trade begrenzen zusätzlich die Signal-/Realisierungs-
        # frequenz. Symbol-Override-Punkte (leer per Default, Zero-Hardcoding — Aktivierung erst nach
        # einem dokumentierten Kalibrierlauf, siehe search_space_overrides.json).
        ema_lo, ema_hi = _bounds_for(strategy, symbol, "ema_period", 50, 300)
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "ema_period": trial.suggest_int("ema_period", ema_lo, ema_hi),
            "rsi_period": trial.suggest_int("rsi_period", 5, 21),
            "rsi_oversold": trial.suggest_float("rsi_oversold", 15.0, 45.0),
            "rsi_overbought": trial.suggest_float("rsi_overbought", 55.0, 85.0),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "AdxAtrMomentumStrategy":
        # Issue #669 — Symbol-Override-Punkte für cooldown_bars/max_bars_in_trade (leer per
        # Default). Issue #699 — `adx_period` wird NICHT MEHR gesampelt: der #691-Trockenlauf
        # verifizierte, dass `DirectionalMovement.value` in NautilusTrader 1.230.0 konstant 0.0
        # bleibt — das ADX-Gate wurde durch eine EMA-Steigungs-Bestätigung ersetzt (Option B,
        # siehe adx_atr_momentum.py-Docstring, analog DonchianRegimeBreakout/#691). `adx_period`
        # ist seither funktional TOT (kein Effekt auf das Entry-Signal) und würde sonst
        # Phantom-Tuning betreiben (Pitfall #4) — bleibt in der Config als Re-Aktivierungspunkt.
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "ema_period": trial.suggest_int("ema_period", 20, 100),
            "atr_multiplier": trial.suggest_float("atr_multiplier", 1.0, 4.0),
            "atr_period": trial.suggest_int("atr_period", 5, 21),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "MeanReversionStrategy":
        params = {
            "keltner_period": trial.suggest_int("keltner_period", 6, 40),
            "keltner_atr_period": trial.suggest_int("keltner_atr_period", 6, 40),
            "keltner_multiplier": trial.suggest_float("keltner_multiplier", 1.0, 3.5),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.3, 2.5),
            # Issue #714 (GR-01) — Obergrenze 96 → 24.
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "SqueezeBreakoutStrategy":
        # Issue #689 — Bollinger-innerhalb-Keltner-Squeeze-Release. `bb_std_dev`/`keltner_multiplier`
        # bewusst NICHT hart auf ein festes Verhältnis fixiert (Pitfall #6-analog): der Optimizer
        # kalibriert die Squeeze-Enge selbst innerhalb der Bounds.
        params = {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.5, 2.5),
            "keltner_period": trial.suggest_int("keltner_period", 10, 40),
            "keltner_multiplier": trial.suggest_float("keltner_multiplier", 1.0, 2.5),
            "min_squeeze_bars": trial.suggest_int("min_squeeze_bars", 3, 18),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 24),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 3.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "OpeningRangeBreakoutStrategy":
        # Issue #690 — Opening-Range-Breakout (Momentum-Ignition am Tagesbeginn).
        params = {
            "or_bars": trial.suggest_int("or_bars", 2, 8),
            "or_atr_buffer": trial.suggest_float("or_atr_buffer", 0.0, 1.0),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 24),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 3.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "DonchianRegimeBreakoutStrategy":
        # Issue #691 — Donchian-Ausbruch, EMA-Steigungs-gegatet (Regime-Filter Option B). Der
        # Trockenlauf (echter NautilusTrader-Engine-Lauf) verifizierte, dass `DirectionalMovement
        # .value` in der installierten NautilusTrader-Version (1.230.0) konstant 0.0 bleibt — Option
        # A (ADX) wurde daher deaktiviert (siehe donchian_regime_breakout.py-Docstring, Pitfall #9
        # des Implementierungs-Leitfadens #688). `adx_period`/`adx_threshold` sind seither
        # funktional TOT (kein Effekt auf das Entry-Signal mehr) und werden daher NICHT gesampelt
        # (sonst Phantom-Tuning, Pitfall #4) — sie bleiben in der Config als Re-Aktivierungs-Punkt
        # für eine künftige NautilusTrader-Version.
        params = {
            "donchian_period": trial.suggest_int("donchian_period", 8, 60),
            "ema_period": trial.suggest_int("ema_period", 20, 100),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 24),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "Rsi2ReversionStrategy":
        # Issue #692 — Connors-RSI(2)-Pullback-/Bounce-Reversion. Untere `rsi_oversold`-Bound
        # (5.0) hält das Signal selektiv (RSI(2) ist stark verrauscht, Spec-Warnung: nicht auf 30
        # anheben, sonst degeneriert es zu einem Dauer-Signal).
        params = {
            "rsi_period": trial.suggest_int("rsi_period", 2, 6),
            "rsi_oversold": trial.suggest_float("rsi_oversold", 5.0, 25.0),
            "rsi_overbought": trial.suggest_float("rsi_overbought", 75.0, 95.0),
            "ema_period": trial.suggest_int("ema_period", 50, 200),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 1, 18),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 8, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "GapContinuationStrategy":
        # Issue #693 — Overnight-/Event-Gap-Continuation. Untere `gap_threshold_pct`-Bound
        # (0.5 %) hält genug Setups offen (Gap-Tage sind ohnehin selten, tournament_overrides
        # senkt min_trades zusätzlich, siehe strategies.json).
        params = {
            "gap_threshold_pct": trial.suggest_float("gap_threshold_pct", 0.005, 0.04),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 3.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, _MAX_BARS_IN_TRADE_CAP),
        }
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    # Issue #713 — dyn_tp_enabled/lambda/gamma einheitlich für ALLE 15 Strategien angehängt (nicht
    # per-Strategie-Zweig dupliziert).
    params.update(_dyn_tp_params(trial))
    return params
