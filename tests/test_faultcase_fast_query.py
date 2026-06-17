# pyright: reportMissingImports=false

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minirag.base import QueryParam
from minirag import operate


def test_faultcase_fast_query_truncates_context_to_fit_model_window(monkeypatch):
    long_context = "上下文片段 " * 5000
    seen: dict[str, str] = {}

    async def fake_build_context(*args, **kwargs):
        return long_context

    async def fake_model(query, system_prompt=None, stream=False, **kwargs):
        seen["query"] = query
        seen["system_prompt"] = system_prompt or ""
        return "OK"

    monkeypatch.setattr(operate, "_build_faultcase_fast_context", fake_build_context)

    result = asyncio.run(
        operate.faultcase_fast_query(
            query="测试问题",
            knowledge_graph_inst=None,
            chunks_vdb=None,
            text_chunks_db=None,
            query_param=QueryParam(
                mode="graph_text_hybrid",
                stream=False,
            ),
            global_config={
                "llm_model_func": fake_model,
                "llm_model_max_token_size": 1024,
            },
        )
    )

    assert result == "OK"
    assert seen["query"] == "测试问题"
    assert "上下文片段" in seen["system_prompt"]
    assert len(operate.encode_string_by_tiktoken(seen["system_prompt"])) <= 1024
