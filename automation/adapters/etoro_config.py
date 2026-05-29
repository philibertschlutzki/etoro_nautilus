from __future__ import annotations

import asyncio
from typing import Literal, TYPE_CHECKING

from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.live.config import LiveExecClientConfig
from nautilus_trader.live.factories import LiveExecClientFactory

if TYPE_CHECKING:
    from automation.adapters.etoro_execution import EToroExecutionClient

class EToroExecClientConfig(LiveExecClientConfig, frozen=True, kw_only=True):
    api_key: str
    user_key: str
    environment: Literal["demo", "real"] = "demo"
    dry_run: bool = True
    state_path: str = "data/state/execution_mapping.json"
    enable_trailing_stop: bool = True

class EToroLiveExecClientFactory(LiveExecClientFactory):
    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: EToroExecClientConfig,
        msgbus: object,
        cache: object,
        clock: object,
        **kwargs: object,
    ) -> "EToroExecutionClient":
        from automation.adapters.etoro_execution import EToroExecutionClient
        return EToroExecutionClient(
            loop=loop,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=InstrumentProvider(),
            api_key=config.api_key,
            user_key=config.user_key,
            environment=config.environment,
            dry_run=config.dry_run,
            state_path=config.state_path,
            enable_trailing_stop=config.enable_trailing_stop,
        )
