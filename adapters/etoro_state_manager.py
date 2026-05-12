from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from nautilus_trader.common.enums import LogColor

class _StateManager:
    """Atomic-write JSON persistence for ClientOrderId → eToro positionId mapping."""

    def __init__(self, state_path: str) -> None:
        self._path = Path(state_path)
        self._mapping: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def load(self, warn_fn: object = None) -> None:
        """Load mapping from disk; start with empty dict on any failure."""
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

    async def get(self, client_order_id: str) -> str | None:
        async with self._lock:
            return self._mapping.get(client_order_id)

    async def set(self, client_order_id: str, position_id: str) -> None:
        async with self._lock:
            self._mapping[client_order_id] = position_id
            await self._persist()

    async def delete(self, client_order_id: str) -> None:
        async with self._lock:
            self._mapping.pop(client_order_id, None)
            await self._persist()

    async def _persist(self) -> None:
        tmp = str(self._path) + ".tmp"
        data = json.dumps(self._mapping, indent=2)
        Path(tmp).write_text(data, encoding="utf-8")
        os.replace(tmp, str(self._path))

    def get_all(self) -> dict[str, str]:
        return dict(self._mapping)
