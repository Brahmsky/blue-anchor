from __future__ import annotations

import json
import shutil
import importlib
from pathlib import Path
from typing import Any

from data_pipeline.raw_chunk_pipeline import reprocess_document_routing
from minirag.datasource_resolver import ResolvedDatasource


_parse_markdown_module = importlib.import_module(
    "data_pipeline.scripts.01_parse_markdown"
)
MarkdownStructuredParser = _parse_markdown_module.MarkdownStructuredParser
build_doc_dir_name = _parse_markdown_module.build_doc_dir_name


def remove_path_if_exists(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def clear_document_pipeline_artifacts(doc_dir: Path) -> None:
    for name in (
        "raw_chunks",
        "good_chunks",
        "long_chunk",
        "tables",
        "images",
        "accepted_records",
        "rejected_chunks",
    ):
        remove_path_if_exists(doc_dir / name)

    for name in ("raw_chunks_scan_report.json", "llm_extract_report.json"):
        remove_path_if_exists(doc_dir / name)


def derive_doc_dir_name(source_path: Path) -> str:
    return build_doc_dir_name(source_path)


def materialize_source_document_to_chunks(
    *,
    source_path: Path,
    content: str,
    datasource: ResolvedDatasource,
    min_length: int = 30,
) -> dict[str, Any]:
    doc_dir_name = derive_doc_dir_name(source_path)
    doc_dir = (datasource.staging_root / "chunks" / doc_dir_name).resolve()
    doc_dir.mkdir(parents=True, exist_ok=True)
    clear_document_pipeline_artifacts(doc_dir)

    parser = MarkdownStructuredParser(
        book_name=source_path.name,
        output_doc_dir=doc_dir,
    )
    parse_result = parser.parse_text_content(content, min_length=min_length)
    routing_result = reprocess_document_routing(
        str(doc_dir),
        outputs_root=str(datasource.staging_root / "chunks"),
    )

    return {
        "doc_dir_name": doc_dir_name,
        "doc_dir": str(doc_dir),
        "raw_chunks_dir": parse_result["raw_chunks_dir"],
        "chapter_count": parse_result["chapter_count"],
        "chunk_count": parse_result["chunk_count"],
        "routing": routing_result["routing"],
        "report_path": routing_result["report_path"],
    }


def purge_extracted_records_for_doc_name(
    datasource: ResolvedDatasource, doc_dir_name: str
) -> bool:
    records_dir = (datasource.staging_root / "extracted" / "records" / doc_dir_name).resolve()
    if not records_dir.exists():
        return False
    shutil.rmtree(records_dir)
    return True


def purge_chunk_outputs_for_doc_name(
    datasource: ResolvedDatasource, doc_dir_name: str
) -> bool:
    doc_dir = (datasource.staging_root / "chunks" / doc_dir_name).resolve()
    if not doc_dir.exists():
        return False
    shutil.rmtree(doc_dir)
    return True


def build_pipeline_sync_payload(
    *,
    sync_result: dict[str, Any],
    records_cleared: bool,
) -> dict[str, Any]:
    return {
        "doc_dir_name": sync_result["doc_dir_name"],
        "doc_dir": sync_result["doc_dir"],
        "raw_chunks_dir": sync_result["raw_chunks_dir"],
        "chapter_count": sync_result["chapter_count"],
        "chunk_count": sync_result["chunk_count"],
        "routing": json.loads(json.dumps(sync_result["routing"])),
        "report_path": sync_result["report_path"],
        "records_cleared": records_cleared,
    }
