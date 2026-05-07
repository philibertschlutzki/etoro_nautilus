import os
import time
import asyncio
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode

# Import the custom adapter
from adapters.etoro_data import EToroDataClient

# Securely load API keys
API_KEY = os.environ.get("ETORO_API_KEY")
USER_KEY = os.environ.get("ETORO_USER_KEY")

if not API_KEY or not USER_KEY:
    raise ValueError("ERROR: Missing API keys! Please set ETORO_API_KEY and ETORO_USER_KEY in your environment.")

if __name__ == "__main__":
    print("Starting Nautilus Trader Setup...")

    # Node Configuration
    config = TradingNodeConfig(
        trader_id="eToro-Bot-01"
    )

    # Initialize Node
    node = TradingNode(config=config)

    # Instantiate the eToro Data Client
    # We access the 'kernel' of the Node directly for strict dependency injection
    etoro_client = EToroDataClient(
        loop=asyncio.get_event_loop(),
        msgbus=node.kernel.msgbus,
        cache=node.kernel.cache,
        clock=node.kernel.clock,
        instrument_provider=node.kernel.instrument_provider,
        api_key=API_KEY,
        user_key=USER_KEY
    )
    
    # Add the client to the Nautilus engine
    node.add_data_client(etoro_client)

    # Fire up the system!
    node.start()
    print("✅ System running! Waiting for eToro Live-Data (TSLA)...")
    print("Press Ctrl+C to exit.")

    try:
        # Endless loop to keep the program open
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nShutting down system safely...")
        node.stop()
        print("Bot stopped.")
