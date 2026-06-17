# pyright: reportMissingImports=false

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

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

from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from minirag.api import minirag_server as ms


TEST_DATASOURCE_ID = "local_ship_docs"
TEST_DATASOURCE_NAME = "船舶维修资料库"
SECONDARY_DATASOURCE_ID = "local_demo_docs"
SECONDARY_DATASOURCE_NAME = "演示本地数据源"


class FakeDocStatusStorage:
    def __init__(self):
        self._data: dict[str, dict] = {}

    async def get_by_id(self, doc_id: str):
        return self._data.get(doc_id)


class FakeMiniRAG:
    instances: list["FakeMiniRAG"] = []

    def __init__(self, *args, **kwargs):
        self.datasource_id = kwargs.get("datasource_id", "")
        self.kwargs = kwargs
        self.doc_status = FakeDocStatusStorage()
        self.faultcase_alias_store = None
        type(self).instances.append(self)

    def seed_document(self, content: str, doc_id: str):
        self.doc_status._data[doc_id] = {
            "content": content,
            "content_summary": content[:80],
            "content_length": len(content),
            "status": "processed",
            "created_at": "2026-04-15T00:00:00",
            "updated_at": "2026-04-15T00:00:00",
            "chunks_count": 1,
        }

    async def ainsert(self, content):
        doc_id = ms.compute_mdhash_id(ms.clean_text(content), prefix="doc-")
        self.seed_document(content, doc_id)
        return None

    async def areindex_document(self, content, *, new_doc_id=None, purge_doc_id=None):
        if purge_doc_id:
            self.doc_status._data.pop(purge_doc_id, None)
        doc_id = new_doc_id or ms.compute_mdhash_id(
            ms.clean_text(content), prefix="doc-"
        )
        self.seed_document(content, doc_id)
        return doc_id

    async def adelete_document(self, doc_id: str):
        self.doc_status._data.pop(doc_id, None)
        return len(self.doc_status._data)


def _build_app(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "MiniRAG", FakeMiniRAG)
    monkeypatch.setattr(ms, "REPO_ROOT", tmp_path)
    FakeMiniRAG.instances.clear()

    datasource_root = tmp_path / "datasources" / TEST_DATASOURCE_ID
    secondary_datasource_root = tmp_path / "datasources" / SECONDARY_DATASOURCE_ID
    input_dir = datasource_root / "source" / "raw"
    working_dir = datasource_root / "outputs" / "graph" / "workdir"
    pipeline_root = datasource_root / "staging" / "extracted" / "records"

    (tmp_path / "datasources").mkdir(parents=True, exist_ok=True)
    datasource_root.mkdir(parents=True, exist_ok=True)
    secondary_datasource_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "datasources" / "registry.json").write_text(
        json.dumps(
            [
                {
                    "id": TEST_DATASOURCE_ID,
                    "name": TEST_DATASOURCE_NAME,
                    "type": "local_fs",
                    "path": f"./datasources/{TEST_DATASOURCE_ID}",
                },
                {
                    "id": SECONDARY_DATASOURCE_ID,
                    "name": SECONDARY_DATASOURCE_NAME,
                    "type": "local_fs",
                    "path": f"./datasources/{SECONDARY_DATASOURCE_ID}",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (datasource_root / "datasource.yaml").write_text(
        "\n".join(
            [
                f"id: {TEST_DATASOURCE_ID}",
                f"name: {TEST_DATASOURCE_NAME}",
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
                f"id: {SECONDARY_DATASOURCE_ID}",
                f"name: {SECONDARY_DATASOURCE_NAME}",
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

    input_dir.mkdir(parents=True, exist_ok=True)
    working_dir.mkdir(parents=True, exist_ok=True)
    pipeline_root.mkdir(parents=True, exist_ok=True)
    (secondary_datasource_root / "source" / "raw").mkdir(parents=True, exist_ok=True)
    (secondary_datasource_root / "outputs" / "graph" / "workdir").mkdir(
        parents=True, exist_ok=True
    )
    (secondary_datasource_root / "staging").mkdir(parents=True, exist_ok=True)

    source_file = input_dir / "doc1.md"
    source_file.write_text("alpha source content", encoding="utf-8")

    pipeline_dir = pipeline_root / "doc1"
    pipeline_dir.mkdir(parents=True)
    record_source_id = ms.compute_mdhash_id(
        "doc1::llm_000", prefix="chunk-faultcase-"
    )
    (pipeline_dir / "diagnostic_records_llm.json").write_text(
        json.dumps(
            {
                "doc_name": "doc1",
                "record_count": 1,
                "records": [
                    {
                        "record_id": "llm_000",
                        "equipment": "Pump",
                        "fault": "Leak",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (working_dir / "graph_chunk_entity_relation.graphml").write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
                '<key id="d0" for="node" attr.name="entity_type" attr.type="string"/>',
                '<key id="d1" for="node" attr.name="description" attr.type="string"/>',
                '<key id="d2" for="node" attr.name="source_id" attr.type="string"/>',
                '<key id="d7" for="edge" attr.name="source_id" attr.type="string"/>',
                '<graph edgedefault="directed">',
                f'<node id="Pump"><data key="d2">{record_source_id}</data></node>',
                '<node id="Leak">'
                '<data key="d0">FAULTCASE</data>'
                '<data key="d1">[故障现象]\n漏油\n[可能原因]\n密封损坏\n[处理步骤]\n更换密封\n[可能后果]\n污染舱底\n[关键部件]\n密封圈</data>'
                f'<data key="d2">{record_source_id}</data>'
                '</node>',
                '<node id="Other"><data key="d2">chunk-faultcase-other</data></node>',
                f'<edge source="Pump" target="Leak"><data key="d7">{record_source_id}</data></edge>',
                '<edge source="Other" target="Pump"><data key="d7">chunk-faultcase-other</data></edge>',
                "</graph>",
                "</graphml>",
            ]
        ),
        encoding="utf-8",
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
    return (
        app,
        source_file,
        pipeline_dir,
        datasource_root,
        secondary_datasource_root,
    )


def test_parse_args_prefers_resolved_datasource_identity(monkeypatch, tmp_path):
    _build_app(tmp_path, monkeypatch)
    datasource_root = tmp_path / "datasources" / "local_ship_docs"

    monkeypatch.setattr(
        sys, "argv", ["pytest", "--datasource-root", str(datasource_root)]
    )
    args = ms.parse_args()

    assert args.datasource_id == TEST_DATASOURCE_ID
    assert args.datasource_root == str(datasource_root.resolve())
    assert args.input_dir == str((datasource_root / "source" / "raw").resolve())
    assert args.working_dir == str(
        (datasource_root / "outputs" / "graph" / "workdir").resolve()
    )
    assert args.datasource_id != args.input_dir


def test_parse_args_legacy_selectors_warn_and_resolve_canonical_datasource(
    monkeypatch, tmp_path
):
    _build_app(tmp_path, monkeypatch)
    datasource_root = tmp_path / "datasources" / "local_ship_docs"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pytest",
            "--input-dir",
            str(datasource_root / "source" / "raw"),
            "--working-dir",
            str(datasource_root / "outputs" / "graph" / "workdir"),
        ],
    )

    with pytest.warns(DeprecationWarning) as recorded:
        args = ms.parse_args()

    messages = {str(item.message) for item in recorded}
    assert any("--input-dir is deprecated" in message for message in messages)
    assert any(
        "Resolving datasources from legacy input_dir/outputs_root/working_dir"
        in message
        for message in messages
    )
    assert args.datasource_id == TEST_DATASOURCE_ID
    assert args.datasource_root == str(datasource_root.resolve())
    assert args.input_dir == str((datasource_root / "source" / "raw").resolve())
    assert args.working_dir == str(
        (datasource_root / "outputs" / "graph" / "workdir").resolve()
    )


def test_document_lifecycle_detail_reindex_delete_and_pipeline_leakage(
    monkeypatch, tmp_path
):
    app, source_file, pipeline_dir, datasource_root, _ = _build_app(
        tmp_path, monkeypatch
    )
    orphan_source = source_file.parent / "orphan.txt"
    orphan_source.write_text("not ready yet", encoding="utf-8")
    client = TestClient(app)

    before = client.get(
        "/documents/summary", params={"datasource_root": str(datasource_root)}
    )
    assert before.status_code == 200
    assert before.json()["datasource_id"] == TEST_DATASOURCE_ID
    assert before.json()["datasource"] == {
        "datasource_id": TEST_DATASOURCE_ID,
        "datasource_root": str(datasource_root.resolve()),
        "source_root": str((datasource_root / "source" / "raw").resolve()),
        "staging_root": str((datasource_root / "staging").resolve()),
        "output_root": str((datasource_root / "outputs").resolve()),
        "input_dir": str((datasource_root / "source" / "raw").resolve()),
        "working_dir": str(
            (datasource_root / "outputs" / "graph" / "workdir").resolve()
        ),
    }
    assert any(
        item["relative_path"].endswith("diagnostic_records_llm.json")
        for item in before.json()["items"]
    )

    input_item = next(
        item for item in before.json()["items"] if item["relative_path"] == "doc1.md"
    )
    assert input_item["datasource_id"] == TEST_DATASOURCE_ID
    assert input_item["resource_kind"] == "input_dir"
    assert input_item["capabilities"]["reindexable"] is True
    assert input_item["ready_to_query"] is True
    assert input_item["pipeline_record_count"] == 1
    assert input_item["graph_nodes"] == 6
    assert input_item["graph_edges"] == 5

    orphan_item = next(
        item for item in before.json()["items"] if item["relative_path"] == "orphan.txt"
    )
    assert orphan_item["resource_kind"] == "input_dir"
    assert orphan_item["ready_to_query"] is False

    pipeline_item = next(
        item
        for item in before.json()["items"]
        if item["relative_path"].endswith("diagnostic_records_llm.json")
    )
    assert pipeline_item["datasource_id"] == TEST_DATASOURCE_ID
    assert pipeline_item["resource_kind"] == "pipeline"
    assert pipeline_item["capabilities"]["read_only"] is True
    assert pipeline_item["graph_nodes"] == 6
    assert pipeline_item["graph_edges"] == 5

    detail = client.get(
        "/documents/file",
        params={
            "relative_path": "source/raw/doc1.md",
            "datasource_root": str(datasource_root),
        },
    )
    assert detail.status_code == 200
    assert detail.json()["resource_kind"] == "input_dir"
    assert detail.json()["capabilities"]["reindexable"] is True
    assert detail.json()["registry_snapshot"]["ready_to_query"] is True

    pipeline_path = "staging/extracted/records/doc1/diagnostic_records_llm.json"
    pipeline_reject = client.post(
        "/documents/file/reindex",
        params={
            "relative_path": pipeline_path,
            "datasource_root": str(datasource_root),
        },
    )
    assert pipeline_reject.status_code == 400
    assert (
        pipeline_reject.json()["detail"] == "pipeline resources do not support reindex"
    )

    pipeline_reprocess_reject = client.post(
        "/documents/file/reprocess",
        params={
            "relative_path": pipeline_path,
            "datasource_root": str(datasource_root),
        },
    )
    assert pipeline_reprocess_reject.status_code == 400
    assert (
        pipeline_reprocess_reject.json()["detail"]
        == "pipeline resources do not support reprocess"
    )

    pipeline_delete_reject = client.delete(
        "/documents/file",
        params={
            "relative_path": pipeline_path,
            "datasource_root": str(datasource_root),
        },
    )
    assert pipeline_delete_reject.status_code == 400
    assert (
        pipeline_delete_reject.json()["detail"]
        == "pipeline resources do not support delete"
    )

    source_file.write_text("beta source content", encoding="utf-8")
    reindex = client.post(
        "/documents/file/reindex",
        params={
            "relative_path": "source/raw/doc1.md",
            "datasource_root": str(datasource_root),
        },
    )
    assert reindex.status_code == 200

    reprocess = client.post(
        "/documents/file/reprocess",
        params={
            "relative_path": "source/raw/doc1.md",
            "datasource_root": str(datasource_root),
        },
    )
    assert reprocess.status_code == 200
    assert reprocess.json()["message"] == "Document reprocessed: doc1.md"

    delete = client.delete(
        "/documents/file",
        params={
            "relative_path": "source/raw/doc1.md",
            "datasource_root": str(datasource_root),
        },
    )
    assert delete.status_code == 200
    assert not source_file.exists()

    repeated_delete = client.delete(
        "/documents/file",
        params={
            "relative_path": "source/raw/doc1.md",
            "datasource_root": str(datasource_root),
        },
    )
    assert repeated_delete.status_code == 404

    after = client.get(
        "/documents/summary", params={"datasource_root": str(datasource_root)}
    )
    assert after.status_code == 200
    items = after.json()["items"]
    assert all(item.get("source_kind") != "pipeline" for item in items)
    assert not pipeline_dir.exists()


def test_upload_generates_chunk_and_routing_artifacts(monkeypatch, tmp_path):
    app, _, _, datasource_root, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    upload = client.post(
        "/documents/upload",
        files={
            "file": (
                "new_manual.md",
                "# 第一章\n这是一个足够长的段落，用于验证上传后会生成 raw chunk 与路由产物，并且能够进入当前知识库的分块加载逻辑。\n",
                "text/markdown",
            )
        },
    )
    assert upload.status_code == 200
    assert upload.json()["status"] == "success"

    source_path = datasource_root / "source" / "raw" / "new_manual.md"
    assert source_path.exists()

    doc_dir = datasource_root / "staging" / "chunks" / "new_manual"
    raw_chunks_dir = doc_dir / "raw_chunks"
    assert raw_chunks_dir.exists()
    chapter_files = sorted(raw_chunks_dir.glob("*.json"))
    assert len(chapter_files) == 1

    chapter_payload = json.loads(chapter_files[0].read_text(encoding="utf-8"))
    assert chapter_payload["doc_name"] == "new_manual.md"
    assert len(chapter_payload["chunks"]) == 1

    good_chunks_dir = doc_dir / "good_chunks"
    assert good_chunks_dir.exists()
    assert sorted(good_chunks_dir.glob("*.json"))
    assert (doc_dir / "raw_chunks_scan_report.json").exists()

    summary = client.get(
        "/documents/summary", params={"datasource_root": str(datasource_root)}
    )
    assert summary.status_code == 200
    assert any(
        item["relative_path"] == "new_manual.md" for item in summary.json()["items"]
    )

    raw_chunk_summary = client.get("/pipeline/raw-chunks/summary")
    assert raw_chunk_summary.status_code == 200
    assert any(
        item["doc_dir"] == "new_manual" for item in raw_chunk_summary.json()["items"]
    )


def test_upload_rejects_pdf_when_backend_support_is_txt_md_xlsx(
    monkeypatch, tmp_path
):
    app, _, _, datasource_root, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    upload = client.post(
        "/documents/upload",
        files={
            "file": (
                "legacy.pdf",
                b"%PDF-1.4 fake content",
                "application/pdf",
            )
        },
    )

    assert upload.status_code == 400
    assert "('.txt', '.md', '.xlsx')" in upload.json()["detail"]

    missing = client.get(
        "/documents/file",
        params={
            "relative_path": "source/raw/doc1.md",
            "datasource_root": str(datasource_root),
        },
    )
    assert missing.status_code == 200


def test_system_config_and_health_expose_resolved_datasource_scope(
    monkeypatch, tmp_path
):
    app, _, _, datasource_root, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    expected_scope = {
        "datasource_id": TEST_DATASOURCE_ID,
        "datasource_root": str(datasource_root.resolve()),
        "source_root": str((datasource_root / "source" / "raw").resolve()),
        "staging_root": str((datasource_root / "staging").resolve()),
        "output_root": str((datasource_root / "outputs").resolve()),
        "input_dir": str((datasource_root / "source" / "raw").resolve()),
        "working_dir": str(
            (datasource_root / "outputs" / "graph" / "workdir").resolve()
        ),
    }

    system_config = client.get("/system/config")
    assert system_config.status_code == 200
    assert system_config.json()["server"] == {
        "host": "0.0.0.0",
        "port": 9733,
        "working_dir": expected_scope["working_dir"],
        "input_dir": expected_scope["input_dir"],
        "datasource_id": TEST_DATASOURCE_ID,
        "datasource_root": expected_scope["datasource_root"],
        "source_root": expected_scope["source_root"],
        "staging_root": expected_scope["staging_root"],
        "output_root": expected_scope["output_root"],
        "log_level": "INFO",
        "auto_scan_at_startup": False,
    }

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["working_directory"] == expected_scope["working_dir"]
    assert health.json()["input_directory"] == expected_scope["input_dir"]
    assert health.json()["datasource"] == expected_scope


def test_system_models_support_identifier_based_switch(monkeypatch, tmp_path):
    registry_dir = tmp_path / "config"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "model_registry.json").write_text(
        json.dumps(
            {
                "llm_models": [
                    {
                        "id": "index-main",
                        "label": "Index Main",
                        "binding": "openai",
                        "host": "http://127.0.0.1:3001/v1",
                        "model": "index-main-model",
                        "roles": ["index"],
                    },
                    {
                        "id": "query-main",
                        "label": "Query Main",
                        "binding": "openai",
                        "host": "http://127.0.0.1:1234/v1",
                        "model": "query-main-model",
                        "roles": ["query", "benchmark"],
                    },
                    {
                        "id": "query-alt",
                        "label": "Query Alt",
                        "binding": "openai",
                        "host": "http://127.0.0.1:1234/v1",
                        "model": "query-alt-model",
                        "roles": ["query", "benchmark"],
                    },
                ],
                "embedding_models": [
                    {
                        "id": "embed-main",
                        "label": "Embed Main",
                        "binding": "openai",
                        "host": "http://127.0.0.1:1234/v1",
                        "model": "embed-main-model",
                        "roles": ["embedding"],
                    },
                    {
                        "id": "embed-alt",
                        "label": "Embed Alt",
                        "binding": "openai",
                        "host": "http://127.0.0.1:1234/v1",
                        "model": "embed-alt-model",
                        "roles": ["embedding"],
                    },
                ],
                "defaults": {
                    "index_llm_id": "index-main",
                    "query_llm_id": "query-main",
                    "embedding_model_id": "embed-main",
                },
            }
        ),
        encoding="utf-8",
    )

    app, _, _, _, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    captured_unload: dict[str, str] = {}

    async def fake_unload(**kwargs):
        captured_unload.update(kwargs)
        return ms.RuntimeUnloadResult(
            status="unloaded",
            binding=kwargs["binding"],
            host=kwargs["host"],
            model=kwargs["model"],
            instance_ids=[kwargs["model"]],
        )

    monkeypatch.setattr(ms, "unload_lmstudio_model_instances", fake_unload)

    catalog = client.get("/system/models")
    assert catalog.status_code == 200
    llm_model_ids = [item["id"] for item in catalog.json()["llm_models"]]
    assert llm_model_ids == ["index-main", "query-main", "query-alt"]
    assert catalog.json()["selection"] == {
        "index_llm_id": "index-main",
        "query_llm_id": "query-main",
        "embedding_model_id": "embed-main",
    }

    updated = client.post(
        "/system/models/select",
        json={
            "query_llm_id": "query-alt",
            "embedding_model_id": "embed-alt",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["selection"] == {
        "index_llm_id": "index-main",
        "query_llm_id": "query-alt",
        "embedding_model_id": "embed-alt",
    }
    assert updated.json()["previous_query_unload"]["status"] == "unloaded"
    assert captured_unload == {
        "binding": "openai",
        "host": "http://127.0.0.1:1234/v1",
        "model": "query-main-model",
        "api_key": None,
    }

    system_config = client.get("/system/config")
    assert system_config.status_code == 200
    assert system_config.json()["llm"]["query_model_id"] == "query-alt"
    assert system_config.json()["llm"]["query_model"] == "query-alt-model"
    assert system_config.json()["embedding"]["model_id"] == "embed-alt"
    assert system_config.json()["embedding"]["model"] == "embed-alt-model"

    assert FakeMiniRAG.instances[-1].kwargs["llm_model_name"] == "query-alt-model"


def test_documents_summary_accepts_id_root_and_legacy_selectors(monkeypatch, tmp_path):
    app, _, _, datasource_root, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    by_id = client.get(
        "/documents/summary", params={"datasource_id": TEST_DATASOURCE_ID}
    )
    assert by_id.status_code == 200
    assert by_id.json()["datasource_id"] == TEST_DATASOURCE_ID

    by_root = client.get(
        "/documents/summary", params={"datasource_root": str(datasource_root)}
    )
    assert by_root.status_code == 200
    assert by_root.json()["datasource_id"] == TEST_DATASOURCE_ID
    assert by_root.json()["datasource"]["datasource_root"] == str(
        datasource_root.resolve()
    )
    assert by_root.json()["items"] == by_id.json()["items"]

    with pytest.warns(
        DeprecationWarning,
        match="Resolving datasources from legacy input_dir/outputs_root/working_dir",
    ):
        legacy = client.get(
            "/documents/summary",
            params={
                "input_dir": str(datasource_root / "source" / "raw"),
                "working_dir": str(datasource_root / "outputs" / "graph" / "workdir"),
            },
        )

    assert legacy.status_code == 200
    assert legacy.json()["datasource_id"] == TEST_DATASOURCE_ID
    assert legacy.json()["items"] == by_id.json()["items"]


def test_documents_summary_rejects_conflicting_datasource_selectors(
    monkeypatch, tmp_path
):
    app, _, _, _, secondary_datasource_root = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    conflict = client.get(
        "/documents/summary",
        params={
            "datasource_id": TEST_DATASOURCE_ID,
            "datasource_root": str(secondary_datasource_root),
        },
    )
    assert conflict.status_code == 400
    assert "Datasource selector conflict" in conflict.json()["detail"]


def test_documents_summary_rejects_missing_datasource(monkeypatch, tmp_path):
    app, _, _, _, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    missing_selector = client.get("/documents/summary")
    assert missing_selector.status_code == 400
    assert "datasource selector is required" in missing_selector.json()["detail"]

    missing_datasource = client.get(
        "/documents/summary", params={"datasource_id": "missing_docs"}
    )
    assert missing_datasource.status_code == 400
    assert "No registered datasource matches" in missing_datasource.json()["detail"]


def test_documents_summary_requires_loaded_graph_datasource(monkeypatch, tmp_path):
    app, _, _, _, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    mismatch = client.get(
        "/documents/summary", params={"datasource_id": SECONDARY_DATASOURCE_ID}
    )
    assert mismatch.status_code == 404
