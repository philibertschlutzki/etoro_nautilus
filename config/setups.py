# config/setups.py

"""
Hier definierst du alle aktiven Handelsstrategien.
Du kannst dieselbe Strategie mehrfach mit verschiedenen Parametern oder Instrumenten laden.
"""

ACTIVE_BOTS = [
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1111",                      # Muss in ETORO_INSTRUMENTS gemappt sein
        "symbol": "TSLA.ETORO",
        "bar_type": "TSLA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5
        }
    },
    # --- BEISPIEL FÜR EIN ZWEITES ASSET ---
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "8671",                          # Die ID aus Schritt 1
        "symbol": "HUT.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "HUT.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "6270",                          # Die ID aus Schritt 1
        "symbol": "RIOT.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "RIOT.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1137",                          # Die ID aus Schritt 1
        "symbol": "NVDA.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "NVDA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "5045",                          # Die ID aus Schritt 1
        "symbol": "FSLY.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "FSLY.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "9119",                          # Die ID aus Schritt 1
        "symbol": "INSM.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "INSM.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100000",                          # Die ID aus Schritt 1
        "symbol": "BTC.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "BTC.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100001",                          # Die ID aus Schritt 1
        "symbol": "ETH.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "ETH.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100017",                          # Die ID aus Schritt 1
        "symbol": "ADA.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "ADA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100003",                          # Die ID aus Schritt 1
        "symbol": "XRP.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "XRP.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100421",                          # Die ID aus Schritt 1
        "symbol": "AERO.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "AERO.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100446",                          # Die ID aus Schritt 1
        "symbol": "HYPE.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "HYPE.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100323",                          # Die ID aus Schritt 1
        "symbol": "ONDO.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "ONDO.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        }
    },
] 