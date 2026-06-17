import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark streaming TTFT (time to first token/chunk)."
    )
    parser.add_argument(
        "--api-kind",
        default="minirag",
        choices=["minirag", "openai-chat"],
        help="Streaming API shape: MiniRAG /query/stream or OpenAI-compatible /chat/completions",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:9728/query/stream",
        help="Streaming query endpoint URL",
    )
    parser.add_argument(
        "--mode",
        default="mini",
        choices=["mini", "light", "naive", "faultcase_fast"],
        help="Query mode",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="One query to test; can be passed multiple times",
    )
    parser.add_argument(
        "--queries-file",
        default=None,
        help="Optional text file with one query per line",
    )
    parser.add_argument(
        "--only-need-context",
        action="store_true",
        help="Call streaming endpoint with only_need_context=true",
    )
    parser.add_argument(
        "--show-chunks",
        type=int,
        default=3,
        help="How many initial chunks to preview",
    )
    parser.add_argument(
        "--model",
        default="qwen3.5-2b",
        help="Model name for openai-chat mode",
    )
    return parser.parse_args()


def load_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    if args.queries:
        queries.extend(args.queries)
    if args.queries_file:
        path = Path(args.queries_file)
        queries.extend(
            [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        )
    if not queries:
        queries = [
            "主辅机冷却水流量变小时应该怎么处理？",
            "操舵装置相关的常见故障有哪些？",
        ]
    return queries


def decode_stream_line(raw_line: bytes) -> dict[str, Any]:
    text = raw_line.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def build_payload(
    api_kind: str,
    query: str,
    mode: str,
    only_need_context: bool,
    model: str,
) -> dict[str, Any]:
    if api_kind == "openai-chat":
        return {
            "model": model,
            "stream": True,
            "temperature": 0,
            "messages": [{"role": "user", "content": query}],
        }
    return {
        "query": query,
        "mode": mode,
        "stream": True,
        "only_need_context": only_need_context,
    }


def extract_text_from_chunk(api_kind: str, raw_line: bytes) -> str:
    text = raw_line.decode("utf-8", errors="replace")
    if api_kind == "openai-chat":
        if not text.startswith("data:"):
            return ""
        payload = text[len("data:") :].strip()
        if payload == "[DONE]":
            return ""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return text
        choices = data.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return delta.get("content") or ""
    data = decode_stream_line(raw_line)
    return data.get("response") or data.get("raw") or ""


def benchmark_one(
    api_kind: str,
    url: str,
    query: str,
    mode: str,
    only_need_context: bool,
    timeout: int,
    show_chunks: int,
    model: str,
) -> dict[str, Any]:
    payload = build_payload(api_kind, query, mode, only_need_context, model)

    start = time.perf_counter()
    response = requests.post(url, json=payload, stream=True, timeout=timeout)
    first_chunk_time = None
    chunks: list[str] = []
    total_chars = 0

    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        text = extract_text_from_chunk(api_kind, raw_line)
        if text:
            now = time.perf_counter()
            if first_chunk_time is None:
                first_chunk_time = now
            chunks.append(text)
            total_chars += len(text)

    end = time.perf_counter()

    return {
        "query": query,
        "status_code": response.status_code,
        "ttft_seconds": None if first_chunk_time is None else round(first_chunk_time - start, 2),
        "full_seconds": round(end - start, 2),
        "chunk_count": len(chunks),
        "total_chars": total_chars,
        "is_true_streaming": len(chunks) > 1,
        "chunk_preview": chunks[:show_chunks],
    }


def main() -> None:
    args = parse_args()
    queries = load_queries(args)
    print(
        json.dumps(
            {
                "api_kind": args.api_kind,
                "url": args.url,
                "mode": args.mode,
                "only_need_context": args.only_need_context,
                "model": args.model if args.api_kind == "openai-chat" else None,
                "query_count": len(queries),
            },
            ensure_ascii=False,
        )
    )
    for query in queries:
        result = benchmark_one(
            api_kind=args.api_kind,
            url=args.url,
            query=query,
            mode=args.mode,
            only_need_context=args.only_need_context,
            timeout=args.timeout,
            show_chunks=args.show_chunks,
            model=args.model,
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
