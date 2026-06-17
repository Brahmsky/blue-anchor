import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return text


def strip_faultcase_suffix(name: str) -> str:
    return re.sub(r"[（(].*?[）)]", "", name).strip()


@dataclass
class NodeRecord:
    name: str
    entity_type: str
    description: str
    source_id: str
    aliases: list[str]


class FaultCaseNoLLMRetriever:
    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.graph = nx.read_graphml(working_dir / "graph_chunk_entity_relation.graphml")
        self.text_chunks = json.loads(
            (working_dir / "kv_store_text_chunks.json").read_text(encoding="utf-8")
        )
        self.nodes: list[NodeRecord] = []
        for name, data in self.graph.nodes(data=True):
            aliases = [name]
            if data.get("entity_type") == "FAULTCASE":
                aliases.append(strip_faultcase_suffix(name))
            if data.get("entity_type") == "EQUIPMENT" and name.endswith("系统"):
                aliases.append(name[:-2])
            self.nodes.append(
                NodeRecord(
                    name=name,
                    entity_type=data.get("entity_type", ""),
                    description=data.get("description", ""),
                    source_id=data.get("source_id", ""),
                    aliases=list(dict.fromkeys([a for a in aliases if a])),
                )
            )

    @staticmethod
    def split_source_ids(raw: str) -> list[str]:
        return [part for part in str(raw or "").split("<SEP>") if part]

    def score_node(self, query: str, node: NodeRecord) -> tuple[float, bool]:
        q = normalize(query)
        score = 0.0
        exact = False
        for alias in node.aliases:
            a = normalize(alias)
            if not a:
                continue
            if a in q:
                score = max(score, 1000 + len(a) * 10)
                exact = True
            elif q in a:
                score = max(score, 300 + len(q) * 5)
            else:
                overlap = sum(1 for ch in set(a) if ch in q)
                if overlap >= 2:
                    score = max(score, overlap * 10 / max(len(set(a)), 1))
        if node.entity_type == "FAULTCASE":
            score += 5
        return score, exact

    def recall(self, query: str, top_k: int = 5) -> dict[str, Any]:
        scored = []
        exact_fault_nodes: list[NodeRecord] = []
        exact_equipment_nodes: list[NodeRecord] = []
        for node in self.nodes:
            score, exact = self.score_node(query, node)
            if score > 0:
                scored.append((score, node))
            if exact and node.entity_type == "FAULTCASE":
                exact_fault_nodes.append(node)
            elif exact and node.entity_type == "EQUIPMENT":
                exact_equipment_nodes.append(node)
        scored.sort(key=lambda item: item[0], reverse=True)

        if exact_fault_nodes:
            expanded = {node.name: node for node in exact_fault_nodes}
            for node in exact_fault_nodes:
                for neighbor in self.graph.neighbors(node.name):
                    expanded.setdefault(
                        neighbor,
                        NodeRecord(
                            name=neighbor,
                            entity_type=self.graph.nodes[neighbor].get("entity_type", ""),
                            description=self.graph.nodes[neighbor].get("description", ""),
                            source_id=self.graph.nodes[neighbor].get("source_id", ""),
                            aliases=[neighbor],
                        ),
                    )
            top_nodes = list(expanded.values())[:top_k]
        elif exact_equipment_nodes:
            expanded = {node.name: node for node in exact_equipment_nodes}
            rescored_neighbors: list[tuple[float, NodeRecord]] = []
            for node in exact_equipment_nodes:
                for neighbor in self.graph.neighbors(node.name):
                    neighbor_data = self.graph.nodes[neighbor]
                    neighbor_node = NodeRecord(
                        name=neighbor,
                        entity_type=neighbor_data.get("entity_type", ""),
                        description=neighbor_data.get("description", ""),
                        source_id=neighbor_data.get("source_id", ""),
                        aliases=[neighbor],
                    )
                    score, exact = self.score_node(query, neighbor_node)
                    if exact or score > 0:
                        rescored_neighbors.append(
                            (900.0 if exact else score, neighbor_node)
                        )
            rescored_neighbors.sort(key=lambda item: item[0], reverse=True)
            remaining = max(0, top_k - len(expanded))
            for _, node in rescored_neighbors[:remaining]:
                expanded.setdefault(node.name, node)
            top_nodes = list(expanded.values())[:top_k]
        else:
            top_nodes = [node for _, node in scored[:top_k]]

        if exact_fault_nodes:
            source_seed_nodes = exact_fault_nodes
        elif exact_equipment_nodes:
            source_seed_nodes = [
                node for node in top_nodes if node.entity_type == "FAULTCASE"
            ] or top_nodes
        else:
            source_seed_nodes = top_nodes

        source_ids: list[str] = []
        seen = set()
        for node in source_seed_nodes:
            for source_id in self.split_source_ids(node.source_id):
                if source_id and source_id not in seen:
                    source_ids.append(source_id)
                    seen.add(source_id)

        sources = [
            {
                "source_id": sid,
                "content": self.text_chunks[sid]["content"],
            }
            for sid in source_ids
            if sid in self.text_chunks
        ]
        entities = [
            {
                "entity_name": node.name,
                "entity_type": node.entity_type,
                "source_id": node.source_id,
                "description": node.description,
            }
            for node in top_nodes
        ]
        return {"query": query, "entities": entities, "sources": sources}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark fault-case retrieval without LLM query parsing."
    )
    parser.add_argument("--working-dir", required=True, help="MiniRAG working directory")
    parser.add_argument("--top-k", type=int, default=5, help="Max recalled entities")
    parser.add_argument(
        "--queries",
        nargs="*",
        default=[
            "主辅机冷却水流量变小时应该怎么处理？",
            "操舵装置相关的常见故障有哪些？",
            "雨刮器使用时有什么注意事项？",
            "24V供电故障先查什么？",
        ],
        help="Queries to benchmark",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    retriever = FaultCaseNoLLMRetriever(Path(args.working_dir))
    print(
        json.dumps(
            {
                "working_dir": str(Path(args.working_dir).resolve()),
                "node_count": retriever.graph.number_of_nodes(),
                "edge_count": retriever.graph.number_of_edges(),
                "chunk_count": len(retriever.text_chunks),
            },
            ensure_ascii=False,
        )
    )
    for query in args.queries:
        start = time.perf_counter()
        result = retriever.recall(query, top_k=args.top_k)
        elapsed = time.perf_counter() - start
        print(
            json.dumps(
                {
                    "query": query,
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "entities": result["entities"],
                    "source_ids": [item["source_id"] for item in result["sources"]],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
