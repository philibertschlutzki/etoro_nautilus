from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

from nautilus_trader.common.enums import LogColor


class _StateManager:
    """Atomic-write JSON persistence for ClientOrderId → eToro positionId mapping.

    Performance characteristics (post-refactor):
    - get_all()        : O(1) — returns a shallow copy of the in-memory dict
    - set() / delete() : O(1) in-memory; disk write is debounced 500 ms
    - COID resolution  : O(1) via two pre-computed reverse-lookup caches

    Reverse-lookup caches (RAM-only, rebuilt on load, never persisted):
    - _req_id_to_coid  : uuid5(coid) → coid  (token matching)
    - _venue_id_to_coid: stored_id   → coid  (positionId / orderId matching)
    """

    def __init__(self, state_path: str) -> None:
        self._path = Path(state_path)
        self._mapping: dict[str, str] = {}
        self._lock = asyncio.Lock()

        # O(1) reverse-lookup caches — in-memory only, rebuilt from _mapping on load
        self._req_id_to_coid: dict[str, str] = {}   # uuid5(coid) → coid
        self._venue_id_to_coid: dict[str, str] = {}  # venue pos/ord-id → coid

        # Deferred flush handle (debounced 500 ms)
        self._flush_task: asyncio.Task[None] | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def load(self, warn_fn=None) -> None:
        """Load mapping from disk; start with empty dict on any failure.
        Rebuilds both O(1) caches after loading.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                data = self._path.read_text(encoding="utf-8")
                loaded = json.loads(data)
                if isinstance(loaded, dict):
                    self._mapping = {str(k): str(v) for k, v in loaded.items()}
                else:
                    self._mapping = {}
            except Exception as exc:
                if warn_fn is not None:
                    warn_fn(
                        f"State load failed ({exc}); starting with empty mapping.",
                        LogColor.YELLOW,
                    )
                self._mapping = {}
        else:
            self._mapping = {}

        # Rebuild reverse-lookup caches from the loaded mapping
        self._req_id_to_coid.clear()
        self._venue_id_to_coid.clear()
        for coid, venue_id in self._mapping.items():
            self._req_id_to_coid[str(uuid.uuid5(uuid.NAMESPACE_OID, coid))] = coid
            self._venue_id_to_coid[venue_id] = coid

    # ── Public API ────────────────────────────────────────────────────────────

    async def get(self, client_order_id: str) -> str | None:
        async with self._lock:
            return self._mapping.get(client_order_id)

    async def set(self, client_order_id: str, position_id: str) -> None:
        async with self._lock:
            # Remove stale venue_id entry if the mapped value is changing
            old_val = self._mapping.get(client_order_id)
            if old_val is not None and old_val != position_id:
                self._venue_id_to_coid.pop(old_val, None)

            self._mapping[client_order_id] = position_id

            # Maintain O(1) reverse-lookup caches
            req_id = str(uuid.uuid5(uuid.NAMESPACE_OID, client_order_id))
            self._req_id_to_coid[req_id] = client_order_id
            self._venue_id_to_coid[position_id] = client_order_id

        self._schedule_flush()

    async def delete(self, client_order_id: str) -> None:
        async with self._lock:
            val = self._mapping.pop(client_order_id, None)
            if val is not None:
                self._venue_id_to_coid.pop(val, None)
            req_id = str(uuid.uuid5(uuid.NAMESPACE_OID, client_order_id))
            self._req_id_to_coid.pop(req_id, None)

        self._schedule_flush()

    def get_all(self) -> dict[str, str]:
        """Shallow copy of the current mapping (no lock needed — single event loop)."""
        return dict(self._mapping)

    # ── Deferred Flush ────────────────────────────────────────────────────────

    def _schedule_flush(self) -> None:
        """Debounced disk write: cancel any pending flush and start a fresh 500 ms timer.

        Multiple set()/delete() calls within 500 ms result in a single write,
        eliminating the 2-3 sequential os.replace() calls per order fill.
        """
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()

        try:
            loop = asyncio.get_running_loop()
            self._flush_task = loop.create_task(
                self._deferred_persist(), name="state_flush"
            )
        except RuntimeError:
            # No running event loop (unit-test context) — persist synchronously
            import asyncio as _asyncio
            _asyncio.get_event_loop().run_until_complete(self._persist_now())

    async def _deferred_persist(self) -> None:
        try:
            await asyncio.sleep(0.5)
            async with self._lock:
                await self._persist()
        except asyncio.CancelledError:
            pass  # A newer flush was scheduled; this one is intentionally dropped

    async def _persist_now(self) -> None:
        """Force-flush without debounce (used on clean shutdown or unit tests)."""
        async with self._lock:
            await self._persist()

    async def _persist(self) -> None:
        """Atomic write: write to .tmp then os.replace() to avoid partial reads."""
        tmp = str(self._path) + ".tmp"
        data = json.dumps(self._mapping, indent=2)
        Path(tmp).write_text(data, encoding="utf-8")
        os.replace(tmp, str(self._path))