from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.script_runtime import (
    add_datasource_arguments,
    resolve_cli_datasource,
    resolve_source_root,
    resolve_user_path,
)


class RawChunk:
    def __init__(
        self,
        book_name: str,
        path_stack: list[str],
        content: str,
        *,
        chunk_type: str = "text",
        metadata: dict[str, Any] | None = None,
    ):
        self.breadcrumb = " > ".join([book_name] + path_stack)
        self.content = content.strip()
        self.chunk_type = chunk_type
        self.metadata = metadata or {}
        self.chunk_id = self._generate_id()
        self.chapter = path_stack[0] if path_stack else "引言及前言"

    def _generate_id(self) -> str:
        payload = f"{self.breadcrumb}_{self.content}".encode("utf-8")
        return hashlib.md5(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "breadcrumb": self.breadcrumb,
            "content": self.content,
            "chunk_type": self.chunk_type,
            "metadata": self.metadata,
        }


class MarkdownStructuredParser:
    def __init__(self, book_name: str, output_doc_dir: Path):
        self.book_name = book_name
        self.book_dir = output_doc_dir
        self.raw_chunks_dir = self.book_dir / "raw_chunks"
        self.raw_chunks_dir.mkdir(parents=True, exist_ok=True)

    def _detect_heading(self, line: str) -> tuple[int | None, str | None]:
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if not match:
            return None, None
        return len(match.group(1)), match.group(2).strip()

    def parse_lines(self, lines: list[str], *, min_length: int = 30) -> dict[str, Any]:
        chunks_by_chapter: dict[str, list[RawChunk]] = defaultdict(list)
        stack: list[tuple[int, str]] = []
        current_content: list[str] = []

        def save_current_chunk() -> None:
            text = "\n".join(current_content).strip()
            if len(text) < min_length:
                current_content.clear()
                return
            path_stack = [item[1] for item in stack]
            chunk_type = "text"
            if re.search(r"\|\s*:?-{3,}:?\s*\|", text):
                chunk_type = "table"
            chunk = RawChunk(
                book_name=self.book_name,
                path_stack=path_stack,
                content=text,
                chunk_type=chunk_type,
                metadata={"level": stack[-1][0] if stack else 0},
            )
            chunks_by_chapter[chunk.chapter].append(chunk)
            current_content.clear()

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("![") and "](" in line:
                current_content.append(line)
                continue

            level, title = self._detect_heading(line)
            if level is not None and title is not None:
                save_current_chunk()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                continue
            current_content.append(line)

        save_current_chunk()
        self._export_to_json(chunks_by_chapter)
        chapter_counts = {
            chapter: len(chunk_list)
            for chapter, chunk_list in chunks_by_chapter.items()
        }
        return {
            "doc_name": self.book_name,
            "doc_dir": str(self.book_dir),
            "raw_chunks_dir": str(self.raw_chunks_dir),
            "chapter_count": len(chapter_counts),
            "chunk_count": sum(chapter_counts.values()),
            "chapters": chapter_counts,
        }

    def parse_text_content(self, text: str, *, min_length: int = 30) -> dict[str, Any]:
        return self.parse_lines(text.splitlines(), min_length=min_length)

    def parse_file(self, md_filepath: Path, *, min_length: int = 30) -> dict[str, Any]:
        return self.parse_text_content(
            md_filepath.read_text(encoding="utf-8"),
            min_length=min_length,
        )

    def _export_to_json(self, chunks_by_chapter: dict[str, list[RawChunk]]) -> None:
        for chapter, chunk_list in chunks_by_chapter.items():
            safe_chapter_name = re.sub(r'[\\/*?:"<>|]', "", chapter)[:50].strip()
            if not safe_chapter_name:
                safe_chapter_name = "未命名章节"

            out_file = self.raw_chunks_dir / f"{safe_chapter_name}.json"
            export_data = {
                "doc_name": self.book_name,
                "chapter": chapter,
                "chunks": [chunk.to_dict() for chunk in chunk_list],
            }
            out_file.write_text(
                json.dumps(export_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse Markdown source docs from datasource source/raw into datasource staging/chunks/<doc>/raw_chunks.",
    )
    add_datasource_arguments(parser)
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Markdown file to parse. Relative paths resolve under the datasource source/raw root unless an existing local path is provided.",
    )
    parser.add_argument(
        "--all-docs",
        action="store_true",
        help="Parse every *.md file under the datasource source/raw root.",
    )
    parser.add_argument(
        "--book-name",
        default=None,
        help="Optional display name stored in breadcrumb/doc_name for a single input file.",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=30,
        help="Minimum character length required before a chunk is emitted.",
    )
    return parser


def resolve_input_files(args: argparse.Namespace) -> list[Path]:
    source_root = resolve_source_root(args)
    if args.all_docs:
        matches = sorted(source_root.rglob("*.md"))
        if not matches:
            raise FileNotFoundError(
                f"No markdown files found under datasource source root: {source_root}"
            )
        return matches

    if not args.input:
        raise ValueError("Provide --input or --all-docs.")

    resolved: list[Path] = []
    for value in args.input:
        path = resolve_user_path(value, relative_to=source_root)
        if not path.exists():
            raise FileNotFoundError(f"Markdown source file does not exist: {path}")
        resolved.append(path)
    return resolved


def build_doc_dir_name(source_path: Path, book_name: str | None = None) -> str:
    candidate = (book_name or source_path.stem).strip() or source_path.stem
    cleaned = re.sub(r'[\\/*?:"<>|]', "", candidate).strip()
    return cleaned or source_path.stem or "unnamed_doc"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    datasource = resolve_cli_datasource(args)
    input_files = resolve_input_files(args)

    if args.book_name and len(input_files) != 1:
        raise ValueError(
            "--book-name can only be used when parsing a single input file."
        )

    output_root = (datasource.staging_root / "chunks").resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for source_path in input_files:
        book_name = args.book_name or source_path.name
        doc_dir = output_root / build_doc_dir_name(source_path, args.book_name)
        parser_instance = MarkdownStructuredParser(
            book_name=book_name, output_doc_dir=doc_dir
        )
        result = parser_instance.parse_file(source_path, min_length=args.min_length)
        result["source_path"] = str(source_path)
        results.append(result)
        print(
            f"parsed: {source_path.name} -> {doc_dir / 'raw_chunks'} | chunks={result['chunk_count']}"
        )

    print(
        json.dumps(
            {
                "datasource_id": datasource.id,
                "source_root": str(datasource.source_root),
                "chunks_root": str(output_root),
                "documents": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
