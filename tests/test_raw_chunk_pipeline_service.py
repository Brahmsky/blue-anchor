# pyright: reportMissingImports=false

"""
test_raw_chunk_pipeline_service.py

Pytest coverage for raw_chunk_pipeline module.
Tests happy-path load/save/validate behavior and path-traversal rejection.
"""

import json
import tempfile
from pathlib import Path

import pytest

from data_pipeline.raw_chunk_pipeline import (
    InvalidChapterJsonError,
    InvalidRelativePathError,
    compute_parser_compatible_chunk_id,
    discover_doc_dirs,
    load_chapter_by_relative_path,
    load_chunks,
    resolve_raw_chunks_dir,
    scan_document,
    validate_chapter_json,
    validate_chunk,
    validate_chunks,
    write_chunk_atomic,
    contains_html_table,
    contains_html_image,
    contains_markdown_image,
    count_tokens,
    get_encoder,
    analyze_chunks,
    route_issue_chunks,
    resolve_active_chunk_root,
)


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

    doc_dir = chunk_root / "test_doc"
    raw_chunks_dir = doc_dir / "raw_chunks"
    raw_chunks_dir.mkdir(parents=True)
    chapter_file = raw_chunks_dir / "test_chapter.json"
    chapter_file.write_text(
        json.dumps(
            {
                "doc_name": "test_doc",
                "chapter": "test_chapter",
                "chunks": [
                    {
                        "chunk_id": "abc123",
                        "breadcrumb": "test_doc > test_chapter",
                        "content": "test content",
                        "chunk_type": "text",
                        "metadata": {},
                    }
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
        "doc_dir": doc_dir,
        "raw_chunks_dir": raw_chunks_dir,
    }


class TestLoadChapterByRelativePath:
    """Tests for load_chapter_by_relative_path with root-boundary enforcement."""

    def test_load_chapter_by_relative_path_success(self, datasource_workspace):
        """Test loading a chapter by datasource output root succeeds."""
        payload = load_chapter_by_relative_path(
            "test_doc/raw_chunks/test_chapter.json",
            outputs_root=str(datasource_workspace["output_root"]),
        )
        assert payload["doc_name"] == "test_doc"
        assert payload["chapter"] == "test_chapter"
        assert len(payload["chunks"]) == 1

    def test_load_chapter_by_relative_path_traversal_rejected(
        self, datasource_workspace
    ):
        """Test that path traversal is rejected."""
        with pytest.raises(InvalidRelativePathError):
            load_chapter_by_relative_path(
                "../../../etc/passwd",
                outputs_root=str(datasource_workspace["output_root"]),
            )

    def test_load_chapter_by_relative_path_missing_file(self, datasource_workspace):
        """Test that missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_chapter_by_relative_path(
                "test_doc/raw_chunks/nonexistent.json",
                outputs_root=str(datasource_workspace["output_root"]),
            )

    def test_load_chapter_by_relative_path_missing_root(self):
        """Test that missing outputs root raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_chapter_by_relative_path(
                "test_doc/chapter.json", outputs_root="/nonexistent/path"
            )

    def test_load_chapter_by_relative_path_sibling_traversal(
        self, datasource_workspace
    ):
        """Test traversal into sibling directory outside root is rejected.

        This tests the case where a path shares the root prefix but resolves
        to a location outside the intended root boundary.
        """
        sibling = datasource_workspace["repo_root"] / "sibling_directory"
        sibling.mkdir()
        sibling_file = sibling / "malicious.json"
        sibling_file.write_text(json.dumps({"doc_name": "x"}), encoding="utf-8")

        real_path = str(sibling.resolve())
        with pytest.raises(InvalidRelativePathError):
            load_chapter_by_relative_path(
                real_path, outputs_root=str(datasource_workspace["output_root"])
            )

    def test_load_chapter_by_relative_path_invalid_json_raises(
        self, datasource_workspace
    ):
        """Test that invalid chapter JSON raises InvalidChapterJsonError."""
        doc_dir = datasource_workspace["chunk_root"] / "test_doc"
        raw_chunks_dir = doc_dir / "raw_chunks"
        invalid_file = raw_chunks_dir / "invalid_chapter.json"
        invalid_file.write_text(
            json.dumps({"wrong_name": "no_chapter", "chunks": "not_a_list"}),
            encoding="utf-8",
        )

        with pytest.raises(InvalidChapterJsonError):
            load_chapter_by_relative_path(
                "test_doc/raw_chunks/invalid_chapter.json",
                outputs_root=str(datasource_workspace["output_root"]),
            )

    def test_datasource_output_root_selector_resolves_to_chunk_root(
        self, datasource_workspace
    ):
        """Datasource output roots should normalize to staging/chunks."""
        resolved = resolve_active_chunk_root(str(datasource_workspace["output_root"]))

        assert resolved == datasource_workspace["chunk_root"].resolve()

    def test_legacy_outputs_selector_resolves_to_datasource_chunk_root(
        self, datasource_workspace, monkeypatch
    ):
        """Legacy outputs_root selectors should normalize to datasource staging/chunks."""
        monkeypatch.chdir(datasource_workspace["repo_root"])

        resolved = resolve_active_chunk_root(
            str(datasource_workspace["legacy_outputs_root"])
        )

        assert resolved == datasource_workspace["chunk_root"].resolve()
        payload = load_chapter_by_relative_path(
            "test_doc/raw_chunks/test_chapter.json",
            outputs_root=str(datasource_workspace["legacy_outputs_root"]),
        )
        assert payload["doc_name"] == "test_doc"


class TestValidateChapterJson:
    """Tests for chapter JSON validation."""

    def test_validate_chapter_json_valid(self):
        """Test validation passes for valid chapter JSON."""
        payload = {"doc_name": "doc", "chapter": "chapter", "chunks": []}
        is_valid, errors = validate_chapter_json(payload)
        assert is_valid is True

    def test_validate_chapter_json_missing_doc_name(self):
        """Test validation catches missing doc_name."""
        payload = {"chapter": "c", "chunks": []}
        is_valid, errors = validate_chapter_json(payload)
        assert is_valid is False
        assert any("doc_name" in err for err in errors)

    def test_validate_chapter_json_missing_chapter(self):
        """Test validation catches missing chapter."""
        payload = {"doc_name": "d", "chunks": []}
        is_valid, errors = validate_chapter_json(payload)
        assert is_valid is False
        assert any("chapter" in err for err in errors)

    def test_validate_chapter_json_missing_chunks(self):
        """Test validation catches missing chunks."""
        payload = {"doc_name": "d", "chapter": "c"}
        is_valid, errors = validate_chapter_json(payload)
        assert is_valid is False
        assert any("chunks" in err for err in errors)

    def test_validate_chapter_json_chunks_not_list(self):
        """Test validation catches non-list chunks."""
        payload = {"doc_name": "d", "chapter": "c", "chunks": "not a list"}
        is_valid, errors = validate_chapter_json(payload)
        assert is_valid is False
        assert any("list" in err for err in errors)


class TestPathDiscovery:
    """Tests for path discovery and validation functions."""

    def test_resolve_raw_chunks_dir_with_document_dir(self, datasource_workspace):
        """Test resolving raw_chunks from a document directory."""
        result = resolve_raw_chunks_dir(str(datasource_workspace["doc_dir"]))
        assert result.name == "raw_chunks"
        assert result.exists()

    def test_resolve_raw_chunks_dir_with_raw_chunks_path(self, datasource_workspace):
        """Test resolving raw_chunks when path is already raw_chunks folder."""
        raw_chunks_dir = datasource_workspace["raw_chunks_dir"]
        result = resolve_raw_chunks_dir(str(raw_chunks_dir))
        assert result.name == "raw_chunks"

    def test_resolve_raw_chunks_dir_raises_on_missing(self):
        """Test that non-existent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            resolve_raw_chunks_dir("/nonexistent/path")

    def test_resolve_raw_chunks_dir_raises_without_raw_chunks(self):
        """Test that directory without raw_chunks raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                resolve_raw_chunks_dir(tmpdir)

    def test_discover_doc_dirs_finds_all_documents(self, datasource_workspace):
        """Test that discover_doc_dirs resolves datasource output roots to chunks."""
        doc_dirs = discover_doc_dirs(str(datasource_workspace["output_root"]))
        assert len(doc_dirs) > 0
        for doc_dir in doc_dirs:
            assert (doc_dir / "raw_chunks").exists()


class TestChunkLoading:
    """Tests for chunk loading functions."""

    def test_load_chunks_returns_all_chapters(self, datasource_workspace):
        """Test that load_chunks returns all chapters in sorted order."""
        raw_chunks_dir = datasource_workspace["raw_chunks_dir"]
        chunks = load_chunks(raw_chunks_dir)
        assert len(chunks) > 0
        for chunk in chunks:
            assert "chunk_id" in chunk
            assert "breadcrumb" in chunk
            assert "content" in chunk
            assert "chapter_file" in chunk
            assert "doc_name" in chunk
            assert "chapter" in chunk


class TestValidation:
    """Tests for chunk validation functions."""

    def test_validate_chunk_with_valid_chunk(self):
        """Test validation of a valid chunk."""
        valid_chunk = {
            "chunk_id": "abc123",
            "breadcrumb": "doc > chapter > section",
            "content": "Some content here",
            "chunk_type": "text",
            "metadata": {},
        }
        is_valid, errors = validate_chunk(valid_chunk)
        assert is_valid is True

    def test_validate_chunk_with_missing_fields(self):
        """Test validation catches missing required fields."""
        invalid_chunk = {"chunk_id": "abc123"}
        is_valid, errors = validate_chunk(invalid_chunk)
        assert is_valid is False

    def test_validate_chunk_with_empty_content(self):
        """Test validation catches empty content."""
        invalid_chunk = {
            "chunk_id": "abc123",
            "breadcrumb": "doc > chapter",
            "content": "",
        }
        is_valid, errors = validate_chunk(invalid_chunk)
        assert is_valid is False

    def test_validate_chunk_with_whitespace_only_content(self):
        """Test validation catches whitespace-only content."""
        invalid_chunk = {
            "chunk_id": "abc123",
            "breadcrumb": "doc > chapter",
            "content": "   \n\t  ",
        }
        is_valid, errors = validate_chunk(invalid_chunk)
        assert is_valid is False

    def test_validate_chunk_with_invalid_metadata_type(self):
        """Test validation catches non-dict metadata."""
        invalid_chunk = {
            "chunk_id": "abc123",
            "breadcrumb": "doc > chapter",
            "content": "content",
            "metadata": "not a dict",
        }
        is_valid, errors = validate_chunk(invalid_chunk)
        assert is_valid is False

    def test_validate_chunks_with_mixed_validity(self):
        """Test validation of a list with mixed valid/invalid chunks."""
        chunks = [
            {"chunk_id": "1", "breadcrumb": "a", "content": "content"},
            {"chunk_id": "2", "breadcrumb": "b"},
            {"chunk_id": "3", "breadcrumb": "c", "content": "c"},
        ]
        result = validate_chunks(chunks)
        assert result["valid_count"] == 2
        assert result["invalid_count"] == 1


class TestChunkIdComputation:
    """Tests for parser-compatible chunk ID computation."""

    def test_compute_parser_compatible_chunk_id_format(self):
        """Test that computed chunk_id is valid MD5 hash."""
        breadcrumb = "doc > chapter > section"
        content = "some content text"
        computed_id = compute_parser_compatible_chunk_id(breadcrumb, content)
        assert len(computed_id) == 32

    def test_compute_parser_compatible_chunk_id_deterministic(self):
        """Test that chunk ID computation is deterministic."""
        breadcrumb = "test > chapter"
        content = "test content"
        id1 = compute_parser_compatible_chunk_id(breadcrumb, content)
        id2 = compute_parser_compatible_chunk_id(breadcrumb, content)
        assert id1 == id2


class TestAtomicWrite:
    """Tests for atomic chunk file writing."""

    def test_write_chunk_atomic_creates_file(self):
        """Test that write_chunk_atomic creates the file correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_chunks_dir = Path(tmpdir) / "raw_chunks"
            raw_chunks_dir.mkdir()
            chunks = [
                {
                    "chunk_id": "test123",
                    "breadcrumb": "doc > chapter",
                    "content": "test content",
                    "chunk_type": "text",
                    "metadata": {},
                }
            ]
            output_path = write_chunk_atomic(
                raw_chunks_dir, "test_chapter.json", "test_doc", "test_chapter", chunks
            )
            assert output_path.exists()
            loaded = json.loads(output_path.read_text(encoding="utf-8"))
            assert loaded["doc_name"] == "test_doc"


class TestHtmlImageDetection:
    """Tests for HTML and image detection functions."""

    def test_contains_html_table_with_table(self):
        """Test detection of HTML table in text."""
        assert contains_html_table("<table><tr><td>cell</td></tr></table>") is True

    def test_contains_html_table_without_table(self):
        """Test that plain text doesn't trigger table detection."""
        assert contains_html_table("This is just regular text.") is False

    def test_contains_html_image_with_img(self):
        """Test detection of HTML image in text."""
        assert contains_html_image('<img src="image.png">') is True

    def test_contains_html_image_without_img(self):
        """Test that text without img tags returns False."""
        assert contains_html_image("No images here") is False

    def test_contains_markdown_image_with_image(self):
        """Test detection of markdown image syntax."""
        assert contains_markdown_image("![alt](image.png)") is True

    def test_contains_markdown_image_without_image(self):
        """Test that text without markdown images returns False."""
        assert contains_markdown_image("No markdown images") is False


class TestTokenCounting:
    """Tests for token counting functions."""

    def test_count_tokens_with_encoder(self):
        """Test token counting with encoder."""
        encoder = get_encoder("gpt-4o-mini")
        token_count = count_tokens(encoder, "This is a test string.")
        assert token_count > 0

    def test_count_tokens_empty_string(self):
        """Test token counting with empty string."""
        encoder = get_encoder("gpt-4o-mini")
        token_count = count_tokens(encoder, "")
        assert token_count == 0


class TestChunkAnalysis:
    """Tests for chunk analysis functions."""

    def test_analyze_chunks_with_temp_chunks(self):
        """Test chunk analysis with temp data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_chunks_dir = Path(tmpdir) / "raw_chunks"
            raw_chunks_dir.mkdir()
            chapter_file = raw_chunks_dir / "chapter.json"
            chapter_file.write_text(
                json.dumps(
                    {
                        "doc_name": "doc",
                        "chapter": "chapter",
                        "chunks": [
                            {
                                "chunk_id": "id1",
                                "breadcrumb": "d > c",
                                "content": "short",
                                "chapter_file": "chapter.json",
                            },
                            {
                                "chunk_id": "id2",
                                "breadcrumb": "d > c2",
                                "content": "x" * 2000,
                                "chapter_file": "chapter.json",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            chunks = load_chunks(raw_chunks_dir)
            encoder = get_encoder("gpt-4o-mini")
            report = analyze_chunks(chunks, encoder=encoder, long_threshold=1500)
            assert "summary" in report
            assert report["summary"]["chunk_count"] == 2


class TestScanDocument:
    """Tests for the scan_document convenience function."""

    def test_scan_document_without_routing(self, datasource_workspace):
        """Test scan_document without routing enabled."""
        report = scan_document(
            datasource_workspace["doc_dir"],
            tokenizer_model="gpt-4o-mini",
            long_threshold=1500,
            preview_length=20,
            write_routing=False,
        )
        assert "summary" in report
        assert report["routing"] == {}


class TestPathTraversalRejection:
    """Tests for path traversal rejection - security tests."""

    def test_resolve_raw_chunks_dir_no_raw_chunks(self):
        """Test that directory without raw_chunks raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                resolve_raw_chunks_dir(tmpdir)


class TestChunkRouting:
    """Tests for issue chunk routing."""

    def test_route_issue_chunks_returns_counts(self, datasource_workspace):
        """Test that route_issue_chunks returns routing counts."""
        doc_dir = datasource_workspace["chunk_root"] / "test_doc"
        raw_chunks_dir = doc_dir / "raw_chunks"
        test_chunks = [
            {
                "chunk_id": "test1",
                "breadcrumb": "doc > chapter1",
                "content": "short content",
                "chunk_type": "text",
                "chapter_file": "chapter1.json",
                "doc_name": "test_doc",
                "chapter": "chapter1",
                "issue_types": [],
                "metadata": {},
            },
            {
                "chunk_id": "test2",
                "breadcrumb": "doc > chapter2",
                "content": "x" * 3000,
                "chunk_type": "text",
                "chapter_file": "chapter2.json",
                "doc_name": "test_doc",
                "chapter": "chapter2",
                "issue_types": ["long_chunk"],
                "metadata": {},
            },
        ]
        chapter_path = raw_chunks_dir / "chapter1.json"
        chapter_path.write_text(
            json.dumps(
                {
                    "doc_name": "test_doc",
                    "chapter": "chapter1",
                    "chunks": [test_chunks[0]],
                }
            ),
            encoding="utf-8",
        )
        counts = route_issue_chunks(raw_chunks_dir, test_chunks)
        assert "good_chunks" in counts
        assert "long_chunk" in counts
