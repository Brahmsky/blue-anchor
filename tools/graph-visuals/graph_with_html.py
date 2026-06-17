import argparse
import random
from pathlib import Path

import networkx as nx
from pyvis.network import Network


def resolve_graph_path(graph_path: str | None) -> Path:
    if graph_path:
        path = Path(graph_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Graph file not found: {path}")
        return path

    candidates = sorted(
        Path("rag_storage").rglob("graph_chunk_entity_relation.graphml"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "No graph_chunk_entity_relation.graphml found under ./rag_storage"
        )
    return candidates[0].resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Render MiniRAG graph as interactive HTML")
    parser.add_argument(
        "graph_path",
        nargs="?",
        help="Optional path to graph_chunk_entity_relation.graphml",
    )
    parser.add_argument(
        "--output",
        default="knowledge_graph.html",
        help="Output HTML file path",
    )
    args = parser.parse_args()

    graph_path = resolve_graph_path(args.graph_path)
    output_path = Path(args.output).expanduser().resolve()

    graph = nx.read_graphml(graph_path)

    net = Network(height="100vh", width="100%", bgcolor="#ffffff", font_color="#222222")
    net.from_nx(graph)

    for node in net.nodes:
        node["color"] = "#{:06x}".format(random.randint(0, 0xFFFFFF))
        title_parts = [f"id: {node.get('id', node.get('label', 'unknown'))}"]
        if node.get("entity_type"):
            title_parts.append(f"type: {node['entity_type']}")
        if node.get("description"):
            title_parts.append(str(node["description"]))
        node["title"] = "<br>".join(title_parts)

    for edge in net.edges:
        title_parts = []
        if edge.get("keywords"):
            title_parts.append(f"keywords: {edge['keywords']}")
        if edge.get("description"):
            title_parts.append(str(edge["description"]))
        if title_parts:
            edge["title"] = "<br>".join(title_parts)

    net.show(str(output_path), notebook=False)
    print(f"Loaded graph: {graph_path}")
    print(f"Nodes: {graph.number_of_nodes()}, Edges: {graph.number_of_edges()}")
    print(f"HTML written to: {output_path}")


if __name__ == "__main__":
    main()
