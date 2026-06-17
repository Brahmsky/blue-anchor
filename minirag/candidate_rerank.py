from __future__ import annotations

import asyncio
import importlib
import site
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .base import QueryParam
from .utils import logger


def _candidate_text(candidate: dict[str, Any]) -> str:
    return (
        candidate.get("content")
        or candidate.get("description")
        or candidate.get("entity_name")
        or ""
    )


def _import_flash_rerank_load():
    try:
        return importlib.import_module("flash_rerank").load
    except Exception:
        candidate_paths = []
        user_site = site.getusersitepackages()
        if user_site:
            candidate_paths.append(Path(user_site))

        roaming_python = Path.home() / "AppData" / "Roaming" / "Python"
        if roaming_python.exists():
            candidate_paths.extend(
                sorted(roaming_python.glob("Python*/site-packages"), reverse=True)
            )

        for candidate_path in candidate_paths:
            candidate_str = str(candidate_path)
            if candidate_path.exists() and candidate_str not in sys.path:
                sys.path.append(candidate_str)
        try:
            return importlib.import_module("flash_rerank").load
        except Exception:
            return None


@lru_cache(maxsize=8)
def _load_local_reranker(model: str, device: str, precision: str):
    load = _import_flash_rerank_load()
    if load is None:
        return None
    try:
        return load(model, device, precision)
    except Exception as e:
        logger.warning("faultcase_fast flash_rerank load failed: %s", e)
        return None


def _parse_rerank_result_item(item: Any) -> tuple[int, float] | None:
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[0]), float(item[1])
    if isinstance(item, dict) and "index" in item and "score" in item:
        return int(item["index"]), float(item["score"])
    return None


def _build_reranked_candidates(
    candidates: list[dict[str, Any]],
    rerank_results: list[Any],
) -> list[dict[str, Any]]:
    index_to_candidate = {index: candidate for index, candidate in enumerate(candidates)}
    reranked_front: list[dict[str, Any]] = []
    seen_indices: set[int] = set()

    for item in rerank_results:
        parsed = _parse_rerank_result_item(item)
        if parsed is None:
            continue
        index, score = parsed
        candidate = index_to_candidate.get(index)
        if candidate is None or index in seen_indices:
            continue
        seen_indices.add(index)
        reranked_front.append(
            {
                **candidate,
                "rerank_score": score,
                "reranked": True,
            }
        )

    if not reranked_front:
        return candidates

    tail = [
        {
            **candidate,
            "reranked": False,
        }
        for index, candidate in enumerate(candidates)
        if index not in seen_indices
    ]
    return reranked_front + tail


def _local_rerank_sync(
    query: str,
    candidates: list[dict[str, Any]],
    model: str,
    device: str,
    precision: str,
) -> list[dict[str, Any]]:
    reranker = _load_local_reranker(model, device, precision)
    if reranker is None:
        return candidates

    documents = [_candidate_text(candidate) for candidate in candidates]
    if not hasattr(reranker, "rerank"):
        if not isinstance(reranker, tuple) or len(reranker) < 2:
            logger.warning("faultcase_fast flash_rerank object has no rerank() method; passthrough")
            return candidates

        tokenizer, hf_model = reranker[0], reranker[1]
        resolved_device = reranker[2] if len(reranker) > 2 else device
        torch = importlib.import_module("torch")
        encoded = tokenizer(
            [(query, document) for document in documents],
            padding=True,
            truncation=True,
            return_tensors="pt",
            max_length=512,
        )
        encoded = {
            key: value.to(resolved_device) if hasattr(value, "to") else value
            for key, value in encoded.items()
        }
        with torch.no_grad():
            outputs = hf_model(**encoded, return_dict=True)
        scores = outputs.logits.view(-1).float().tolist()
        results = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        return _build_reranked_candidates(candidates, results)

    results = reranker.rerank(query, documents, len(documents))
    return _build_reranked_candidates(candidates, results)


async def rerank_faultcase_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    query_param: QueryParam,
    global_config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not query_param.faultcase_rerank_enabled:
        return candidates
    if len(candidates) <= 1:
        return candidates

    max_candidates = max(1, query_param.faultcase_rerank_max_candidates)
    front = candidates[:max_candidates]
    tail = candidates[max_candidates:]

    timeout_seconds = max(query_param.faultcase_rerank_timeout_ms, 1) / 1000.0
    model = (global_config.get("faultcase_rerank_model") or "").strip()
    device = (global_config.get("faultcase_rerank_device") or "cpu").strip()
    precision = (global_config.get("faultcase_rerank_precision") or "fp32").strip()

    if not model:
        logger.warning(
            "faultcase_fast rerank enabled but FAULTCASE_RERANK_MODEL is not configured; passthrough"
        )
        return candidates

    try:
        reranked_front = await asyncio.wait_for(
            asyncio.to_thread(
                _local_rerank_sync,
                query,
                front,
                model,
                device,
                precision,
            ),
            timeout=timeout_seconds,
        )
    except Exception as e:
        logger.warning(f"faultcase_fast rerank degraded to passthrough: {e}")
        return candidates

    return reranked_front + tail
