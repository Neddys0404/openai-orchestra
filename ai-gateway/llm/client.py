from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
import httpx


class UpstreamClient:
    async def completion(self, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{endpoint}/chat/completions", json=payload)

            logger = logging.getLogger(__name__)

            logger.warning(
                "===== RAW UPSTREAM RESPONSE =====\n%s",
                response.text,
            )

            response.raise_for_status()
            return response.json()

    async def stream(self, endpoint: str, payload: dict[str, Any], timeout: float) -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{endpoint}/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for chunk in response.aiter_raw():
                    yield chunk

    async def text_completion(self, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Proxy OpenAI's legacy text-completions API without changing its payload."""
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{endpoint}/completions", json=payload)
            response.raise_for_status()
            return response.json()

    async def stream_text_completion(self, endpoint: str, payload: dict[str, Any], timeout: float) -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{endpoint}/completions", json=payload) as response:
                response.raise_for_status()
                async for chunk in response.aiter_raw():
                    yield chunk
