"""
raw_chunk_pipeline.py

Reusable backend module for raw chunk discovery, load, analyze, routing, save, and validation.
Extracted from data_pipeline/scripts/02_build_domain_graph.py to enable import-safe usage
from FastAPI endpoints without CLI side effects.

This module provides file-backed semantics only - no vector DB, graph, or doc_status involvement.
"""

import hashlib
import json
import re
import statistics
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import tiktoken

from minirag.datasource_resolver import DatasourceResolutionError, resolve_datasource


# =============================================================================
# Patterns for HTML/Image detection (mirrors 02_build_domain_graph.py)
# =============================================================================

HTML_TABLE_PATTERNS = [
    re.compile(r"<table\b", re.IGNORECASE),
    re.compile(r"<tr\b", re.IGNORECASE),
    re.compile(r"<td\b", re.IGNORECASE),
    re.compile(r"<th\b", re.IGNORECASE),
]

HTML_IMAGE_PATTERNS = [
    re.compile(r"<img\b", re.IGNORECASE),
    re.compile(r"<figure\b", re.IGNORECASE),
    re.compile(r"<svg\b", re.IGNORECASE),
]

MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]+\)")


# =============================================================================
# Dataclass for chunk issues (mirrors 02_build_domain_graph.py)
# =============================================================================


@dataclass
class ChunkIssue:
    """Represents a detected issue with a raw chunk."""

    chapter_file: str
    chunk_id: str
    breadcrumb: str
    token_count: int
    issue_types: list[str]
    content_preview: str


# =============================================================================
# Constants and errors
# =============================================================================

OUTPUTS_ROOT_DEFAULT = "datasources/local_ship_docs/staging/chunks"


def resolve_active_chunk_root(outputs_root: str = OUTPUTS_ROOT_DEFAULT) -> Path:
    """
    Resolve the effective raw chunk root for filesystem operations.

    Deprecated compatibility inputs like ``data_pipeline/outputs`` are still accepted,
    but when they map to a registered datasource they are translated to
    ``<datasource>/staging/chunks``. Non-resolver paths (for isolated tests or direct
    chunk-root callers) continue to resolve as-is.
    """
    selector_path = Path(outputs_root).expanduser()
    if not selector_path.is_absolute():
        selector_path = (Path(__file__).resolve().parents[1] / selector_path).resolve()
    else:
        selector_path = selector_path.resolve()
    repo_root = _discover_repo_root_for_selector(selector_path)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            datasource = resolve_datasource(
                outputs_root=str(selector_path),
                repo_root=repo_root,
            )
        return (datasource.staging_root / "chunks").resolve()
    except DatasourceResolutionError:
        return selector_path


def _discover_repo_root_for_selector(selector_path: Path) -> Path | None:
    """Find the nearest repo-like root carrying datasources/registry.json."""
    candidates: list[Path] = []
    if selector_path.is_absolute():
        candidates.extend(selector_path.parents)

    cwd = Path.cwd().resolve()
    candidates.append(cwd)
    candidates.extend(cwd.parents)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "datasources" / "registry.json").exists():
            return candidate
    return None


class InvalidRelativePathError(ValueError):
    """Raised when relative path traversal is detected."""

    pass


class InvalidChapterJsonError(ValueError):
    """Raised when chapter JSON has invalid structure."""

    pass


# =============================================================================
# Core path/discovery functions
# =============================================================================


def load_chapter_by_relative_path(
    relative_path: str,
    outputs_root: str = OUTPUTS_ROOT_DEFAULT,
) -> dict[str, Any]:
    """
    Load a single chapter JSON file by relative path rooted under the active chunk root.

    This is the deterministic helper for loading one chapter by relative path
    with explicit root-boundary enforcement.

    Args:
        relative_path: Relative path like "doc_name/raw_chunks/chapter.json" or "doc_name/chapter.json"
        outputs_root: Chunk root selector or legacy outputs selector

    Returns:
        Parsed chapter JSON dict with doc_name, chapter, chunks

    Raises:
        InvalidRelativePathError: If path attempts to escape the active chunk root
        FileNotFoundError: If file does not exist
        InvalidChapterJsonError: If JSON lacks required top-level fields
    """
    root = resolve_active_chunk_root(outputs_root)
    if not root.exists():
        raise FileNotFoundError(f"Outputs root does not exist: {root}")

    requested = root / relative_path
    requested_resolved = requested.resolve()

    try:
        is_relative = requested_resolved.is_relative_to(root)
    except ValueError:
        is_relative = False

    if not is_relative:
        raise InvalidRelativePathError(
            f"Path traversal rejected: {relative_path} resolves outside {outputs_root}"
        )

    if not requested_resolved.exists():
        raise FileNotFoundError(f"Chapter file not found: {requested_resolved}")

    payload = json.loads(requested_resolved.read_text(encoding="utf-8"))
    is_valid, errors = validate_chapter_json(payload, str(requested_resolved))
    if not is_valid:
        raise InvalidChapterJsonError(f"Invalid chapter JSON: {errors}")
    return payload


def validate_chapter_json(
    payload: dict[str, Any], source: str = "unknown"
) -> tuple[bool, list[str]]:
    """
    Validate the top-level chapter JSON structure.

    Required fields: doc_name, chapter, chunks
    chunks must be a list.

    Args:
        payload: Parsed chapter JSON dict
        source: Source file path for error messages

    Returns:
        Tuple of (is_valid, list_of_error_messages)
    """
    errors = []

    for field in ("doc_name", "chapter", "chunks"):
        if field not in payload:
            errors.append(f"Missing required field: {field}")

    chunks = payload.get("chunks")
    if chunks is not None and not isinstance(chunks, list):
        errors.append(f"chunks must be a list, got {type(chunks).__name__}")

    return (len(errors) == 0, errors)


def resolve_raw_chunks_dir(doc_dir: str) -> Path:
    """
    Resolve the raw_chunks directory from a document directory path.

    Accepts:
        - Path to a document directory (e.g., outputs/船舶电气设备维护与修理)
        - Path directly to raw_chunks folder

    Returns:
        Path to the raw_chunks directory

    Raises:
        FileNotFoundError: If raw_chunks cannot be found
    """
    path = Path(doc_dir).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    if path.is_dir() and path.name == "raw_chunks":
        return path
    candidate = path / "raw_chunks"
    if candidate.exists() and candidate.is_dir():
        return candidate
    raise FileNotFoundError(f"Cannot find raw_chunks under: {path}")


def discover_doc_dirs(outputs_root: str) -> list[Path]:
    """
    Discover all document directories under the active chunk root that contain raw_chunks.

    Returns:
        List of Path objects for each document directory, sorted alphabetically
    """
    root = resolve_active_chunk_root(outputs_root)
    if not root.exists():
        raise FileNotFoundError(f"Outputs root does not exist: {root}")
    doc_dirs = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "raw_chunks").exists():
            doc_dirs.append(child)
    return doc_dirs


# =============================================================================
# Encoding and token counting (mirrors 02_build_domain_graph.py)
# =============================================================================


def get_encoder(model_name: str):
    """Get tiktoken encoder for the given model name."""
    try:
        return tiktoken.encoding_for_model(model_name)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(encoder, text: str) -> int:
    """Count tokens in text using the provided encoder."""
    return len(encoder.encode(text or ""))


# =============================================================================
# HTML/Image detection (mirrors 02_build_domain_graph.py)
# =============================================================================


def contains_html_table(text: str) -> bool:
    """Check if text contains HTML table markup."""
    return any(pattern.search(text or "") for pattern in HTML_TABLE_PATTERNS)


def contains_html_image(text: str) -> bool:
    """Check if text contains HTML image markup."""
    return any(pattern.search(text or "") for pattern in HTML_IMAGE_PATTERNS)


def contains_markdown_image(text: str) -> bool:
    """Check if text contains markdown image syntax."""
    return bool(MARKDOWN_IMAGE_PATTERN.search(text or ""))


def normalize_preview(text: str, limit: int) -> str:
    """Normalize text preview to specified character limit."""
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


# =============================================================================
# Chunk loading (mirrors 02_build_domain_graph.py:142-155)
# =============================================================================


def load_chunks(raw_chunks_dir: Path) -> list[dict[str, Any]]:
    """
    Load all chunks from all JSON files in raw_chunks directory.

    JSON files are processed in sorted order (alphabetical by filename).
    Each JSON file should have the format:
        {
            "doc_name": "...",
            "chapter": "...",
            "chunks": [
                {"chunk_id": "...", "breadcrumb": "...", "content": "...", ...}
            ]
        }

    Returns:
        List of chunk dictionaries with chapter_file, doc_name, chapter added
    """
    loaded: list[dict[str, Any]] = []
    for json_file in sorted(raw_chunks_dir.glob("*.json")):
        payload = json.loads(json_file.read_text(encoding="utf-8"))
        for chunk in payload.get("chunks", []):
            loaded.append(
                {
                    "chapter_file": json_file.name,
                    "doc_name": payload.get("doc_name", ""),
                    "chapter": payload.get("chapter", ""),
                    **chunk,
                }
            )
    return loaded


# =============================================================================
# Validation - DomainChunk-compatible payload (mirrors domain_models.py)
# =============================================================================

REQUIRED_CHUNK_FIELDS = {"chunk_id", "breadcrumb", "content"}
OPTIONAL_CHUNK_FIELDS = {"chunk_type", "metadata"}


def validate_chunk(chunk: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate that a chunk dictionary is DomainChunk-compatible.

    Required fields: chunk_id, breadcrumb, content
    Optional fields: chunk_type (default: "text"), metadata (default: {})

    Returns:
        Tuple of (is_valid, list_of_error_messages)
    """
    errors = []

    # Check required fields
    for field in REQUIRED_CHUNK_FIELDS:
        if field not in chunk:
            errors.append(f"Missing required field: {field}")
        elif not chunk[field]:
            errors.append(f"Empty value for required field: {field}")

    # Validate content is not just whitespace
    content = chunk.get("content", "")
    if content and not content.strip():
        errors.append("content contains only whitespace")

    # Validate metadata is a dict if present
    metadata = chunk.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        errors.append(f"metadata must be a dict, got {type(metadata).__name__}")

    # Validate chunk_type is string if present
    chunk_type = chunk.get("chunk_type")
    if chunk_type is not None and not isinstance(chunk_type, str):
        errors.append(f"chunk_type must be a string, got {type(chunk_type).__name__}")

    return (len(errors) == 0, errors)


def validate_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Validate a list of chunks and return validation report.

    Returns:
        Dict with valid_count, invalid_count, and list of validation errors
    """
    valid_count = 0
    invalid_count = 0
    errors = []

    for idx, chunk in enumerate(chunks):
        is_valid, chunk_errors = validate_chunk(chunk)
        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1
            errors.append(
                {
                    "chunk_index": idx,
                    "chapter_file": chunk.get("chapter_file", "unknown"),
                    "chunk_id": chunk.get("chunk_id", "unknown"),
                    "errors": chunk_errors,
                }
            )

    return {
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "errors": errors,
    }


# =============================================================================
# Parser-compatible chunk ID computation (mirrors 01_parse_markdown.py:25-27)
# =============================================================================


def compute_parser_compatible_chunk_id(breadcrumb: str, content: str) -> str:
    """
    Recompute chunk_id using the parser-compatible algorithm.

    This matches the behavior of RawChunk._generate_id() in 01_parse_markdown.py:
        hashlib.md5(f"{breadcrumb}_{content}".encode('utf-8')).hexdigest()

    This is useful when validating that edited chunks still produce expected IDs,
    or when re-processing raw chunks after manual edits.
    """
    raw_str = f"{breadcrumb}_{content}".encode("utf-8")
    return hashlib.md5(raw_str).hexdigest()


# =============================================================================
# Atomic chunk file writing (new - for save operations)
# =============================================================================


def write_chunk_atomic(
    raw_chunks_dir: Path,
    chapter_file: str,
    doc_name: str,
    chapter: str,
    chunks: list[dict[str, Any]],
) -> Path:
    """
    Atomically write a chapter JSON file to raw_chunks directory.

    Uses temporary file + rename for atomicity (no partial writes on failure).

    Returns:
        Path to the written JSON file
    """
    output_path = raw_chunks_dir / chapter_file

    # Build payload matching the format from 01_parse_markdown.py
    payload = {"doc_name": doc_name, "chapter": chapter, "chunks": chunks}

    # Atomic write: temp file + rename
    temp_path = output_path.with_suffix(".tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(output_path)
    except Exception:
        # Clean up temp file on failure
        if temp_path.exists():
            temp_path.unlink()
        raise

    return output_path


# =============================================================================
# Chunk analysis (mirrors 02_build_domain_graph.py:158-279)
# =============================================================================


def analyze_chunks(
    chunks: list[dict[str, Any]],
    encoder,
    long_threshold: int = 1500,
    preview_length: int = 20,
) -> dict[str, Any]:
    """
    Analyze chunks for token counts and various issues.

    Issues detected:
        - long_chunk: token_count >= long_threshold
        - empty_content: content is whitespace-only
        - html_table: contains HTML table markup
        - html_image: contains HTML image markup
        - markdown_image: contains markdown image syntax

    Returns:
        Dict with summary statistics, per-file stats, issues, and chunk details
    """
    token_counts: list[int] = []
    issues: list[ChunkIssue] = []
    chunk_details: list[dict[str, Any]] = []

    total_html_table = 0
    total_html_image = 0
    total_markdown_image = 0
    total_long = 0
    total_empty = 0

    per_file: dict[str, dict[str, Any]] = {}

    for chunk in chunks:
        content = chunk.get("content", "") or ""
        token_count = count_tokens(encoder, content)
        token_counts.append(token_count)

        issue_types: list[str] = []
        if token_count >= long_threshold:
            total_long += 1
            issue_types.append("long_chunk")
        if not content.strip():
            total_empty += 1
            issue_types.append("empty_content")
        if contains_html_table(content):
            total_html_table += 1
            issue_types.append("html_table")
        if contains_html_image(content):
            total_html_image += 1
            issue_types.append("html_image")
        if contains_markdown_image(content):
            total_markdown_image += 1
            issue_types.append("markdown_image")

        if issue_types:
            issues.append(
                ChunkIssue(
                    chapter_file=chunk["chapter_file"],
                    chunk_id=chunk.get("chunk_id", ""),
                    breadcrumb=chunk.get("breadcrumb", ""),
                    token_count=token_count,
                    issue_types=issue_types,
                    content_preview=normalize_preview(content, preview_length),
                )
            )

        chunk_details.append(
            {
                "doc_name": chunk.get("doc_name", ""),
                "chapter": chunk.get("chapter", ""),
                "chapter_file": chunk["chapter_file"],
                "chunk_id": chunk.get("chunk_id", ""),
                "chunk_type": chunk.get("chunk_type", ""),
                "breadcrumb": chunk.get("breadcrumb", ""),
                "content": content,
                "metadata": chunk.get("metadata", {}),
                "token_count": token_count,
                "issue_types": issue_types,
                "content_preview": normalize_preview(content, preview_length),
            }
        )

        file_key = chunk["chapter_file"]
        file_stats = per_file.setdefault(
            file_key,
            {
                "chapter": chunk.get("chapter", ""),
                "chunk_count": 0,
                "token_counts": [],
                "long_chunk_count": 0,
                "html_table_count": 0,
                "html_image_count": 0,
                "markdown_image_count": 0,
            },
        )
        file_stats["chunk_count"] += 1
        file_stats["token_counts"].append(token_count)
        if "long_chunk" in issue_types:
            file_stats["long_chunk_count"] += 1
        if "html_table" in issue_types:
            file_stats["html_table_count"] += 1
        if "html_image" in issue_types:
            file_stats["html_image_count"] += 1
        if "markdown_image" in issue_types:
            file_stats["markdown_image_count"] += 1

    per_file_summary: dict[str, Any] = {}
    for file_key, file_stats in per_file.items():
        counts = file_stats.pop("token_counts")
        per_file_summary[file_key] = {
            **file_stats,
            "avg_tokens": round(sum(counts) / len(counts), 2) if counts else 0,
            "max_tokens": max(counts) if counts else 0,
            "min_tokens": min(counts) if counts else 0,
        }

    token_summary = {
        "chunk_count": len(chunks),
        "avg_tokens": round(sum(token_counts) / len(token_counts), 2)
        if token_counts
        else 0,
        "median_tokens": statistics.median(token_counts) if token_counts else 0,
        "max_tokens": max(token_counts) if token_counts else 0,
        "min_tokens": min(token_counts) if token_counts else 0,
        "long_chunk_count": total_long,
        "empty_chunk_count": total_empty,
        "html_table_count": total_html_table,
        "html_image_count": total_html_image,
        "markdown_image_count": total_markdown_image,
    }

    return {
        "summary": token_summary,
        "per_file": per_file_summary,
        "issues": [_issue_to_dict(item) for item in issues],
        "chunk_details": chunk_details,
    }


def _issue_to_dict(issue: ChunkIssue) -> dict[str, Any]:
    """Convert ChunkIssue dataclass to dict for serialization."""
    return {
        "chapter_file": issue.chapter_file,
        "chunk_id": issue.chunk_id,
        "breadcrumb": issue.breadcrumb,
        "token_count": issue.token_count,
        "issue_types": issue.issue_types,
        "content_preview": issue.content_preview,
    }


# =============================================================================
# Issue chunk routing (mirrors 02_build_domain_graph.py:299-341)
# =============================================================================


def reset_routing_dirs(doc_root: Path) -> None:
    """Clear existing files in routing directories."""
    for folder_name in ["good_chunks", "long_chunk", "tables", "images"]:
        target_dir = doc_root / folder_name
        if target_dir.exists():
            for child in target_dir.iterdir():
                if child.is_file():
                    child.unlink()


def route_issue_chunks(
    raw_chunks_dir: Path, chunk_details: list[dict[str, Any]]
) -> dict[str, int]:
    """
    Route chunks to issue-specific directories based on their issue types.

    Directories created under doc_root:
        - good_chunks/ - chunks with no issues
        - long_chunk/ - chunks exceeding token threshold
        - tables/ - chunks containing HTML tables
        - images/ - chunks containing HTML or markdown images

    Returns:
        Dict mapping directory names to file counts
    """
    doc_root = raw_chunks_dir.parent
    reset_routing_dirs(doc_root)

    routing = {
        "good_chunk": doc_root / "good_chunks",
        "long_chunk": doc_root / "long_chunk",
        "html_table": doc_root / "tables",
        "html_image": doc_root / "images",
        "markdown_image": doc_root / "images",
    }

    counts = {"good_chunks": 0, "long_chunk": 0, "tables": 0, "images": 0}
    for chunk in chunk_details:
        issue_types = chunk.get("issue_types", [])
        routed_types = issue_types if issue_types else ["good_chunk"]
        for issue_type in routed_types:
            target_dir = routing.get(issue_type)
            if target_dir is None:
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "doc_name": chunk.get("doc_name", ""),
                "chapter": chunk.get("chapter", ""),
                "source": "raw_chunks_scan",
                "issue_folder": target_dir.name,
                "issue_type": issue_type,
                "chunk": _build_chunk_payload(chunk),
            }
            safe_issue_type = issue_type.replace("/", "_")
            output_name = f"{safe_issue_type}__{chunk.get('chunk_id', 'chunk')}.json"
            output_path = target_dir / output_name
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            counts[target_dir.name] = counts.get(target_dir.name, 0) + 1
    return counts


def _build_chunk_payload(chunk: dict[str, Any]) -> dict[str, Any]:
    """Build the chunk payload for routing."""
    return {
        "chunk_id": chunk.get("chunk_id", ""),
        "chapter_file": chunk.get("chapter_file", ""),
        "breadcrumb": chunk.get("breadcrumb", ""),
        "content": chunk.get("content", ""),
        "chunk_type": chunk.get("chunk_type", ""),
        "metadata": chunk.get("metadata", {}),
    }


# =============================================================================
# Chunk editing (new - for save operations)
# =============================================================================


class ChunkEditError(ValueError):
    """Raised when chunk edit fails."""

    pass


class ChunkSplitError(ValueError):
    """Raised when chunk split fails."""

    pass


class ChunkMergeError(ValueError):
    """Raised when chunk merge fails."""

    pass


class ChunkDeleteError(ValueError):
    """Raised when chunk deletion fails."""

    pass


class ChunkReprocessError(ValueError):
    """Raised when document-scoped raw chunk reprocess fails."""

    pass


def edit_chunk(
    relative_path: str,
    chunk_id: str,
    content: str,
    breadcrumb: Optional[str] = None,
    chunk_type: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    outputs_root: str = OUTPUTS_ROOT_DEFAULT,
) -> dict[str, Any]:
    """
    Edit a single chunk in a chapter JSON file.

    This function:
    1. Loads the chapter JSON file by relative_path
    2. Finds the target chunk by chunk_id
    3. Updates chunk fields (content, breadcrumb, chunk_type, metadata)
    4. Regenerates chunk_id if content or breadcrumb changes
    5. Preserves chunk_id if only metadata/chunk_type changes
    6. Atomically writes the file back

    Args:
        relative_path: Path to chapter JSON like "doc_name/raw_chunks/chapter.json"
        chunk_id: Current chunk_id of the chunk to edit
        content: New content (required, must be non-empty after strip)
        breadcrumb: New breadcrumb (optional, if omitted keeps existing)
        chunk_type: New chunk_type (optional, if omitted keeps existing)
        metadata: New metadata (optional, if omitted keeps existing)
        outputs_root: Chunk root selector or legacy outputs selector

    Returns:
        Dict with:
        - relative_path: The edited file path
        - old_chunk_id: Original chunk_id
        - new_chunk_id: New chunk_id (may equal old if no ID-regenerating change)
        - success: True

    Raises:
        InvalidRelativePathError: If path attempts to escape outputs root
        FileNotFoundError: If file or chunk not found
        InvalidChapterJsonError: If JSON is invalid
        ChunkEditError: If content is whitespace-only
    """
    # Validate content is non-empty after strip
    if not content or not content.strip():
        raise ChunkEditError("content cannot be empty or whitespace-only")

    # Load the chapter file
    chapter_data = load_chapter_by_relative_path(relative_path, outputs_root)

    # Find the target chunk
    chunks = chapter_data.get("chunks", [])
    target_index = None
    old_chunk = None
    for idx, chunk in enumerate(chunks):
        if chunk.get("chunk_id") == chunk_id:
            target_index = idx
            old_chunk = chunk
            break

    if target_index is None or old_chunk is None:
        raise ChunkEditError(f"Chunk not found: {chunk_id}")

    # Determine what changed
    old_breadcrumb = old_chunk.get("breadcrumb", "")
    old_content = old_chunk.get("content", "")
    old_chunk_type = old_chunk.get("chunk_type", "text")
    old_metadata = old_chunk.get("metadata", {})

    # Use provided values or fall back to existing
    new_breadcrumb = breadcrumb if breadcrumb is not None else old_breadcrumb
    new_content = content.strip()
    new_chunk_type = chunk_type if chunk_type is not None else old_chunk_type
    new_metadata = metadata if metadata is not None else old_metadata

    # Check if we need to regenerate chunk_id
    content_changed = new_content != old_content
    breadcrumb_changed = new_breadcrumb != old_breadcrumb
    needs_id_regen = content_changed or breadcrumb_changed

    # Build new chunk
    if needs_id_regen:
        new_chunk_id = compute_parser_compatible_chunk_id(new_breadcrumb, new_content)
    else:
        new_chunk_id = chunk_id  # Preserve old ID

    new_chunk = {
        "chunk_id": new_chunk_id,
        "breadcrumb": new_breadcrumb,
        "content": new_content,
        "chunk_type": new_chunk_type,
        "metadata": new_metadata,
    }

    # Replace the chunk, preserving order
    new_chunks = list(chunks)
    new_chunks[target_index] = new_chunk

    # Extract chapter file path for writing
    root = resolve_active_chunk_root(outputs_root)
    chapter_file = root / relative_path

    # Determine raw_chunks dir and chapter name
    raw_chunks_dir = chapter_file.parent
    doc_name = chapter_data.get("doc_name", "")
    chapter_name = chapter_data.get("chapter", "")

    # Atomically write the updated chapter
    write_chunk_atomic(
        raw_chunks_dir=raw_chunks_dir,
        chapter_file=chapter_file.name,
        doc_name=doc_name,
        chapter=chapter_name,
        chunks=new_chunks,
    )

    return {
        "relative_path": relative_path,
        "old_chunk_id": chunk_id,
        "new_chunk_id": new_chunk_id,
        "success": True,
    }


# =============================================================================
# Chunk splitting (new - for manual management)
# =============================================================================


def split_chunk(
    relative_path: str,
    chunk_id: str,
    left_content: str,
    right_content: str,
    outputs_root: str = OUTPUTS_ROOT_DEFAULT,
) -> dict[str, Any]:
    """
    Split a chunk into two new chunks in the same chapter file.

    This function:
    1. Loads the chapter JSON file by relative_path
    2. Finds the target chunk by chunk_id
    3. Validates both left_content and right_content are non-empty after strip
    4. Creates two new chunks replacing the original
    5. Both new chunks inherit breadcrumb/chunk_type/metadata from original
    6. Both new chunks get new parser-compatible chunk_ids
    7. Atomically writes the file back

    Args:
        relative_path: Path to chapter JSON like "doc_name/raw_chunks/chapter.json"
        chunk_id: Current chunk_id of the chunk to split
        left_content: Content for the left (first) new chunk
        right_content: Content for the right (second) new chunk
        outputs_root: Chunk root selector or legacy outputs selector

    Returns:
        Dict with:
        - relative_path: The edited file path
        - original_chunk_id: Original chunk_id
        - left_chunk_id: New chunk_id for left portion
        - right_chunk_id: New chunk_id for right portion
        - success: True

    Raises:
        InvalidRelativePathError: If path attempts to escape outputs root
        FileNotFoundError: If file or chunk not found
        InvalidChapterJsonError: If JSON is invalid
        ChunkSplitError: If content is whitespace-only or chunk not found
    """
    # Validate both contents are non-empty after strip
    if not left_content or not left_content.strip():
        raise ChunkSplitError("left_content cannot be empty or whitespace-only")
    if not right_content or not right_content.strip():
        raise ChunkSplitError("right_content cannot be empty or whitespace-only")

    # Load the chapter file
    chapter_data = load_chapter_by_relative_path(relative_path, outputs_root)

    # Find the target chunk
    chunks = chapter_data.get("chunks", [])
    target_index = None
    old_chunk = None
    for idx, chunk in enumerate(chunks):
        if chunk.get("chunk_id") == chunk_id:
            target_index = idx
            old_chunk = chunk
            break

    if target_index is None or old_chunk is None:
        raise ChunkSplitError(f"Chunk not found: {chunk_id}")

    # Inherit fields from original chunk
    old_breadcrumb = old_chunk.get("breadcrumb", "")
    old_chunk_type = old_chunk.get("chunk_type", "text")
    old_metadata = old_chunk.get("metadata", {})

    # Create new chunk IDs using parser-compatible algorithm
    left_content_stripped = left_content.strip()
    right_content_stripped = right_content.strip()

    left_chunk_id = compute_parser_compatible_chunk_id(
        old_breadcrumb, left_content_stripped
    )
    right_chunk_id = compute_parser_compatible_chunk_id(
        old_breadcrumb, right_content_stripped
    )

    # Build two new chunks
    left_chunk = {
        "chunk_id": left_chunk_id,
        "breadcrumb": old_breadcrumb,
        "content": left_content_stripped,
        "chunk_type": old_chunk_type,
        "metadata": old_metadata,
    }

    right_chunk = {
        "chunk_id": right_chunk_id,
        "breadcrumb": old_breadcrumb,
        "content": right_content_stripped,
        "chunk_type": old_chunk_type,
        "metadata": old_metadata,
    }

    # Replace the chunk, preserving order
    new_chunks = list(chunks)
    new_chunks[target_index] = left_chunk
    new_chunks.insert(target_index + 1, right_chunk)

    # Extract chapter file path for writing
    root = resolve_active_chunk_root(outputs_root)
    chapter_file = root / relative_path

    # Determine raw_chunks dir and chapter name
    raw_chunks_dir = chapter_file.parent
    doc_name = chapter_data.get("doc_name", "")
    chapter_name = chapter_data.get("chapter", "")

    # Atomically write the updated chapter
    write_chunk_atomic(
        raw_chunks_dir=raw_chunks_dir,
        chapter_file=chapter_file.name,
        doc_name=doc_name,
        chapter=chapter_name,
        chunks=new_chunks,
    )

    return {
        "relative_path": relative_path,
        "original_chunk_id": chunk_id,
        "left_chunk_id": left_chunk_id,
        "right_chunk_id": right_chunk_id,
        "success": True,
    }


# =============================================================================
# Chunk merging (new - for manual management)
# =============================================================================


def merge_chunks(
    relative_path: str,
    first_chunk_id: str,
    second_chunk_id: str,
    outputs_root: str = OUTPUTS_ROOT_DEFAULT,
) -> dict[str, Any]:
    """
    Merge two adjacent chunks into one new chunk in the same chapter file.

    This function:
    1. Loads the chapter JSON file by relative_path
    2. Finds both chunks by their chunk_ids
    3. Validates chunks are adjacent (consecutive indices)
    4. Merges content with newline separator
    5. Keeps first chunk's breadcrumb/chunk_type/metadata
    6. Creates new parser-compatible chunk_id
    7. Atomically writes the file back

    Args:
        relative_path: Path to chapter JSON like "doc_name/raw_chunks/chapter.json"
        first_chunk_id: chunk_id of the first (earlier) chunk
        second_chunk_id: chunk_id of the second (later) chunk
        outputs_root: Chunk root selector or legacy outputs selector

    Returns:
        Dict with:
        - relative_path: The edited file path
        - first_chunk_id: Original first chunk_id
        - second_chunk_id: Original second chunk_id
        - merged_chunk_id: New chunk_id for merged chunk
        - success: True

    Raises:
        InvalidRelativePathError: If path attempts to escape outputs root
        FileNotFoundError: If file or chunk not found
        InvalidChapterJsonError: If JSON is invalid
        ChunkMergeError: If chunk not found or chunks are not adjacent
    """
    # Load the chapter file
    chapter_data = load_chapter_by_relative_path(relative_path, outputs_root)

    # Find both chunks
    chunks = chapter_data.get("chunks", [])
    first_index = None
    second_index = None
    first_chunk = None
    second_chunk = None

    for idx, chunk in enumerate(chunks):
        if chunk.get("chunk_id") == first_chunk_id:
            first_index = idx
            first_chunk = chunk
        if chunk.get("chunk_id") == second_chunk_id:
            second_index = idx
            second_chunk = chunk

    if first_chunk is None:
        raise ChunkMergeError(f"First chunk not found: {first_chunk_id}")
    if second_chunk is None:
        raise ChunkMergeError(f"Second chunk not found: {second_chunk_id}")

    # Check adjacency - must be consecutive indices
    if first_index is None or second_index is None:
        raise ChunkMergeError(
            f"One or both chunks not found: {first_chunk_id}, {second_chunk_id}"
        )

    # Adjacent means second_index is first_index + 1
    if second_index != first_index + 1:
        raise ChunkMergeError(
            f"Chunks are not adjacent. "
            f"Chunk indices must be consecutive for merge. "
            f"first_chunk at index {first_index}, second_chunk at index {second_index}"
        )

    # Use first chunk's metadata, merge content
    merged_content = (
        first_chunk.get("content", "").strip()
        + "\n\n"
        + second_chunk.get("content", "").strip()
    )
    merged_breadcrumb = first_chunk.get("breadcrumb", "")
    merged_chunk_type = first_chunk.get("chunk_type", "text")
    merged_metadata = first_chunk.get("metadata", {})

    # Create new chunk ID
    merged_chunk_id = compute_parser_compatible_chunk_id(
        merged_breadcrumb, merged_content
    )

    # Build merged chunk
    merged_chunk = {
        "chunk_id": merged_chunk_id,
        "breadcrumb": merged_breadcrumb,
        "content": merged_content,
        "chunk_type": merged_chunk_type,
        "metadata": merged_metadata,
    }

    # Replace both chunks with single merged chunk, preserving order
    new_chunks = list(chunks)
    new_chunks[first_index] = merged_chunk
    del new_chunks[first_index + 1]

    # Extract chapter file path for writing
    root = resolve_active_chunk_root(outputs_root)
    chapter_file = root / relative_path

    # Determine raw_chunks dir and chapter name
    raw_chunks_dir = chapter_file.parent
    doc_name = chapter_data.get("doc_name", "")
    chapter_name = chapter_data.get("chapter", "")

    # Atomically write the updated chapter
    write_chunk_atomic(
        raw_chunks_dir=raw_chunks_dir,
        chapter_file=chapter_file.name,
        doc_name=doc_name,
        chapter=chapter_name,
        chunks=new_chunks,
    )

    return {
        "relative_path": relative_path,
        "first_chunk_id": first_chunk_id,
        "second_chunk_id": second_chunk_id,
        "merged_chunk_id": merged_chunk_id,
        "success": True,
    }


def delete_chunk(
    relative_path: str,
    chunk_id: str,
    outputs_root: str = OUTPUTS_ROOT_DEFAULT,
) -> dict[str, Any]:
    """
    Delete one chunk from a chapter JSON file and persist to disk atomically.

    Args:
        relative_path: Path to chapter JSON like "doc_name/raw_chunks/chapter.json"
        chunk_id: chunk_id of the chunk to delete
        outputs_root: Chunk root selector or legacy outputs selector

    Returns:
        Dict with deleted chunk info and the next recommended active chunk id.

    Raises:
        InvalidRelativePathError: If path attempts to escape outputs root
        FileNotFoundError: If file not found
        InvalidChapterJsonError: If JSON is invalid
        ChunkDeleteError: If target chunk is not found
    """
    chapter_data = load_chapter_by_relative_path(relative_path, outputs_root)

    chunks = chapter_data.get("chunks", [])
    target_index = None
    for idx, chunk in enumerate(chunks):
        if chunk.get("chunk_id") == chunk_id:
            target_index = idx
            break

    if target_index is None:
        raise ChunkDeleteError(f"Chunk not found: {chunk_id}")

    new_chunks = list(chunks)
    del new_chunks[target_index]

    root = resolve_active_chunk_root(outputs_root)
    chapter_file = root / relative_path
    raw_chunks_dir = chapter_file.parent

    write_chunk_atomic(
        raw_chunks_dir=raw_chunks_dir,
        chapter_file=chapter_file.name,
        doc_name=chapter_data.get("doc_name", ""),
        chapter=chapter_data.get("chapter", ""),
        chunks=new_chunks,
    )

    next_chunk_id = None
    if new_chunks:
        next_index = min(target_index, len(new_chunks) - 1)
        next_chunk_id = new_chunks[next_index].get("chunk_id")

    return {
        "relative_path": relative_path,
        "deleted_chunk_id": chunk_id,
        "next_chunk_id": next_chunk_id,
        "remaining_chunk_count": len(new_chunks),
        "success": True,
    }


# =============================================================================
# Convenience function for full scan workflow (non-CLI)
# =============================================================================


def scan_document(
    doc_dir: Path,
    tokenizer_model: str = "gpt-4o-mini",
    long_threshold: int = 1500,
    preview_length: int = 20,
    write_routing: bool = False,
) -> dict[str, Any]:
    """
    Perform a full document scan: load, analyze, optionally route.

    This is the import-safe version of the CLI scan_one_doc_dir() function.
    Use this for FastAPI endpoints that need analysis without CLI side effects.

    Args:
        doc_dir: Path to document directory (containing raw_chunks)
        tokenizer_model: tiktoken model for token counting
        long_threshold: Token count threshold for "long_chunk" flag
        preview_length: Character limit for content preview
        write_routing: Whether to write routed issue chunks (default: False)

    Returns:
        Dict with analysis report, config, and optionally routing counts
    """
    raw_chunks_dir = resolve_raw_chunks_dir(str(doc_dir))
    encoder = get_encoder(tokenizer_model)
    chunks = load_chunks(raw_chunks_dir)
    report = analyze_chunks(
        chunks,
        encoder=encoder,
        long_threshold=long_threshold,
        preview_length=preview_length,
    )

    routing = {}
    if write_routing:
        routing = route_issue_chunks(raw_chunks_dir, report["chunk_details"])

    report["config"] = {
        "doc_dir": str(raw_chunks_dir.parent),
        "raw_chunks_dir": str(raw_chunks_dir),
        "tokenizer_model": tokenizer_model,
        "long_threshold": long_threshold,
        "preview_length": preview_length,
    }
    report["routing"] = routing

    return report


def write_scan_report(doc_dir: Path, report: dict[str, Any]) -> Path:
    """Write a document-scoped raw chunk scan report next to routing outputs."""
    report_path = doc_dir / "raw_chunks_scan_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report_path


def resolve_document_dir(
    doc_dir: str, outputs_root: str = OUTPUTS_ROOT_DEFAULT
) -> Path:
    """Resolve one discovered document directory under the active chunk root."""
    root = resolve_active_chunk_root(outputs_root)
    if not root.exists():
        raise FileNotFoundError(f"Outputs root does not exist: {root}")

    requested_path = Path(doc_dir).expanduser()
    candidate_paths = []
    if requested_path.is_absolute():
        candidate_paths.append(requested_path.resolve())
    else:
        candidate_paths.append((root / requested_path).resolve())
        candidate_paths.append((root / requested_path.name).resolve())

    discovered_dirs = discover_doc_dirs(str(root))
    discovered_by_path = {path.resolve(): path.resolve() for path in discovered_dirs}
    discovered_by_name = {path.name: path.resolve() for path in discovered_dirs}

    for candidate in candidate_paths:
        try:
            if candidate.is_relative_to(root) and candidate in discovered_by_path:
                return discovered_by_path[candidate]
        except ValueError:
            continue

    if requested_path.name in discovered_by_name:
        return discovered_by_name[requested_path.name]

    raise ChunkReprocessError(
        f"doc_dir must resolve to one discovered document directory under {root}"
    )


def reprocess_document_routing(
    doc_dir: str,
    outputs_root: str = OUTPUTS_ROOT_DEFAULT,
    tokenizer_model: str = "gpt-4o-mini",
    long_threshold: int = 1500,
    preview_length: int = 20,
) -> dict[str, Any]:
    """Refresh deterministic raw chunk routing artifacts for one document only."""
    resolved_doc_dir = resolve_document_dir(doc_dir, outputs_root=outputs_root)
    report = scan_document(
        resolved_doc_dir,
        tokenizer_model=tokenizer_model,
        long_threshold=long_threshold,
        preview_length=preview_length,
        write_routing=True,
    )
    report_path = write_scan_report(resolved_doc_dir, report)
    return {
        "doc_dir": str(resolved_doc_dir),
        "report_path": str(report_path),
        "summary": report["summary"],
        "routing": report["routing"],
    }
