def sample_params(strategy: str, trial) -> dict:
    if strategy == "HourlyMeanReversionStrategy":
        return {
            "keltner_period": trial.suggest_int("keltner_period", 6, 40),
            "keltner_atr_period": trial.suggest_int("keltner_atr_period", 6, 40),
            "keltner_multiplier": trial.suggest_float("keltner_multiplier", 1.0, 3.5),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.3, 2.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, 96),
        }
    elif strategy == "SmaCrossoverStrategy":
        return {
            "sma_period": trial.suggest_int("sma_period", 5, 60),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
        }
    elif strategy == "ComboTrendVwapStrategy":
        # Konzept §4 alignment (ISSUE-OPT-377): macd_fast 3–14, macd_gap 4–26
        # ⇒ macd_slow = fast + gap (Gap garantiert fast < slow für den MACD-Indikator).
        fast = trial.suggest_int("macd_fast", 3, 14)
        gap = trial.suggest_int("macd_gap", 4, 26)
        return {
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
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, 120),
        }
    elif strategy == "FlashCrashReversalStrategy":
        # Issue #446 — `vol_surge_multiplier` ENTFERNT (Phantom-Tuning): kein Volumen-Pfad in der
        # Strategie und 1h-Bars haben `volume=1.0`. Stattdessen die ECHTEN Entry-Felder
        # `bb_period`/`bb_std_dev` (die BB-Crash-Schwelle) tunbar machen — dadurch beeinflusst das
        # Sampling die Round-Trip-Zahl nachweislich. `rsi_overbought` bleibt bewusst fix (Exit-Gate).
        return {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.5, 3.0),
            "rsi_period": trial.suggest_int("rsi_period", 2, 14),
            "rsi_oversold": trial.suggest_int("rsi_oversold", 10, 30),
            "atr_period": trial.suggest_int("atr_period", 5, 20),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 6, 48),
        }
    elif strategy == "VolatilityBreakoutPumpStrategy":
        # Issue #446 — `bb_std` → `bb_std_dev` (echter Config-Feldname). `vol_window`/`vol_threshold`
        # ENTFERNT (Phantom-Tuning): die Strategie hat keinen Volumen-Pfad, und synthetische 1h-Bars
        # tragen konstant `volume=1.0` (hourly_strategy_base.py:174) — ein Volumen-Filter feuert nie
        # (gleiche Architekturentscheidung wie dynamic_breakout/vwap_exhaustion). Getunt werden nur
        # die echten BB-Entry-Felder + Trade-Management.
        return {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.5, 3.0),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, 72),
        }
    elif strategy == "VwapExhaustionStrategy":
        # Issue #446 — `vwap_window` → `vwap_period` (echter Config-Feldname). `rsi_period`/
        # `rsi_extreme` ENTFERNT (Phantom-Tuning): VwapExhaustion ist bewusst „Price-Deviation only"
        # und besitzt KEINEN RSI-Indikator (siehe Modul-Docstring). Getunt werden nur die echten
        # Felder.
        return {
            "vwap_period": trial.suggest_int("vwap_period", 10, 50),
            "deviation_threshold": trial.suggest_float("deviation_threshold", 0.005, 0.03),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 6, 48),
        }
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
