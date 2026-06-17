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
        description="Compare a GraphML source graph with its Neo4j-imported version."
    )
    parser.add_argument("--graphml", required=True)
    parser.add_argument("--neo4j-uri", required=True)
    parser.add_argument("--neo4j-username", required=True)
    parser.add_argument("--neo4j-password", required=True)
    parser.add_argument("--neo4j-database", default="neo4j")
    parser.add_argument("--sample-size", type=int, default=10)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    graph_path = Path(args.graphml).expanduser().resolve()
    graph = nx.read_graphml(graph_path)

    os.environ["NEO4J_URI"] = args.neo4j_uri
    os.environ["NEO4J_USERNAME"] = args.neo4j_username
    os.environ["NEO4J_PASSWORD"] = args.neo4j_password
    os.environ["NEO4J_DATABASE"] = args.neo4j_database

    storage = Neo4JStorage(namespace="chunk_entity_relation", global_config={})
    neo_summary = await storage.get_graph_summary()
    nx_types = sorted(
        {
            str(data.get("entity_type"))
            for _, data in graph.nodes(data=True)
            if data.get("entity_type") is not None
        }
    )
    neo_types = sorted([item["type"] for item in neo_summary["type_counts"]])

    print("networkx_nodes", graph.number_of_nodes())
    print("neo4j_nodes", neo_summary["total_nodes"])
    print("networkx_edges", graph.number_of_edges())
    print("neo4j_edges", neo_summary["total_edges"])
    print("networkx_types", nx_types)
    print("neo4j_types", neo_types)

    sample_nodes = list(graph.nodes())[: args.sample_size]
    for node_id in sample_nodes:
        nx_node = graph.nodes[node_id]
        neo_node = await storage.get_node(str(node_id))
        print(f"sample_node={node_id}")
        print("  networkx_keys", sorted(nx_node.keys()))
        print("  neo4j_keys", sorted((neo_node or {}).keys()))
        print(
            "  property_match",
            {
                key: (str(nx_node.get(key)) == str((neo_node or {}).get(key)))
                for key in ["entity_type", "description", "source_id"]
                if key in nx_node or key in (neo_node or {})
            },
        )
        nx_neighbors = sorted([other for left, other in graph.edges(node_id)])
        neo_neighbors = sorted(
            [
                target if source == str(node_id) else source
                for source, target in (await storage.get_node_edges(str(node_id)) or [])
            ]
        )
        print("  neighbor_match", nx_neighbors == neo_neighbors)

    sample_edges = list(graph.edges(data=True))[: args.sample_size]
    for source, target, edge_data in sample_edges:
        neo_edge = await storage.get_edge(str(source), str(target))
        print(f"sample_edge={source}->{target}")
        print("  edge_keys_networkx", sorted(edge_data.keys()))
        print("  edge_keys_neo4j", sorted((neo_edge or {}).keys()))
        print(
            "  edge_property_match",
            {
                key: (str(edge_data.get(key)) == str((neo_edge or {}).get(key)))
                for key in [
                    "description",
                    "keywords",
                    "relation_type",
                    "source_id",
                    "weight",
                ]
                if key in edge_data or key in (neo_edge or {})
            },
        )

    nx_type_sample = nx_types[: min(3, len(nx_types))]
    neo_type_records = await storage.get_node_from_types(nx_type_sample)
    print("type_lookup_sample", nx_type_sample)
    print(
        "type_lookup_count_networkx",
        sum(
            1
            for _, data in graph.nodes(data=True)
            if str(data.get("entity_type")) in nx_type_sample
        ),
    )
    print("type_lookup_count_neo4j", len(neo_type_records or []))


if __name__ == "__main__":
    asyncio.run(main())
