import os
from dataclasses import dataclass
from typing import Any, Union

from neo4j import AsyncGraphDatabase

from minirag.base import BaseGraphStorage
from minirag.utils import logger


def _clean_props(props: dict[str, Any] | None) -> dict[str, Any]:
    if not props:
        return {}
    cleaned = {}
    for key, value in props.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            cleaned[key] = [str(item) for item in value]
        elif isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned


@dataclass
class Neo4JStorage(BaseGraphStorage):
    def __post_init__(self):
        self._uri = os.getenv("NEO4J_URI")
        self._username = os.getenv("NEO4J_USERNAME")
        self._password = os.getenv("NEO4J_PASSWORD")
        self._database = os.getenv("NEO4J_DATABASE", "neo4j")
        if not all([self._uri, self._username, self._password]):
            raise ValueError(
                "NEO4J_URI, NEO4J_USERNAME, and NEO4J_PASSWORD must be set for Neo4JStorage"
            )

        uri = self._uri
        username = self._username
        password = self._password
        assert uri is not None and username is not None and password is not None

        self._driver = AsyncGraphDatabase.driver(uri, auth=(username, password))

    async def index_done_callback(self):
        return None

    async def query_done_callback(self):
        return None

    async def _run(self, query: Any, **params):
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, **params)
            return [record async for record in result]

    async def get_types(self) -> tuple[list[str], list[str]]:
        records = await self._run(
            """
            MATCH (n:GraphNode)
            WHERE n.entity_type IS NOT NULL
            RETURN DISTINCT n.entity_type AS entity_type
            """
        )
        types_with_case = [
            record["entity_type"] for record in records if record["entity_type"]
        ]
        types = sorted({str(item).lower() for item in types_with_case})
        return types, sorted(set(types_with_case))

    async def get_node_from_types(self, type_list) -> list[dict[str, Any]]:
        records = await self._run(
            """
            MATCH (n:GraphNode)
            WHERE n.entity_type IN $type_list
            RETURN n.node_id AS entity_name, properties(n) AS props
            """,
            type_list=list(type_list or []),
        )
        return [
            {
                **record["props"],
                "entity_name": record["entity_name"],
            }
            for record in records
        ]

    async def has_node(self, node_id: str) -> bool:
        records = await self._run(
            "MATCH (n:GraphNode {node_id: $node_id}) RETURN count(n) > 0 AS exists",
            node_id=node_id,
        )
        return bool(records and records[0]["exists"])

    async def has_edge(self, source_node_id: str, target_node_id: str) -> bool:
        records = await self._run(
            """
            MATCH (a:GraphNode {node_id: $source_node_id})-[r:RELATED_TO]-(b:GraphNode {node_id: $target_node_id})
            RETURN count(r) > 0 AS exists
            """,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )
        return bool(records and records[0]["exists"])

    async def node_degree(self, node_id: str) -> int:
        records = await self._run(
            """
            MATCH (n:GraphNode {node_id: $node_id})
            RETURN size([(n)-[:RELATED_TO]-() | 1]) AS degree
            """,
            node_id=node_id,
        )
        return int(records[0]["degree"]) if records else 0

    async def edge_degree(self, src_id: str, tgt_id: str) -> int:
        return await self.node_degree(src_id) + await self.node_degree(tgt_id)

    async def get_node(self, node_id: str) -> Union[dict, None]:
        records = await self._run(
            "MATCH (n:GraphNode {node_id: $node_id}) RETURN properties(n) AS props",
            node_id=node_id,
        )
        return records[0]["props"] if records else None

    async def get_edge(
        self, source_node_id: str, target_node_id: str
    ) -> Union[dict, None]:
        records = await self._run(
            """
            MATCH (a:GraphNode {node_id: $source_node_id})-[r:RELATED_TO]-(b:GraphNode {node_id: $target_node_id})
            RETURN properties(r) AS props
            LIMIT 1
            """,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )
        return records[0]["props"] if records else None

    async def get_node_edges(self, source_node_id: str):
        records = await self._run(
            """
            MATCH (a:GraphNode {node_id: $source_node_id})-[r:RELATED_TO]-(b:GraphNode)
            RETURN a.node_id AS source, b.node_id AS target
            ORDER BY b.node_id
            """,
            source_node_id=source_node_id,
        )
        return [(record["source"], record["target"]) for record in records] or None

    async def upsert_node(self, node_id: str, node_data: dict[str, str]):
        props = _clean_props(node_data)
        records = await self._run(
            """
            MERGE (n:GraphNode {node_id: $node_id})
            SET n += $props
            RETURN n.node_id AS node_id
            """,
            node_id=node_id,
            props=props,
        )
        return records[0]["node_id"] if records else node_id

    async def upsert_edge(
        self, source_node_id: str, target_node_id: str, edge_data: dict[str, str]
    ):
        props = _clean_props(edge_data)
        await self._run(
            """
            MERGE (a:GraphNode {node_id: $source_node_id})
            MERGE (b:GraphNode {node_id: $target_node_id})
            MERGE (a)-[r:RELATED_TO]-(b)
            SET r += $props
            """,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            props=props,
        )

    async def delete_node(self, node_id: str):
        await self._run(
            "MATCH (n:GraphNode {node_id: $node_id}) DETACH DELETE n",
            node_id=node_id,
        )

    async def embed_nodes(self, algorithm: str):
        raise NotImplementedError("Node embedding is not used in the current mainline.")

    async def get_all_labels(self, limit: int | None = None) -> list[str]:
        query = "MATCH (n:GraphNode) RETURN n.node_id AS node_id ORDER BY n.node_id"
        params: dict[str, int] = {}
        if limit is not None and limit > 0:
            query += " LIMIT $limit"
            params["limit"] = int(limit)
        records = await self._run(query, **params)
        return [record["node_id"] for record in records]

    async def get_all_label_entries(
        self, limit: int | None = None
    ) -> list[dict[str, str]]:
        query = """
            MATCH (n:GraphNode)
            RETURN n.node_id AS label, n.entity_type AS entity_type
            ORDER BY n.node_id
        """
        params: dict[str, int] = {}
        if limit is not None and limit > 0:
            query += "\nLIMIT $limit"
            params["limit"] = int(limit)
        records = await self._run(query, **params)
        return [
            {
                "label": str(record["label"]),
                "entity_type": str(record["entity_type"] or "UNKNOWN"),
            }
            for record in records
            if record["label"]
        ]

    async def get_graph(self) -> dict[str, list[dict[str, Any]]]:
        node_records = await self._run(
            """
            MATCH (n:GraphNode)
            RETURN n.node_id AS node_id, properties(n) AS props
            ORDER BY n.node_id
            """
        )
        edge_records = await self._run(
            """
            MATCH (source:GraphNode)-[r:RELATED_TO]->(target:GraphNode)
            RETURN
              source.node_id AS source,
              target.node_id AS target,
              properties(r) AS props,
              type(r) AS relation_type
            ORDER BY source, target
            """
        )
        return {
            "nodes": [
                {
                    "id": str(record["node_id"]),
                    "labels": [str(record["node_id"])],
                    **(record["props"] or {}),
                }
                for record in node_records
                if record["node_id"]
            ],
            "edges": [
                {
                    "source": str(record["source"]),
                    "target": str(record["target"]),
                    "type": str(record["relation_type"] or "RELATED_TO"),
                    **(record["props"] or {}),
                }
                for record in edge_records
                if record["source"] and record["target"]
            ],
        }

    async def get_knowledge_graph(self, node_label: str, max_depth: int = 5):
        safe_max_depth = max(0, int(max_depth))
        node_records = await self._run(
            f"""
            MATCH (start:GraphNode {{node_id: $node_label}})
            OPTIONAL MATCH p=(start)-[:RELATED_TO*0..{safe_max_depth}]-(neighbor:GraphNode)
            UNWIND nodes(p) AS n
            RETURN DISTINCT n.node_id AS node_id, properties(n) AS props
            ORDER BY n.node_id
            """,
            node_label=node_label,
        )
        edge_records = await self._run(
            f"""
            MATCH (start:GraphNode {{node_id: $node_label}})
            OPTIONAL MATCH p=(start)-[r:RELATED_TO*1..{safe_max_depth}]-(neighbor:GraphNode)
            UNWIND relationships(p) AS rel
            WITH DISTINCT rel
            RETURN startNode(rel).node_id AS source, endNode(rel).node_id AS target, properties(rel) AS props
            ORDER BY source, target
            """,
            node_label=node_label,
        )
        return {
            "nodes": [
                {"id": record["node_id"], **(record["props"] or {})}
                for record in node_records
            ],
            "edges": [
                {
                    "source": record["source"],
                    "target": record["target"],
                    **(record["props"] or {}),
                }
                for record in edge_records
            ],
        }

    async def get_node_as_entity(self, node_id: str) -> Union[dict, None]:
        node = await self.get_node(node_id)
        if not node:
            return None
        return {
            "entity_name": node_id,
            **node,
        }

    async def get_graph_summary(self) -> dict[str, Any]:
        total_nodes_result = await self._run(
            "MATCH (n:GraphNode) RETURN count(n) AS count"
        )
        total_edges_result = await self._run(
            "MATCH (:GraphNode)-[r:RELATED_TO]-(:GraphNode) RETURN count(r) AS count"
        )
        type_counts_result = await self._run(
            """
            MATCH (n:GraphNode)
            WHERE n.entity_type IS NOT NULL
            RETURN n.entity_type AS type, count(*) AS count
            ORDER BY count DESC, type ASC
            """
        )
        return {
            "total_nodes": int(total_nodes_result[0]["count"])
            if total_nodes_result
            else 0,
            "total_edges": int(total_edges_result[0]["count"])
            if total_edges_result
            else 0,
            "type_counts": [
                {"type": record["type"], "count": int(record["count"])}
                for record in type_counts_result
            ],
        }

    async def close(self) -> None:
        if getattr(self, "_driver", None) is not None:
            await self._driver.close()
            logger.info("Closed Neo4JStorage driver")
