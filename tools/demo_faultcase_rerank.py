import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minirag.base import QueryParam
from minirag.candidate_rerank import rerank_faultcase_candidates


async def main():
    query = "电磁阀怎么检查"
    candidates = [
        {
            "id": "chunk-a",
            "content": "主机无法启动，先检查燃油系统和蓄电池。",
            "score": 1002.0,
        },
        {
            "id": "chunk-b",
            "content": "燃油电磁阀故障会导致主机启动困难。",
            "score": 1001.0,
        },
        {"id": "chunk-d", "content": "电磁阀卡滞会造成燃油供应不足。", "score": 101.0},
    ]
    result = await rerank_faultcase_candidates(
        query,
        candidates,
        QueryParam(
            faultcase_rerank_enabled=True,
            faultcase_rerank_max_candidates=2,
            faultcase_rerank_timeout_ms=20000,
        ),
        {
            "faultcase_rerank_model": "BAAI/bge-reranker-base",
        },
    )

    print("query:", query)
    print("input_ids:", [item["id"] for item in candidates])
    print("output_ids:", [item["id"] for item in result])
    print(
        "degraded_to_passthrough:",
        [item["id"] for item in candidates] == [item["id"] for item in result],
    )


if __name__ == "__main__":
    asyncio.run(main())
