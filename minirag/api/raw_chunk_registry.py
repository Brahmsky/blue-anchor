"""
raw_chunk_registry.py

File-backed registry for raw chunk metadata, tracking dirty/reprocess/downstream state.
Separate from DocumentRegistry to avoid scope bleed into MiniRAG upload/index flows.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_pipeline.raw_chunk_pipeline import resolve_active_chunk_root
from minirag.utils import load_json, write_json


class RawChunkRegistry:
    """
    Persist raw chunk metadata keyed by relative path under the active chunk root.

    This registry tracks:
    - File discovery state (doc_dir, doc_name, chapter, chunk_count)
    - Edit state (dirty, last_edited_at)
    - Reprocess state (last_reprocessed_at, last_reprocess_status)
    - Downstream state (downstream_state: stale | routing_refreshed)

    The source of truth remains the chapter JSON files under
    ``<datasource>/staging/chunks/*/raw_chunks/*.json`` after selector normalization.
    """

    def __init__(
        self,
        working_dir: str,
        outputs_root: str = "datasources/local_ship_docs/staging/chunks",
    ):
        self.working_dir = Path(working_dir).resolve()
        self.outputs_root = resolve_active_chunk_root(outputs_root)
        self.store_file = self.working_dir / "raw_chunk_registry.json"
        self._data: Dict[str, Dict[str, Any]] = load_json(str(self.store_file)) or {}

    def _normalize_relative_path(self, relative_path: str) -> str:
        return str(Path(relative_path)).replace("\\", "/")

    def sync_from_disk(self) -> None:
        """
        Scan the active chunk root for chapter JSON files and sync registry.

        For each discovered file, record:
        - relative_path (relative to outputs_root)
        - doc_dir (parent directory name)
        - doc_name (from JSON)
        - chapter (from JSON)
        - chunk_count (len of chunks array)
        - Preserve existing dirty, last_edited_at, last_reprocessed_at, last_reprocess_status, downstream_state
        """
        if not self.outputs_root.exists():
            return

        existing_keys: set[str] = set()

        # Iterate through document directories
        for doc_dir in sorted(self.outputs_root.iterdir()):
            if not doc_dir.is_dir():
                continue

            raw_chunks_dir = doc_dir / "raw_chunks"
            if not raw_chunks_dir.exists() or not raw_chunks_dir.is_dir():
                continue

            doc_name = doc_dir.name

            # Process each chapter JSON file in raw_chunks
            for chapter_file in sorted(raw_chunks_dir.glob("*.json")):
                relative_path = self._normalize_relative_path(
                    str(chapter_file.relative_to(self.outputs_root))
                )
                existing_keys.add(relative_path)

                try:
                    payload = self._load_chapter_metadata(chapter_file, doc_name)
                except Exception:
                    # Skip invalid JSON files
                    continue

                current = self._data.get(relative_path, {})
                self._data[relative_path] = {
                    **current,
                    "relative_path": relative_path,
                    "doc_dir": doc_name,
                    "doc_name": payload.get("doc_name", doc_name),
                    "chapter": payload.get("chapter", chapter_file.stem),
                    "chunk_count": payload.get("chunk_count", 0),
                    # Preserve existing state fields if present
                    "dirty": current.get("dirty", False),
                    "last_edited_at": current.get("last_edited_at"),
                    "last_reprocessed_at": current.get("last_reprocessed_at"),
                    "last_reprocess_status": current.get(
                        "last_reprocess_status", "idle"
                    ),
                    "downstream_state": current.get("downstream_state", "stale"),
                }

        # Remove stale entries for deleted files
        stale_keys = [k for k in self._data.keys() if k not in existing_keys]
        for key in stale_keys:
            self._data.pop(key, None)

        self._save()

    def _load_chapter_metadata(
        self, chapter_file: Path, doc_name: str
    ) -> Dict[str, Any]:
        """Load minimal metadata from a chapter JSON file."""
        import json

        content = json.loads(chapter_file.read_text(encoding="utf-8"))
        chunks = content.get("chunks", [])
        return {
            "doc_name": content.get("doc_name", doc_name),
            "chapter": content.get("chapter", chapter_file.stem),
            "chunk_count": len(chunks),
        }

    def get_by_relative_path(self, relative_path: str) -> Optional[Dict[str, Any]]:
        """Get registry entry by relative path."""
        return self._data.get(self._normalize_relative_path(relative_path))

    def get_all_entries(self) -> List[Dict[str, Any]]:
        """Get all registry entries as a list."""
        return list(self._data.values())

    def get_by_doc_dir(self, doc_dir: str) -> List[Dict[str, Any]]:
        """Get all registry entries for one document directory."""
        return [
            entry for entry in self._data.values() if entry.get("doc_dir") == doc_dir
        ]

    def mark_dirty(self, relative_path: str) -> None:
        """Mark a file as dirty (edited but not reprocessed)."""
        relative_path = self._normalize_relative_path(relative_path)
        if relative_path in self._data:
            self._data[relative_path]["dirty"] = True
            self._data[relative_path]["last_edited_at"] = datetime.now().isoformat()
            self._data[relative_path]["downstream_state"] = "stale"
            self._save()

    def mark_clean(self, relative_path: str) -> None:
        """Mark a file as clean (no pending edits)."""
        relative_path = self._normalize_relative_path(relative_path)
        if relative_path in self._data:
            self._data[relative_path]["dirty"] = False
            self._save()

    def mark_reprocess_started(self, relative_path: str) -> None:
        """Mark reprocess as started for a file."""
        relative_path = self._normalize_relative_path(relative_path)
        if relative_path in self._data:
            self._data[relative_path]["last_reprocessed_at"] = (
                datetime.now().isoformat()
            )
            self._data[relative_path]["last_reprocess_status"] = "running"
            self._save()

    def mark_reprocess_completed(self, relative_path: str) -> None:
        """Mark reprocess as completed for a file."""
        relative_path = self._normalize_relative_path(relative_path)
        if relative_path in self._data:
            self._data[relative_path]["last_reprocessed_at"] = (
                datetime.now().isoformat()
            )
            self._data[relative_path]["last_reprocess_status"] = "success"
            self._data[relative_path]["downstream_state"] = "routing_refreshed"
            self._save()

    def mark_reprocess_failed(self, relative_path: str, error: str) -> None:
        """Mark reprocess as failed for a file."""
        relative_path = self._normalize_relative_path(relative_path)
        if relative_path in self._data:
            self._data[relative_path]["last_reprocessed_at"] = (
                datetime.now().isoformat()
            )
            self._data[relative_path]["last_reprocess_status"] = "failed"
            # Keep downstream_state as stale on failure
            self._save()

    def mark_doc_reprocess_started(self, doc_dir: str) -> None:
        """Mark reprocess as started for every chapter file in one document."""
        updated = False
        for relative_path, entry in self._data.items():
            if entry.get("doc_dir") != doc_dir:
                continue
            self._data[relative_path]["last_reprocessed_at"] = (
                datetime.now().isoformat()
            )
            self._data[relative_path]["last_reprocess_status"] = "running"
            updated = True
        if updated:
            self._save()

    def mark_doc_reprocess_completed(self, doc_dir: str) -> None:
        """Mark reprocess as completed for every chapter file in one document."""
        updated = False
        for relative_path, entry in self._data.items():
            if entry.get("doc_dir") != doc_dir:
                continue
            self._data[relative_path]["last_reprocessed_at"] = (
                datetime.now().isoformat()
            )
            self._data[relative_path]["last_reprocess_status"] = "success"
            self._data[relative_path]["downstream_state"] = "routing_refreshed"
            self._data[relative_path]["dirty"] = False
            updated = True
        if updated:
            self._save()

    def mark_doc_reprocess_failed(self, doc_dir: str, error: str) -> None:
        """Mark reprocess as failed for every chapter file in one document."""
        updated = False
        for relative_path, entry in self._data.items():
            if entry.get("doc_dir") != doc_dir:
                continue
            self._data[relative_path]["last_reprocessed_at"] = (
                datetime.now().isoformat()
            )
            self._data[relative_path]["last_reprocess_status"] = "failed"
            updated = True
        if updated:
            self._save()

    def build_summary(self) -> Dict[str, Any]:
        """
        Build a summary of all raw chunk entries.

        Returns:
            Dict with stats and sorted items list
        """
        items = sorted(
            self._data.values(),
            key=lambda item: item.get("relative_path", ""),
        )

        stats = {
            "total": len(items),
            "dirty": 0,
            "clean": 0,
            "reprocessing": 0,
            "downstream_stale": 0,
            "downstream_refreshed": 0,
        }

        for item in items:
            if item.get("dirty"):
                stats["dirty"] += 1
            else:
                stats["clean"] += 1

            status = item.get("last_reprocess_status", "idle")
            if status == "running":
                stats["reprocessing"] += 1

            downstream = item.get("downstream_state", "stale")
            if downstream == "stale":
                stats["downstream_stale"] += 1
            else:
                stats["downstream_refreshed"] += 1

        return {
            "stats": stats,
            "items": items,
        }

    def _save(self) -> None:
        """Save registry to disk."""
        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        write_json(self._data, str(self.store_file))
