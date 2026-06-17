import asyncio
from contextlib import asynccontextmanager

from minirag.llm import lmstudio


def test_normalize_base_url_appends_v1_when_missing():
    assert lmstudio._normalize_base_url(None) == "http://127.0.0.1:1234/v1"
    assert (
        lmstudio._normalize_base_url("http://127.0.0.1:1234")
        == "http://127.0.0.1:1234/v1"
    )
    assert (
        lmstudio._normalize_base_url("http://127.0.0.1:1234/")
        == "http://127.0.0.1:1234/v1"
    )
    assert (
        lmstudio._normalize_base_url("http://127.0.0.1:1234/v1")
        == "http://127.0.0.1:1234/v1"
    )
    assert (
        lmstudio._normalize_base_url("http://127.0.0.1:1234/api/v1")
        == "http://127.0.0.1:1234/v1"
    )


def test_lmstudio_stream_ignores_non_content_sse_lines(monkeypatch):
    seen = {}

    class FakeResponse:
        is_success = True

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in [
                "",
                ": ping",
                "event: message",
                'data: {"choices":[{"delta":{"content":"Hello"}}]}',
                "data: not-json",
                'data: {"choices":[]}',
                'data: {"choices":[{"delta":{}}]}',
                '{"choices":[{"delta":{"content":" world"}}]}',
                "data: [DONE]",
                'data: {"choices":[{"delta":{"content":"ignored"}}]}',
            ]:
                yield line

    class FakeAsyncClient:
        def __init__(self, timeout, trust_env=None):
            seen["timeout"] = timeout
            seen["trust_env"] = trust_env

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        @asynccontextmanager
        async def stream(self, method, url, headers=None, json=None):
            seen["request"] = {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
            }
            yield FakeResponse()

    monkeypatch.setattr(lmstudio.httpx, "AsyncClient", FakeAsyncClient)

    async def collect_chunks():
        stream = await lmstudio.lmstudio_model_if_cache(
            "demo-model",
            "hello?",
            stream=True,
            host="http://127.0.0.1:1234/v1",
        )
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        return chunks

    assert asyncio.run(collect_chunks()) == ["Hello", " world"]
    assert seen["timeout"] == 300
    assert seen["trust_env"] is False
    assert seen["request"]["method"] == "POST"
    assert seen["request"]["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert seen["request"]["json"]["stream"] is True
    assert seen["request"]["json"]["messages"][-1] == {
        "role": "user",
        "content": "hello?",
    }

def test_lmstudio_loaded_context_length_disables_proxy_for_localhost(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "models": [
                    {
                        "key": "demo-model",
                        "loaded_instances": [
                            {
                                "id": "demo-model",
                                "config": {"context_length": 8192},
                            }
                        ],
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, timeout, trust_env=None):
            seen["timeout"] = timeout
            seen["trust_env"] = trust_env

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            seen["request"] = {"url": url, "headers": headers}
            return FakeResponse()

    monkeypatch.setattr(lmstudio.httpx, "AsyncClient", FakeAsyncClient)
    lmstudio._CONTEXT_LENGTH_CACHE.clear()

    detected = asyncio.run(
        lmstudio.lmstudio_loaded_context_length(
            "demo-model",
            host="http://127.0.0.1:1234/v1",
        )
    )

    assert detected == 8192
    assert seen["timeout"] == 10
    assert seen["trust_env"] is False
    assert seen["request"]["url"] == "http://127.0.0.1:1234/api/v1/models"


def test_lmstudio_flattens_qwen35_messages_to_single_user_message():
    messages = lmstudio._build_messages(
        "qwen3.5-2b",
        "为什么全船无24V供电？",
        system_prompt="只依据上下文回答",
        history_messages=[
            {"role": "user", "content": "上一轮问题"},
            {"role": "assistant", "content": "上一轮回答"},
        ],
    )

    assert messages == [
        {
            "role": "user",
            "content": (
                "System instructions:\n只依据上下文回答\n\n"
                "Conversation history:\nUser:\n上一轮问题\n\nAssistant:\n上一轮回答\n\n"
                "Current user query:\n为什么全船无24V供电？"
            ),
        }
    ]
