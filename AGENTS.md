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

```
etoro_nautilus/
├── adapters/
│   ├── etoro_config.py          # Pydantic config classes for execution client
│   ├── etoro_data.py            # LiveMarketDataClient (WebSocket ticks)
│   ├── etoro_execution.py       # LiveExecutionClient (REST orders + WS fills)
│   ├── etoro_rate_limiter.py    # Async token-bucket rate limiter
│   ├── etoro_state_manager.py   # Persistent ClientOrderId → positionId mapping
│   └── instrument_map.py        # eToro numeric IDs → Nautilus InstrumentId strings
│
├── config/
│   └── setups.py                # ACTIVE_BOTS list + ETORO_EXECUTION settings
│
├── strategies/
│   ├── __init__.py
│   ├── sma_crossover.py         # Simple SMA crossover
│   ├── tesla_combo_strategy.py  # SMA + MACD + BB + VWAP combo
│   ├── vwap_exhaustion.py       # VWAP deviation + volume spike mean reversion
│   ├── dynamic_breakout.py      # Volume spike + price breakout
│   ├── adx_atr_momentum.py      # ADX trend strength + ATR trailing stop
│   ├── trend_pullback.py        # EMA-200 trend + RSI pullback entries
│   ├── mean_reversion.py        # Keltner channel mean reversion
│   ├── flash_crash_reversal.py  # Bollinger band crash + RSI oversold reversal
│   └── volatility_breakout.py   # Bollinger band upper breakout (pump rider)
│
├── dev_scripts/
│   ├── etoro_api_probe.py       # Tests REST endpoints for availability
│   ├── etoro_api_probe_all.py   # Full spec REST endpoint diagnostics
│   ├── etoro_balance.py         # Fetches account balance + positions
│   ├── etoro_deploy_agent_portfolio.py  # Creates agent portfolios
│   ├── etoro_execution_test.py  # Live ping-pong order test (buy + close)
│   ├── etoro_execution_tests_all_orders.py  # Tests limit + market + cancel flow
│   ├── etoro_show_portfolio.py  # Shows general portfolio
│   ├── etoro_tesla_tracker.py   # Raw WebSocket listener for debugging
│   ├── get_instruments_id.py    # Looks up eToro numeric IDs by symbol name
│   └── read_parquet.py          # Inspects recorded Parquet data files
│
├── manuals/                     # Human-readable operational guides
│   ├── deployment.md
│   ├── backtesting_manual.md
│   └── new_tickers.md
│
├── run_bot.py                   # Main entry point: live trading orchestrator
├── run_catalog.py               # Data recorder (runs separately)
├── run_backtest.py              # Backtesting runner
├── backtesting_config.json      # Backtest configuration
├── requirements.txt
└── .env                         # API keys (never commit this)
```

---

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
|--------|---------|-------------------|
| `get_instruments_id.py` | Look up eToro IDs for symbols | No |
| `etoro_api_probe.py` | Test REST endpoint availability | No |
| `etoro_api_probe_all.py` | Full spec REST diagnostics | No |
| `etoro_balance.py` | Show account balance and positions | No |
| `etoro_show_portfolio.py` | General portfolio view | No |
| `etoro_tesla_tracker.py` | Raw WebSocket listener | No |
| `read_parquet.py` | Inspect recorded data files | No |
| `etoro_deploy_agent_portfolio.py` | Create agent portfolios | **API write** |
| `etoro_execution_test.py` | Ping-pong: buy + close once | **YES** (if `dry_run=False`) |
| `etoro_execution_tests_all_orders.py` | Full order test + cleanup | **YES** (if `dry_run=False`) |

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
After placing a limit order, the stored ID in `_StateManager` is initially the `x-request-id` UUID (a token), not the real numeric `orderId`. The real orderId arrives asynchronously via WebSocket (`trading.order.accepted`). Until that arrives, DELETE requests to cancel the limit order will return HTTP 400. The cancel handler deals with this by launching a background task (`_background_cancel_limit()`) that uses Rate-Matching (rate + instrumentID + isBuy) to find the real orderID via the PnL endpoint once eToro registers the order (typically 3-5 seconds later). This ensures the strategy is unblocked immediately while the cancellation happens asynchronously.

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
| 2026-05-15 | Erweitert `_build_market_open_payload()` um `TP:<pct>`- und `TSL:1`-Tag-Unterstützung; TSL wird nur aktiviert wenn gleichzeitig SL:<pct> Tag gesetzt ist; `enable_trailing_stop`-Konstruktor-Flag bleibt gespeichert, beeinflusst aber nicht den Payload-Bau; SL-Warning für by-units-Pfad ergänzt | `adapters/etoro_execution.py`, `AGENTS.md` |
| 2026-05-15 | Neues Skript `etoro_execution_tests_advanced.py` für erweiterte API-Tests (Short-Eröffnung, SL, TP, TSL); Order-Matching per client_order_id; 180s Timeout; sys.path-Fix | `dev_scripts/etoro_execution_tests_advanced.py`, `AGENTS.md` |

---

*Last updated: 2026-05-15. Update this date and the changelog above whenever you modify this file.*
