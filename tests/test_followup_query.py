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


def test_faultcase_fast_query_ignores_history_and_uses_single_turn_query(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_build_context(
        query,
        knowledge_graph_inst,
        chunks_vdb,
        text_chunks_db,
        query_param,
        global_config=None,
        alias_store=None,
        max_source_candidates=None,
    ):
        seen["retrieval_query"] = query
        seen["max_source_candidates"] = max_source_candidates
        return "上下文"

    async def fake_model(
        query, system_prompt=None, history_messages=None, stream=False, **kwargs
    ):
        seen["model_query"] = query
        seen["history_messages"] = history_messages
        seen["stream"] = stream
        return "回答"

    monkeypatch.setattr(operate, "_build_faultcase_fast_context", fake_build_context)

    result = asyncio.run(
        operate.faultcase_fast_query(
            query="那第二步呢？",
            knowledge_graph_inst=None,
            chunks_vdb=None,
            text_chunks_db=None,
            query_param=QueryParam(
                mode="graph_text_hybrid",
                stream=False,
                conversation_history=[
                    {
                        "role": "user",
                        "content": "冷藏集装箱压缩机组不能起动时，通常应怎样排查？",
                    },
                    {
                        "role": "assistant",
                        "content": "先检查控制电源，再看热继电器。",
                    },
                ],
                history_turns=3,
            ),
            global_config={
                "llm_model_func": fake_model,
                "llm_model_max_token_size": 8192,
            },
        )
    )

    assert result == "回答"
    assert seen["model_query"] == "那第二步呢？"
    assert seen["stream"] is False
    assert seen["history_messages"] is None
    assert seen["retrieval_query"] == "那第二步呢？"
    assert seen["max_source_candidates"] is None
