import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minirag.base import QueryParam
from minirag.chunk_recall import (
    compute_simple_lexical_score,
    hybrid_chunk_recall,
)
from minirag.operate import _build_faultcase_fast_context


class FakeChunkVectorStorage:
    async def query(self, query: str, top_k: int = 5):
        del query, top_k
        return [
            {"id": "chunk-b", "distance": 0.11},
            {"id": "chunk-c", "distance": 0.23},
        ]


class FakeTextChunksDB:
    def __init__(self):
        self.data = {
            "chunk-a": {"content": "主机无法启动，先检查燃油系统和蓄电池。"},
            "chunk-b": {"content": "燃油电磁阀故障会导致主机启动困难。"},
            "chunk-c": {"content": "舵机压力异常时优先检查液压泵和滤芯。"},
            "chunk-d": {"content": "电磁阀卡滞会造成燃油供应不足。"},
        }

    async def all_keys(self):
        return list(self.data.keys())

    async def get_by_id(self, chunk_id: str):
        return self.data.get(chunk_id)

    async def get_by_ids(self, ids, fields=None):
        del fields
        return [self.data.get(chunk_id) for chunk_id in ids]


class FakeGraphStorage:
    async def get_types(self):
        return ["EQUIPMENT", "FAULTCASE"], ["EQUIPMENT", "FAULTCASE"]

    async def get_node_from_types(self, _types):
        return [
            {
                "entity_name": "主机系统无法启动",
                "entity_type": "FAULTCASE",
                "description": "主机无法启动故障卡",
                "source_id": "chunk-a",
            }
        ]

    async def get_node_edges(self, _source_node_id: str):
        return []

    async def get_node(self, _node_id: str):
        return None


def test_simple_lexical_score_prefers_direct_match():
    direct = compute_simple_lexical_score(
        "电磁阀怎么检查", "电磁阀卡滞会造成燃油供应不足"
    )
    weak = compute_simple_lexical_score(
        "电磁阀怎么检查", "舵机压力异常时优先检查液压泵"
    )
    assert direct > weak
    assert direct > 0


def test_hybrid_chunk_recall_merges_and_dedupes():
    result = asyncio.run(
        hybrid_chunk_recall(
            "电磁阀怎么检查",
            ["chunk-a", "chunk-b"],
            FakeChunkVectorStorage(),
            FakeTextChunksDB(),
            QueryParam(
                faultcase_chunk_recall_top_k=4,
                faultcase_chunk_vector_top_k=2,
                faultcase_chunk_lexical_top_k=3,
                faultcase_chunk_lexical_scan_limit=4,
            ),
        )
    )

    ids = [item["id"] for item in result]
    assert ids[0] == "chunk-a"
    assert len(ids) == len(set(ids))
    assert "chunk-b" in ids
    assert any("lexical" in item["sources"] for item in result)


def test_faultcase_fast_context_uses_merged_chunks():
    context = asyncio.run(
        _build_faultcase_fast_context(
            "电磁阀怎么检查",
            FakeGraphStorage(),
            FakeChunkVectorStorage(),
            FakeTextChunksDB(),
            QueryParam(
                top_k=3,
                faultcase_chunk_recall_top_k=4,
                faultcase_chunk_vector_top_k=2,
                faultcase_chunk_lexical_top_k=3,
                faultcase_chunk_lexical_scan_limit=4,
            ),
        )
    )

    assert context is not None
    assert "chunk-a" in context
    assert "chunk-b" in context
    assert "chunk-d" in context
