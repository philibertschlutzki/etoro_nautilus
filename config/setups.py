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
    # {
    #     "strategy_class": "SmaCrossoverStrategy",
    #     "etoro_id": "1001",                    # Apple (z.B.)
    #     "symbol": "AAPL.ETORO",
    #     "bar_type": "AAPL.ETORO-1-MINUTE-MID-INTERNAL",
    #     "params": {
    #         "sma_period": 10                   # Anderer SMA Parameter für Apple
    #     }
    # }
]