# Momentum-LS Smart Portfolio Integration

## Overview
The Momentum-LS integration is a top-level orchestrator that dynamically fetches the current symbol universe from a specific eToro Smart Portfolio, backtests all available strategies against that universe to pick the optimal strategy per symbol, and automatically manages dynamic capital allocation when running the live system.

## Prerequisites
- Standard Python 3.10+ virtual environment.
- Required dependencies installed via `pip install -r requirements.txt`.
- `.env` file populated with:
  ```env
  ETORO_API_KEY=your_key
  ETORO_USER_KEY=your_user_key
  MOMENTUM_LS_USERNAME=etoro_username_of_the_smart_portfolio
  ETORO_CONFIRM_LIVE=1  # Required ONLY if environment='real' and dry_run=False
  ```

## Daily Workflow
The workflow enforces a strict sequential dependency. You **must** run these steps in order, otherwise the tournament will fail or produce incomplete results.

### Step 1: Fetch the Current Universe
This script retrieves the current holdings of the Smart Portfolio and resolves their eToro internal IDs to Nautilus-compatible symbols.
```bash
python3 dev_scripts/momentum_ls_universe.py --output data/universe/momentum_ls.json
```

### Step 2: Ensure Historical Data Exists
The backtest tournament evaluates the past 6 months of tick data. Check your `data/nautilus/data/quote_tick/` directory. If any new tickers were added to the universe that you do not have Parquet data for, you must fetch the fallback candles:
```bash
# Example for ADA.ETORO
python3 dev_scripts/momentum_ls_fetch_candles.py --etoro-id 100017 --symbol ADA.ETORO --months 6
```
*(If the eToro API returns no data, check the `instrumentId` mapping or your API keys).*

### Step 3: Run the Backtest Tournament
The tournament **requires** the universe JSON and the historical Parquet data to exist. It ranks all implemented strategies for each symbol using the Sortino ratio (primary), Calmar ratio (secondary), and a Profit Factor > 1.5.
```bash
python3 dev_scripts/momentum_ls_tournament.py --universe data/universe/momentum_ls.json --output logs/tournament_today.json
```

### Step 4: Launch the Live Bot
The orchestrator ties everything together. It reads the universe, parses the tournament winners, sets up the `MomentumLSAllocator` for dynamic sizing, and launches the Nautilus engine.
```bash
python3 dev_scripts/momentum_ls_run.py --tournament logs/tournament_today.json
```
*Note: Use `--dry-run` to test the wiring without sending actual orders.*

## Interpreting Tournament Output
The tournament outputs both a JSON file and a printed console table:
- **Sortino**: Primary ranking metric.
- **Calmar**: Secondary tie-breaker metric.
- **PF (Profit Factor)**: Strategies with a PF <= 1.5 are automatically disqualified.
- **Win?**: Checked (`✓`) if the strategy is the absolute winner for that specific symbol. The live runner will automatically configure the bot using this strategy.

## Adding a New Instrument
If the eToro Smart Portfolio adds a new asset, it will show up as an `Unknown` symbol with `symbol: null` during Step 1. To fix this:
1. Lookup the eToro ID using `dev_scripts/get_instruments_id.py`.
2. Add the ID and `SYMBOL.ETORO` mapping to `adapters/instrument_map.py`.
3. Fetch the historical candles via Step 2.
4. Rerun Step 1 and Step 3.

## Troubleshooting
- **No valid symbols to trade after cross-referencing...**
  - Usually means none of the symbols passed the PF > 1.5 test, OR your parquet data directory is completely empty. Ensure Step 2 was run.
- **Simulation failed for [Symbol]...**
  - Indicates the historical parquet data is malformed or the strategy config is strictly rejecting the default parameters. Check the logs for exact errors.
- **Universe data is stale...**
  - `dev_scripts/momentum_ls_run.py` will warn if the `fetched_at` timestamp is older than 24 hours. Ensure you run Step 1 daily.
