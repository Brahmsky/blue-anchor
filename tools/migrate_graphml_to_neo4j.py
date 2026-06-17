import argparse
import asyncio
import os
import sys
from pathlib import Path

import networkx as nx

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from minirag.kg.neo4j_impl import Neo4JStorage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate an existing GraphML graph into Neo4j without rebuilding from source documents."
    )
    parser.add_argument(
        "--graphml",
        required=True,
        help="Path to graph_chunk_entity_relation.graphml",
    )
    parser.add_argument("--neo4j-uri", required=True)
    parser.add_argument("--neo4j-username", required=True)
    parser.add_argument("--neo4j-password", required=True)
    parser.add_argument("--neo4j-database", default="neo4j")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing GraphNode/RELATED_TO data before import",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    graph_path = Path(args.graphml).expanduser().resolve()
    if not graph_path.exists():
        raise FileNotFoundError(f"GraphML not found: {graph_path}")

    os.environ["NEO4J_URI"] = args.neo4j_uri
    os.environ["NEO4J_USERNAME"] = args.neo4j_username
    os.environ["NEO4J_PASSWORD"] = args.neo4j_password
    os.environ["NEO4J_DATABASE"] = args.neo4j_database

    graph = nx.read_graphml(graph_path)
    storage = Neo4JStorage(namespace="chunk_entity_relation", global_config={})

    async with storage._driver.session(database=args.neo4j_database) as session:

        async def import_tx(tx):
            if args.clear:
                result = await tx.run("MATCH (n:GraphNode) DETACH DELETE n")
                await result.consume()
            for node_id, node_data in graph.nodes(data=True):
                result = await tx.run(
                    """
                    MERGE (n:GraphNode {node_id: $node_id})
                    SET n += $props
                    """,
                    node_id=str(node_id),
                    props=dict(node_data),
                )
                await result.consume()
            for source, target, edge_data in graph.edges(data=True):
                result = await tx.run(
                    """
                    MERGE (a:GraphNode {node_id: $source})
                    MERGE (b:GraphNode {node_id: $target})
                    MERGE (a)-[r:RELATED_TO]-(b)
                    SET r += $props
                    """,
                    source=str(source),
                    target=str(target),
                    props=dict(edge_data),
                )
                await result.consume()

        await session.execute_write(import_tx)

    print(
        f"Imported {graph.number_of_nodes()} nodes and {graph.number_of_edges()} edges into Neo4j from {graph_path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
