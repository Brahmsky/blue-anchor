import asyncio
from types import SimpleNamespace


from minirag.llm import openai as openai_shim


def test_openai_complete_if_cache_uses_global_model_name(monkeypatch):
    captured = {}

    async def fake_lmstudio_model_if_cache(model, prompt, **kwargs):
        captured["model"] = model
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(
        openai_shim,
        "lmstudio_model_if_cache",
        fake_lmstudio_model_if_cache,
    )

    hashing_kv = SimpleNamespace(global_config={"llm_model_name": "qwen3.5-2b"})
    result = asyncio.run(
        openai_shim.openai_complete_if_cache(
            "为什么全船无24V供电？",
            system_prompt="只依据上下文回答",
            base_url="http://localhost:1234/v1",
            api_key="EMPTY",
            hashing_kv=hashing_kv,
        )
    )

    assert result == "ok"
    assert captured["model"] == "qwen3.5-2b"
    assert captured["prompt"] == "为什么全船无24V供电？"
    assert captured["kwargs"]["system_prompt"] == "只依据上下文回答"
    assert captured["kwargs"]["host"] == "http://localhost:1234/v1"
    assert captured["kwargs"]["api_key"] == "EMPTY"


def test_openai_complete_if_cache_requires_model_name():
    try:
        asyncio.run(openai_shim.openai_complete_if_cache("测试 prompt"))
    except ValueError as exc:
        assert "configured model name" in str(exc)
    else:
        raise AssertionError("Expected missing model configuration to raise ValueError")


def test_openai_complete_if_cache_prefers_explicit_model_kwarg(monkeypatch):
    captured = {}

    async def fake_lmstudio_model_if_cache(model, prompt, **kwargs):
        captured["model"] = model
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(
        openai_shim,
        "lmstudio_model_if_cache",
        fake_lmstudio_model_if_cache,
    )

    result = asyncio.run(
        openai_shim.openai_complete_if_cache(
            "显式 model prompt",
            model="explicit-model",
            hashing_kv=None,
        )
    )

    assert result == "ok"
    assert captured == {
        "model": "explicit-model",
        "prompt": "显式 model prompt",
    }
