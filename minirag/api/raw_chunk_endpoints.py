# pyright: reportMissingImports=false

"""
raw_chunk_endpoints.py

FastAPI endpoint registration for raw chunk summary and detail endpoints.
This module is separate from minirag_server.py to allow test reuse without
triggering heavy dependencies like ascii_colors, MiniRAG, etc.
"""

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from minirag.api.raw_chunk_registry import RawChunkRegistry
from data_pipeline.raw_chunk_pipeline import (
    load_chapter_by_relative_path,
    edit_chunk,
    split_chunk,
    merge_chunks,
    delete_chunk,
    reprocess_document_routing,
    InvalidRelativePathError,
    InvalidChapterJsonError,
    ChunkEditError,
    ChunkSplitError,
    ChunkMergeError,
    ChunkDeleteError,
    ChunkReprocessError,
)


class ChunkEditRequest(BaseModel):
    """Request body for chunk edit endpoint."""

    relative_path: str
    chunk_id: str
    content: str
    breadcrumb: Optional[str] = None
    chunk_type: Optional[str] = None
    metadata: Optional[dict] = None


class ChunkSplitRequest(BaseModel):
    """Request body for chunk split endpoint."""

    relative_path: str
    chunk_id: str
    left_content: str
    right_content: str


class ChunkMergeRequest(BaseModel):
    """Request body for chunk merge endpoint."""

    relative_path: str
    first_chunk_id: str
    second_chunk_id: str


class ChunkReprocessRequest(BaseModel):
    """Request body for document-scoped raw chunk reprocess endpoint."""

    doc_dir: str


class ChunkDeleteRequest(BaseModel):
    """Request body for chunk delete endpoint."""

    relative_path: str
    chunk_id: str


def register_raw_chunk_endpoints(
    app: FastAPI,
    raw_chunk_registry: RawChunkRegistry,
    outputs_root: str,
    dependencies: list[Any] | None = None,
) -> FastAPI:
    """
    Register raw chunk summary and detail endpoints on a FastAPI app.

    This function is exported so both production (minirag_server.py) and tests
    can use the same route registration logic without duplication.

    Args:
        app: FastAPI application instance
        raw_chunk_registry: RawChunkRegistry instance
        outputs_root: Path string to outputs root directory

    Returns:
        The app with endpoints registered
    """

    # Keep test and production registration behavior aligned.
    raw_chunk_registry.sync_from_disk()
    route_dependencies = dependencies or []

    @app.get("/pipeline/raw-chunks/summary", dependencies=route_dependencies)
    async def get_raw_chunks_summary():
        """Get summary of all raw chunk files discovered under outputs_root."""
        raw_chunk_registry.sync_from_disk()
        summary = raw_chunk_registry.build_summary()
        return {"outputs_root": outputs_root, **summary}

    @app.get("/pipeline/raw-chunks/file", dependencies=route_dependencies)
    async def get_raw_chunks_file_detail(relative_path: str):
        """Get detail for a single chapter file by relative_path."""
        if not relative_path:
            raise HTTPException(
                status_code=400, detail="relative_path query parameter is required"
            )

        try:
            chapter_data = load_chapter_by_relative_path(
                relative_path, outputs_root=outputs_root
            )
        except InvalidRelativePathError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except InvalidChapterJsonError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to load chapter: {str(e)}"
            )

        registry_entry = raw_chunk_registry.get_by_relative_path(relative_path)
        chunks = chapter_data.get("chunks", [])
        chunk_details = []
        for idx, chunk in enumerate(chunks):
            chunk_details.append(
                {
                    "chunk_index": idx,
                    "chunk_id": chunk.get("chunk_id", ""),
                    "breadcrumb": chunk.get("breadcrumb", ""),
                    "content": chunk.get("content", ""),
                    "chunk_type": chunk.get("chunk_type", ""),
                    "metadata": chunk.get("metadata", {}),
                }
            )

        return {
            "relative_path": relative_path,
            "doc_name": chapter_data.get("doc_name", ""),
            "chapter": chapter_data.get("chapter", ""),
            "chunk_count": len(chunks),
            "chunks": chunk_details,
            "registry": {
                "dirty": registry_entry.get("dirty", False)
                if registry_entry
                else False,
                "last_edited_at": registry_entry.get("last_edited_at")
                if registry_entry
                else None,
                "last_reprocessed_at": registry_entry.get("last_reprocessed_at")
                if registry_entry
                else None,
                "last_reprocess_status": registry_entry.get(
                    "last_reprocess_status", "idle"
                )
                if registry_entry
                else "idle",
                "downstream_state": registry_entry.get("downstream_state", "stale")
                if registry_entry
                else "stale",
            }
            if registry_entry
            else None,
        }

    @app.put("/pipeline/raw-chunks/chunks/edit", dependencies=route_dependencies)
    async def edit_raw_chunk(request: ChunkEditRequest):
        """Edit a single chunk in a chapter JSON file."""
        if not request.content or not request.content.strip():
            raise HTTPException(
                status_code=400, detail="content cannot be empty or whitespace-only"
            )

        try:
            result = edit_chunk(
                relative_path=request.relative_path,
                chunk_id=request.chunk_id,
                content=request.content,
                breadcrumb=request.breadcrumb,
                chunk_type=request.chunk_type,
                metadata=request.metadata,
                outputs_root=outputs_root,
            )
        except InvalidRelativePathError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except InvalidChapterJsonError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ChunkEditError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to edit chunk: {str(e)}"
            )

        raw_chunk_registry.mark_dirty(request.relative_path)

        return result

    @app.post("/pipeline/raw-chunks/chunks/split", dependencies=route_dependencies)
    async def split_raw_chunk(request: ChunkSplitRequest):
        """Split a chunk into two new chunks."""
        if not request.left_content or not request.left_content.strip():
            raise HTTPException(
                status_code=400,
                detail="left_content cannot be empty or whitespace-only",
            )
        if not request.right_content or not request.right_content.strip():
            raise HTTPException(
                status_code=400,
                detail="right_content cannot be empty or whitespace-only",
            )

        try:
            result = split_chunk(
                relative_path=request.relative_path,
                chunk_id=request.chunk_id,
                left_content=request.left_content,
                right_content=request.right_content,
                outputs_root=outputs_root,
            )
        except InvalidRelativePathError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except InvalidChapterJsonError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ChunkSplitError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to split chunk: {str(e)}"
            )

        raw_chunk_registry.mark_dirty(request.relative_path)

        return result

    @app.post("/pipeline/raw-chunks/chunks/merge", dependencies=route_dependencies)
    async def merge_raw_chunks(request: ChunkMergeRequest):
        """Merge two adjacent chunks into one."""
        try:
            result = merge_chunks(
                relative_path=request.relative_path,
                first_chunk_id=request.first_chunk_id,
                second_chunk_id=request.second_chunk_id,
                outputs_root=outputs_root,
            )
        except InvalidRelativePathError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except InvalidChapterJsonError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ChunkMergeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to merge chunks: {str(e)}"
            )

        raw_chunk_registry.mark_dirty(request.relative_path)

        return result

    @app.post("/pipeline/raw-chunks/chunks/delete", dependencies=route_dependencies)
    async def delete_raw_chunk(request: ChunkDeleteRequest):
        """Delete one chunk from chapter JSON and persist to disk."""
        try:
            result = delete_chunk(
                relative_path=request.relative_path,
                chunk_id=request.chunk_id,
                outputs_root=outputs_root,
            )
        except InvalidRelativePathError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except InvalidChapterJsonError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ChunkDeleteError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to delete chunk: {str(e)}"
            )

        raw_chunk_registry.mark_dirty(request.relative_path)

        return result

    @app.post("/pipeline/raw-chunks/reprocess", dependencies=route_dependencies)
    async def reprocess_raw_chunks(request: ChunkReprocessRequest):
        """Refresh raw chunk scan/routing artifacts for one discovered document only."""
        if not request.doc_dir or not request.doc_dir.strip():
            raise HTTPException(status_code=400, detail="doc_dir cannot be empty")

        raw_chunk_registry.sync_from_disk()
        doc_key = request.doc_dir.strip().rstrip("/")
        doc_name = doc_key.split("/")[-1]
        doc_entries = raw_chunk_registry.get_by_doc_dir(doc_name)
        if not doc_entries:
            raise HTTPException(
                status_code=404,
                detail=(
                    "doc_dir must resolve to one discovered document directory under "
                    f"{outputs_root}"
                ),
            )

        raw_chunk_registry.mark_doc_reprocess_started(doc_name)

        try:
            result = reprocess_document_routing(doc_key, outputs_root=outputs_root)
        except (ChunkReprocessError, FileNotFoundError) as e:
            raw_chunk_registry.mark_doc_reprocess_failed(doc_name, str(e))
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raw_chunk_registry.mark_doc_reprocess_failed(doc_name, str(e))
            raise HTTPException(
                status_code=500, detail=f"Failed to reprocess raw chunks: {str(e)}"
            )

        raw_chunk_registry.sync_from_disk()
        raw_chunk_registry.mark_doc_reprocess_completed(doc_name)

        return {
            **result,
            "last_reprocess_status": "success",
            "downstream_state": "routing_refreshed",
        }

    return app
