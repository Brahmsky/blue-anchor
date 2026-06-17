import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minirag.base import QueryParam
from minirag.chunk_recall import hybrid_chunk_recall


class DemoChunkVectorStorage:
    async def query(self, query: str, top_k: int = 5):
        del top_k
        if "电磁阀" in query:
            return [
                {"id": "chunk-b", "distance": 0.08},
                {"id": "chunk-d", "distance": 0.17},
            ]
        return [{"id": "chunk-c", "distance": 0.21}]


class DemoTextChunksDB:
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


async def main():
    query = "电磁阀怎么检查"
    graph_seed_chunk_ids = ["chunk-a", "chunk-b"]
    result = await hybrid_chunk_recall(
        query,
        graph_seed_chunk_ids,
        DemoChunkVectorStorage(),
        DemoTextChunksDB(),
        QueryParam(
            faultcase_chunk_recall_top_k=4,
            faultcase_chunk_vector_top_k=2,
            chunk_bm25_top_k=3,
            chunk_bm25_scan_limit=4,
        ),
    )

    print("query:", query)
    print("graph_seed_chunk_ids:", graph_seed_chunk_ids)
    print("merged_chunk_candidates:")
    for item in result:
        print(
            f"- {item['id']} | score={item['score']} | sources={','.join(item['sources'])}"
        )


if __name__ == "__main__":
    asyncio.run(main())
