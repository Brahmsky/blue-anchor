# pyright: reportMissingImports=false

"""
test_raw_chunk_pipeline_api.py

Pytest coverage for raw chunk summary/detail API endpoints.
Tests use the PRODUCTION route registration from minirag_server.py.
"""

import json
import tempfile
from pathlib import Path

import pytest


# =============================================================================
# Test fixtures
# =============================================================================


@pytest.fixture
def datasource_workspace(tmp_path: Path):
    """Create a datasource-shaped workspace rooted at staging/chunks."""
    datasource_root = tmp_path / "datasources" / "local_ship_docs"
    source_root = datasource_root / "source" / "raw"
    staging_root = datasource_root / "staging"
    chunk_root = staging_root / "chunks"
    output_root = datasource_root / "outputs"
    working_dir = output_root / "graph" / "workdir"

    source_root.mkdir(parents=True)
    chunk_root.mkdir(parents=True)
    working_dir.mkdir(parents=True)

    registry_path = tmp_path / "datasources" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            [
                {
                    "id": "local_ship_docs",
                    "name": "Local Ship Docs",
                    "type": "local_fs",
                    "path": "./datasources/local_ship_docs",
                }
            ]
        ),
        encoding="utf-8",
    )

    (datasource_root / "datasource.yaml").write_text(
        "\n".join(
            [
                "id: local_ship_docs",
                "name: Local Test Docs",
                "type: local_fs",
                "paths:",
                "  source_root: ./source/raw",
                "  staging_root: ./staging",
                "  output_root: ./outputs",
                "pipeline:",
                "  profile: test_profile",
                "",
            ]
        ),
        encoding="utf-8",
    )

    root = chunk_root

    # Create first document
    doc_dir_1 = root / "test_doc_1"
    doc_dir_1.mkdir()
    raw_chunks_dir_1 = doc_dir_1 / "raw_chunks"
    raw_chunks_dir_1.mkdir()

    chapter_1 = raw_chunks_dir_1 / "chapter_a.json"
    chapter_1.write_text(
        json.dumps(
            {
                "doc_name": "test_doc_1",
                "chapter": "Chapter A",
                "chunks": [
                    {
                        "chunk_id": "abc123",
                        "breadcrumb": "test_doc_1 > Chapter A > Section 1",
                        "content": "First chunk content",
                        "chunk_type": "text",
                        "metadata": {},
                    },
                    {
                        "chunk_id": "def456",
                        "breadcrumb": "test_doc_1 > Chapter A > Section 2",
                        "content": "<table><tr><td>Second chunk table</td></tr></table>",
                        "chunk_type": "text",
                        "metadata": {},
                    },
                    {
                        "chunk_id": "ghi789",
                        "breadcrumb": "test_doc_1 > Chapter A > Section 3",
                        "content": "![diagram](image.png) " + "token " * 2000,
                        "chunk_type": "text",
                        "metadata": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # Create second document
    doc_dir_2 = root / "test_doc_2"
    doc_dir_2.mkdir()
    raw_chunks_dir_2 = doc_dir_2 / "raw_chunks"
    raw_chunks_dir_2.mkdir()

    chapter_2 = raw_chunks_dir_2 / "chapter_b.json"
    chapter_2.write_text(
        json.dumps(
            {
                "doc_name": "test_doc_2",
                "chapter": "Chapter B",
                "chunks": [
                    {
                        "chunk_id": "xyz789",
                        "breadcrumb": "test_doc_2 > Chapter B",
                        "content": "Only chunk",
                        "chunk_type": "text",
                        "metadata": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    return {
        "repo_root": tmp_path,
        "datasource_id": "local_ship_docs",
        "datasource_root": datasource_root,
        "output_root": output_root,
        "chunk_root": chunk_root,
        "legacy_outputs_root": tmp_path / "data_pipeline" / "outputs",
        "working_dir": working_dir,
    }


@pytest.fixture
def test_app(datasource_workspace, monkeypatch):
    """
    Create a FastAPI test app using the PRODUCTION route registration.

    This uses the SAME register_raw_chunk_endpoints function that
    create_app uses - no duplicate endpoint logic.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Import the ACTUAL production route registration function (separate module)
    from minirag.api.raw_chunk_endpoints import register_raw_chunk_endpoints
    from minirag.api.raw_chunk_registry import RawChunkRegistry

    outputs_root_str = str(datasource_workspace["output_root"])
    working_dir_str = str(datasource_workspace["working_dir"])

    # Initialize raw chunk registry - same as production
    monkeypatch.chdir(datasource_workspace["repo_root"])
    raw_chunk_registry = RawChunkRegistry(working_dir_str, outputs_root_str)
    raw_chunk_registry.sync_from_disk()

    # Create app and register endpoints using the PRODUCTION function
    app = FastAPI()
    register_raw_chunk_endpoints(app, raw_chunk_registry, outputs_root_str)

    return app


# =============================================================================
# API endpoint tests - use production route registration
# =============================================================================


class TestSummaryEndpoint:
    """Tests for GET /pipeline/raw-chunks/summary endpoint."""

    def test_summary_happy_path(self, test_app, datasource_workspace):
        """Test summary returns 200 while registered via datasource output root."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get("/pipeline/raw-chunks/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["outputs_root"] == str(datasource_workspace["output_root"])
        assert "stats" in data
        assert "items" in data

    def test_summary_returns_doc_info(self, test_app):
        """Test summary items include doc_name, chapter, chunk_count."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get("/pipeline/raw-chunks/summary")

        assert response.status_code == 200
        data = response.json()

        items = data["items"]
        assert len(items) > 0

        item = items[0]
        assert "relative_path" in item
        assert "doc_name" in item
        assert "chapter" in item
        assert "chunk_count" in item

    def test_summary_legacy_outputs_selector_uses_datasource_chunk_root(
        self, datasource_workspace, monkeypatch
    ):
        """Legacy outputs_root values should still expose datasource chunk content."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from minirag.api.raw_chunk_endpoints import register_raw_chunk_endpoints
        from minirag.api.raw_chunk_registry import RawChunkRegistry

        monkeypatch.chdir(datasource_workspace["repo_root"])
        app = FastAPI()
        registry = RawChunkRegistry(
            str(datasource_workspace["working_dir"]),
            str(datasource_workspace["legacy_outputs_root"]),
        )
        register_raw_chunk_endpoints(
            app,
            registry,
            str(datasource_workspace["legacy_outputs_root"]),
        )

        client = TestClient(app)
        response = client.get("/pipeline/raw-chunks/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["stats"]["total"] == 2
        assert {item["relative_path"] for item in data["items"]} == {
            "test_doc_1/raw_chunks/chapter_a.json",
            "test_doc_2/raw_chunks/chapter_b.json",
        }


class TestDetailEndpoint:
    """Tests for GET /pipeline/raw-chunks/file endpoint."""

    def test_detail_happy_path(self, test_app):
        """Test detail returns 200 with chunks for valid relative_path."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "test_doc_1/raw_chunks/chapter_a.json"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["relative_path"] == "test_doc_1/raw_chunks/chapter_a.json"
        assert data["doc_name"] == "test_doc_1"
        assert data["chapter"] == "Chapter A"
        assert data["chunk_count"] == 3
        assert len(data["chunks"]) == 3

    def test_detail_returns_422_on_missing_relative_path(self, test_app):
        """Test detail returns 422 when relative_path is missing (FastAPI validation)."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get("/pipeline/raw-chunks/file")

        assert response.status_code == 422

    def test_detail_returns_400_on_empty_relative_path(self, test_app):
        """Test detail returns 400 when relative_path is empty string."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get("/pipeline/raw-chunks/file", params={"relative_path": ""})

        assert response.status_code == 400

    def test_detail_returns_400_on_invalid_traversal(self, test_app):
        """Test detail returns 400 for path traversal attempt."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get(
            "/pipeline/raw-chunks/file", params={"relative_path": "../../../etc/passwd"}
        )

        assert response.status_code == 400

    def test_detail_returns_404_for_nonexistent_file(self, test_app):
        """Test detail returns 404 for nonexistent file."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "nonexistent/file.json"},
        )

        assert response.status_code == 404

    def test_detail_preserves_chunk_order(self, test_app):
        """Test detail preserves chunk order from source file."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "test_doc_1/raw_chunks/chapter_a.json"},
        )

        assert response.status_code == 200
        data = response.json()

        chunks = data["chunks"]
        assert chunks[0]["breadcrumb"] == "test_doc_1 > Chapter A > Section 1"
        assert chunks[0]["chunk_index"] == 0
        assert chunks[1]["breadcrumb"] == "test_doc_1 > Chapter A > Section 2"
        assert chunks[1]["chunk_index"] == 1


# =============================================================================
# Real-world integration tests
# =============================================================================


class TestEditEndpoint:
    """Tests for PUT /pipeline/raw-chunks/chunks/edit endpoint."""

    def test_edit_content_happy_path(self, test_app, datasource_workspace):
        """Test edit returns 200 with updated chunk."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.put(
            "/pipeline/raw-chunks/chunks/edit",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "content": "Updated first chunk content",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["old_chunk_id"] == "abc123"
        assert data["new_chunk_id"] != "abc123"

    def test_edit_metadata_only_preserves_chunk_id(
        self, test_app, datasource_workspace
    ):
        """Test metadata-only edit preserves chunk_id."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)

        metadata = {"key": "value"}
        response = client.put(
            "/pipeline/raw-chunks/chunks/edit",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "content": "First chunk content",
                "metadata": metadata,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["new_chunk_id"] == "abc123"

    def test_edit_whitespace_only_rejected(self, test_app):
        """Test whitespace-only content returns 400."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.put(
            "/pipeline/raw-chunks/chunks/edit",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "content": "   ",
            },
        )

        assert response.status_code == 400
        assert "whitespace" in response.json()["detail"].lower()

    def test_edit_missing_chunk_id_returns_422(self, test_app):
        """Test missing chunk_id returns 422 (FastAPI validation)."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.put(
            "/pipeline/raw-chunks/chunks/edit",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "content": "New content",
            },
        )

        assert response.status_code == 422

    def test_edit_nonexistent_chunk_returns_400(self, test_app):
        """Test nonexistent chunk returns 400 (ChunkEditError converted to HTTP 400)."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.put(
            "/pipeline/raw-chunks/chunks/edit",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "nonexistent_id",
                "content": "New content",
            },
        )

        assert response.status_code == 400

    def test_edit_marks_registry_dirty(self, test_app, datasource_workspace):
        """Test edit marks registry as dirty."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)

        client.put(
            "/pipeline/raw-chunks/chunks/edit",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "content": "Updated content",
            },
        )

        detail_response = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "test_doc_1/raw_chunks/chapter_a.json"},
        )
        data = detail_response.json()
        assert data["registry"]["dirty"] is True


class TestSplitEndpoint:
    """Tests for POST /pipeline/raw-chunks/chunks/split endpoint."""

    def test_split_happy_path(self, test_app, datasource_workspace):
        """Test split returns 200 with two new chunk IDs."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/split",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "left_content": "Left portion of content",
                "right_content": "Right portion of content",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["original_chunk_id"] == "abc123"
        assert data["left_chunk_id"] != "abc123"
        assert data["right_chunk_id"] != "abc123"
        assert data["left_chunk_id"] != data["right_chunk_id"]

    def test_split_whitespace_left_rejected(self, test_app):
        """Test whitespace-only left_content returns 400."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/split",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "left_content": "   ",
                "right_content": "Valid right",
            },
        )

        assert response.status_code == 400
        assert "left_content" in response.json()["detail"].lower()

    def test_split_whitespace_right_rejected(self, test_app):
        """Test whitespace-only right_content returns 400."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/split",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "left_content": "Valid left",
                "right_content": "   ",
            },
        )

        assert response.status_code == 400
        assert "right_content" in response.json()["detail"].lower()

    def test_split_missing_chunk_id_returns_422(self, test_app):
        """Test missing chunk_id returns 422 (FastAPI validation)."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/split",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "left_content": "Left",
                "right_content": "Right",
            },
        )

        assert response.status_code == 422

    def test_split_nonexistent_chunk_returns_400(self, test_app):
        """Test nonexistent chunk returns 400 (ChunkSplitError converted to HTTP 400)."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/split",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "nonexistent_id",
                "left_content": "Left",
                "right_content": "Right",
            },
        )

        assert response.status_code == 400

    def test_split_marks_registry_dirty(self, test_app, datasource_workspace):
        """Test split marks registry as dirty."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)

        client.post(
            "/pipeline/raw-chunks/chunks/split",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "left_content": "Left content",
                "right_content": "Right content",
            },
        )

        detail_response = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "test_doc_1/raw_chunks/chapter_a.json"},
        )
        data = detail_response.json()
        assert data["registry"]["dirty"] is True


class TestMergeEndpoint:
    """Tests for POST /pipeline/raw-chunks/chunks/merge endpoint."""

    def test_merge_adjacent_happy_path(self, test_app, datasource_workspace):
        """Test merge of adjacent chunks returns 200 with merged chunk ID."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/merge",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "first_chunk_id": "abc123",
                "second_chunk_id": "def456",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["first_chunk_id"] == "abc123"
        assert data["second_chunk_id"] == "def456"
        assert data["merged_chunk_id"] != "abc123"
        assert data["merged_chunk_id"] != "def456"

    def test_merge_nonadjacent_rejected(self, test_app):
        """Test merge of non-adjacent chunks returns 400."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/merge",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "first_chunk_id": "abc123",
                "second_chunk_id": "ghi789",
            },
        )

        assert response.status_code == 400
        assert "not adjacent" in response.json()["detail"].lower()

    def test_merge_missing_chunk_id_returns_422(self, test_app):
        """Test missing chunk_id returns 422 (FastAPI validation)."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/merge",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "first_chunk_id": "abc123",
            },
        )

        assert response.status_code == 422

    def test_merge_nonexistent_chunk_returns_400(self, test_app):
        """Test nonexistent chunk returns 400 (ChunkMergeError converted to HTTP 400)."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/chunks/merge",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "first_chunk_id": "nonexistent_1",
                "second_chunk_id": "nonexistent_2",
            },
        )

        assert response.status_code == 400

    def test_merge_marks_registry_dirty(self, test_app, datasource_workspace):
        """Test merge marks registry as dirty."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)

        client.post(
            "/pipeline/raw-chunks/chunks/merge",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "first_chunk_id": "abc123",
                "second_chunk_id": "def456",
            },
        )

        detail_response = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "test_doc_1/raw_chunks/chapter_a.json"},
        )
        data = detail_response.json()
        assert data["registry"]["dirty"] is True


class TestReprocessEndpoint:
    """Tests for POST /pipeline/raw-chunks/reprocess endpoint."""

    def test_reprocess_refreshes_routing_and_clears_dirty(
        self, test_app, datasource_workspace
    ):
        """Test successful reprocess regenerates routing artifacts and clears dirty state."""
        from fastapi.testclient import TestClient

        client = TestClient(test_app)

        edit_response = client.put(
            "/pipeline/raw-chunks/chunks/edit",
            json={
                "relative_path": "test_doc_1/raw_chunks/chapter_a.json",
                "chunk_id": "abc123",
                "content": "Updated first chunk content before reprocess",
            },
        )
        assert edit_response.status_code == 200

        response = client.post(
            "/pipeline/raw-chunks/reprocess",
            json={"doc_dir": "test_doc_1"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["doc_dir"].endswith("test_doc_1")
        assert data["last_reprocess_status"] == "success"
        assert data["downstream_state"] == "routing_refreshed"
        assert data["routing"]["good_chunks"] == 1
        assert data["routing"]["tables"] == 1
        assert data["routing"]["images"] == 1
        assert data["routing"]["long_chunk"] == 1

        report_path = (
            datasource_workspace["chunk_root"]
            / "test_doc_1"
            / "raw_chunks_scan_report.json"
        )
        assert report_path.exists()

        good_dir = datasource_workspace["chunk_root"] / "test_doc_1" / "good_chunks"
        tables_dir = datasource_workspace["chunk_root"] / "test_doc_1" / "tables"
        images_dir = datasource_workspace["chunk_root"] / "test_doc_1" / "images"
        long_dir = datasource_workspace["chunk_root"] / "test_doc_1" / "long_chunk"

        assert good_dir.exists()
        assert tables_dir.exists()
        assert images_dir.exists()
        assert long_dir.exists()
        assert len(list(good_dir.glob("*.json"))) == 1
        assert len(list(tables_dir.glob("*.json"))) == 1
        assert len(list(images_dir.glob("*.json"))) == 1
        assert len(list(long_dir.glob("*.json"))) == 1

        detail_response = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "test_doc_1/raw_chunks/chapter_a.json"},
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["registry"]["dirty"] is False
        assert detail["registry"]["last_reprocess_status"] == "success"
        assert detail["registry"]["downstream_state"] == "routing_refreshed"
        assert detail["registry"]["last_reprocessed_at"] is not None

        untouched_detail = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "test_doc_2/raw_chunks/chapter_b.json"},
        )
        untouched_registry = untouched_detail.json()["registry"]
        assert untouched_registry["last_reprocess_status"] == "idle"
        assert untouched_registry["downstream_state"] == "stale"

        assert not (
            datasource_workspace["chunk_root"] / "test_doc_1" / "accepted_records"
        ).exists()
        assert not (
            datasource_workspace["chunk_root"] / "test_doc_1" / "records"
        ).exists()

    def test_reprocess_failure_records_failed_status_without_corrupting_source(
        self, test_app, datasource_workspace, monkeypatch
    ):
        """Test reprocess failure records failed status and leaves source JSON unchanged."""
        from fastapi.testclient import TestClient
        import minirag.api.raw_chunk_endpoints as raw_chunk_endpoints

        chapter_path = (
            datasource_workspace["chunk_root"]
            / "test_doc_1"
            / "raw_chunks"
            / "chapter_a.json"
        )
        original_source = chapter_path.read_text(encoding="utf-8")

        def fail_reprocess(*args, **kwargs):
            raise RuntimeError("simulated routing failure")

        monkeypatch.setattr(
            raw_chunk_endpoints, "reprocess_document_routing", fail_reprocess
        )

        client = TestClient(test_app)
        response = client.post(
            "/pipeline/raw-chunks/reprocess",
            json={"doc_dir": "test_doc_1"},
        )

        assert response.status_code == 500
        assert "failed to reprocess raw chunks" in response.json()["detail"].lower()

        assert chapter_path.read_text(encoding="utf-8") == original_source

        detail_response = client.get(
            "/pipeline/raw-chunks/file",
            params={"relative_path": "test_doc_1/raw_chunks/chapter_a.json"},
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["registry"]["last_reprocess_status"] == "failed"
        assert detail["registry"]["downstream_state"] == "stale"
        assert detail["registry"]["last_reprocessed_at"] is not None
        assert not (
            datasource_workspace["chunk_root"]
            / "test_doc_1"
            / "raw_chunks_scan_report.json"
        ).exists()
