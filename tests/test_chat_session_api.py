# pyright: reportMissingImports=false

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

rouge_module = types.ModuleType("rouge")
ascii_colors_module = types.ModuleType("ascii_colors")
pm_module = types.ModuleType("pipmaster")
neo4j_module = types.ModuleType("neo4j")
dotenv_module = types.ModuleType("dotenv")


class _FakeRouge:
    def __init__(self, *args, **kwargs):
        pass


class _FakeASCIIColors:
    @staticmethod
    def info(*args, **kwargs):
        return None

    @staticmethod
    def yellow(*args, **kwargs):
        return None

    @staticmethod
    def warning(*args, **kwargs):
        return None

    @staticmethod
    def error(*args, **kwargs):
        return None


def _trace_exception(*args, **kwargs):
    return None


def _is_installed(*args, **kwargs):
    return True


def _install(*args, **kwargs):
    return None


setattr(rouge_module, "Rouge", _FakeRouge)
setattr(ascii_colors_module, "ASCIIColors", _FakeASCIIColors)
setattr(ascii_colors_module, "trace_exception", _trace_exception)
setattr(pm_module, "is_installed", _is_installed)
setattr(pm_module, "install", _install)
setattr(neo4j_module, "AsyncGraphDatabase", object)
setattr(dotenv_module, "load_dotenv", lambda *args, **kwargs: None)

sys.modules.setdefault("rouge", rouge_module)
sys.modules.setdefault("ascii_colors", ascii_colors_module)
sys.modules.setdefault("pipmaster", pm_module)
sys.modules.setdefault("neo4j", neo4j_module)
sys.modules.setdefault("dotenv", dotenv_module)

from minirag.api import minirag_server as ms


TEST_DATASOURCE_ID = "local_ship_docs"


class FakeMiniRAG:
    instances: list["FakeMiniRAG"] = []
    stream_chunks: list[str] = ["第一段", "第二段"]
    non_stream_response: str = "第一段第二段"

    def __init__(self, *args, **kwargs):
        self.datasource_id = kwargs.get("datasource_id", "")
        self.faultcase_alias_store = None
        self.calls: list[dict[str, object]] = []
        FakeMiniRAG.instances.append(self)

    async def aquery(self, query, param):
        self.calls.append(
            {
                "query": query,
                "conversation_history": list(param.conversation_history),
                "history_turns": param.history_turns,
                "mode": param.mode,
                "stream": param.stream,
            }
        )

        if not param.stream:
            return type(self).non_stream_response

        async def _stream():
            for chunk in type(self).stream_chunks:
                yield chunk

        return _stream()


def _build_app(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "MiniRAG", FakeMiniRAG)
    monkeypatch.setattr(ms, "REPO_ROOT", tmp_path)
    FakeMiniRAG.instances.clear()

    datasource_root = tmp_path / "datasources" / TEST_DATASOURCE_ID
    (tmp_path / "datasources").mkdir(parents=True, exist_ok=True)
    datasource_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "datasources" / "registry.json").write_text(
        json.dumps(
            [
                {
                    "id": TEST_DATASOURCE_ID,
                    "name": "船舶维修资料库",
                    "type": "local_fs",
                    "path": f"./datasources/{TEST_DATASOURCE_ID}",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (datasource_root / "datasource.yaml").write_text(
        "\n".join(
            [
                f"id: {TEST_DATASOURCE_ID}",
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

    (datasource_root / "source" / "raw").mkdir(parents=True, exist_ok=True)
    (datasource_root / "staging").mkdir(parents=True, exist_ok=True)
    (datasource_root / "outputs" / "graph" / "workdir").mkdir(
        parents=True, exist_ok=True
    )

    monkeypatch.setattr(sys, "argv", ["pytest"])
    args = ms.parse_args()
    args.graph_storage = "NetworkXStorage"
    args.llm_binding = "ollama"
    args.embedding_binding = "ollama"
    args.query_llm_binding = "ollama"
    args.llm_binding_host = "http://localhost:11434"
    args.embedding_binding_host = "http://localhost:11434"
    args.query_llm_binding_host = "http://localhost:11434"
    args.auto_scan_at_startup = False

    app = ms.create_app(args)
    return app, datasource_root


def test_chat_state_roundtrip_and_markdown_export(monkeypatch, tmp_path):
    FakeMiniRAG.stream_chunks = ["第一段", "第二段"]
    FakeMiniRAG.non_stream_response = "第一段第二段"
    app, datasource_root = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    initial = client.get("/chat/state", params={"datasource_id": TEST_DATASOURCE_ID})
    assert initial.status_code == 200
    assert initial.json()["sessions"] == []

    payload = {
        "datasource_id": TEST_DATASOURCE_ID,
        "active_session_id": "session-a",
        "sessions": [
            {
                "id": "session-a",
                "title": "压缩机启动排查",
                "updatedAt": 1760000000000,
                "manualTitle": True,
                "messages": [
                    {
                        "id": "user-1",
                        "role": "user",
                        "content": "压缩机为什么起不来？",
                        "timestamp": "09:00",
                    },
                    {
                        "id": "assistant-1",
                        "role": "assistant",
                        "content": "先检查控制电源，再看热继电器。",
                        "timestamp": "09:00",
                        "mode": "graph_text_hybrid",
                        "endpoint": "/query/stream/plain",
                        "latencyMs": 210,
                        "contextSources": ["doc1.md"],
                        "evidenceItems": [
                            {
                                "id": "evidence-1",
                                "title": "doc1.md",
                                "snippet": "控制电源异常会导致无法启动",
                                "raw": "控制电源异常会导致无法启动",
                            }
                        ],
                    },
                ],
            }
        ],
    }

    saved = client.put("/chat/state", json=payload)
    assert saved.status_code == 200
    assert saved.json()["active_session_id"] == "session-a"
    assert saved.json()["sessions"][0]["manualTitle"] is True

    store_file = (
        datasource_root / "outputs" / "graph" / "workdir" / "chat_sessions.json"
    )
    assert store_file.exists()

    exported = client.post(
        "/chat/export",
        json={
            "datasource_id": TEST_DATASOURCE_ID,
            "session_id": "session-a",
            "message_id": "assistant-1",
        },
    )
    assert exported.status_code == 200
    export_payload = exported.json()
    export_path = Path(export_payload["path"])
    assert export_path.exists()
    assert export_payload["relative_path"].startswith(
        "datasources/local_ship_docs/outputs/exports/chat/"
    )
    assert "# 单条回答导出" in export_payload["content"]
    assert "压缩机为什么起不来？" in export_payload["content"]
    export_text = export_path.read_text(encoding="utf-8")
    assert "# 单条回答导出" in export_text
    assert "压缩机为什么起不来？" in export_text
    assert "先检查控制电源，再看热继电器。" in export_text
    assert "doc1.md" in export_text


def test_query_plain_ignores_conversation_history_in_backend(monkeypatch, tmp_path):
    FakeMiniRAG.stream_chunks = ["第一段", "第二段"]
    FakeMiniRAG.non_stream_response = "第一段第二段"
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/query/plain",
        json={
            "query": "那第二步呢？",
            "mode": "graph_text_hybrid",
            "stream": False,
            "only_need_context": False,
            "conversation_history": [
                {"role": "user", "content": "压缩机为什么起不来？"},
                {
                    "role": "assistant",
                    "content": "先检查控制电源，再看热继电器。",
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.text == "第一段第二段"

    query_rag = FakeMiniRAG.instances[-1]
    assert query_rag.calls[-1]["query"] == "那第二步呢？"
    assert query_rag.calls[-1]["conversation_history"] == []
    assert query_rag.calls[-1]["history_turns"] == 0
    assert query_rag.calls[-1]["stream"] is False


def test_chat_export_download_returns_markdown_attachment(monkeypatch, tmp_path):
    FakeMiniRAG.stream_chunks = ["第一段", "第二段"]
    FakeMiniRAG.non_stream_response = "第一段第二段"
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    payload = {
        "datasource_id": TEST_DATASOURCE_ID,
        "active_session_id": "session-a",
        "sessions": [
            {
                "id": "session-a",
                "title": "压缩机启动排查",
                "updatedAt": 1760000000000,
                "manualTitle": True,
                "messages": [
                    {
                        "id": "user-1",
                        "role": "user",
                        "content": "压缩机为什么起不来？",
                        "timestamp": "09:00",
                    },
                    {
                        "id": "assistant-1",
                        "role": "assistant",
                        "content": "先检查控制电源，再看热继电器。",
                        "timestamp": "09:00",
                        "mode": "graph_text_hybrid",
                        "endpoint": "/query/stream/plain",
                    },
                ],
            }
        ],
    }

    saved = client.put("/chat/state", json=payload)
    assert saved.status_code == 200

    downloaded = client.post(
        "/chat/export/download",
        json={
            "datasource_id": TEST_DATASOURCE_ID,
            "session_id": "session-a",
            "message_id": "assistant-1",
        },
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("text/markdown")
    assert "attachment;" in downloaded.headers["content-disposition"]
    assert "单条回答导出" in downloaded.text
    assert "压缩机为什么起不来？" in downloaded.text
    assert "先检查控制电源，再看热继电器。" in downloaded.text


def test_query_stream_plain_falls_back_to_non_stream_on_empty_stream(
    monkeypatch, tmp_path
):
    FakeMiniRAG.stream_chunks = []
    FakeMiniRAG.non_stream_response = "回退成功"
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/query/stream/plain",
        json={
            "query": "空流时回退",
            "mode": "graph_text_hybrid",
            "stream": True,
            "only_need_context": False,
            "conversation_history": [],
        },
    )

    assert response.status_code == 200
    assert response.text == "回退成功"

    query_rag = FakeMiniRAG.instances[-1]
    assert [call["stream"] for call in query_rag.calls[-2:]] == [True, False]


def test_query_plain_returns_502_for_empty_non_stream(monkeypatch, tmp_path):
    FakeMiniRAG.stream_chunks = ["第一段", "第二段"]
    FakeMiniRAG.non_stream_response = ""
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/query/plain",
        json={
            "query": "空响应",
            "mode": "graph_text_hybrid",
            "stream": False,
            "only_need_context": False,
            "conversation_history": [],
        },
    )

    assert response.status_code == 502
    assert "empty response body" in response.json()["detail"]
