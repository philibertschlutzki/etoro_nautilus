"""
automation/ai_loop/client.py
=============================
Issue #1104 — Async DeepSeek API client for the AI-Loop.

Reads ``DEEPSEEK_API_KEY`` (and optional overrides) from the SAME shared ``.env`` the rest of
``automation/`` uses — the lookup order below is IDENTICAL to
``automation/backtest_runner.py``'s convention (``automation/.env`` first, ``PROJECT_ROOT/.env``
as fallback), so there is no separate AI-Loop-only env file (hard architecture constraint, see
``automation/ai_loop/__init__.py``). This module performs outbound HTTPS calls only — no
filesystem writes; it is not one of the AI-Loop's two write interfaces.

Uses ``aiohttp`` (already pinned in ``automation/requirements.txt`` as ``aiohttp>=3.9.0``)
rather than ``httpx``: aiohttp already covers everything this client needs (async POST with a
JSON body, per-request timeouts, connector reuse via ``ClientSession``) and is used elsewhere in
``automation/`` (see ``catalog_service.py``) — adding a second HTTP client library for one new
module would be an unjustified new dependency.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Search .env in automation/ first, then PROJECT_ROOT (repo root) as fallback — same convention
# as automation/backtest_runner.py / automation/catalog_service.py / automation/historical_fetcher.py.
_THIS_DIR = Path(__file__).resolve().parent          # automation/ai_loop
_AUTOMATION_DIR = _THIS_DIR.parent                    # automation/
_ENV_FILE = _AUTOMATION_DIR / ".env"
if not _ENV_FILE.exists():
    _ENV_FILE = _AUTOMATION_DIR.parent / ".env"
load_dotenv(str(_ENV_FILE))

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.5

REASONER_MODEL = "deepseek-reasoner"  # R1 — used by reasoning.StrategyReasoner
CHAT_MODEL = "deepseek-chat"          # V3 — used by synthesizer.CodeSynthesizer / validator.StaticValidator


class DeepSeekAPIError(RuntimeError):
    """Raised when the DeepSeek API returns a non-recoverable error after all retries, or when
    ``DEEPSEEK_API_KEY`` is missing."""


class DeepSeekClient:
    """Minimal async DeepSeek client with bounded retries/backoff and explicit timeouts.

    ``call_reasoner`` always targets ``deepseek-reasoner`` (R1, hypothesis formulation);
    ``call_chat`` always targets ``deepseek-chat`` (V3, code generation / self-correction).

    Error-handling follows ``automation/AGENTS.md`` §14: HTTP 429 respects ``Retry-After``,
    timeouts/5xx retry with exponential backoff, bounded by ``max_retries``
    (``AI_LOOP_MAX_RETRIES`` from the shared env, default 3).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout_s: float | None = None,
        max_retries: int | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY")
        self.base_url = (base_url or os.getenv("DEEPSEEK_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_s = (
            timeout_s if timeout_s is not None else float(os.getenv("DEEPSEEK_TIMEOUT_S", DEFAULT_TIMEOUT_S))
        )
        self.max_retries = (
            max_retries if max_retries is not None else int(os.getenv("AI_LOOP_MAX_RETRIES", DEFAULT_MAX_RETRIES))
        )
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "DeepSeekClient":
        await self._ensure_session()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def _post_chat_completion(self, payload: dict[str, Any]) -> str:
        if not self.api_key:
            raise DeepSeekAPIError(
                "DEEPSEEK_API_KEY ist nicht gesetzt — in .env eintragen (automation/.env oder "
                "Projekt-Root .env, siehe .env.example)."
            )
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        session = await self._ensure_session()
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                    if resp.status == 429:
                        retry_after = _parse_retry_after(resp.headers.get("Retry-After"), attempt)
                        logger.warning(
                            "DeepSeek 429 (Rate-Limit) — Retry-After=%.1fs (Versuch %d/%d).",
                            retry_after, attempt, self.max_retries,
                        )
                        if attempt >= self.max_retries:
                            raise DeepSeekAPIError(f"DeepSeek 429 nach {self.max_retries} Versuchen weiterhin Rate-Limited.")
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status >= 500:
                        body = await resp.text()
                        raise DeepSeekAPIError(f"DeepSeek {resp.status}: {body[:500]}")
                    resp.raise_for_status()
                    data = await resp.json()
                    return _extract_content(data)
            except (aiohttp.ClientError, asyncio.TimeoutError, DeepSeekAPIError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                backoff = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                logger.warning(
                    "DeepSeek-Call fehlgeschlagen (Versuch %d/%d): %s — Retry in %.1fs.",
                    attempt, self.max_retries, exc, backoff,
                )
                await asyncio.sleep(backoff)
        raise DeepSeekAPIError(f"DeepSeek-Call nach {self.max_retries} Versuch(en) fehlgeschlagen: {last_exc}")

    async def call_reasoner(self, payload: dict[str, Any]) -> str:
        """Sends ``payload`` (typically ``{"messages": [...], ...}`` built by
        ``reasoning.StrategyReasoner``) to ``deepseek-reasoner`` (R1) and returns the raw text
        content of the model's response. ``model`` is always forced to ``REASONER_MODEL``
        regardless of what the caller passes in ``payload``."""
        body = dict(payload)
        body["model"] = REASONER_MODEL
        return await self._post_chat_completion(body)

    async def call_chat(self, prompt: str) -> str:
        """Sends a single user ``prompt`` to ``deepseek-chat`` (V3) and returns the raw text
        content of the model's response."""
        body = {"model": CHAT_MODEL, "messages": [{"role": "user", "content": prompt}]}
        return await self._post_chat_completion(body)


def _parse_retry_after(raw: str | None, attempt: int) -> float:
    if raw is None:
        return float(2 ** attempt)
    try:
        return float(raw)
    except ValueError:
        return float(2 ** attempt)


def _extract_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise DeepSeekAPIError(f"DeepSeek-Antwort ohne 'choices': {data!r}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is None:
        raise DeepSeekAPIError(f"DeepSeek-Antwort ohne 'content': {data!r}")
    return content
