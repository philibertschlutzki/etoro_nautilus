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
        fast = trial.suggest_int("macd_fast", 8, 20)
        gap = trial.suggest_int("macd_gap", 6, 20)
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
            
            # Trade-Management
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, 120),
        }
    elif strategy == "FlashCrashReversalStrategy":
        return {
            "rsi_period": trial.suggest_int("rsi_period", 2, 14),
            "rsi_oversold": trial.suggest_int("rsi_oversold", 10, 30),
            "vol_surge_multiplier": trial.suggest_float("vol_surge_multiplier", 2.0, 6.0),
            "atr_period": trial.suggest_int("atr_period", 5, 20),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 6, 48),
        }
    elif strategy == "VolatilityBreakoutPumpStrategy":
        return {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std": trial.suggest_float("bb_std", 1.5, 3.0),
            "vol_window": trial.suggest_int("vol_window", 5, 20),
            "vol_threshold": trial.suggest_float("vol_threshold", 1.5, 4.0),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 12, 72),
        }
    elif strategy == "VwapExhaustionStrategy":
        return {
            "vwap_window": trial.suggest_int("vwap_window", 10, 50),
            "deviation_threshold": trial.suggest_float("deviation_threshold", 0.005, 0.03),
            "rsi_period": trial.suggest_int("rsi_period", 2, 14),
            "rsi_extreme": trial.suggest_int("rsi_extreme", 10, 30),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 6, 48),
        }
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
