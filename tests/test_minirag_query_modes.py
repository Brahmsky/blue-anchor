# pyright: reportMissingImports=false

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import minirag.minirag as minirag_module
from minirag.base import QueryParam
from minirag.minirag import MiniRAG


def _build_minirag_stub(monkeypatch: pytest.MonkeyPatch) -> MiniRAG:
    rag = cast(Any, object.__new__(MiniRAG))
    rag.faultcase_alias_store = object()
    rag.chunk_entity_relation_graph = object()
    rag.chunks_vdb = object()
    rag.text_chunks = object()

    async def _query_done():
        return None

    rag._query_done = _query_done
    monkeypatch.setattr(minirag_module, "fields", lambda _: [])
    return cast(MiniRAG, rag)


def test_aquery_accepts_faultcase_fast_and_reaches_query_path(
    monkeypatch: pytest.MonkeyPatch,
):
    rag = _build_minirag_stub(monkeypatch)
    seen: dict[str, object] = {}

    async def fake_faultcase_fast_query(
        query,
        knowledge_graph_inst,
        chunks_vdb,
        text_chunks_db,
        query_param,
        global_config,
    ):
        seen["query"] = query
        seen["graph"] = knowledge_graph_inst
        seen["chunks_vdb"] = chunks_vdb
        seen["text_chunks_db"] = text_chunks_db
        seen["mode"] = query_param.mode
        seen["alias_store"] = global_config["faultcase_alias_store"]
        return "faultcase_fast-ok"

    monkeypatch.setattr(
        minirag_module, "faultcase_fast_query", fake_faultcase_fast_query
    )

    result = asyncio.run(
        rag.aquery(
            "舵机电力系统故障怎么办",
            QueryParam(mode=cast(Any, "faultcase_fast")),
        )
    )

    assert result == "faultcase_fast-ok"
    assert seen == {
        "query": "舵机电力系统故障怎么办",
        "graph": rag.chunk_entity_relation_graph,
        "chunks_vdb": rag.chunks_vdb,
        "text_chunks_db": rag.text_chunks,
        "mode": "faultcase_fast",
        "alias_store": rag.faultcase_alias_store,
    }


def test_aquery_still_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch):
    rag = _build_minirag_stub(monkeypatch)

    async def unexpected_faultcase_fast_query(*args, **kwargs):
        raise AssertionError(
            "faultcase_fast_query should not be reached for unknown modes"
        )

    monkeypatch.setattr(
        minirag_module, "faultcase_fast_query", unexpected_faultcase_fast_query
    )

    with pytest.raises(ValueError, match="Unknown mode mystery_mode"):
        asyncio.run(rag.aquery("测试", QueryParam(mode=cast(Any, "mystery_mode"))))
