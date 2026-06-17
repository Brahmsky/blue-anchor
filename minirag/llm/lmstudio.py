"""
LM Studio OpenAI-compatible interface.

This adapter talks directly to LM Studio's `/v1` OpenAI-compatible endpoints
instead of routing through the Ollama Python client.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Union
from urllib.parse import urlparse
from urllib.parse import urlsplit, urlunsplit

if sys.version_info < (3, 9):
    from typing import AsyncIterator
else:
    from collections.abc import AsyncIterator

import httpx
import numpy as np
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_CONTEXT_LENGTH_CACHE: dict[tuple[str, str], tuple[int | None, float]] = {}


logger = logging.getLogger(__name__)


def _normalize_base_url(host: str | None) -> str:
    if not host:
        return "http://127.0.0.1:1234/v1"
    normalized = host.rstrip("/")
    parts = urlsplit(normalized)
    path = parts.path.rstrip("/")

    if path.endswith("/api/v1"):
        path = path[: -len("/api/v1")] + "/v1"
    elif not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"

    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _normalize_models_url(host: str | None) -> str:
    base_url = _normalize_base_url(host)
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")] + "/api/v1/models"
    else:
        path = f"{path}/api/v1/models"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _build_headers(api_key: str | None) -> dict[str, str]:
    normalized_key = (api_key or "").strip()
    headers = {"Content-Type": "application/json"}
    if normalized_key and normalized_key.lower() not in {"lm-studio", "ollama"}:
        headers["Authorization"] = f"Bearer {normalized_key}"
    return headers


def _should_trust_env(base_url: str) -> bool:
    """
    Avoid proxy inheritance for local LM Studio / Ollama-style endpoints.

    The host is often configured as 127.0.0.1 or localhost. If the shell exports
    ALL_PROXY=socks5://..., httpx will try to route even local requests through a
    SOCKS proxy and require ``socksio``. Local inference endpoints should talk to
    the loopback interface directly instead.
    """
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").strip().lower()
    return hostname not in {"127.0.0.1", "localhost", "::1"}


def _raise_for_status_with_body(response: httpx.Response) -> None:
    if response.is_success:
        return

    body = response.text[:2000]
    logger.error(
        "LM Studio request failed: status=%s url=%s body=%s",
        response.status_code,
        response.request.url,
        body,
    )
    response.raise_for_status()


def _normalize_message_content(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _should_flatten_qwen_messages(model: str | None) -> bool:
    normalized = (model or "").strip().lower()
    return normalized.startswith("qwen3.5")


def _flatten_qwen_messages(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict] | None = None,
) -> list[dict]:
    sections: list[str] = []

    normalized_system_prompt = _normalize_message_content(system_prompt)
    if normalized_system_prompt:
        sections.append(f"System instructions:\n{normalized_system_prompt}")

    if history_messages:
        history_lines: list[str] = []
        for item in history_messages:
            role = _normalize_message_content(item.get("role", "user")) or "user"
            content = _normalize_message_content(item.get("content"))
            if not content:
                continue
            history_lines.append(f"{role.capitalize()}:\n{content}")
        if history_lines:
            sections.append("Conversation history:\n" + "\n\n".join(history_lines))

    normalized_prompt = _normalize_message_content(prompt)
    sections.append(f"Current user query:\n{normalized_prompt or '<empty>'}")
    return [{"role": "user", "content": "\n\n".join(sections)}]


def _build_messages(
    model: str,
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict] | None = None,
) -> list[dict]:
    if _should_flatten_qwen_messages(model):
        return _flatten_qwen_messages(prompt, system_prompt, history_messages)

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history_messages:
        messages.extend(history_messages)
    messages.append({"role": "user", "content": prompt})
    return messages


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError)),
)
async def lmstudio_model_if_cache(
    model: str,
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict] | None = None,
    **kwargs,
) -> Union[str, AsyncIterator[str]]:
    stream = bool(kwargs.pop("stream", False))
    host = kwargs.pop("host", None)
    timeout = kwargs.pop("timeout", None)
    api_key = kwargs.pop("api_key", None)
    kwargs.pop("hashing_kv", None)
    kwargs.pop("max_tokens", None)
    kwargs.pop("options", None)

    base_url = _normalize_base_url(host)
    headers = _build_headers(api_key)
    trust_env = _should_trust_env(base_url)
    payload: dict = {
        "model": model,
        "messages": _build_messages(model, prompt, system_prompt, history_messages),
        "stream": stream,
    }
    if "format" in kwargs:
        payload["response_format"] = {"type": "json_object"}

    if stream:

        async def inner() -> AsyncIterator[str]:
            async with httpx.AsyncClient(
                timeout=timeout or 300, trust_env=trust_env
            ) as client:
                async with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    _raise_for_status_with_body(response)
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":") or line.startswith("event:"):
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices")
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content

        return inner()

    async with httpx.AsyncClient(timeout=timeout or 300, trust_env=trust_env) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        _raise_for_status_with_body(response)
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def lmstudio_model_complete(
    prompt, system_prompt=None, history_messages=[], keyword_extraction=False, **kwargs
) -> Union[str, AsyncIterator[str]]:
    keyword_extraction = kwargs.pop("keyword_extraction", None)
    if keyword_extraction:
        kwargs["format"] = "json"
    model_name = kwargs["hashing_kv"].global_config["llm_model_name"]
    return await lmstudio_model_if_cache(
        model_name,
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        **kwargs,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError)),
)
async def lmstudio_embed(texts: list[str], embed_model, **kwargs) -> np.ndarray:
    host = kwargs.pop("host", None)
    timeout = kwargs.pop("timeout", None)
    api_key = kwargs.pop("api_key", None)
    base_url = _normalize_base_url(host)
    headers = _build_headers(api_key)
    trust_env = _should_trust_env(base_url)

    async with httpx.AsyncClient(timeout=timeout or 300, trust_env=trust_env) as client:
        response = await client.post(
            f"{base_url}/embeddings",
            headers=headers,
            json={"model": embed_model, "input": texts},
        )
        _raise_for_status_with_body(response)
        data = response.json()
        return np.array([item["embedding"] for item in data["data"]])


async def lmstudio_loaded_context_length(
    model: str,
    host: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> int | None:
    base_url = _normalize_base_url(host)
    cache_key = (base_url, model)
    now = time.monotonic()
    cached = _CONTEXT_LENGTH_CACHE.get(cache_key)
    if cached and now - cached[1] < 30:
        return cached[0]

    models_url = _normalize_models_url(host)
    headers = _build_headers(api_key)
    trust_env = _should_trust_env(base_url)
    detected: int | None = None

    async with httpx.AsyncClient(
        timeout=timeout or 10, trust_env=trust_env
    ) as client:
        response = await client.get(models_url, headers=headers)
        response.raise_for_status()
        payload = response.json()

    for item in payload.get("models", []):
        key = str(item.get("key") or "").strip()
        loaded_instances = item.get("loaded_instances") or []
        loaded_match = any(
            str(instance.get("id") or "").strip() == model
            for instance in loaded_instances
            if isinstance(instance, dict)
        )
        if key != model and not loaded_match:
            continue

        for instance in loaded_instances:
            if not isinstance(instance, dict):
                continue
            config = instance.get("config") or {}
            context_length = config.get("context_length")
            if isinstance(context_length, int) and context_length > 0:
                detected = context_length
                break
        break

    _CONTEXT_LENGTH_CACHE[cache_key] = (detected, now)
    return detected
