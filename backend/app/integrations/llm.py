"""integrations / llm."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import asyncio
import os


class LiteLLMClientWrapper:
    """Async LiteLLM wrapper supporting OpenAI-style tool calling."""

    def __init__(self, default_model: str = "gpt-4o-mini",
                 temperature: float = 0.2, max_tokens: int = 4096,
                 max_retries: int = 3, retry_backoff: float = 1.0):
        self.default_model = default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    async def complete(self, messages: List[Dict[str, Any]], model: Optional[str] = None,
                       tools: Optional[List[Dict[str, Any]]] = None,
                       temperature: Optional[float] = None,
                       max_tokens: Optional[int] = None):
        import litellm
        target = model or self.default_model
        kwargs: Dict[str, Any] = {
            "model": target,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        # Route through the LiteLLM Proxy gateway when configured. The gateway
        # gives the admin dashboard: virtual keys, spend/token logs, tracing.
        proxy_url = os.environ.get("LITELLM_PROXY_URL", "").strip()
        if proxy_url:
            kwargs["model"] = f"litellm_proxy/{target}"
            kwargs["api_base"] = proxy_url
            # Prefer a per-user virtual key; fall back to the master key.
            proxy_key = (
                os.environ.get("LITELLM_PROXY_KEY", "").strip()
                or os.environ.get("LITELLM_MASTER_KEY", "").strip()
            )
            if proxy_key:
                kwargs["api_key"] = proxy_key

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return await litellm.acompletion(**kwargs)
            except Exception as e:  # transient provider/network errors
                last_error = e
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.retry_backoff * (2 ** attempt))
        raise last_error  # type: ignore[misc]
