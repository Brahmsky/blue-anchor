from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import tiktoken


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.script_runtime import add_datasource_arguments, resolve_chunks_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Count characters and tokens for raw chunk files under datasource staging/chunks."
    )
    add_datasource_arguments(parser)
    parser.add_argument(
        "--pause-every",
        type=int,
        default=0,
        help="Pause after every N chunk rows while printing details. Use 0 to disable pauses.",
    )
    return parser


def count_content_tokens(outputs_dir: Path, *, pause_every: int = 0) -> None:
    if not outputs_dir.exists():
        print(f"错误：目录 {outputs_dir} 不存在")
        return

    encoding = tiktoken.get_encoding("cl100k_base")

    total_files = 0
    total_chunks = 0
    total_tokens = 0
    total_chars = 0

    stats_by_folder: dict[str, dict[str, int]] = {}
    chunk_details: list[dict[str, Any]] = []

    for folder in sorted(outputs_dir.iterdir()):
        if not folder.is_dir():
            continue

        raw_chunks_dir = folder / "raw_chunks"
        if not raw_chunks_dir.exists():
            continue

        folder_tokens = 0
        folder_chars = 0
        folder_chunks = 0
        folder_files = 0

        for json_file in sorted(raw_chunks_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if "chunks" in data:
                    for chunk in data["chunks"]:
                        if "content" not in chunk:
                            continue
                        content = chunk["content"]
                        tokens = len(encoding.encode(content))
                        chars = len(content)
                        chunk_details.append(
                            {
                                "folder": folder.name,
                                "file": json_file.name,
                                "chunk_id": chunk.get("chunk_id", ""),
                                "breadcrumb": chunk.get("breadcrumb", ""),
                                "tokens": tokens,
                                "chars": chars,
                            }
                        )

                        folder_tokens += tokens
                        folder_chars += chars
                        folder_chunks += 1

                folder_files += 1
            except Exception as exc:  # noqa: BLE001
                print(f"处理文件 {json_file} 时出错：{exc}")

        if folder_chunks > 0:
            stats_by_folder[folder.name] = {
                "files": folder_files,
                "chunks": folder_chunks,
                "tokens": folder_tokens,
                "chars": folder_chars,
            }
            total_files += folder_files
            total_chunks += folder_chunks
            total_tokens += folder_tokens
            total_chars += folder_chars

    print("=" * 80)
    print("Content Token 统计报告")
    print("=" * 80)
    print()

    print("按文件夹统计：")
    print("-" * 80)
    print(
        f"{'文件夹名称':<40} {'文件数':<8} {'chunk数':<8} {'字符数':<12} {'Token数':<12}"
    )
    print("-" * 80)

    for folder_name, stats in sorted(stats_by_folder.items()):
        print(
            f"{folder_name:<40} {stats['files']:<8} {stats['chunks']:<8} {stats['chars']:<12,} {stats['tokens']:<12,}"
        )

    print("-" * 80)
    print(
        f"{'总计':<40} {total_files:<8} {total_chunks:<8} {total_chars:<12,} {total_tokens:<12,}"
    )
    print()

    print("统计摘要：")
    print("-" * 80)
    print(f"总文件夹数：{len(stats_by_folder)}")
    print(f"总文件数：{total_files}")
    print(f"总 chunk 数：{total_chunks}")
    print(f"总字符数：{total_chars:,}")
    print(f"总 Token 数：{total_tokens:,}")
    if total_chunks:
        print(f"平均每 chunk 字符数：{total_chars / total_chunks:.2f}")
        print(f"平均每 chunk Token 数：{total_tokens / total_chunks:.2f}")
    if total_chars:
        print(f"平均 Token/字符 比率：{total_tokens / total_chars:.4f}")
    print("=" * 80)
    print()

    print("所有 Chunk 详细信息：")
    print("=" * 80)
    print(
        f"{'文件夹':<30} {'文件名':<30} {'Chunk ID':<35} {'字符数':<10} {'Token数':<10}"
    )
    print("-" * 80)

    for idx, chunk in enumerate(chunk_details, start=1):
        folder_name = (
            chunk["folder"][:28] + ".."
            if len(chunk["folder"]) > 30
            else chunk["folder"]
        )
        file_name = (
            chunk["file"][:28] + ".." if len(chunk["file"]) > 30 else chunk["file"]
        )
        chunk_id = (
            chunk["chunk_id"][:33] + ".."
            if len(chunk["chunk_id"]) > 35
            else chunk["chunk_id"]
        )
        print(
            f"{folder_name:<30} {file_name:<30} {chunk_id:<35} {chunk['chars']:<10,} {chunk['tokens']:<10,}"
        )

        if pause_every and idx % pause_every == 0:
            print(f"--- 已显示 {idx}/{len(chunk_details)} 个 chunk ---")
            input("按回车键继续...")

    print("-" * 80)
    print(f"共显示 {len(chunk_details)} 个 chunk")
    print("=" * 80)


def main() -> None:
    args = build_parser().parse_args()
    count_content_tokens(resolve_chunks_root(args), pause_every=args.pause_every)


if __name__ == "__main__":
    main()
