import argparse
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import tiktoken


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.script_runtime import (
    add_datasource_arguments,
    discover_doc_dirs as discover_doc_dirs_under_root,
    resolve_chunks_root,
    resolve_doc_dir_argument,
)


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


@dataclass
class ChunkIssue:
    chapter_file: str
    chunk_id: str
    breadcrumb: str
    token_count: int
    issue_types: list[str]
    content_preview: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan datasource staging/chunks raw_chunks and route issue files under each datasource document directory."
    )
    add_datasource_arguments(parser)
    parser.add_argument(
        "--doc-dir",
        default=None,
        help="Path or name of one datasource document directory under staging/chunks, or directly to its raw_chunks folder.",
    )
    parser.add_argument(
        "--all-docs",
        action="store_true",
        help="Scan every datasource document directory under staging/chunks that contains raw_chunks.",
    )
    parser.add_argument(
        "--tokenizer-model",
        default="gpt-4o-mini",
        help="Tokenizer model name for tiktoken counting.",
    )
    parser.add_argument(
        "--long-threshold",
        type=int,
        default=1500,
        help="Chunks above this token count are flagged as long.",
    )
    parser.add_argument(
        "--preview-length",
        type=int,
        default=20,
        help="Content preview length for issue reporting.",
    )
    parser.add_argument(
        "--write-report",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to write per-doc raw_chunks_scan_report.json.",
    )
    parser.add_argument(
        "--write-root-summary",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to write outputs-root/raw_chunks_scan_summary.json.",
    )
    return parser


def resolve_raw_chunks_dir(doc_dir: str) -> Path:
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
    root = Path(outputs_root).expanduser().resolve()
    return discover_doc_dirs_under_root(root, "raw_chunks")


def get_encoder(model_name: str):
    try:
        return tiktoken.encoding_for_model(model_name)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(encoder, text: str) -> int:
    return len(encoder.encode(text or ""))


def contains_html_table(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in HTML_TABLE_PATTERNS)


def contains_html_image(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in HTML_IMAGE_PATTERNS)


def contains_markdown_image(text: str) -> bool:
    return bool(MARKDOWN_IMAGE_PATTERN.search(text or ""))


def normalize_preview(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def load_chunks(raw_chunks_dir: Path) -> list[dict[str, Any]]:
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


def analyze_chunks(
    chunks: list[dict[str, Any]],
    encoder,
    long_threshold: int,
    preview_length: int,
) -> dict[str, Any]:
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
        "issues": [asdict(item) for item in issues],
        "chunk_details": chunk_details,
    }


def write_report(raw_chunks_dir: Path, report: dict[str, Any]) -> Path:
    doc_root = raw_chunks_dir.parent
    report_path = doc_root / "raw_chunks_scan_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report_path


def build_chunk_payload(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk.get("chunk_id", ""),
        "chapter_file": chunk.get("chapter_file", ""),
        "breadcrumb": chunk.get("breadcrumb", ""),
        "content": chunk.get("content", ""),
        "chunk_type": chunk.get("chunk_type", ""),
        "metadata": chunk.get("metadata", {}),
    }


def reset_routing_dirs(doc_root: Path) -> None:
    for folder_name in ["good_chunks", "long_chunk", "tables", "images"]:
        target_dir = doc_root / folder_name
        if target_dir.exists():
            for child in target_dir.iterdir():
                if child.is_file():
                    child.unlink()


def write_routed_issue_chunks(
    raw_chunks_dir: Path, chunks: list[dict[str, Any]]
) -> dict[str, int]:
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
    for chunk in chunks:
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
                "chunk": build_chunk_payload(chunk),
            }
            safe_issue_type = issue_type.replace("/", "_")
            output_name = f"{safe_issue_type}__{chunk.get('chunk_id', 'chunk')}.json"
            output_path = target_dir / output_name
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            counts[target_dir.name] = counts.get(target_dir.name, 0) + 1
    return counts


def scan_one_doc_dir(
    doc_dir: Path,
    tokenizer_model: str,
    long_threshold: int,
    preview_length: int,
    write_report_file: bool,
) -> dict[str, Any]:
    raw_chunks_dir = resolve_raw_chunks_dir(str(doc_dir))
    encoder = get_encoder(tokenizer_model)
    chunks = load_chunks(raw_chunks_dir)
    report = analyze_chunks(
        chunks,
        encoder=encoder,
        long_threshold=long_threshold,
        preview_length=preview_length,
    )
    routed_counts = write_routed_issue_chunks(raw_chunks_dir, report["chunk_details"])
    report["config"] = {
        "doc_dir": str(raw_chunks_dir.parent),
        "raw_chunks_dir": str(raw_chunks_dir),
        "tokenizer_model": tokenizer_model,
        "long_threshold": long_threshold,
        "preview_length": preview_length,
    }
    report["routing"] = routed_counts
    report_path = write_report(raw_chunks_dir, report) if write_report_file else None
    print_summary(report, report_path, long_threshold)
    return {
        "doc_dir": str(doc_dir),
        "report_path": str(report_path) if report_path else "",
        "summary": report["summary"],
        "routing": routed_counts,
    }


def write_root_summary(chunks_root: Path, results: list[dict[str, Any]]) -> Path:
    root = chunks_root.expanduser().resolve()
    summary_path = root / "raw_chunks_scan_summary.json"
    summary = {
        "doc_count": len(results),
        "documents": results,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary_path


def print_summary(
    report: dict[str, Any], report_path: Path | None, long_threshold: int
) -> None:
    summary = report["summary"]
    print("=== Raw Chunk Scan Summary ===")
    if report_path:
        print(f"report: {report_path}")
    print(f"chunk_count: {summary['chunk_count']}")
    print(f"avg_tokens: {summary['avg_tokens']}")
    print(f"median_tokens: {summary['median_tokens']}")
    print(f"max_tokens: {summary['max_tokens']}")
    print(f"min_tokens: {summary['min_tokens']}")
    print(f"long_chunk_threshold: {long_threshold}")
    print(f"long_chunk_count: {summary['long_chunk_count']}")
    print(f"empty_chunk_count: {summary['empty_chunk_count']}")
    print(f"html_table_count: {summary['html_table_count']}")
    print(f"html_image_count: {summary['html_image_count']}")
    print(f"markdown_image_count: {summary['markdown_image_count']}")

    print("\n=== Per File Summary ===")
    for file_name, file_stats in report.get("per_file", {}).items():
        print(
            f"- {file_name} | chunks={file_stats['chunk_count']} | "
            f"avg_tokens={file_stats['avg_tokens']} | max_tokens={file_stats['max_tokens']} | "
            f"long={file_stats['long_chunk_count']} | "
            f"html_table={file_stats['html_table_count']} | "
            f"html_image={file_stats['html_image_count']} | "
            f"markdown_image={file_stats['markdown_image_count']}"
        )

    if report["issues"]:
        print("\n=== Top Issues Preview ===")
        for item in report["issues"][:10]:
            print(
                f"- {item['chapter_file']} | {item['chunk_id']} | tokens={item['token_count']} | "
                f"issues={','.join(item['issue_types'])}"
            )
            print(f"  breadcrumb: {item['breadcrumb']}")
            print(f"  preview: {item['content_preview']}")

    if report.get("chunk_details"):
        print("\n=== Per Chunk Details ===")
        for item in report["chunk_details"]:
            issue_text = (
                ",".join(item["issue_types"]) if item["issue_types"] else "none"
            )
            print(
                f"- {item['chapter_file']} | {item['chunk_id']} | "
                f"type={item['chunk_type']} | tokens={item['token_count']} | issues={issue_text}"
            )
            # print(f"  breadcrumb: {item['breadcrumb']}")
            print(f"  preview: {item['content_preview']}")
    else:
        print("\n=== Issues Preview ===")
        print("No issues detected under current thresholds and patterns.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    results: list[dict[str, Any]] = []
    chunks_root = resolve_chunks_root(args)

    if args.all_docs or not args.doc_dir:
        doc_dirs = discover_doc_dirs(str(chunks_root))
    else:
        doc_dirs = [resolve_doc_dir_argument(args.doc_dir, chunks_root)]

    for doc_dir in doc_dirs:
        print(f"\n===== Scanning: {doc_dir.name} =====")
        result = scan_one_doc_dir(
            doc_dir=doc_dir,
            tokenizer_model=args.tokenizer_model,
            long_threshold=args.long_threshold,
            preview_length=args.preview_length,
            write_report_file=args.write_report,
        )
        results.append(result)

    if args.write_root_summary:
        summary_path = write_root_summary(chunks_root, results)
        print(f"\nroot summary: {summary_path}")


if __name__ == "__main__":
    main()
