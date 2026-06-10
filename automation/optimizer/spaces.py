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
    elif strategy == "DynamicBreakoutStrategy":
        return {
            "price_breakout_period": trial.suggest_int("price_breakout_period", 5, 40),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
        }
    elif strategy == "FlashCrashReversalStrategy":
        return {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "rsi_period": trial.suggest_int("rsi_period", 5, 20),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
        }
    elif strategy == "VolatilityBreakoutPumpStrategy":
        return {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.5, 3.5),
        }
    elif strategy == "ComboTrendVwapStrategy":
        macd_fast = trial.suggest_int("macd_fast", 8, 20)
        macd_gap = trial.suggest_int("macd_gap", 5, 20)
        return {
            "sma_period": trial.suggest_int("sma_period", 20, 100),
            "macd_fast": macd_fast,
            "macd_slow": macd_fast + macd_gap,
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
        }
    elif strategy == "VwapExhaustionStrategy":
        return {
            "deviation_threshold": trial.suggest_float("deviation_threshold", 0.005, 0.05),
            "vwap_period": trial.suggest_int("vwap_period", 12, 48),
        }
    elif strategy == "MeanReversionStrategy":
        return {
            "keltner_period": trial.suggest_int("keltner_period", 10, 40),
            "keltner_multiplier": trial.suggest_float("keltner_multiplier", 1.0, 3.5),
        }
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
