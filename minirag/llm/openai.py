"""OpenAI-compatible shim backed by the LM Studio adapter helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .lmstudio import lmstudio_embed, lmstudio_model_if_cache


async def openai_complete_if_cache(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs,
) -> str | AsyncIterator[str]:
    model = kwargs.pop("model", None)
    if model is None:
        hashing_kv = kwargs.get("hashing_kv")
        global_config = getattr(hashing_kv, "global_config", None)
        if isinstance(global_config, dict):
            model = global_config.get("llm_model_name")
    if not model:
        raise ValueError("openai_complete_if_cache requires a configured model name")

    return await lmstudio_model_if_cache(
        model,
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        host=base_url,
        api_key=api_key,
        **kwargs,
    )


async def openai_embed(
    texts: list[str],
    model: str,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs,
) -> Any:
    return await lmstudio_embed(
        texts,
        embed_model=model,
        host=base_url,
        api_key=api_key,
        **kwargs,
    )


__all__ = ["openai_complete_if_cache", "openai_embed"]
