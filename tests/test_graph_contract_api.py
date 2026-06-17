import json
import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

rouge_module = types.ModuleType("rouge")
ascii_colors_module = types.ModuleType("ascii_colors")
pm_module = types.ModuleType("pipmaster")


class _FakeRouge:
    def __init__(self, *args, **kwargs):
        pass


setattr(rouge_module, "Rouge", _FakeRouge)
sys.modules.setdefault("rouge", rouge_module)


class _FakeASCIIColors:
    @staticmethod
    def info(*args, **kwargs):
        return None

    @staticmethod
    def yellow(*args, **kwargs):
        return None


def _trace_exception(*args, **kwargs):
    return None


setattr(ascii_colors_module, "ASCIIColors", _FakeASCIIColors)
setattr(ascii_colors_module, "trace_exception", _trace_exception)
sys.modules.setdefault("ascii_colors", ascii_colors_module)


def _is_installed(*args, **kwargs):
    return True


def _install(*args, **kwargs):
    return None


setattr(pm_module, "is_installed", _is_installed)
setattr(pm_module, "install", _install)
sys.modules.setdefault("pipmaster", pm_module)


class FakeMiniRAG:
    instances: list["FakeMiniRAG"] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.datasource_id = kwargs.get("datasource_id", "")
        self.faultcase_alias_store = None
        self.calls: list[tuple[str, tuple, dict]] = []
        self.full_graph = {
            "nodes": [
                {"id": "A", "labels": ["A"], "entity_type": "EQUIPMENT"},
                {"id": "B", "labels": ["B"], "entity_type": "FAULTCASE"},
            ],
            "edges": [
                {"source": "A", "target": "B", "type": "RELATED_TO"},
            ],
        }
        self.subgraphs = {
            "A": {
                "nodes": [
                    {"id": "A", "labels": ["A"], "entity_type": "EQUIPMENT"},
                    {"id": "B", "labels": ["B"], "entity_type": "FAULTCASE"},
                ],
                "edges": [
                    {"source": "A", "target": "B", "type": "RELATED_TO"},
                ],
            }
        }
        self.node_details = {
            "A": {
                "label": "A",
                "entity_type": "EQUIPMENT",
                "degree": 1,
                "properties": {"entity_name": "A"},
                "relationships": [],
            }
        }
        FakeMiniRAG.instances.append(self)

    async def get_graph_labels(self, datasource_id=None):
        self.calls.append(("get_graph_labels", (datasource_id,), {}))
        self._assert_datasource(datasource_id)
        return ["A", "B"]

    async def get_graph_summary(self, datasource_id=None):
        self.calls.append(("get_graph_summary", (datasource_id,), {}))
        self._assert_datasource(datasource_id)
        return {"total_nodes": 2, "total_edges": 1, "type_counts": []}

    async def get_graph_full(self, datasource_id=None, max_nodes=1000, max_edges=5000):
        self.calls.append(("get_graph_full", (datasource_id, max_nodes, max_edges), {}))
        self._assert_datasource(datasource_id)
        if (
            len(self.full_graph["nodes"]) > max_nodes
            or len(self.full_graph["edges"]) > max_edges
        ):
            raise ValueError(
                f"Full graph too large for datasource export: {len(self.full_graph['nodes'])} nodes, {len(self.full_graph['edges'])} edges"
            )
        return self.full_graph

    async def get_graps(self, nodel_label, max_depth=5, datasource_id=None):
        self.calls.append(("get_graps", (nodel_label, max_depth, datasource_id), {}))
        self._assert_datasource(datasource_id)
        if nodel_label not in self.subgraphs:
            raise ValueError("Graph node not found")
        return self.subgraphs[nodel_label]

    async def get_graph_node_detail(
        self, node_label, max_relationships=20, datasource_id=None
    ):
        self.calls.append(
            (
                "get_graph_node_detail",
                (node_label, max_relationships, datasource_id),
                {},
            )
        )
        self._assert_datasource(datasource_id)
        return self.node_details.get(node_label)

    def _assert_datasource(self, datasource_id):
        if self.datasource_id and datasource_id != self.datasource_id:
            raise ValueError(f"Datasource mismatch: requested {datasource_id}")


def _build_test_app(monkeypatch, tmp_path: Path):
    from minirag.api import minirag_server

    monkeypatch.setattr(minirag_server, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(minirag_server, "MiniRAG", FakeMiniRAG)
    FakeMiniRAG.instances.clear()

    datasource_root = tmp_path / "datasources" / "local_ship_docs"
    secondary_datasource_root = tmp_path / "datasources" / "local_demo_docs"
    (tmp_path / "datasources").mkdir(parents=True, exist_ok=True)
    datasource_root.mkdir(parents=True, exist_ok=True)
    secondary_datasource_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "datasources" / "registry.json").write_text(
        json.dumps(
            [
                {
                    "id": "local_ship_docs",
                    "name": "船舶维修资料库",
                    "type": "local_fs",
                    "path": "./datasources/local_ship_docs",
                },
                {
                    "id": "local_demo_docs",
                    "name": "演示本地数据源",
                    "type": "local_fs",
                    "path": "./datasources/local_demo_docs",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (datasource_root / "datasource.yaml").write_text(
        "\n".join(
            [
                "id: local_ship_docs",
                "name: 船舶维修资料库",
                "type: local_fs",
                "paths:",
                "  source_root: ./source/raw",
                "  staging_root: ./staging",
                "  output_root: ./outputs",
                "pipeline:",
                "  profile: ship_faultcase_graph",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (secondary_datasource_root / "datasource.yaml").write_text(
        "\n".join(
            [
                "id: local_demo_docs",
                "name: 演示本地数据源",
                "type: local_fs",
                "paths:",
                "  source_root: ./source/raw",
                "  staging_root: ./staging",
                "  output_root: ./outputs",
                "pipeline:",
                "  profile: ship_faultcase_graph",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (datasource_root / "source" / "raw").mkdir(parents=True, exist_ok=True)
    (datasource_root / "outputs" / "graph" / "workdir").mkdir(
        parents=True, exist_ok=True
    )
    (datasource_root / "staging").mkdir(parents=True, exist_ok=True)
    (secondary_datasource_root / "source" / "raw").mkdir(parents=True, exist_ok=True)
    (secondary_datasource_root / "outputs" / "graph" / "workdir").mkdir(
        parents=True, exist_ok=True
    )
    (secondary_datasource_root / "staging").mkdir(parents=True, exist_ok=True)

    args = minirag_server.parse_args()
    args.graph_storage = "NetworkXStorage"
    args.llm_binding = "ollama"
    args.embedding_binding = "ollama"
    args.query_llm_binding = "ollama"
    args.llm_binding_host = "http://localhost:11434"
    args.embedding_binding_host = "http://localhost:11434"
    args.query_llm_binding_host = "http://localhost:11434"
    args.auto_scan_at_startup = False

    app = minirag_server.create_app(args)
    return app, FakeMiniRAG.instances[-1], args.datasource_id


def _test_client(app):
    return importlib.import_module("fastapi.testclient").TestClient(app)


class TestGraphContractAPI:
    def test_full_graph_happy_path(self, monkeypatch, tmp_path):
        app, fake_query, datasource_id = _build_test_app(monkeypatch, tmp_path)
        client = _test_client(app)

        response = client.get(
            "/graphs",
            params={"datasource_id": datasource_id, "mode": "full"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["datasource_id"] == datasource_id
        assert data["graph_mode"] == "full"
        assert data["graph_state"] == "ready"
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert fake_query.calls[-1][0] == "get_graph_full"

    def test_subgraph_happy_path(self, monkeypatch, tmp_path):
        app, fake_query, datasource_id = _build_test_app(monkeypatch, tmp_path)
        client = _test_client(app)

        response = client.get(
            "/graphs",
            params={"datasource_id": datasource_id, "label": "A"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["datasource_id"] == datasource_id
        assert data["graph_mode"] == "label"
        assert data["label"] == "A"
        assert len(data["nodes"]) == 2
        assert fake_query.calls[-1][0] == "get_graps"

    def test_datasource_scope_rejection(self, monkeypatch, tmp_path):
        app, _, datasource_id = _build_test_app(monkeypatch, tmp_path)
        client = _test_client(app)

        response = client.get(
            "/graph/summary",
            params={"datasource_id": "local_demo_docs"},
        )

        assert response.status_code == 404
        assert "Datasource not found" in response.json()["detail"]

    def test_empty_full_graph_returns_explicit_empty_state(self, monkeypatch, tmp_path):
        app, fake_query, datasource_id = _build_test_app(monkeypatch, tmp_path)
        fake_query.full_graph = {"nodes": [], "edges": []}
        client = _test_client(app)

        response = client.get(
            "/graphs",
            params={"datasource_id": datasource_id, "mode": "full"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["graph_state"] == "empty"
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_full_graph_guardrail_rejects_oversized_payload(
        self, monkeypatch, tmp_path
    ):
        app, fake_query, datasource_id = _build_test_app(monkeypatch, tmp_path)
        fake_query.full_graph = {
            "nodes": [
                {"id": "A", "labels": ["A"], "entity_type": "EQUIPMENT"},
                {"id": "B", "labels": ["B"], "entity_type": "FAULTCASE"},
            ],
            "edges": [],
        }
        client = _test_client(app)

        response = client.get(
            "/graphs",
            params={"datasource_id": datasource_id, "mode": "full", "max_nodes": 1},
        )

        assert response.status_code == 413
        assert "too large" in response.json()["detail"].lower()
