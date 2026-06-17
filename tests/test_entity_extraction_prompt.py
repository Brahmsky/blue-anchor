# pyright: reportMissingImports=false

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minirag.base import TextChunkSchema
from minirag.operate import extract_entity_candidates
from minirag.prompt import PROMPTS


def test_extract_entity_candidates_formats_examples_placeholder_safely():
    prompts: list[str] = []

    async def fake_llm(prompt: str, history_messages=None):
        del history_messages
        prompts.append(prompt)
        return PROMPTS["DEFAULT_COMPLETION_DELIMITER"]

    chunks = cast(
        dict[str, TextChunkSchema],
        {
            "chunk-1": {
                "tokens": 8,
                "content": "主机故障导致停机。",
                "full_doc_id": "doc-1",
                "chunk_order_index": 0,
            }
        },
    )

    nodes, edges = asyncio.run(
        extract_entity_candidates(
            chunks,
            {
                "llm_model_func": fake_llm,
                "entity_extract_max_gleaning": 0,
            },
        )
    )

    assert nodes == {}
    assert edges == {}
    assert len(prompts) == 1
    assert "{examples}" not in prompts[0]
    assert "-Examples-" in prompts[0]
    assert "Text: 主机故障导致停机。" in prompts[0]
