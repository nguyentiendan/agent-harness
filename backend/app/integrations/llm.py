"""integrations / llm."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import asyncio
import os


class LiteLLMClientWrapper:
    """Async LiteLLM wrapper supporting OpenAI-style tool calling."""

    def __init__(
        self,
        default_model: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        user_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        self.user_id = user_id
        self.run_id = run_id
        self.default_model = default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.context_runtime = None

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        temperature=None,
        max_tokens=None,
        purpose="generation",
    ):
        from app.observability import tracing

        target = model or self.default_model
        output = self.max_tokens if max_tokens is None else max_tokens
        if self.user_id and self.run_id:
            if self.context_runtime is None:
                from app.context.runtime import ContextRuntime

                self.context_runtime = ContextRuntime(self.user_id, self.run_id)
            if purpose == "generation":
                prepared = await self.context_runtime.prepare(
                    messages, target, tools, output, self.complete
                )
                messages, output = (
                    prepared.messages,
                    prepared.effective_max_output_tokens,
                )
            elif purpose in ("summary", "judge"):
                # Summary input is bounded by its own builder; dispatch applies
                # the same model limits and authorization again below.
                from app.context.limits import resolve_model_limits
                from app.context.tokens import count_payload
                from app.billing import catalog, gateway

                metadata = await asyncio.to_thread(catalog.lookup, target)
                if not metadata:
                    metadata = (await asyncio.to_thread(gateway.priced_models)).get(
                        target, {}
                    )
                limits = resolve_model_limits(metadata, self.context_runtime.policy)
                output = min(output, limits.max_output_tokens)
                reserve = max(
                    self.context_runtime.policy.safety_tokens,
                    int(
                        limits.context_window
                        * self.context_runtime.policy.safety_fraction
                    ),
                )
                if (
                    count_payload(messages, tools or [], target).tokens
                    + output
                    + reserve
                    > limits.context_window
                ):
                    raise ValueError("Auxiliary model context exceeds budget")
            else:
                raise ValueError("Unknown model purpose")
        with tracing.span("model", {"model": target, "purpose": purpose}) as sid:
            if self.context_runtime:
                tracing.capture_payload(
                    tracing.current_trace.get(),
                    sid,
                    {"messages": messages, "tools": tools},
                    self.context_runtime.policy,
                )
            response = await self._dispatch(
                messages,
                model=target,
                tools=tools,
                temperature=temperature,
                max_tokens=output,
            )
            if self.context_runtime:
                tracing.capture_payload(
                    tracing.current_trace.get(),
                    sid,
                    {"response": response.choices[0].message.content},
                    self.context_runtime.policy,
                )
            return response

    async def _dispatch(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        from app.billing import policy

        target = model or self.default_model
        if policy.enabled():
            from app.billing.completion import complete

            return await complete(
                self.user_id,
                self.run_id,
                messages,
                target,
                tools,
                self.temperature if temperature is None else temperature,
                max_tokens or self.max_tokens,
                expected_model_revision=self.context_runtime.dispatch_revision
                if self.context_runtime
                else None,
            )
        import litellm

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
            # Legacy CLI requires an explicit scoped key; never use management credentials.
            proxy_key = os.environ.get("LITELLM_PROXY_KEY", "").strip()
            if not proxy_key:
                raise RuntimeError(
                    "Legacy gateway calls require an explicit scoped key."
                )
            kwargs["api_key"] = proxy_key

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return await litellm.acompletion(**kwargs)
            except Exception as e:  # transient provider/network errors
                last_error = e
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.retry_backoff * (2**attempt))
        raise last_error  # type: ignore[misc]
