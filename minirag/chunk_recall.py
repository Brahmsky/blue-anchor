from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import jieba
from rank_bm25 import BM25Okapi

from .base import BaseKVStorage, BaseVectorStorage, QueryParam, TextChunkSchema
from .faultcase_alias_router import normalize_alias_text
from .utils import logger


@dataclass
class ChunkRecallCandidate:
    chunk_id: str
    content: str
    score: float
    sources: set[str] = field(default_factory=set)
    vector_distance: Optional[float] = None
    lexical_score: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sources"] = sorted(self.sources)
        payload["id"] = self.chunk_id
        return payload


def _normalize_chunk_id(value: str) -> str:
    return (value or "").strip()


def _rank_score(rank_index: int, total: int, base: float) -> float:
    remaining = max(total - rank_index, 1)
    return base + float(remaining)


def compute_simple_lexical_score(query: str, content: str) -> float:
    normalized_query = normalize_alias_text(query)
    normalized_content = normalize_alias_text(content)
    if not normalized_query or not normalized_content:
        return 0.0

    if normalized_query in normalized_content:
        return 100.0 + min(len(normalized_query), 20)

    query_chars = set(normalized_query)
    content_chars = set(normalized_content)
    overlap = len(query_chars & content_chars)
    if overlap < 2:
        return 0.0

    coverage = overlap / max(len(query_chars), 1)
    prefix_bonus = 5.0 if normalized_content.startswith(normalized_query[:2]) else 0.0
    return round(coverage * 40.0 + prefix_bonus, 4)


def _normalize_keyword_terms(query: str) -> list[str]:
    normalized_query = normalize_alias_text(query)
    if not normalized_query:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    # Respect explicit whitespace-separated keywords when the user provides them,
    # otherwise segment the natural-language query with jieba for BM25 retrieval.
    raw_query = str(query or "").strip()
    explicit_terms = raw_query.split() if any(ch.isspace() for ch in raw_query) else []

    if explicit_terms:
        iterable = explicit_terms
    else:
        iterable = jieba.cut_for_search(normalized_query)

    for raw_term in iterable:
        normalized = normalize_alias_text(raw_term)
        if not normalized or normalized in seen:
            continue
        if len(normalized) == 1 and normalized not in {"阀", "泵"}:
            continue
        seen.add(normalized)
        terms.append(normalized)

    if terms:
        return terms

    return [normalized_query]


def _tokenize_bm25_document(content: str) -> list[str]:
    normalized_content = normalize_alias_text(content)
    if not normalized_content:
        return []

    return [token.strip() for token in jieba.cut_for_search(normalized_content) if token.strip()]


async def _fetch_chunk_payloads(
    text_chunks_db: BaseKVStorage[TextChunkSchema],
    chunk_ids: list[str],
) -> dict[str, TextChunkSchema]:
    if not chunk_ids:
        return {}

    if hasattr(text_chunks_db, "get_by_ids"):
        payloads = await text_chunks_db.get_by_ids(chunk_ids)
    else:
        payloads = await asyncio.gather(
            *[text_chunks_db.get_by_id(chunk_id) for chunk_id in chunk_ids]
        )

    return {
        chunk_id: payload
        for chunk_id, payload in zip(chunk_ids, payloads)
        if payload is not None
    }


async def recall_vector_chunks(
    query: str,
    chunks_vdb: Optional[BaseVectorStorage],
    top_k: int,
) -> list[dict[str, Any]]:
    if chunks_vdb is None or top_k <= 0:
        return []

    try:
        hits = await chunks_vdb.query(query, top_k=top_k)
        return hits or []
    except Exception as e:
        logger.warning(f"faultcase_fast vector chunk recall failed: {e}")
        return []


async def collect_lexical_candidate_ids(
    seed_chunk_ids: list[str],
    vector_chunk_ids: list[str],
    text_chunks_db: BaseKVStorage[TextChunkSchema],
    scan_limit: int,
) -> list[str]:
    candidate_ids: list[str] = []
    seen: set[str] = set()

    for chunk_id in [*seed_chunk_ids, *vector_chunk_ids]:
        normalized = _normalize_chunk_id(chunk_id)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidate_ids.append(normalized)

    if len(candidate_ids) >= scan_limit:
        return candidate_ids[:scan_limit]

    try:
        all_chunk_ids = await text_chunks_db.all_keys()
    except Exception as e:
        logger.warning(f"faultcase_fast lexical candidate listing failed: {e}")
        return candidate_ids

    for chunk_id in all_chunk_ids:
        normalized = _normalize_chunk_id(chunk_id)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidate_ids.append(normalized)
        if len(candidate_ids) >= scan_limit:
            break

    return candidate_ids


async def recall_lexical_chunks(
    query: str,
    text_chunks_db: BaseKVStorage[TextChunkSchema],
    candidate_ids: list[str],
    top_k: int,
) -> list[dict[str, Any]]:
    if top_k <= 0 or not candidate_ids:
        return []

    payload_map = await _fetch_chunk_payloads(text_chunks_db, candidate_ids)
    scored: list[dict[str, Any]] = []
    for chunk_id, payload in payload_map.items():
        lexical_score = compute_simple_lexical_score(query, payload.get("content", ""))
        if lexical_score <= 0:
            continue
        scored.append(
            {
                "id": chunk_id,
                "content": payload.get("content", ""),
                "lexical_score": lexical_score,
            }
        )

    scored.sort(key=lambda item: item["lexical_score"], reverse=True)
    return scored[:top_k]


async def recall_bm25_chunks(
    query: str,
    text_chunks_db: BaseKVStorage[TextChunkSchema],
    candidate_ids: list[str],
    top_k: int,
) -> list[dict[str, Any]]:
    if top_k <= 0 or not candidate_ids:
        return []

    payload_map = await _fetch_chunk_payloads(text_chunks_db, candidate_ids)
    query_terms = _normalize_keyword_terms(query)
    if not query_terms or not payload_map:
        return []

    chunk_ids: list[str] = []
    corpus_tokens: list[list[str]] = []

    for chunk_id, payload in payload_map.items():
        tokens = _tokenize_bm25_document(payload.get("content", ""))
        if not tokens:
            continue
        chunk_ids.append(chunk_id)
        corpus_tokens.append(tokens)

    if not corpus_tokens:
        return []

    scores = BM25Okapi(corpus_tokens).get_scores(query_terms)
    ranked = sorted(
        [
            (chunk_id, float(score))
            for chunk_id, score in zip(chunk_ids, scores)
            if float(score) > 0
        ],
        key=lambda item: item[1],
        reverse=True,
    )

    return [
        {
            "id": chunk_id,
            "content": payload_map[chunk_id].get("content", ""),
            "bm25_score": round(score, 6),
        }
        for chunk_id, score in ranked[:top_k]
    ]


async def hybrid_chunk_recall(
    query: str,
    seed_chunk_ids: list[str],
    chunks_vdb: Optional[BaseVectorStorage],
    text_chunks_db: BaseKVStorage[TextChunkSchema],
    query_param: QueryParam,
) -> list[dict[str, Any]]:
    normalized_seed_chunk_ids = [
        chunk_id
        for chunk_id in dict.fromkeys(
            [_normalize_chunk_id(chunk_id) for chunk_id in seed_chunk_ids]
        )
        if chunk_id
    ]

    vector_hits = await recall_vector_chunks(
        query,
        chunks_vdb,
        top_k=query_param.faultcase_chunk_vector_top_k,
    )
    vector_chunk_ids = [
        _normalize_chunk_id(item.get("id", ""))
        for item in vector_hits
        if _normalize_chunk_id(item.get("id", ""))
    ]

    lexical_candidate_ids = await collect_lexical_candidate_ids(
        normalized_seed_chunk_ids,
        vector_chunk_ids,
        text_chunks_db,
        scan_limit=max(query_param.faultcase_chunk_lexical_scan_limit, 2000),
    )
    lexical_hits = await recall_lexical_chunks(
        query,
        text_chunks_db,
        lexical_candidate_ids,
        top_k=query_param.faultcase_chunk_lexical_top_k,
    )

    merged: dict[str, ChunkRecallCandidate] = {}
    chunk_payload_map = await _fetch_chunk_payloads(
        text_chunks_db,
        list(
            dict.fromkeys(
                normalized_seed_chunk_ids
                + vector_chunk_ids
                + [item["id"] for item in lexical_hits]
            )
        ),
    )

    for rank_index, chunk_id in enumerate(normalized_seed_chunk_ids):
        payload = chunk_payload_map.get(chunk_id)
        if payload is None:
            continue
        # Graph hits score: base 100.0, rank 0 gets 100.0, rank 1 gets 99.0...
        score_val = 100.0 - rank_index
        merged[chunk_id] = ChunkRecallCandidate(
            chunk_id=chunk_id,
            content=payload.get("content", ""),
            score=score_val,
            sources={"graph_seed"},
        )

    for rank_index, item in enumerate(vector_hits):
        chunk_id = _normalize_chunk_id(item.get("id", ""))
        payload = chunk_payload_map.get(chunk_id)
        if not chunk_id or payload is None:
            continue
        # Vector hits score: interleave slightly below graph
        vector_score = 99.5 - rank_index
        candidate = merged.setdefault(
            chunk_id,
            ChunkRecallCandidate(
                chunk_id=chunk_id,
                content=payload.get("content", ""),
                score=vector_score,
            ),
        )
        candidate.score = max(candidate.score, vector_score)
        candidate.sources.add("vector")
        candidate.vector_distance = item.get("distance")

    for rank_index, item in enumerate(lexical_hits):
        chunk_id = item["id"]
        payload = chunk_payload_map.get(chunk_id)
        if payload is None:
            continue
        # Lexical hits supplement graph/vector hits without displacing graph seeds.
        lexical_score = 98.5 - rank_index
        candidate = merged.setdefault(
            chunk_id,
            ChunkRecallCandidate(
                chunk_id=chunk_id,
                content=payload.get("content", ""),
                score=lexical_score,
            ),
        )
        candidate.score = max(candidate.score, lexical_score)
        candidate.sources.add("lexical")
        candidate.lexical_score = float(item.get("lexical_score", item.get("bm25_score", 0.0)))

    ranked = sorted(merged.values(), key=lambda item: item.score, reverse=True)
    return [
        candidate.to_dict()
        for candidate in ranked[: query_param.faultcase_chunk_recall_top_k]
    ]
