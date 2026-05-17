# config/setups.py

"""
Hier definierst du alle aktiven Handelsstrategien.
Du kannst dieselbe Strategie mehrfach mit verschiedenen Parametern oder Instrumenten laden.
"""

# --- API Test Settings --------------------------------------------------------
# Dieser Block wird nur vom Test-Script verwendet
ETORO_API_TEST = {
    "environment": "real",       # real oder demo
    "dry_run": False,            # Muss False sein, damit echte Orders rausgehen
    "symbol": "ADA.ETORO",       # Cardano eignet sich gut (kleiner Preis pro Unit)
    "trade_amount_usd": 11.0,    # Etwas über den 10$ Mindestbetrag zur Sicherheit
    "test_account_id": "TEST_01", # Label für das Logging
    "enable_trailing_stop": False, # TSL deaktiviert bis Phase-4-Test bestätigt welche Kombination eToro unterstützt
}

# --- eToro execution settings -------------------------------------------------
ETORO_EXECUTION = {
    "environment": "real",   # "demo" | "real"
    "dry_run": False,         # MUST be set False explicitly to send real orders
    "enable_trailing_stop": False, # TSL deaktiviert bis Phase-4-Test bestätigt welche Kombination eToro unterstützt
}

ACTIVE_BOTS = [
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1111",                      # Muss in ETORO_INSTRUMENTS gemappt sein
        "symbol": "TSLA.ETORO",
        "bar_type": "TSLA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
    # --- BEISPIEL FÜR EIN ZWEITES ASSET ---
    {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "8671",                          # Die ID aus Schritt 1
        "symbol": "HUT.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "HUT.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "6270",                          # Die ID aus Schritt 1
        "symbol": "RIOT.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "RIOT.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1137",                          # Die ID aus Schritt 1
        "symbol": "NVDA.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "NVDA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "5045",                          # Die ID aus Schritt 1
        "symbol": "FSLY.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "FSLY.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "9119",                          # Die ID aus Schritt 1
        "symbol": "INSM.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "INSM.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100000",                          # Die ID aus Schritt 1
        "symbol": "BTC.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "BTC.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100001",                          # Die ID aus Schritt 1
        "symbol": "ETH.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "ETH.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100017",                          # Die ID aus Schritt 1
        "symbol": "ADA.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "ADA.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100003",                          # Die ID aus Schritt 1
        "symbol": "XRP.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "XRP.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100421",                          # Die ID aus Schritt 1
        "symbol": "AERO.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "AERO.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100446",                          # Die ID aus Schritt 1
        "symbol": "HYPE.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "HYPE.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100323",                          # Die ID aus Schritt 1
        "symbol": "ONDO.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "ONDO.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "2380",                          # Die ID aus Schritt 1
        "symbol": "01211.HK.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "01211.HK.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "6700",                          # Die ID aus Schritt 1
        "symbol": "XPEV.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "XPEV.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "7991",                          # Die ID aus Schritt 1
        "symbol": "PLTR.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "PLTR.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "2587",                          # Die ID aus Schritt 1
        "symbol": "RHM.DE.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "RHM.DE.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "4232",                          # Die ID aus Schritt 1
        "symbol": "NVS.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "NVS.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "1402",                          # Die ID aus Schritt 1
        "symbol": "ROP.ZU.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "ROP.ZU.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100043",                          # Die ID aus Schritt 1
        "symbol": "DOGE.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "DOGE.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100080",                          # Die ID aus Schritt 1
        "symbol": "SHIBxM.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "SHIBxM.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100303",                          # Die ID aus Schritt 1
        "symbol": "PEPExM.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "PEPExM.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100063",                          # Die ID aus Schritt 1
        "symbol": "SOL.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "SOL.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "100085",                          # Die ID aus Schritt 1
        "symbol": "AVAX.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "AVAX.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "22",                          # Die ID aus Schritt 1
        "symbol": "NATGAS.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "NATGAS.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "3208",                          # Die ID aus Schritt 1
        "symbol": "PALL.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "PALL.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "62",                          # Die ID aus Schritt 1
        "symbol": "USDTRY.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "USDTRY.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
        {
        "strategy_class": "SmaCrossoverStrategy",
        "etoro_id": "42",                          # Die ID aus Schritt 1
        "symbol": "USDZAR.ETORO",                      # Muss mit instrument_map.py übereinstimmen
        "bar_type": "USDZAR.ETORO-1-MINUTE-MID-INTERNAL",
        "params": {
            "sma_period": 5                         # Eigene Strategie-Parameter
        },
        "trade_amount_usd": 100.0,
        "max_open_positions": 1,
    },
] 
