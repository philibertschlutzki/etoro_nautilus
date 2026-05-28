"""Tests for adapters/etoro_execution.py.

Covers: _RateLimiter, _StateManager, dry-run end-to-end.
Run with: pytest tests/ -q
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from archive.adapters.etoro_execution import (
    EToroExecutionClient,
    _RateLimiter,
    _StateManager,
)
from nautilus_trader.model.enums import OrderSide, OrderType
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Quantity


# ── Rate Limiter ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_starts_at_capacity() -> None:
    rl = _RateLimiter(capacity=20)
    assert rl.tokens == 20


@pytest.mark.asyncio
async def test_rate_limiter_open_consumes_token() -> None:
    rl = _RateLimiter(capacity=5)
    result = await rl.acquire("OPEN")
    assert result is True
    assert rl.tokens == 4


@pytest.mark.asyncio
async def test_rate_limiter_open_rejected_at_zero() -> None:
    rl = _RateLimiter(capacity=3)
    for _ in range(3):
        assert await rl.acquire("OPEN") is True
    assert rl.tokens == 0
    # OPEN must fail immediately when empty
    result = await rl.acquire("OPEN")
    assert result is False
    assert rl.tokens == 0


@pytest.mark.asyncio
async def test_rate_limiter_refill_adds_two_tokens() -> None:
    """After two ticks (simulating 6 s), exactly 2 tokens are added."""
    rl = _RateLimiter(capacity=20)
    # Exhaust all tokens
    for _ in range(20):
        assert await rl.acquire("OPEN") is True
    assert rl.tokens == 0

    # Two refill ticks (each adds 1 token)
    await rl._tick()
    assert rl.tokens == 1
    await rl._tick()
    assert rl.tokens == 2


@pytest.mark.asyncio
async def test_rate_limiter_refill_does_not_exceed_capacity() -> None:
    rl = _RateLimiter(capacity=5)
    # Already at capacity; ticking should not go above 5
    await rl._tick()
    assert rl.tokens == 5


@pytest.mark.asyncio
async def test_rate_limiter_close_queued_and_completed() -> None:
    """25 CLOSE requests submitted in a burst all complete; none are lost."""
    rl = _RateLimiter(capacity=20)
    completed: list[bool] = []

    async def close_req() -> None:
        r = await rl.acquire("CLOSE")
        completed.append(r)

    tasks = [asyncio.create_task(close_req()) for _ in range(25)]

    # Yield enough times for the first 20 tasks (with tokens available) to finish
    for _ in range(40):
        await asyncio.sleep(0)

    # 5 ticks to drain the queued close requests (only 20 tokens existed)
    for _ in range(5):
        await rl._tick()
        await asyncio.sleep(0)

    await asyncio.gather(*tasks)
    assert len(completed) == 25
    assert all(r is True for r in completed)


@pytest.mark.asyncio
async def test_rate_limiter_close_queued_while_open_fails() -> None:
    """When capacity is 0, OPEN returns False but CLOSE queues."""
    rl = _RateLimiter(capacity=1)
    await rl.acquire("OPEN")  # drain the single token
    assert rl.tokens == 0

    # OPEN should fail immediately
    assert await rl.acquire("OPEN") is False

    # CLOSE should eventually succeed after a refill
    close_task = asyncio.create_task(rl.acquire("CLOSE"))
    await asyncio.sleep(0)  # task is now waiting on the future
    assert not close_task.done()

    await rl._tick()  # refill 1 token + drain queue
    await asyncio.sleep(0)

    assert close_task.done()
    assert close_task.result() is True


# ── State Manager ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_manager_round_trip(tmp_path: Path) -> None:
    state_file = str(tmp_path / "mapping.json")
    sm = _StateManager(state_file)
    await sm.load()

    await sm.set("O-001", "pos-abc")
    await sm.set("O-002", "pos-def")

    # Re-load from the same file
    sm2 = _StateManager(state_file)
    await sm2.load()

    assert sm2.get_all() == {"O-001": "pos-abc", "O-002": "pos-def"}


@pytest.mark.asyncio
async def test_state_manager_get_and_delete(tmp_path: Path) -> None:
    sm = _StateManager(str(tmp_path / "mapping.json"))
    await sm.load()

    await sm.set("O-001", "pos-111")
    assert await sm.get("O-001") == "pos-111"
    assert await sm.get("O-999") is None

    await sm.delete("O-001")
    assert await sm.get("O-001") is None


@pytest.mark.asyncio
async def test_state_manager_missing_file_starts_empty(tmp_path: Path) -> None:
    sm = _StateManager(str(tmp_path / "nonexistent" / "mapping.json"))
    await sm.load()
    assert sm.get_all() == {}


@pytest.mark.asyncio
async def test_state_manager_atomic_write_failure_leaves_file_intact(
    tmp_path: Path,
) -> None:
    """If os.replace raises, the live file must be unchanged."""
    state_file = tmp_path / "mapping.json"
    sm = _StateManager(str(state_file))
    await sm.load()
    await sm.set("O-001", "pos-before")

    original_content = state_file.read_text(encoding="utf-8")

    with patch("os.replace", side_effect=OSError("disk full")):
        try:
            await sm.set("O-002", "pos-new")
        except OSError:
            pass

    assert state_file.read_text(encoding="utf-8") == original_content


@pytest.mark.asyncio
async def test_state_manager_concurrent_writes_no_loss(tmp_path: Path) -> None:
    """100 concurrent set() calls must all be persisted with no corruption."""
    sm = _StateManager(str(tmp_path / "mapping.json"))
    await sm.load()

    async def writer(i: int) -> None:
        await sm.set(f"order_{i}", f"pos_{i}")

    await asyncio.gather(*[writer(i) for i in range(100)])

    mapping = sm.get_all()
    assert len(mapping) == 100
    for i in range(100):
        assert mapping[f"order_{i}"] == f"pos_{i}"

    # Verify the file also reflects all entries
    sm2 = _StateManager(str(tmp_path / "mapping.json"))
    await sm2.load()
    assert len(sm2.get_all()) == 100


# ── Dry-run end-to-end ────────────────────────────────────────────────────────


def _make_mock_order(
    coid: str = "O-DRY-001",
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
) -> MagicMock:
    order = MagicMock()
    order.client_order_id = ClientOrderId(coid)
    order.strategy_id = MagicMock()
    order.instrument_id = InstrumentId.from_str("TSLA.ETORO")
    order.side = side
    order.order_type = order_type
    order.quantity = Quantity(1.0, precision=0)
    # Limit price for limit orders
    order.price = MagicMock()
    order.price.__float__ = lambda self: 200.0
    return order


class _StubClient:
    """Minimal stub that borrows methods from EToroExecutionClient for unit tests.

    Avoids constructing the Cython base class (LiveExecutionClient.__new__ is
    unsafe to call without the full Nautilus runtime).
    """

    # Borrow async methods as unbound functions — Python will bind them correctly
    _submit_order_async = EToroExecutionClient._submit_order_async  # type: ignore[attr-defined]
    _build_payload = EToroExecutionClient._build_close_payload  # stub for dry run tests
    _build_limit_payload = EToroExecutionClient._build_limit_payload  # stub for dry run tests
    _build_market_open_payload = EToroExecutionClient._build_market_open_payload  # stub for dry run tests
    _order_req_id = staticmethod(EToroExecutionClient._order_req_id)
    _handle_dry_run = EToroExecutionClient._submit_order_async  # dry run logic was moved to submit_order_async

    def __init__(self) -> None:
        self._dry_run = True
        self._rest_base = "https://fake.etoro.com"
        self._pnl_base = "https://fake.etoro.com/pnl"
        self._instrument_to_etoro = {"TSLA.ETORO": "1111"}
        self._enable_trailing_stop = False
        self._session: object = None

        clock = MagicMock()
        clock.timestamp_ns.return_value = 1_000_000_000
        self._clock = clock

        log = MagicMock()
        self._log = log

        cache = MagicMock()
        cache.quote_tick.return_value = None
        instrument = MagicMock()
        instrument.price_precision = 2
        cache.instrument.return_value = instrument
        self._cache = cache

    # No-op stubs for generate_* (overridden per test)
    def generate_order_accepted(self, *args, **kwargs) -> None: ...
    def generate_order_filled(self, *args, **kwargs) -> None: ...
    def generate_order_submitted(self, *args, **kwargs) -> None: ...
    def generate_order_rejected(self, *args, **kwargs) -> None: ...


def _make_minimal_client() -> _StubClient:
    return _StubClient()


@pytest.mark.asyncio
async def test_dry_run_handle_dry_run_emits_accepted_and_filled(
    tmp_path: Path,
) -> None:
    client = _make_minimal_client()
    state = _StateManager(str(tmp_path / "state.json"))
    await state.load()
    client._state = state
    rl = _RateLimiter(capacity=1)
    client._rate_limiter = rl

    accepted_calls: list = []
    filled_calls: list = []
    client.generate_order_accepted = lambda *a, **kw: accepted_calls.append((a, kw))
    client.generate_order_filled = lambda *a, **kw: filled_calls.append((a, kw))

    command = MagicMock()
    order = _make_mock_order()
    command.order = order
    await client._submit_order_async(command)

    assert len(accepted_calls) == 1, "Must emit exactly one accepted event"
    assert len(filled_calls) == 1, "Must emit exactly one filled event"
    assert await state.get("O-DRY-001") is not None


@pytest.mark.asyncio
async def test_dry_run_limit_order_no_fill(tmp_path: Path) -> None:
    """A limit order in dry-run should emit accepted but NOT filled."""
    client = _make_minimal_client()
    state = _StateManager(str(tmp_path / "state.json"))
    await state.load()
    client._state = state
    rl = _RateLimiter(capacity=1)
    client._rate_limiter = rl
    accepted_calls: list = []
    filled_calls: list = []
    client.generate_order_accepted = lambda *a, **kw: accepted_calls.append((a, kw))
    client.generate_order_filled = lambda *a, **kw: filled_calls.append((a, kw))

    command = MagicMock()
    order = _make_mock_order(order_type=OrderType.LIMIT)
    command.order = order
    await client._submit_order_async(command)

    assert len(accepted_calls) == 1
    assert len(filled_calls) == 0, "Limit order must not auto-fill in dry-run"


@pytest.mark.asyncio
async def test_dry_run_no_aiohttp_session_used(tmp_path: Path) -> None:
    """_handle_dry_run must not touch any HTTP session."""
    client = _make_minimal_client()
    state = _StateManager(str(tmp_path / "state.json"))
    await state.load()
    client._state = state
    rl = _RateLimiter(capacity=1)
    client._rate_limiter = rl
    client.generate_order_accepted = MagicMock()
    client.generate_order_filled = MagicMock()

    assert client._session is None

    with patch("aiohttp.ClientSession") as mock_session_cls:
        command = MagicMock()
        order = _make_mock_order()
        command.order = order
        await client._submit_order_async(command)
        mock_session_cls.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_deterministic_fake_position_id(tmp_path: Path) -> None:
    """Two calls with the same client_order_id produce the same fake positionId."""
    import uuid as _uuid

    coid = "O-STABLE-123"
    expected_pos_id = str(_uuid.uuid5(_uuid.NAMESPACE_OID, coid))

    client = _make_minimal_client()
    state = _StateManager(str(tmp_path / "state.json"))
    await state.load()
    client._state = state
    rl = _RateLimiter(capacity=1)
    client._rate_limiter = rl
    client.generate_order_accepted = MagicMock()
    client.generate_order_filled = MagicMock()

    command = MagicMock()
    order = _make_mock_order(coid=coid)
    command.order = order
    await client._submit_order_async(command)

    assert await state.get(coid) == expected_pos_id


@pytest.mark.asyncio
async def test_dry_run_submit_order_async_rate_limit_rejection(
    tmp_path: Path,
) -> None:
    """When rate-limiter is empty, OPEN order generates rejected event."""
    client = _make_minimal_client()
    state = _StateManager(str(tmp_path / "state.json"))
    await state.load()
    client._state = state

    rl = _RateLimiter(capacity=1)
    await rl.acquire("OPEN")  # exhaust the single token
    client._rate_limiter = rl

    submitted_calls: list = []
    rejected_calls: list = []
    client.generate_order_submitted = lambda *a, **kw: submitted_calls.append((a, kw))
    client.generate_order_rejected = lambda *a, **kw: rejected_calls.append((a, kw))
    client.generate_order_accepted = MagicMock()
    client.generate_order_filled = MagicMock()

    async def fake_classify(order):  # type: ignore[override]
        return False, None

    client._classify_order = fake_classify  # type: ignore[method-assign]

    command = MagicMock()
    command.order = _make_mock_order("O-REJECT-001")
    await client._submit_order_async(command)

    assert len(submitted_calls) == 1, "submitted must be emitted before rate-limit check"
    assert len(rejected_calls) == 1, "rejected must be emitted on rate-limit exhaustion"
    assert "rate_limit" in str(rejected_calls[0])
