from __future__ import annotations

import argparse
import json
from urllib.parse import urlsplit, urlunsplit

import httpx


def normalize_lmstudio_admin_base(url: str) -> str:
    normalized = url.rstrip("/")
    parts = urlsplit(normalized)
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")] + "/api/v1"
    elif not path.endswith("/api/v1"):
        path = f"{path}/api/v1" if path else "/api/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Real-request E2E smoke for runtime model switching."
    )
    parser.add_argument(
        "--backend-url",
        default="http://127.0.0.1:9733",
        help="MiniRAG backend base URL",
    )
    parser.add_argument(
        "--lmstudio-url",
        default="http://127.0.0.1:1234/v1",
        help="LM Studio OpenAI-compatible base URL",
    )
    parser.add_argument(
        "--target-query-model-id",
        required=True,
        help="Registry query_llm_id to switch to",
    )
    parser.add_argument(
        "--prompt",
        action="append",
        dest="prompts",
        help="Prompt sent after switching. Can be provided multiple times.",
    )
    parser.add_argument(
        "--expect-substring",
        action="append",
        dest="expected_substrings",
        default=[],
        help="Expected substring for the corresponding prompt. Order-sensitive.",
    )
    parser.add_argument(
        "--allow-unload-failure",
        action="store_true",
        help="Do not fail the script if previous model unload fails.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lmstudio_admin_base = normalize_lmstudio_admin_base(args.lmstudio_url)
    prompts = args.prompts or ["请只回复“backend-ok”"]
    if args.expected_substrings and len(args.expected_substrings) != len(prompts):
        raise SystemExit("--expect-substring count must match --prompt count")

    with httpx.Client(timeout=30) as client:
        before_models = client.get(f"{args.backend_url}/system/models")
        before_models.raise_for_status()
        before_payload = before_models.json()

        switch_response = client.post(
            f"{args.backend_url}/system/models/select",
            json={
                "query_llm_id": args.target_query_model_id,
                "unload_previous_query_model": True,
            },
        )
        switch_response.raise_for_status()
        switch_payload = switch_response.json()

        query_results: list[dict[str, object]] = []
        for index, prompt in enumerate(prompts):
            query_response = client.post(
                f"{args.backend_url}/query/plain",
                json={
                    "query": prompt,
                    "mode": "graph_text_hybrid",
                    "only_need_context": False,
                    "conversation_history": [],
                },
            )
            query_response.raise_for_status()
            body = query_response.text.strip()
            if not body:
                raise AssertionError(f"query {index} returned empty body")
            if "Traceback" in body or "HTTPException" in body:
                raise AssertionError(f"query {index} returned backend error text: {body[:200]}")

            expected = (
                args.expected_substrings[index]
                if index < len(args.expected_substrings)
                else None
            )
            if expected and expected not in body:
                raise AssertionError(
                    f"query {index} missing expected substring {expected!r}: {body[:400]}"
                )

            query_results.append(
                {
                    "prompt": prompt,
                    "status_code": query_response.status_code,
                    "response_preview": body[:400],
                    "matched_expected_substring": expected,
                }
            )

        after_config = client.get(f"{args.backend_url}/system/config")
        after_config.raise_for_status()

        lmstudio_models = client.get(f"{lmstudio_admin_base}/models")
        lmstudio_models.raise_for_status()

    unload_result = switch_payload.get("previous_query_unload")
    if (
        unload_result
        and unload_result.get("status") == "failed"
        and not args.allow_unload_failure
    ):
        raise AssertionError(
            f"previous query unload failed: {unload_result.get('message')}"
        )

    loaded_instances = {
        item["key"]: item.get("loaded_instances", [])
        for item in lmstudio_models.json().get("models", [])
        if item.get("loaded_instances")
    }
    if args.target_query_model_id not in loaded_instances:
        raise AssertionError(
            f"target model {args.target_query_model_id!r} is not loaded after switch"
        )

    previous_query_id = before_payload["selection"]["query_llm_id"]
    if (
        unload_result
        and unload_result.get("status") == "unloaded"
        and previous_query_id != args.target_query_model_id
        and previous_query_id in loaded_instances
    ):
        raise AssertionError(
            f"previous query model {previous_query_id!r} is still loaded after unload"
        )

    summary = {
        "before_selection": before_payload["selection"],
        "after_selection": switch_payload["selection"],
        "previous_query_unload": unload_result,
        "after_query_runtime": {
            "query_model_id": after_config.json()["llm"]["query_model_id"],
            "query_model": after_config.json()["llm"]["query_model"],
            "query_binding": after_config.json()["llm"]["query_binding"],
            "query_host": after_config.json()["llm"]["query_binding_host"],
        },
        "query_results": query_results,
        "lmstudio_loaded_instances": loaded_instances,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
