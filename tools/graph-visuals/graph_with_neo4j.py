import argparse
import json
from pathlib import Path

from neo4j import GraphDatabase

from minirag.utils import xml_to_json

BATCH_SIZE_NODES = 500
BATCH_SIZE_EDGES = 100


def parse_args():
    parser = argparse.ArgumentParser(
        description="Import a MiniRAG GraphML knowledge graph into Neo4j"
    )
    parser.add_argument(
        "graphml_path",
        help="Path to graph_chunk_entity_relation.graphml",
    )
    parser.add_argument(
        "--uri",
        default="bolt://localhost:7687",
        help="Neo4j bolt URI",
    )
    parser.add_argument(
        "--username",
        default="neo4j",
        help="Neo4j username",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Neo4j password",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Optional path for the intermediate JSON export",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete all existing nodes and relationships before import",
    )
    return parser.parse_args()


def convert_xml_to_json(xml_path: Path, output_path: Path | None):
    if not xml_path.exists():
        raise FileNotFoundError(f"GraphML file not found: {xml_path}")

    json_data = xml_to_json(str(xml_path))
    if not json_data:
        raise ValueError("Failed to convert GraphML to JSON")

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON file created: {output_path}")

    return json_data


def process_in_batches(tx, query, data, batch_size, key):
    for i in range(0, len(data), batch_size):
        batch = data[i : i + batch_size]
        tx.run(query, {key: batch})


def main():
    args = parse_args()
    graphml_path = Path(args.graphml_path).expanduser().resolve()
    json_output = (
        Path(args.json_output).expanduser().resolve()
        if args.json_output
        else graphml_path.with_name("graph_data.json")
    )

    json_data = convert_xml_to_json(graphml_path, json_output)
    nodes = json_data.get("nodes", [])
    edges = json_data.get("edges", [])

    clear_query = "MATCH (n) DETACH DELETE n"

    create_nodes_query = """
    UNWIND $nodes AS node
    MERGE (e:Entity {id: node.id})
    SET e.entity_type = node.entity_type,
        e.description = node.description,
        e.source_id = node.source_id,
        e.displayName = node.id
    RETURN count(*)
    """

    create_edges_query = """
    UNWIND $edges AS edge
    MATCH (source {id: edge.source})
    MATCH (target {id: edge.target})
    MERGE (source)-[r:RELATED {keywords: edge.keywords, source_id: edge.source_id}]->(target)
    SET r.weight = edge.weight,
        r.description = edge.description
    RETURN count(*)
    """

    set_labels_query = """
    MATCH (n:Entity)
    WHERE n.entity_type IS NOT NULL AND trim(n.entity_type) <> ""
    SET n.displayName = n.id
    RETURN count(*)
    """

    driver = GraphDatabase.driver(args.uri, auth=(args.username, args.password))

    try:
        with driver.session() as session:
            if args.clear:
                session.run(clear_query)
                print("Cleared existing Neo4j data")

            session.execute_write(
                process_in_batches,
                create_nodes_query,
                nodes,
                BATCH_SIZE_NODES,
                "nodes",
            )
            print(f"Imported {len(nodes)} nodes")

            session.execute_write(
                process_in_batches,
                create_edges_query,
                edges,
                BATCH_SIZE_EDGES,
                "edges",
            )
            print(f"Imported {len(edges)} edges")

            session.run(set_labels_query)
            print("Neo4j import completed")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
