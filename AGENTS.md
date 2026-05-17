# AGENTS.md — eToro Nautilus Multi-Bot Platform

> **Purpose:** This file is the authoritative guide for Jules (and any AI coding agent) working on this repository. Keep it updated whenever you make structural changes, add new adapters, or modify core conventions. This file must reflect the current state of the codebase at all times.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Structure](#2-repository-structure)
3. [Architecture & Data Flow](#3-architecture--data-flow)
4. [Core Framework: Nautilus Trader](#4-core-framework-nautilus-trader)
5. [Adapter Layer](#5-adapter-layer)
   - 5.1 [EToroDataClient (WebSocket)](#51-etorodataclient-websocket)
   - 5.2 [EToroExecutionClient (REST + WebSocket)](#52-etoroexecutionclient-rest--websocket)
   - 5.3 [StateManager](#53-statemanager)
   - 5.4 [RateLimiter](#54-ratelimiter)
   - 5.5 [InstrumentMap](#55-instrumentmap)
6. [Strategy Layer](#6-strategy-layer)
7. [Configuration System](#7-configuration-system)
8. [eToro API Reference](#8-etoro-api-reference)
9. [Order Lifecycle & Reconciliation](#9-order-lifecycle--reconciliation)
10. [Adding New Instruments](#10-adding-new-instruments)
11. [Adding New Strategies](#11-adding-new-strategies)
12. [Safety & Risk Controls](#12-safety--risk-controls)
13. [Error Handling Conventions](#13-error-handling-conventions)
14. [Testing & Development Scripts](#14-testing--development-scripts)
15. [Environment Setup](#15-environment-setup)
16. [Common Pitfalls & Known Issues](#16-common-pitfalls--known-issues)
17. [Code Style & Conventions](#17-code-style--conventions)
18. [Changelog (Agent-Maintained)](#18-changelog-agent-maintained)

---

## 1. Project Overview

This is a **live algorithmic trading system** built on [Nautilus Trader](https://nautilustrader.io/) with custom adapters for the eToro brokerage API. It supports:

- **Multiple parallel strategies** across multiple instruments in a single process.
- **Real-time tick/bar data** via eToro's WebSocket API.
- **Order execution** via eToro's REST API with WebSocket fill confirmations.
- **Passive data recording** to Parquet format (via `run_catalog.py`).
- **Backtesting** against recorded Parquet data (via `run_backtest.py`).

**Critical constraint:** This system interacts with real financial markets. Bugs in order logic, position state, or reconciliation can cause real monetary losses. Every change to `adapters/` must be reviewed with extreme care.

---

## 2. Repository Structure

```text
etoro_nautilus/
├── .agents/                        # AI system prompts and documentation
│   ├── Integration_Guide.md
│   ├── JULES_SYSTEM_PROMPT.md
│   ├── API_docs_etoro.md
│   └── testing.md
├── adapters/                       # eToro Integration Layer
│   ├── etoro_config.py             # Config objects and Factory methods
│   ├── etoro_data.py               # WebSocket client (QuoteTicks/Bars)
│   ├── etoro_execution.py          # REST API client (Order execution)
│   ├── etoro_rate_limiter.py       # Priority Queue for Rate Limits
│   ├── etoro_state_manager.py      # Persistence of Order/Position IDs
│   ├── instrument_map.py           # Hardcoded Symbol <-> eToroID Map
│   └── momentum_ls_allocator.py    # Capital allocator for Momentum-LS
├── backtesting/                    # Backtesting Engine
│   ├── backtesting_config.json
│   └── run_backtest.py
├── config/
│   └── setups.py                   # Strategy configurations & Credentials
├── data/                           # Data storage
│   ├── nautilus/                   # Parquet files for backtesting
│   ├── state/                      # Runtime state (execution_mapping.json)
│   └── universe/                   # Universe configurations (momentum_ls.json)
├── dev_scripts/                    # Standalone tests and utils
│   ├── auto_map_insturments.py
│   ├── compact_parquet.py
│   ├── etoro_api/
│   ├── etoro_api_probe.py
│   ├── etoro_api_probe_all.py
│   ├── etoro_balance.py
│   ├── etoro_close_orphans.py
│   ├── etoro_connectivity_test.py
│   ├── etoro_deploy_agent_portfolio.py
│   ├── etoro_execution_test.py
│   ├── etoro_execution_tests_all_orders.py
│   ├── etoro_fetch_history.py
│   ├── etoro_tesla_tracker.py
│   ├── get_instruments_id.py
│   ├── momentum_ls_fetch_candles.py
│   ├── momentum_ls_fetch_candles_auto.py
│   ├── momentum_ls_run.py          # Orchestrator for Momentum-LS
│   ├── momentum_ls_simulator.py
│   ├── momentum_ls_tournament.py
│   ├── momentum_ls_universe.py
│   ├── read_parquet.py
│   └── test_nautilus.py
├── logs/                           # Runtime logs
├── manuals/                        # Detailed Handbooks
│   ├── backtesting_manual.md
│   ├── deployment.md
│   ├── feature_automation_LS.md
│   ├── momentum_ls.md
│   └── new_tickers.md
├── strategies/                     # Trading Logic
│   ├── __init__.py
│   ├── adx_atr_momentum.py
│   ├── dynamic_breakout.py
│   ├── flash_crash_reversal.py
│   ├── mean_reversion.py
│   ├── momentum_ls_base.py
│   ├── momentum_ls_sma.py
│   ├── sma_crossover.py
│   ├── tesla_combo_strategy.py
│   ├── trend_pullback.py
│   ├── volatility_breakout.py
│   └── vwap_exhaustion.py
├── tests/                          # Unit tests
│   ├── __init__.py
│   ├── test_etoro_execution.py
│   ├── test_execution.py
│   ├── test_momentum_ls_allocator.py
│   ├── test_stop_loss_payload.py
│   ├── test_tournament_metrics.py
│   └── test_universe_fetcher.py
├── AGENTS.md                       # This file
├── README.md                       # High-level overview
├── requirements.txt                # Python dependencies
├── run_bot.py                      # Main entry point (Live Trading)
└── run_catalog.py                  # Main entry point (Data Recording)
```

## 3. Architecture & Data Flow

### Live Trading Flow

```
.env (API keys)
    │
    ▼
run_bot.py
    │
    ├── EToroDataClientConfig ──► EToroDataClient
    │                                    │
    │                            WebSocket wss://ws.etoro.com/ws
    │                                    │
    │                            QuoteTick events ──► Nautilus MessageBus
    │                                                        │
    ├── EToroExecClientConfig ──► EToroExecutionClient       │
    │                                    │                   │
    │                            REST API (orders)           │
    │                            WebSocket (fills/events)    │
    │                                                        ▼
    └── Strategy instances ◄──────────── on_bar() / on_quote_tick()
              │
              ▼ submit_order()
         EToroExecutionClient._submit_order_async()
              │
              ├── POST /market-open-orders/by-amount
              ├── POST /market-close-orders/positions/{posId}
              └── POST /limit-orders
```

### State Persistence

```
ClientOrderId (Nautilus internal)
    │
    ▼ _StateManager
data/state/execution_mapping.json
    │
    ▼
eToro positionId / orderId (used for close/cancel REST calls)
```

### Process Separation

- `run_bot.py` and `run_catalog.py` are **completely independent processes**.
- They both connect to the same eToro WebSocket but do not share memory.
- Both are managed by `systemd` on the production VM.
- On any WebSocket error, both call `os._exit(1)` to allow systemd to restart them.

---

## 4. Core Framework: Nautilus Trader

### Key Concepts

| Concept | Description |
|---------|-------------|
| `TradingNode` | Top-level container. Manages clients, traders, and the event loop. |
| `LiveMarketDataClient` | Subclassed by `EToroDataClient`. Delivers market data into the Nautilus cache and msgbus. |
| `LiveExecutionClient` | Subclassed by `EToroExecutionClient`. Handles order submission and generates execution events. |
| `Strategy` | Receives data events and submits orders via `self.submit_order()`. |
| `StrategyConfig` | Pydantic-based, **must use `frozen=True`**. Passed to strategy at construction. |
| `InstrumentId` | Globally unique identifier: `"SYMBOL.VENUE"` format, e.g. `"TSLA.ETORO"`. |
| `BarType` | Specifies bar aggregation: `"TSLA.ETORO-1-MINUTE-MID-INTERNAL"`. |
| `ClientOrderId` | Nautilus-internal order ID. Never sent to eToro directly. |
| `VenueOrderId` | The eToro-side ID (positionId or orderId). |
| `msgbus` | Internal publish/subscribe bus. Instruments are published to `"data.instrument.ETORO.{id}"`. |

### Lifecycle Methods

```python
# Client lifecycle
async def _connect(self) -> None   # Called when TradingNode starts
async def _disconnect(self) -> None  # Called when TradingNode stops

# Strategy lifecycle
def on_start(self) -> None         # Subscribe to data here
def on_stop(self) -> None          # Unsubscribe here
def on_bar(self, bar: Bar) -> None
def on_quote_tick(self, tick: QuoteTick) -> None
def on_order_filled(self, event) -> None
def on_order_rejected(self, event) -> None
def on_position_opened(self, event) -> None
def on_position_closed(self, event) -> None
```

### Generating Execution Events (in EToroExecutionClient)

Always use these methods — never manipulate the cache directly:

```python
self.generate_order_submitted(strategy_id, instrument_id, client_order_id, ts_event)
self.generate_order_accepted(strategy_id, instrument_id, client_order_id, venue_order_id, ts_event)
self.generate_order_filled(strategy_id, instrument_id, client_order_id, venue_order_id,
                           venue_position_id, trade_id, order_side, order_type,
                           last_qty, last_px, quote_currency, commission,
                           liquidity_side, ts_event)
self.generate_order_rejected(strategy_id, instrument_id, client_order_id, reason, ts_event)
self.generate_order_canceled(strategy_id, instrument_id, client_order_id, venue_order_id, ts_event)
self.generate_account_state(balances, margins, reported, ts_event)
```

---

## 5. Adapter Layer

### 5.1 EToroDataClient (WebSocket)

**File:** `adapters/etoro_data.py`

**Purpose:** Connects to eToro's WebSocket, authenticates, subscribes to instrument price feeds, and delivers `QuoteTick` objects into the Nautilus data pipeline.

**Connection flow:**
1. `_connect()` → retry loop (max 5 attempts, exponential backoff up to 60s)
2. SSL context via `ssl.create_default_context()`
3. `_register_instruments()` → creates `Equity` objects in cache/provider/msgbus
4. `_authenticate()` → sends auth payload, waits for non-empty response
5. `_subscribe_etoro_instruments()` → subscribes to `instrument:{eid}` topics
6. Spawns `_message_loop()` as a Nautilus task

**Message processing:**
- Only handles `type == "Trading.Instrument.Rate"` and `type == "Snapshot"`.
- `content` field can be either a JSON string or a dict — always normalize.
- Topic format: `"instrument:{eToro_numeric_id}"`.
- Tick is only published when at least one of bid/ask has changed.
- Heartbeat log every 60 ticks (configurable via `_HEARTBEAT_INTERVAL`).

**Instrument registration rules:**

| Symbol contains | `price_precision` | `price_increment` |
|-----------------|-------------------|-------------------|
| `SHIB` or `PEPE` | 8 | `1e-8` |
| `BTC` or `ETH` | 2 | `0.01` |
| All others | 5 | `0.00001` |

All instruments use `size_precision=0` (integer quantities). This is intentional — eToro orders are placed in USD amounts, so fractional units are not needed.

**Crypto symbol set (`_CRYPTO_SYMBOLS`):** Used only for log classification, not for trading logic. When adding a new crypto, add its symbol to this frozenset.

**On any WebSocket disconnect/error:** Calls `os._exit(1)`. systemd is responsible for restart. Do NOT add in-process reconnection logic — this is intentional design.

---

### 5.2 EToroExecutionClient (REST + WebSocket)

**File:** `adapters/etoro_execution.py`

**Purpose:** Manages all order operations. Connects to eToro via both REST (for order submission/cancellation) and WebSocket (for fill notifications). Maintains state persistence and implements reconciliation fallback.

#### REST Endpoints

| Operation | Method | Endpoint |
|-----------|--------|----------|
| Open market order (by amount) | POST | `.../market-open-orders/by-amount` |
| Open market order (by units) | POST | `.../market-open-orders/by-units` |
| Close position | POST | `.../market-close-orders/positions/{positionId}` |
| Place limit order | POST | `.../limit-orders` |
| Cancel limit order | DELETE | `.../limit-orders/{orderId}` |
| Get PnL / positions | GET | `https://public-api.etoro.com/api/v1/trading/info/{env}/pnl` |

**Base URLs:**
- Demo: `https://public-api.etoro.com/api/v1/trading/execution/demo`
- Real: `https://public-api.etoro.com/api/v1/trading/execution`

#### Required HTTP Headers (always include all four)

```python
{
    "x-api-key": self._api_key,
    "x-user-key": self._user_key,
    "x-request-id": str(uuid.uuid4()),  # Unique per request
    "Content-Type": "application/json",
}
```

**`x-request-id`** is critical — eToro uses it for idempotency and order matching (stored as `token` in PnL response). For order submissions, use `self._order_req_id(client_order_id_value)` (UUID5 derived from coid) as the request ID so it's deterministic and matchable.

#### Market Open Payload

**Note:** Stop Loss, Take Profit, and Trailing Stop Loss are configured via order tags (e.g., `SL:<pct>`, `TP:<pct>`, `TSL:1`) which are parsed and injected into the payload. Limit orders explicitly require `IsNoStopLoss: False` and a valid `StopLossRate > 0`.



```python
# by-amount (preferred, when quote tick is available):
{
    "InstrumentID": int(etoro_id),
    "IsBuy": True,
    "Leverage": 1,
    "Amount": round(qty * ask_price, 2),  # USD amount
}

# by-units (fallback, when no quote tick available):
{
    "InstrumentID": int(etoro_id),
    "IsBuy": True,
    "Leverage": 1,
    "AmountInUnits": float(qty),
}
```

#### Close Position Payload

```python
{
    "InstrumentID": int(etoro_id),
    "UnitsToDeduct": None,  # Close entire position
}
```
> **Critical:** The `InstrumentID` field is strictly required when closing settled positions. Without it, the eToro API returns an HTTP 400 error ("InstrumentId does not exist").

**URL format:** `POST .../market-close-orders/positions/{eToro_positionId}`

#### Limit Order Payload

```python
{
    "InstrumentID": int(etoro_id),
    "IsBuy": True/False,
    "Leverage": 1,
    "Rate": float(limit_price),
    "Amount": round(qty * limit_price, 2),
    "StopLossRate": sl_rate,  # Required; see _build_limit_payload() for calculation
    "IsNoStopLoss": False,
    "IsNoTakeProfit": True,
}
```

**Important:** eToro requires a valid `StopLossRate` for limit orders. `IsNoStopLoss: True` may be rejected. The current implementation uses a nominal stop-loss (50% below for BUY, 100% above for SELL) as a constraint satisfier.

#### WebSocket Topics (Execution Client)

Subscribes to: `"trading.notifications"` and `"portfolio.positions"`.

Relevant message types (checked via `msg_type.lower()`):
- `"trading.position.opened"` / `"position.opened"` — fill event
- `"trading.order.filled"` / `"orderfilled"` — fill event
- `"trading.position.closed"` / `"position.closed"` — close fill
- `"trading.order.accepted"` / `"order.accepted"` — order accepted
- `"trading.order.canceled"` / `"order.cancelled"` — order canceled

#### Order Matching Logic

The execution client maintains a `_ws_buffer` (max 50 messages) for WS messages that arrive before the corresponding REST response. When an order is accepted via REST, the buffer is replayed to catch early fill events.

WS messages are matched to orders by checking (in priority order):
1. `token` / `requestId` in content == `_order_req_id(coid)` (UUID5)
2. `positionId` in content == stored position ID
3. `orderId` in content == stored order ID

#### `_poll_for_fill()` — Reconciliation Fallback

Runs as a background task after any market order submission. Parameters:
- **20 attempts × 5 seconds = 100 seconds total** — designed to cover eToro's 30–90s settlement window.
- Checks `cache.order(client_order_id).status.name == "FILLED"` each iteration.
- For **open orders**: calls `_reconcile_via_pnl()` which scans PnL positions by token or orderId.
- For **close orders**: checks that the positionId is no longer in the PnL positions list.

**Do not reduce the attempt count or sleep interval** — eToro's settlement can take up to 90 seconds.

---

### 5.3 StateManager

**File:** `adapters/etoro_state_manager.py`

Maps `ClientOrderId` (str) → eToro `positionId` or `orderId` (str).

Persists atomically to JSON via temp file + `os.replace()`. This ensures the mapping survives process restarts.

**Default path:** `data/state/execution_mapping.json`

API:
```python
await state.load(warn_fn)          # Call once at startup
await state.get(client_order_id)   # Returns str | None
await state.set(client_order_id, position_id)
await state.delete(client_order_id)
state.get_all()                    # Returns dict copy (sync, no lock)
```

**Critical:** `get_all()` is synchronous and used in hot paths for WS message matching. It returns a dict copy — safe to iterate while other tasks modify the underlying mapping.

---

### 5.4 RateLimiter

**File:** `adapters/etoro_rate_limiter.py`

Token bucket: **capacity=20, refill=1 token per 3 seconds**.

```python
await rate_limiter.acquire("CLOSE")  # Queues and waits — NEVER dropped
await rate_limiter.acquire("OPEN")   # Returns False immediately if no tokens
await rate_limiter.acquire("LIMIT")  # Returns False immediately if no tokens
```

**CLOSE orders are always prioritized.** They queue with `asyncio.PriorityQueue` and are served as soon as a token is available. This prevents positions from getting stuck open due to rate limiting.

**Lifecycle:** Call `await rate_limiter.start()` in `_connect()` and `await rate_limiter.stop()` in `_disconnect()`.

---

### 5.5 InstrumentMap

**File:** `adapters/instrument_map.py`

```python
ETORO_INSTRUMENTS = {
    "1111": "TSLA.ETORO",
    "100000": "BTC.ETORO",
    # ... etc.
}
```

- **Keys:** eToro's numeric instrument IDs as strings.
- **Values:** Nautilus `InstrumentId` strings in `"SYMBOL.VENUE"` format.

The execution client builds the reverse mapping at init:
```python
self._instrument_to_etoro = {v: k for k, v in ETORO_INSTRUMENTS.items()}
```

**ETORO_INSTRUMENTS is the single source of truth.** Both the data client and the execution client import from here. Never hardcode instrument IDs elsewhere.

---


### 5.6 MomentumLSAllocator
(`adapters/momentum_ls_allocator.py`)

A thread-safe capital allocator for the Momentum-LS live trading orchestrator.
It is injected into strategies to override their quantity computation dynamically.

**Key constraints:**
1. **No-interference rule:** If there is an existing open position for the queried instrument, allocation returns `0.0`.
2. **Dynamic slicing:** Capital is dynamically sliced based on `account_balance / pending_signals`, where pending signals are universe instruments currently without an open position.

## 6. Strategy Layer

All strategies live in `strategies/` and follow a strict pattern.

### Required Structure

```python
from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy

class MyStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str          # e.g. "TSLA.ETORO"
    bar_type: str               # e.g. "TSLA.ETORO-1-MINUTE-MID-INTERNAL"
    trade_amount_usd: float = 100.0
    max_open_positions: int = 1
    # ... strategy-specific params

class MyStrategy(Strategy):
    def __init__(self, config: MyStrategyConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        # Initialize indicators here

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)          # and/or
        self.subscribe_quote_ticks(self.instrument_id)

    def on_stop(self) -> None:
        self.unsubscribe_bars(self.bar_type)
        self.unsubscribe_quote_ticks(self.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        # 1. Feed indicators
        # 2. Check initialized
        # 3. Evaluate signal
        # 4. Call _on_buy_signal() or _on_sell_signal()
```

### Position Management Pattern

Every strategy must follow this pattern before placing an order:

```python
def _on_buy_signal(self, bar: Bar) -> None:
    positions = self.cache.positions_open(instrument_id=self.instrument_id)
    if positions:
        pos = positions[0]
        if pos.side == PositionSide.LONG:
            return   # Already long, do nothing
        self._close_position(pos)   # Close opposite (short) position
        return   # Do NOT open new position in same bar
    if len(self.cache.positions_open()) >= self.config.max_open_positions:
        return
    qty = self._compute_quantity(bar)
    if qty is None:
        return
    order = self.order_factory.market(
        instrument_id=self.instrument_id,
        order_side=OrderSide.BUY,
        quantity=qty,
        time_in_force=TimeInForce.GTC,
    )
    self.submit_order(order)
```

### Quantity Calculation

**Always use this exact pattern:**
```python
def _compute_quantity(self, bar: Bar) -> Quantity | None:
    instrument = self.cache.instrument(self.instrument_id)
    if instrument is None:
        self._log.error(f"[{self.instrument_id}] Instrument not in cache")
        return None
    units = self.config.trade_amount_usd / float(bar.close)
    return instrument.make_qty(units)
```

`instrument.make_qty()` applies `size_precision=0` (rounds to integer), which is correct — eToro converts the integer unit count to USD internally using the current rate.

### Closing Positions

```python
def _close_position(self, pos) -> None:
    exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
    order = self.order_factory.market(
        instrument_id=self.instrument_id,
        order_side=exit_side,
        quantity=pos.quantity,
        time_in_force=TimeInForce.GTC,
    )
    self.submit_order(order)
```

**Never use `quantity > pos.quantity` for closing.** The execution client uses the Nautilus cache to detect close orders (compares order side against open position side) and routes them to `market-close-orders`.

### Available Indicators (Nautilus built-ins)

```python
from nautilus_trader.indicators import (
    SimpleMovingAverage,
    ExponentialMovingAverage,
    MovingAverageConvergenceDivergence,
    BollingerBands,
    AverageTrueRange,
    RelativeStrengthIndex,
    KeltnerChannel,
    DirectionalMovement,  # provides ADX value via .value
)
```

Feed bars to indicators: `self.indicator.handle_bar(bar)`
Check ready: `if not self.indicator.initialized: return`

---



### Available Strategies in the Repository

#### 1. `AdxAtrMomentumStrategy` (`adx_atr_momentum.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `adx_period: int = 14`, `atr_period: int = 14`, `adx_threshold: float = 25.0`, `stop_loss_atr_multiplier: float = 2.0`, `max_open_positions: int = 1`
- **Indicators:** `ExponentialMovingAverage`, `AverageTrueRange`
- **Signal Logic:** Generates buy signals when ADX is above the threshold indicating a strong trend, and filters entries using EMA alignment. Uses ATR to dynamically calculate stop-loss levels.

#### 2. `DynamicBreakoutStrategy` (`dynamic_breakout.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `lookback_period: int = 20`, `breakout_multiplier: float = 1.5`, `max_open_positions: int = 1`
- **Indicators:** None (Price action based)
- **Signal Logic:** Tracks rolling high/low over a lookback period and enters positions when current price breaks out beyond a multiplier of the recent range.

#### 3. `FlashCrashReversalStrategy` (`flash_crash_reversal.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `bb_period: int = 20`, `bb_std_dev: float = 2.5`, `rsi_period: int = 14`, `rsi_oversold: float = 25.0`, `max_open_positions: int = 1`
- **Indicators:** `BollingerBands`, `RelativeStrengthIndex`
- **Signal Logic:** Looks for extreme oversold conditions by requiring price to pierce the lower Bollinger Band concurrently with an RSI below the oversold threshold to catch rapid reversals.

#### 4. `MeanReversionStrategy` (`mean_reversion.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `lookback: int = 20`, `z_score_threshold: float = 2.0`, `max_open_positions: int = 1`
- **Indicators:** None
- **Signal Logic:** Calculates a rolling Z-Score of the closing price and takes mean-reversion trades when the score exceeds the configured threshold.

#### 5. `MomentumLSBaseStrategy` (`momentum_ls_base.py`)
- **Config fields:** N/A (Base Class)
- **Indicators:** None
- **Signal Logic:** Abstract base class that injects the `MomentumLSAllocator`. Subclasses must implement the actual entry logic. The base handles overriding `_compute_quantity()` by querying the allocator for the assigned USD amount.

#### 6. `MomentumLSSmaStrategy` (`momentum_ls_sma.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `sma_period: int = 5`, `max_open_positions: int = 1`
- **Indicators:** `SimpleMovingAverage`
- **Signal Logic:** A proof-of-concept subclass of `MomentumLSBaseStrategy`. It implements a rudimentary single SMA crossover to generate signals for tournament integration testing.

#### 7. `SmaCrossoverStrategy` (`sma_crossover.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `fast_sma: int = 10`, `slow_sma: int = 30`, `max_open_positions: int = 1`
- **Indicators:** `SimpleMovingAverage`
- **Signal Logic:** Classic dual moving average crossover. Generates buy signals when the fast SMA crosses above the slow SMA, and sell signals on a cross below.

#### 8. `ComboTrendVwapStrategy` (`tesla_combo_strategy.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `fast_ema: int = 9`, `slow_ema: int = 21`, `bb_period: int = 20`, `bb_std: float = 2.0`, `atr_period: int = 14`, `max_open_positions: int = 1`
- **Indicators:** `SimpleMovingAverage`, `ExponentialMovingAverage`, `BollingerBands`, `AverageTrueRange`
- **Signal Logic:** A highly specific combination strategy originally tuned for Tesla. It demands alignment across multiple timeframes and bands before confirming a trend continuation entry.

#### 9. `TrendPullbackStrategy` (`trend_pullback.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `ema_trend_period: int = 50`, `rsi_period: int = 14`, `rsi_pullback_level: float = 40.0`, `max_open_positions: int = 1`
- **Indicators:** `ExponentialMovingAverage`, `RelativeStrengthIndex`
- **Signal Logic:** Defines the primary trend using a slow EMA and waits for the RSI to dip to a pullback level (e.g., 40) before buying the dip in the direction of the macro trend.

#### 10. `VolatilityBreakoutPumpStrategy` (`volatility_breakout.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `bb_period: int = 20`, `bb_std_dev: float = 2.0`, `volume_multiplier: float = 2.5`, `max_open_positions: int = 1`
- **Indicators:** `BollingerBands`
- **Signal Logic:** Monitors Bollinger Band width to detect volatility compression (a "squeeze") and enters when price breaks the upper band accompanied by a volume spike.

#### 11. `VwapExhaustionStrategy` (`vwap_exhaustion.py`)
- **Config fields:** `instrument_id: str`, `bar_type: str`, `deviation_multiplier: float = 2.5`, `max_open_positions: int = 1`
- **Indicators:** None (Custom VWAP calculation internal to strategy)
- **Signal Logic:** Fades extreme moves by shorting or selling when the price deviates significantly far from the volume-weighted average price.

## 7. Configuration System

### `config/setups.py`

#### `ETORO_EXECUTION` — Global execution settings
```python
ETORO_EXECUTION = {
    "environment": "demo",   # "demo" | "real"
    "dry_run": True,         # Set False explicitly for live execution
    "enable_trailing_stop": True,
}
```

#### `ACTIVE_BOTS` — Per-bot configurations
```python
{
    "strategy_class": "SmaCrossoverStrategy",  # Must match STRATEGY_REGISTRY key in run_bot.py
    "etoro_id": "1111",                         # Must exist in instrument_map.py
    "symbol": "TSLA.ETORO",                     # Must match instrument_map.py value
    "bar_type": "TSLA.ETORO-1-MINUTE-MID-INTERNAL",
    "params": {
        "sma_period": 5                         # Strategy-specific params (spread into Config)
    },
    "trade_amount_usd": 100.0,
    "max_open_positions": 1,
}
```

#### `ETORO_API_TEST` — For dev_scripts/etoro_execution_test.py only
```python
ETORO_API_TEST = {
    "environment": "real",
    "dry_run": False,
    "symbol": "ADA.ETORO",
    "trade_amount_usd": 11.0,
    "test_account_id": "TEST_01"
}
```

### `run_bot.py` — `STRATEGY_REGISTRY`

When adding a new strategy, register it here:
```python
STRATEGY_REGISTRY = {
    "MyNewStrategy": ("strategies.my_new_strategy", "MyNewStrategy", "MyNewStrategyConfig"),
    # format: "RegistryKey": ("module.path", "StrategyClassName", "ConfigClassName")
}
```

The `strategy_id` is constructed as: `"{strategy_class}_{symbol}_{index}"`.

---

## 8. eToro API Reference

### WebSocket Protocol

**URL:** `wss://ws.etoro.com/ws`

**Authentication payload:**
```json
{
  "id": "<uuid4>",
  "operation": "Authenticate",
  "data": {
    "userKey": "YOUR_USER_KEY",
    "apiKey": "YOUR_API_KEY"
  }
}
```

**Subscribe payload:**
```json
{
  "id": "<uuid4>",
  "operation": "Subscribe",
  "data": {
    "topics": ["instrument:1111", "instrument:100000"],
    "snapshot": true
  }
}
```

**Incoming message envelope:**
```json
{
  "messages": [
    {
      "type": "Trading.Instrument.Rate",
      "topic": "instrument:1111",
      "content": "{\"Bid\": 123.45, \"Ask\": 123.50, \"Date\": \"2024-01-01T12:00:00Z\"}"
    }
  ]
}
```

Note: `content` is a **JSON string** in most cases, not an embedded object. Always parse it:
```python
content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
```

**Snapshot message** (`type == "Snapshot"`) contains additional fields:
- `IsMarketOpen`: `"true"` / `"false"` (string, not bool)
- `AllowBuy`: `"true"` / `"false"` (string, not bool)

### REST API

**Identity:**
- GET `https://public-api.etoro.com/api/v1/me` → `{gcid, realCid, demoCid}`

**Market data:**
- GET `https://public-api.etoro.com/api/v1/market-data/search?internalSymbolFull={SYMBOL}`

**PnL / Portfolio structure:**
```json
{
  "clientPortfolio": {
    "credits": 1000.0,
    "positions": [
      {
        "positionID": "12345",
        "instrumentID": 1111,
        "units": 1.0,
        "openRate": 200.0,
        "isBuy": true,
        "token": "<req_id>",
        "orderId": "<order_id>"
      }
    ],
    "ordersForOpen": [...],
    "mirrors": [...]
  }
}
```

**Important:** The real PnL endpoint wraps data in `clientPortfolio`. Always unwrap:
```python
data = await resp.json()
data = data.get("clientPortfolio", data)
```

### Rate Limits & Timing

- eToro can take **30–90 seconds** for a new position to appear in the PnL endpoint.
- Market open orders typically settle in 5–30 seconds.
- Limit order cancel via DELETE may fail with HTTP 400 if the stored ID is still a UUID token (before WS delivers the real orderId). The `_cancel_order_async` method handles this by falling back to a PnL lookup.
- HTTP 502/504 on order submission: treat as unknown — start polling, don't reject.
- HTTP 404 on close: position already closed — emit `order_canceled`.

---

## 9. Order Lifecycle & Reconciliation

### Market Open Order

```
Strategy.submit_order()
    → EToroExecutionClient._submit_order_async()
        → generate_order_submitted()
        → rate_limiter.acquire("OPEN")    [if False: generate_order_rejected()]
        → POST /market-open-orders/by-amount
            → 2xx: state.set(coid, new_pos_id)
                    generate_order_accepted()
                    _poll_for_fill() task started
            → 502/504: _poll_for_fill() task started (best effort)
            → other: generate_order_rejected()
        → WebSocket: trading.position.opened → generate_order_filled()
        → OR polling: _reconcile_via_pnl() → generate_order_filled()
```

### Close Position Order

```
Strategy.submit_order(SELL when LONG position exists)
    → EToroExecutionClient._submit_order_async()
        → detected as close (opposite side, open position exists)
        → pos_id = state.get(opening_order_id)
        → POST /market-close-orders/positions/{pos_id}
            → 2xx: generate_order_accepted()
                    _poll_for_fill(is_close=True) task started
            → 404: generate_order_canceled() (already closed)
```

### Limit Order

```
→ POST /limit-orders
    → 2xx: generate_order_accepted()
           WS will later deliver real orderId (updates state via _process_ws_message)
→ Strategy cancels: cancel_order()
    → DELETE /limit-orders/{stored_id}
        → 400: launch background task to retry with real orderId from PnL lookup via Rate-Matching (rate + instrumentID + isBuy)
        → always: generate_order_canceled() (optimistic, unblocks strategy immediately)
```

### `_query_order()` — Nautilus Inflight Reconciliation

Called by Nautilus automatically for orders in `INITIALIZED`, `SUBMITTED`, or `ACCEPTED` state. Triggers `_reconcile_via_pnl()`. This is the passive reconciliation path — it complements active polling.

---

## 10. Adding New Instruments

**Step 1:** Find the eToro numeric ID:
```bash
# Edit dev_scripts/get_instruments_id.py, add symbol to SYMBOLS_TO_LOOKUP
python3 dev_scripts/get_instruments_id.py
```

**Step 2:** Add to `adapters/instrument_map.py`:
```python
ETORO_INSTRUMENTS = {
    # ... existing entries ...
    "NEW_ID": "NEWSYM.ETORO",
}
```

**Step 3:** If the symbol is a cryptocurrency, add it to `_CRYPTO_SYMBOLS` in `adapters/etoro_data.py`:
```python
_CRYPTO_SYMBOLS: frozenset[str] = frozenset({
    "BTC", "ETH", ..., "NEWSYM",
})
```

**Step 4:** Set correct price precision in `_register_instruments()` in `etoro_data.py` if the default rules don't apply (e.g. a new meme coin with 8 decimal places that doesn't contain "SHIB" or "PEPE").

**Step 5:** Add a bot configuration in `config/setups.py`:
```python
{
    "strategy_class": "SmaCrossoverStrategy",
    "etoro_id": "NEW_ID",
    "symbol": "NEWSYM.ETORO",
    "bar_type": "NEWSYM.ETORO-1-MINUTE-MID-INTERNAL",
    "params": {"sma_period": 5},
    "trade_amount_usd": 100.0,
    "max_open_positions": 1,
}
```

**Validation checklist:**
- [ ] `etoro_id` key in `ETORO_INSTRUMENTS` == `etoro_id` in bot config
- [ ] `symbol` value in `ETORO_INSTRUMENTS` == `symbol` in bot config
- [ ] `bar_type` starts with the same `symbol`
- [ ] No typos in symbol (eToro uses `SHIBxM` not `SHIB`, `PEPExM` not `PEPE`)

---

## 11. Adding New Strategies

**Step 1:** Create `strategies/my_new_strategy.py` following the pattern in Section 6.

Required elements:
- `MyNewStrategyConfig(StrategyConfig, frozen=True)` with at minimum: `instrument_id`, `bar_type`, `trade_amount_usd`, `max_open_positions`
- `MyNewStrategy(Strategy)` with all lifecycle methods
- `_compute_quantity()`, `_on_buy_signal()`, `_on_sell_signal()`, `_close_position()` helpers

**Step 2:** Register in `run_bot.py`:
```python
STRATEGY_REGISTRY = {
    # ... existing ...
    "MyNewStrategy": ("strategies.my_new_strategy", "MyNewStrategy", "MyNewStrategyConfig"),
}
```

**Step 3:** Add to `config/setups.py` `ACTIVE_BOTS`:
```python
{
    "strategy_class": "MyNewStrategy",
    "etoro_id": "1111",
    "symbol": "TSLA.ETORO",
    "bar_type": "TSLA.ETORO-1-MINUTE-MID-INTERNAL",
    "params": {
        "my_param": 42
    },
    "trade_amount_usd": 100.0,
    "max_open_positions": 1,
}
```

**Rules:**
- `params` dict is spread as `**kwargs` into the config constructor. Every key in `params` must correspond to a field in the Config class.
- `strategy_id`, `instrument_id`, `bar_type`, `trade_amount_usd`, `max_open_positions` are injected by `run_bot.py` — do NOT put them in `params`.
- All config fields must have default values or be set via `params` / the bot spec.

---

## 12. Safety & Risk Controls

### Live Trading Safety Interlock

**To enable real trading**, ALL of the following must be true:
1. `ETORO_EXECUTION["environment"] == "real"` in `config/setups.py`
2. `ETORO_EXECUTION["dry_run"] == False` in `config/setups.py`
3. `ETORO_CONFIRM_LIVE=1` set in `.env`

If any condition is missing, `run_bot.py` logs a `CRITICAL` error and exits with code 1.

**Never modify `_check_live_safety_interlock()` to weaken these checks.**

### Dry Run Mode

When `dry_run=True`:
- No REST API calls are made for order execution.
- `generate_order_accepted()` is called immediately with a fake UUID.
- Market orders get an immediate `generate_order_filled()` with `last_px=1.0`.
- Limit orders are accepted but never filled.
- Balance is reported as `Money(0, USD)`.

### `os._exit(1)` Convention

Used (not `sys.exit()`) in:
- `EToroDataClient._connect()` after all retry attempts fail
- `EToroDataClient._message_loop()` on any WebSocket disconnect/error
- `EToroExecutionClient._connect_ws()` after all retry attempts fail
- `EToroExecutionClient._ws_message_loop()` on any WebSocket error

This is intentional: `os._exit(1)` bypasses Python cleanup and immediately terminates. systemd's `Restart=always` policy then restarts the process. Do NOT replace these with graceful reconnection logic without also updating the systemd service configuration.

### Position Size Limits

The current system uses `max_open_positions=1` per strategy per instrument. This is enforced inside each strategy via:
```python
if len(self.cache.positions_open()) >= self.config.max_open_positions:
    return
```

Note: `cache.positions_open()` (no instrument filter) counts across ALL instruments. `cache.positions_open(instrument_id=...)` counts only for that instrument. The current strategies use the unfiltered version for the global cap — be aware of this when running many bots in parallel.

---

## 13. Error Handling Conventions

### In Adapters

- WebSocket errors → `self._log.error(...)` + `os._exit(1)`
- REST HTTP 5xx → start `_poll_for_fill()`, do not reject order
- REST HTTP 4xx (non-404) → `generate_order_rejected()` with reason string
- REST HTTP 404 on close → `generate_order_canceled()` (position gone)
- REST timeout → start `_poll_for_fill()`, do not reject order
- PnL fetch failure → log warning, return `Money(0, USD)` or `False`

### In Strategies

- Instrument not in cache → log error, return `None` from `_compute_quantity()`
- Always guard `if qty is None: return` after `_compute_quantity()`
- Never `raise` in `on_bar()` or `on_quote_tick()` — exceptions terminate the strategy

### In `_process_message()` (DataClient)

Wrapped in `try/except Exception as e: self._log.error(...)`. The message loop continues after a malformed message — a single bad tick should not crash the data stream.

---

## 14. Testing & Development Scripts

All dev scripts are in `dev_scripts/` and load credentials from `.env` automatically via `python-dotenv`.


| Script | Purpose | Sends Real Orders? |
|--------|---------|--------------------|
| `auto_map_insturments.py` | Auto-generates mapping of InstrumentID to Symbol. | No |
| `compact_parquet.py` | Utility to compact Parquet files. | No |
| `etoro_api_probe.py` | Probes specific eToro endpoints for limits/behavior. | No |
| `etoro_api_probe_all.py` | Probes multiple eToro endpoints. | No |
| `etoro_balance.py` | Fetches account balance. | No |
| `etoro_close_orphans.py` | Cleans up stray open positions. | Yes (Close only) |
| `etoro_connectivity_test.py` | Tests WS & REST connectivity without placing orders. | No |
| `etoro_deploy_agent_portfolio.py` | Deployment script for portfolios. | Conditional |
| `etoro_execution_test.py` | Runs `EToroExecutionClient` in a test mode. | Conditional |
| `etoro_execution_tests_all_orders.py` | Comprehensive order testing script. | Yes |
| `etoro_fetch_history.py` | Fetches historical data. | No |
| `etoro_tesla_tracker.py` | Example script tracking Tesla. | No |
| `get_instruments_id.py` | Looks up instrument IDs based on symbols. | No |
| `momentum_ls_fetch_candles.py` | Fetches candle data for Momentum-LS. | No |
| `momentum_ls_fetch_candles_auto.py` | Automated candle data fetcher for Momentum-LS. | No |
| `momentum_ls_run.py` | Live trading orchestrator for Momentum-LS. | Yes |
| `momentum_ls_simulator.py` | Simulator for Momentum-LS strategies. | No |
| `momentum_ls_tournament.py` | Runs tournament selection for Momentum-LS. | No |
| `momentum_ls_universe.py` | Fetches and filters the Momentum-LS universe. | No |
| `read_parquet.py` | Utility to read Parquet files. | No |
| `test_nautilus.py` | Test for loading the Nautilus environment. | No |

### Running an Execution Test

```bash
# Always confirm interactively (requires typing "j")
python3 dev_scripts/etoro_execution_test.py

# The script uses ETORO_API_TEST config from config/setups.py
# Make sure environment and dry_run are set correctly before running
```

### Emergency Cleanup

`etoro_execution_tests_all_orders.py` includes an `emergency_cleanup()` function that closes all open positions and limit orders for the test instrument. It is always called at the end of the test, regardless of success or failure. Use it as a reference for manual cleanup if needed.

---

## 15. Environment Setup

### `.env` file (never commit)

```env
ETORO_API_KEY=your_api_key_here
ETORO_USER_KEY=your_user_key_here
ETORO_CONFIRM_LIVE=1   # Only add this line when intentionally enabling live trading
```

### Python Requirements

- Python 3.10+ required
- Key packages: `nautilus_trader`, `websockets`, `aiohttp`, `python-dotenv`, `pandas`, `pyarrow`

### Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Directory Structure (auto-created at runtime)

```
data/
├── state/
│   └── execution_mapping.json   # Created by StateManager
└── nautilus/                    # Created by run_catalog.py
    └── nautilus_data/
        └── quote_tick/

logs/
└── bot_YYYY-MM-DD_HH-MM-SS.log  # Created by run_bot.py
```

---

## 16. Common Pitfalls & Known Issues

### 1. PnL Envelope Unwrapping
The real PnL endpoint wraps data in `clientPortfolio`. The demo endpoint may or may not. Always unwrap:
```python
data = data.get("clientPortfolio", data)
```
Forgetting this causes all position/balance lookups to return empty lists.

### 2. Limit Order ID vs. Token
After placing a limit order, the stored ID in `_StateManager` is initially the `x-request-id` UUID (a token), not the real numeric `orderId`. The real orderId arrives asynchronously via WebSocket (`trading.order.accepted`). Until that arrives, DELETE requests to cancel the limit order will return HTTP 400. The cancel handler deals with this by launching a background task (`_background_cancel_limit()`) that uses Rate-Matching (rate + instrumentID + isBuy) to find the real orderID via the PnL endpoint. It waits in stepped delays (`2s → 3s → 5s`, total 10s) until eToro registers the order in the PnL. This ensures the strategy is unblocked immediately while the cancellation happens asynchronously.

### 3. `content` as JSON String
eToro's WebSocket sends `content` as a JSON-encoded string, not an embedded object. Always check and parse:
```python
if isinstance(content_raw, str):
    content = json.loads(content_raw)
```

### 4. `IsMarketOpen` and `AllowBuy` are Strings
In Snapshot messages, these fields are `"true"` / `"false"` strings, not Python booleans. Always compare with the string: `content.get("IsMarketOpen") == "true"`.

### 5. Size Precision is Always 0
All instruments are registered with `size_precision=0`. `instrument.make_qty(units)` will floor/round to integer. This is correct for eToro — never change this to a non-zero value without verifying eToro API behavior for fractional units.

### 6. `cache.positions_open()` Counts All Instruments
The unfiltered `self.cache.positions_open()` call counts across all instruments. If running 28 bots each with `max_open_positions=1`, the global count limit will be hit immediately after the first position is opened. Consider whether strategies should use the instrument-filtered version for the cap check.

### 7. WebSocket Reconnection is Intentionally Absent
The system uses `os._exit(1)` + systemd restart instead of in-process reconnection. Do not add `asyncio` reconnection loops to the WebSocket clients — it conflicts with Nautilus's task management and has caused subtle state corruption issues.

### 8. Emergency State After Crash
If `run_bot.py` crashes while orders are in-flight, `data/state/execution_mapping.json` will still contain the last known order→position mappings. On restart, `StateManager.load()` reads this file, allowing `_poll_for_fill()` and `_query_order()` to reconcile the state with eToro's PnL endpoint.

### 9. `etoro_execution.py` vs `etoro_execution.py.orig`
The `.orig` file is the previous version kept for reference. It has known issues (no Real PnL envelope unwrapping, shorter poll timeout, simpler limit cancel logic). Always edit `etoro_execution.py`, not the `.orig` file. Do not delete `.orig` without confirmation.

### 10. `etoro_config.py` Import in Test Scripts
Some dev scripts import `EToroExecClientConfig` and `EToroLiveExecClientFactory` from `adapters.etoro_config`, while `run_bot.py` imports them from `adapters.etoro_execution`. Both are valid paths — `etoro_config.py` re-exports these classes for historical reasons. Keep both import paths working.

---

## 17. Code Style & Conventions

### Language
- All **code** is in English.
- All **log messages** are in German (following the existing convention).
- **Comments** are in German in `adapters/` and `strategies/`, but English is acceptable in `dev_scripts/`.

### Logging
```python
self._log.info("Nachricht", LogColor.GREEN)    # Success / info
self._log.warning("Nachricht", LogColor.YELLOW) # Recoverable issue
self._log.error("Nachricht", LogColor.RED)      # Error requiring action
self._log.debug("Nachricht", LogColor.CYAN)     # Verbose debug info
```

### Type Hints
- Use Python 3.10+ union syntax: `str | None` (not `Optional[str]`)
- All public methods in adapters must have return type annotations
- Strategy methods do not require return type annotations (Nautilus convention)

### Async Conventions
- All adapter methods that call I/O are `async def`
- Use `asyncio.wait_for(..., timeout=...)` for all external calls
- Use `asyncio.sleep()` for delays, never `time.sleep()`
- Use `with suppress(asyncio.CancelledError)` when awaiting tasks that may be cancelled

### Nautilus Task Creation
```python
# Correct: creates a managed Nautilus task
self.create_task(self._my_coroutine(), log_msg="task_name")

# Incorrect: creates an unmanaged asyncio task
asyncio.create_task(self._my_coroutine())
```

Use `self.create_task()` inside adapter classes so Nautilus can track and cancel tasks properly.

### Configuration Classes
```python
class MyConfig(StrategyConfig, frozen=True, kw_only=True):
    # frozen=True is REQUIRED for StrategyConfig subclasses
    # kw_only=True is required for LiveExecClientConfig subclasses
```

---

## 18. Changelog (Agent-Maintained)

> **Instructions for Jules:** When you make changes to this repository, append an entry to this section. Include the date, a brief description of what changed, and which files were modified. This log helps track what changes have been applied automatically.

| Date | Change | Files Modified |
|------|--------|----------------|
| 2026-05-17 | Documentation audit: full repository sync, all sections verified | `AGENTS.md` |
| 2026-05-17 | Overhauled manuals/ directory: updated deployment.md, backtesting_manual.md, new_tickers.md, momentum_ls.md, feature_automation_LS.md and added TESTING.md | `manuals/*` |
| 2026-05-14 | Added `momentum_ls_run.py` live orchestrator that combines universe, allocator, and tournament JSONs to launch safe live nodes. Included 24h stale-universe check and identical safety interlocks | `dev_scripts/momentum_ls_run.py`, `AGENTS.md` |
| 2026-05-14 | Added `SL:<pct>` tag convention in `_build_market_open_payload()`; backward-compatible, existing bots unaffected | `adapters/etoro_execution.py`, `dev_scripts/etoro_execution_tests_all_orders.py`, `AGENTS.md` |
| 2026-05-14 | Added `MomentumLSAllocator`, `MomentumLSBaseStrategy` and `MomentumLSSmaStrategy` to implement no-interference rule and dynamic capital sizing. | `adapters/momentum_ls_allocator.py`, `strategies/momentum_ls_base.py`, `strategies/momentum_ls_sma.py`, `AGENTS.md` |
| 2026-05-14 | Added `momentum_ls_simulator.py` and `momentum_ls_tournament.py` for backtesting tournament tracking Sortino, Calmar and PF logic over QuoteTicks | `dev_scripts/momentum_ls_simulator.py`, `dev_scripts/momentum_ls_tournament.py`, `AGENTS.md` |
| 2026-05-14 | Added `momentum_ls_fetch_candles.py` for fetching OHLCV data as a Parquet fallback mechanism matching exact quote tick schema dtypes | `dev_scripts/momentum_ls_fetch_candles.py`, `AGENTS.md` |
| 2026-05-14 | Added `momentum_ls_universe.py` to fetch eToro Smart Portfolio universe for Momentum-LS strategies | `dev_scripts/momentum_ls_universe.py`, `AGENTS.md` |
| 2026-05-14 | Refactored limit order rate-matching cancel to run as a background task to prevent strategy blocking during the 3-5s eToro PnL delay | `adapters/etoro_execution.py`, `AGENTS.md` |
| 2026-05-14 | Updated limit order cancellation logic to use Rate-Matching (rate + instrumentID + isBuy) as eToro PnL entryOrders lack correlation tokens | `adapters/etoro_execution.py`, `AGENTS.md` |
| 2026-05-14 | Fixed JULES_SYSTEM_PROMPT.md to state that InstrumentID is required in close position payload | `.agents/JULES_SYSTEM_PROMPT.md` |
| *(initial)* | AGENTS.md created from full repository analysis | `AGENTS.md` |

---

*Last updated: 2026-05-17. Update this date and the changelog above whenever you modify this file.*
