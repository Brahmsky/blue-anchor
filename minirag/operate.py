import asyncio
import inspect
import json
import re
from typing import Callable, Union
from collections import Counter, defaultdict
import warnings
import json_repair

from .utils import (
    list_of_list_to_csv,
    truncate_list_by_token_size,
    split_string_by_multi_markers,
    logger,
    locate_json_string_body_from_string,
    process_combine_contexts,
    clean_str,
    edge_vote_path,
    encode_string_by_tiktoken,
    decode_tokens_by_tiktoken,
    is_float_regex,
    pack_user_ass_to_openai_messages,
    compute_mdhash_id,
    calculate_similarity,
    cal_path_score_list,
)
from .base import (
    BaseGraphStorage,
    BaseKVStorage,
    BaseVectorStorage,
    TextChunkSchema,
    QueryParam,
)
from .candidate_rerank import rerank_faultcase_candidates
from .chunk_recall import (
    collect_lexical_candidate_ids,
    hybrid_chunk_recall,
    recall_bm25_chunks,
)
from .faultcase_alias_router import normalize_alias_text, route_faultcase_query
from .prompt import GRAPH_FIELD_SEP, PROMPTS

ExtractionReviewCallback = Callable[[dict, dict], tuple[dict, dict]]


def chunking_by_token_size(
    content: str, overlap_token_size=128, max_token_size=1024, tiktoken_model="gpt-4o"
):
    tokens = encode_string_by_tiktoken(content, model_name=tiktoken_model)
    results = []
    for index, start in enumerate(
        range(0, len(tokens), max_token_size - overlap_token_size)
    ):
        chunk_content = decode_tokens_by_tiktoken(
            tokens[start : start + max_token_size], model_name=tiktoken_model
        )
        results.append(
            {
                "tokens": min(max_token_size, len(tokens) - start),
                "content": chunk_content.strip(),
                "chunk_order_index": index,
            }
        )
    return results


async def _handle_entity_relation_summary(
    entity_or_relation_name: str,
    description: str,
    global_config: dict,
) -> str:
    tiktoken_model_name = global_config["tiktoken_model_name"]
    summary_max_tokens = global_config["entity_summary_to_max_tokens"]

    tokens = encode_string_by_tiktoken(description, model_name=tiktoken_model_name)
    if len(tokens) < summary_max_tokens:  # No need for summary
        return description


async def _handle_single_entity_extraction(
    record_attributes: list[str],
    chunk_key: str,
):
    if len(record_attributes) < 4 or record_attributes[0] != '"entity"':
        return None
    # add this record as a node in the G
    entity_name = clean_str(record_attributes[1].upper())
    if not entity_name.strip():
        return None
    entity_type = clean_str(record_attributes[2].upper())
    entity_description = clean_str(record_attributes[3])
    entity_source_id = chunk_key
    return dict(
        entity_name=entity_name,
        entity_type=entity_type,
        description=entity_description,
        source_id=entity_source_id,
    )


async def _handle_single_relationship_extraction(
    record_attributes: list[str],
    chunk_key: str,
):
    if len(record_attributes) < 5 or record_attributes[0] != '"relationship"':
        return None
    # add this record as edge
    source = clean_str(record_attributes[1].upper())
    target = clean_str(record_attributes[2].upper())
    edge_description = clean_str(record_attributes[3])

    edge_keywords = clean_str(record_attributes[4])
    relation_type, normalized_keywords = _extract_relation_type_and_keywords(
        edge_keywords
    )
    edge_source_id = chunk_key
    weight = (
        float(record_attributes[-1]) if is_float_regex(record_attributes[-1]) else 1.0
    )
    return dict(
        src_id=source,
        tgt_id=target,
        weight=weight,
        description=edge_description,
        keywords=normalized_keywords,
        relation_type=relation_type,
        source_id=edge_source_id,
    )


def _extract_relation_type_and_keywords(edge_keywords: str) -> tuple[str, str]:
    """
    兼容当前 6 槽关系协议，同时从 keywords 中拆出可路由的 relation_type。

    推荐 few-shot 产出格式：
    `Causes_Fault | 因果关系, 电压异常`
    `Resolved_By；维修动作, 更换AVR`
    """
    raw = (edge_keywords or "").strip()
    if not raw:
        return "UNSPECIFIED", ""

    for delimiter in ("|", "｜", ";", "；"):
        if delimiter in raw:
            head, tail = raw.split(delimiter, 1)
            relation_type = clean_str(head.strip())
            normalized_tail = clean_str(tail.strip())
            if relation_type:
                return relation_type, normalized_tail or relation_type

    return "UNSPECIFIED", raw


async def _merge_nodes_then_upsert(
    entity_name: str,
    nodes_data: list[dict],
    knowledge_graph_inst: BaseGraphStorage,
    global_config: dict,
):
    already_entitiy_types = []
    already_source_ids = []
    already_description = []

    already_node = await knowledge_graph_inst.get_node(entity_name)
    if already_node is not None:
        already_entitiy_types.append(already_node["entity_type"])
        already_source_ids.extend(
            split_string_by_multi_markers(already_node["source_id"], [GRAPH_FIELD_SEP])
        )
        already_description.append(already_node["description"])

    entity_type = sorted(
        Counter(
            [dp["entity_type"] for dp in nodes_data] + already_entitiy_types
        ).items(),
        key=lambda x: x[1],
        reverse=True,
    )[0][0]

    description = GRAPH_FIELD_SEP.join(
        sorted(set([dp["description"] for dp in nodes_data] + already_description))
    )
    source_id = GRAPH_FIELD_SEP.join(
        set([dp["source_id"] for dp in nodes_data] + already_source_ids)
    )

    # description = await _handle_entity_relation_summary(
    #     entity_name, description, global_config
    # )
    node_data = dict(
        entity_type=entity_type,
        description=description,
        source_id=source_id,
    )
    await knowledge_graph_inst.upsert_node(
        entity_name,
        node_data=node_data,
    )
    node_data["entity_name"] = entity_name
    return node_data


async def _merge_edges_then_upsert(
    src_id: str,
    tgt_id: str,
    edges_data: list[dict],
    knowledge_graph_inst: BaseGraphStorage,
    global_config: dict,
):
    already_weights = []
    already_source_ids = []
    already_description = []
    already_keywords = []
    already_relation_types = []

    if await knowledge_graph_inst.has_edge(src_id, tgt_id):
        already_edge = await knowledge_graph_inst.get_edge(src_id, tgt_id)
        already_weights.append(already_edge["weight"])
        already_source_ids.extend(
            split_string_by_multi_markers(already_edge["source_id"], [GRAPH_FIELD_SEP])
        )
        already_description.append(already_edge["description"])
        already_keywords.extend(
            split_string_by_multi_markers(already_edge["keywords"], [GRAPH_FIELD_SEP])
        )
        if "relation_type" in already_edge and already_edge["relation_type"]:
            already_relation_types.extend(
                split_string_by_multi_markers(
                    already_edge["relation_type"], [GRAPH_FIELD_SEP]
                )
            )

    weight = sum([dp["weight"] for dp in edges_data] + already_weights)
    description = GRAPH_FIELD_SEP.join(
        sorted(set([dp["description"] for dp in edges_data] + already_description))
    )
    keywords = GRAPH_FIELD_SEP.join(
        sorted(set([dp["keywords"] for dp in edges_data] + already_keywords))
    )
    relation_type = GRAPH_FIELD_SEP.join(
        sorted(
            set(
                [
                    dp.get("relation_type", "UNSPECIFIED")
                    for dp in edges_data
                    if dp.get("relation_type")
                ]
                + already_relation_types
            )
        )
    )
    source_id = GRAPH_FIELD_SEP.join(
        set([dp["source_id"] for dp in edges_data] + already_source_ids)
    )
    for need_insert_id in [src_id, tgt_id]:
        if not (await knowledge_graph_inst.has_node(need_insert_id)):
            await knowledge_graph_inst.upsert_node(
                need_insert_id,
                node_data={
                    "source_id": source_id,
                    "description": description,
                    "entity_type": '"UNKNOWN"',
                },
            )
    # description = await _handle_entity_relation_summary(
    #     (src_id, tgt_id), description, global_config
    # )
    await knowledge_graph_inst.upsert_edge(
        src_id,
        tgt_id,
        edge_data=dict(
            weight=weight,
            description=description,
            keywords=keywords,
            relation_type=relation_type or "UNSPECIFIED",
            source_id=source_id,
        ),
    )

    edge_data = dict(
        src_id=src_id,
        tgt_id=tgt_id,
        description=description,
        keywords=keywords,
        relation_type=relation_type or "UNSPECIFIED",
    )

    return edge_data


async def extract_entities(
    chunks: dict[str, TextChunkSchema],
    knowledge_graph_inst: BaseGraphStorage,
    entity_vdb: BaseVectorStorage,
    entity_name_vdb: BaseVectorStorage,
    relationships_vdb: BaseVectorStorage,
    global_config: dict,
    review_callback: ExtractionReviewCallback | None = None,
) -> Union[BaseGraphStorage, None]:
    maybe_nodes, maybe_edges = await extract_entity_candidates(chunks, global_config)

    if review_callback is not None:
        reviewed = review_callback(dict(maybe_nodes), dict(maybe_edges))
        if inspect.isawaitable(reviewed):
            reviewed = await reviewed
        maybe_nodes, maybe_edges = reviewed

    return await merge_extracted_candidates(
        maybe_nodes,
        maybe_edges,
        knowledge_graph_inst,
        entity_vdb,
        entity_name_vdb,
        relationships_vdb,
        global_config,
    )


async def extract_entity_candidates(
    chunks: dict[str, TextChunkSchema],
    global_config: dict,
) -> tuple[dict, dict]:
    use_llm_func: callable = global_config["llm_model_func"]
    entity_extract_max_gleaning = global_config["entity_extract_max_gleaning"]

    ordered_chunks = list(chunks.items())
    logger.info(
        "Entity candidate extraction started for %s chunks with gleaning=%s",
        len(ordered_chunks),
        entity_extract_max_gleaning,
    )
    # if global_config['RAGmode'] == 'minirag':
    #     # entity_extract_prompt = PROMPTS["entity_extraction_noDes"]
    #     entity_extract_prompt = PROMPTS["entity_extraction"]
    # else:
    entity_extract_prompt = PROMPTS["entity_extraction"]

    context_base = dict(
        tuple_delimiter=PROMPTS["DEFAULT_TUPLE_DELIMITER"],
        record_delimiter=PROMPTS["DEFAULT_RECORD_DELIMITER"],
        completion_delimiter=PROMPTS["DEFAULT_COMPLETION_DELIMITER"],
        entity_types=",".join(PROMPTS["DEFAULT_ENTITY_TYPES"]),
        examples=PROMPTS.get("entity_extraction_examples", ""),
    )
    continue_prompt = PROMPTS["entiti_continue_extraction"]

    if_loop_prompt = PROMPTS["entiti_if_loop_extraction"]

    already_processed = 0
    already_entities = 0
    already_relations = 0

    async def _process_single_content(chunk_key_dp: tuple[str, TextChunkSchema]):
        nonlocal already_processed, already_entities, already_relations
        chunk_key = chunk_key_dp[0]
        chunk_dp = chunk_key_dp[1]
        content = chunk_dp["content"]
        hint_prompt = entity_extract_prompt.format(**context_base, input_text=content)
        final_result = await use_llm_func(hint_prompt)

        history = pack_user_ass_to_openai_messages(hint_prompt, final_result)
        for now_glean_index in range(entity_extract_max_gleaning):
            glean_result = await use_llm_func(continue_prompt, history_messages=history)

            history += pack_user_ass_to_openai_messages(continue_prompt, glean_result)
            final_result += glean_result
            if now_glean_index == entity_extract_max_gleaning - 1:
                break

            if_loop_result: str = await use_llm_func(
                if_loop_prompt, history_messages=history
            )
            if_loop_result = if_loop_result.strip().strip('"').strip("'").lower()
            if if_loop_result != "yes":
                break

        records = split_string_by_multi_markers(
            final_result,
            [context_base["record_delimiter"], context_base["completion_delimiter"]],
        )

        maybe_nodes = defaultdict(list)
        maybe_edges = defaultdict(list)
        for record in records:
            record = re.search(r"\((.*)\)", record)
            if record is None:
                continue
            record = record.group(1)
            record_attributes = split_string_by_multi_markers(
                record, [context_base["tuple_delimiter"]]
            )
            if_entities = await _handle_single_entity_extraction(
                record_attributes, chunk_key
            )
            if if_entities is not None:
                maybe_nodes[if_entities["entity_name"]].append(if_entities)
                continue

            if_relation = await _handle_single_relationship_extraction(
                record_attributes, chunk_key
            )
            if if_relation is not None:
                maybe_edges[(if_relation["src_id"], if_relation["tgt_id"])].append(
                    if_relation
                )
        already_processed += 1
        already_entities += len(maybe_nodes)
        already_relations += len(maybe_edges)
        now_ticks = PROMPTS["process_tickers"][
            already_processed % len(PROMPTS["process_tickers"])
        ]
        print(
            f"{now_ticks} Processed {already_processed} chunks, {already_entities} entities(duplicated), {already_relations} relations(duplicated)\r",
            end="",
            flush=True,
        )
        return dict(maybe_nodes), dict(maybe_edges)

    # use_llm_func is wrapped in ascynio.Semaphore, limiting max_async callings
    results = await asyncio.gather(
        *[_process_single_content(c) for c in ordered_chunks]
    )
    print()  # clear the progress bar
    maybe_nodes = defaultdict(list)
    maybe_edges = defaultdict(list)
    for m_nodes, m_edges in results:
        for k, v in m_nodes.items():
            maybe_nodes[k].extend(v)
        for k, v in m_edges.items():
            maybe_edges[tuple(sorted(k))].extend(v)
    logger.info(
        "Entity candidate extraction finished with %s entity groups and %s relationship groups",
        len(maybe_nodes),
        len(maybe_edges),
    )
    return dict(maybe_nodes), dict(maybe_edges)


async def merge_extracted_candidates(
    maybe_nodes: dict,
    maybe_edges: dict,
    knowledge_graph_inst: BaseGraphStorage,
    entity_vdb: BaseVectorStorage,
    entity_name_vdb: BaseVectorStorage,
    relationships_vdb: BaseVectorStorage,
    global_config: dict,
) -> Union[BaseGraphStorage, None]:
    all_entities_data = await asyncio.gather(
        *[
            _merge_nodes_then_upsert(k, v, knowledge_graph_inst, global_config)
            for k, v in maybe_nodes.items()
        ]
    )
    all_relationships_data = await asyncio.gather(
        *[
            _merge_edges_then_upsert(k[0], k[1], v, knowledge_graph_inst, global_config)
            for k, v in maybe_edges.items()
        ]
    )
    if not len(all_entities_data):
        logger.warning("Didn't extract any entities, maybe your LLM is not working")
        return None
    if not len(all_relationships_data):
        logger.warning(
            "Didn't extract any relationships, maybe your LLM is not working"
        )
        return None

    if entity_vdb is not None:
        data_for_vdb = {
            compute_mdhash_id(dp["entity_name"], prefix="ent-"): {
                "content": dp["entity_name"] + dp["description"],
                "entity_name": dp["entity_name"],
            }
            for dp in all_entities_data
        }
        await entity_vdb.upsert(data_for_vdb)
    if entity_vdb is not None:
        data_for_vdb = {
            compute_mdhash_id(dp["entity_name"], prefix="ent-"): {
                "content": dp["entity_name"] + " " + dp["description"],
                "entity_name": dp["entity_name"],
            }
            for dp in all_entities_data
        }
        await entity_vdb.upsert(data_for_vdb)

    if entity_name_vdb is not None:
        data_for_vdb = {
            compute_mdhash_id(dp["entity_name"], prefix="Ename-"): {
                "content": dp["entity_name"],
                "entity_name": dp["entity_name"],
            }
            for dp in all_entities_data
        }
        await entity_name_vdb.upsert(data_for_vdb)

    if relationships_vdb is not None:
        data_for_vdb = {
            compute_mdhash_id(dp["src_id"] + dp["tgt_id"], prefix="rel-"): {
                "src_id": dp["src_id"],
                "tgt_id": dp["tgt_id"],
                "content": dp["keywords"]
                + " "
                + dp.get("relation_type", "UNSPECIFIED")
                + " "
                + dp["src_id"]
                + " "
                + dp["tgt_id"]
                + " "
                + dp["description"],
            }
            for dp in all_relationships_data
        }

        await relationships_vdb.upsert(data_for_vdb)

    return knowledge_graph_inst


def _normalize_query_anchor(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().strip('"').strip("'").strip()


def _collect_node_source_chunk_ids(node_datas, max_chunks: int):
    chunk_ids = []
    seen = set()
    sorted_nodes = sorted(
        node_datas,
        key=lambda node: node.get("Score", 0),
        reverse=True,
    )
    for node in sorted_nodes:
        for chunk_id in split_string_by_multi_markers(
            node.get("source_id", ""), [GRAPH_FIELD_SEP]
        ):
            normalized_chunk_id = _normalize_query_anchor(chunk_id)
            if not normalized_chunk_id or normalized_chunk_id in seen:
                continue
            seen.add(normalized_chunk_id)
            chunk_ids.append(normalized_chunk_id)
            if len(chunk_ids) >= max_chunks:
                return chunk_ids
    return chunk_ids


def _normalize_faultcase_text(value: str) -> str:
    return normalize_alias_text(value)


def _build_faultcase_aliases(entity_name: str, entity_type: str) -> list[str]:
    aliases = [entity_name]
    stripped = re.sub(r"[（(].*?[）)]", "", entity_name).strip()
    if stripped:
        aliases.append(stripped)
    if entity_type == "EQUIPMENT" and entity_name.endswith("系统"):
        aliases.append(entity_name[:-2])
    return list(dict.fromkeys([alias for alias in aliases if alias]))


def _score_faultcase_node(
    query: str, entity_name: str, entity_type: str
) -> tuple[float, bool]:
    if entity_type == "COMPONENT":
        return 0.0, False
    normalized_query = _normalize_faultcase_text(query)
    score = 0.0
    exact = False
    for alias in _build_faultcase_aliases(entity_name, entity_type):
        normalized_alias = _normalize_faultcase_text(alias)
        if not normalized_alias:
            continue
        if normalized_alias in normalized_query:
            score = max(score, 1000 + len(normalized_alias) * 10)
            exact = True
        elif normalized_query in normalized_alias:
            score = max(score, 300 + len(normalized_query) * 5)
        else:
            overlap = sum(1 for ch in set(normalized_alias) if ch in normalized_query)
            if overlap >= 2:
                score = max(score, overlap * 10 / max(len(set(normalized_alias)), 1))
    if entity_type == "FAULTCASE":
        score += 5
    return score, exact


async def _load_faultcase_candidate_nodes(knowledge_graph_inst: BaseGraphStorage):
    _, type_pool_with_case = await knowledge_graph_inst.get_types()
    node_datas = await knowledge_graph_inst.get_node_from_types(type_pool_with_case)
    node_datas = node_datas or []
    cleaned_nodes = []
    for node in node_datas:
        if not node:
            continue
        entity_type = _normalize_query_anchor(node.get("entity_type", ""))
        if entity_type not in {"EQUIPMENT", "FAULTCASE", "COMPONENT"}:
            continue
        cleaned_nodes.append(
            {
                "entity_name": node.get("entity_name", ""),
                "entity_type": entity_type,
                "description": node.get("description", ""),
                "source_id": node.get("source_id", ""),
            }
        )
    return cleaned_nodes


def _build_router_type_bonus(preferred_entity_types: list[str]) -> dict[str, float]:
    bonus_map: dict[str, float] = {}
    total = len(preferred_entity_types)
    for index, entity_type in enumerate(preferred_entity_types):
        bonus_map[entity_type] = float((total - index) * 5)
    return bonus_map


def _build_alias_hit_map(router_result: dict) -> dict[str, dict]:
    alias_hit_map: dict[str, dict] = {}
    for hit in router_result.get("alias_hits", []):
        canonical_name = hit.get("canonical_name")
        if not canonical_name:
            continue
        current = alias_hit_map.get(canonical_name)
        if current is None or hit.get("score", 0) > current.get("score", 0):
            alias_hit_map[canonical_name] = hit
    return alias_hit_map


async def _faultcase_fast_recall(
    query: str,
    knowledge_graph_inst: BaseGraphStorage,
    chunks_vdb: BaseVectorStorage,
    query_param: QueryParam,
    alias_store=None,
):
    candidate_nodes = await _load_faultcase_candidate_nodes(knowledge_graph_inst)
    router_result = route_faultcase_query(query, alias_store)
    router_type_bonus = _build_router_type_bonus(
        router_result.get("preferred_entity_types", [])
    )
    alias_hit_map = _build_alias_hit_map(router_result)
    scored = []
    exact_faultcase_nodes = []
    exact_equipment_nodes = []
    exact_component_nodes = []
    for node in candidate_nodes:
        alias_hit = alias_hit_map.get(node["entity_name"])
        if alias_hit:
            score = alias_hit.get("score", 0) + router_type_bonus.get(
                node["entity_type"], 0
            )
            exact = True
        else:
            score, exact = _score_faultcase_node(
                query, node["entity_name"], node["entity_type"]
            )
            if score > 0:
                score += router_type_bonus.get(node["entity_type"], 0)
        if score > 0:
            scored.append((score, node))
        if exact and node["entity_type"] == "FAULTCASE":
            exact_faultcase_nodes.append(node)
        elif exact and node["entity_type"] == "EQUIPMENT":
            exact_equipment_nodes.append(node)
        elif exact and node["entity_type"] == "COMPONENT":
            exact_component_nodes.append(node)
    scored.sort(key=lambda item: item[0], reverse=True)

    if exact_faultcase_nodes:
        # If the query already anchors a concrete fault case, do not fan out to sibling
        # cases under the same equipment. Only keep the matched fault card and its
        # directly connected equipment entry.
        expanded_nodes = {
            node["entity_name"]: {**node, "score": 1000.0}
            for node in exact_faultcase_nodes
        }
        for node in exact_faultcase_nodes:
            neighbors = await knowledge_graph_inst.get_node_edges(node["entity_name"])
            for src_id, tgt_id in neighbors or []:
                neighbor_name = tgt_id if src_id == node["entity_name"] else src_id
                neighbor_data = await knowledge_graph_inst.get_node(neighbor_name)
                if not neighbor_data:
                    continue
                expanded_nodes.setdefault(
                    neighbor_name,
                    {
                        "entity_name": neighbor_name,
                        "entity_type": _normalize_query_anchor(
                            neighbor_data.get("entity_type", "")
                        ),
                        "description": neighbor_data.get("description", ""),
                        "source_id": neighbor_data.get("source_id", ""),
                        "score": 900.0,
                    },
                )
        top_nodes = list(expanded_nodes.values())[: query_param.top_k]
    elif exact_equipment_nodes:
        # For equipment-level hits, keep the equipment anchor but re-score its direct
        # fault-card neighbors against the full query. This avoids pulling every fault
        # card under a broad equipment node into the answer context.
        expanded_nodes = {
            node["entity_name"]: {**node, "score": 1000.0}
            for node in exact_equipment_nodes
        }
        rescored_neighbors = []
        for node in exact_equipment_nodes:
            neighbors = await knowledge_graph_inst.get_node_edges(node["entity_name"])
            for src_id, tgt_id in neighbors or []:
                neighbor_name = tgt_id if src_id == node["entity_name"] else src_id
                neighbor_data = await knowledge_graph_inst.get_node(neighbor_name)
                if not neighbor_data:
                    continue
                neighbor_entity_type = _normalize_query_anchor(
                    neighbor_data.get("entity_type", "")
                )
                score, exact = _score_faultcase_node(
                    query, neighbor_name, neighbor_entity_type
                )
                if exact or score > 0:
                    rescored_neighbors.append(
                        {
                            "entity_name": neighbor_name,
                            "entity_type": neighbor_entity_type,
                            "description": neighbor_data.get("description", ""),
                            "source_id": neighbor_data.get("source_id", ""),
                            "score": 900.0 if exact else score,
                        }
                    )

        rescored_neighbors.sort(key=lambda item: item.get("score", 0), reverse=True)
        remaining_slots = max(0, query_param.top_k - len(expanded_nodes))
        for neighbor in rescored_neighbors[:remaining_slots]:
            expanded_nodes.setdefault(neighbor["entity_name"], neighbor)

        top_nodes = list(expanded_nodes.values())[: query_param.top_k]
    elif exact_component_nodes:
        expanded_nodes = {
            node["entity_name"]: {**node, "score": 1000.0}
            for node in exact_component_nodes
        }
        rescored_neighbors = []
        for node in exact_component_nodes:
            neighbors = await knowledge_graph_inst.get_node_edges(node["entity_name"])
            for src_id, tgt_id in neighbors or []:
                neighbor_name = tgt_id if src_id == node["entity_name"] else src_id
                neighbor_data = await knowledge_graph_inst.get_node(neighbor_name)
                if not neighbor_data:
                    continue
                neighbor_entity_type = _normalize_query_anchor(
                    neighbor_data.get("entity_type", "")
                )
                if neighbor_entity_type not in {"EQUIPMENT", "FAULTCASE"}:
                    continue
                score, exact = _score_faultcase_node(
                    query, neighbor_name, neighbor_entity_type
                )
                neighbor_score = max(
                    score + router_type_bonus.get(neighbor_entity_type, 0),
                    900.0 if neighbor_entity_type == "FAULTCASE" else 850.0,
                )
                if exact:
                    neighbor_score += 40.0
                rescored_neighbors.append(
                    {
                        "entity_name": neighbor_name,
                        "entity_type": neighbor_entity_type,
                        "description": neighbor_data.get("description", ""),
                        "source_id": neighbor_data.get("source_id", ""),
                        "score": neighbor_score,
                    }
                )

        rescored_neighbors.sort(key=lambda item: item.get("score", 0), reverse=True)
        remaining_slots = max(0, query_param.top_k - len(expanded_nodes))
        for neighbor in rescored_neighbors[:remaining_slots]:
            expanded_nodes.setdefault(neighbor["entity_name"], neighbor)

        top_nodes = list(expanded_nodes.values())[: query_param.top_k]
    else:
        top_nodes = [
            {**node, "score": score} for score, node in scored[: query_param.top_k]
        ]

    if exact_faultcase_nodes:
        source_seed_nodes = exact_faultcase_nodes
    elif exact_equipment_nodes:
        source_seed_nodes = [
            node for node in top_nodes if node.get("entity_type") == "FAULTCASE"
        ] or top_nodes
    elif exact_component_nodes:
        source_seed_nodes = [
            node
            for node in top_nodes
            if node.get("entity_type") in {"FAULTCASE", "COMPONENT"}
        ] or top_nodes
    else:
        source_seed_nodes = top_nodes

    source_ids = _collect_node_source_chunk_ids(
        source_seed_nodes,
        max_chunks=2
        if (exact_faultcase_nodes or exact_equipment_nodes or exact_component_nodes)
        else max(2, min(4, int(query_param.top_k / 2) or 1)),
    )
    return top_nodes, source_ids


def _normalize_conversation_history(query_param: QueryParam) -> list[dict]:
    max_messages = max(0, int(query_param.history_turns or 0)) * 2
    normalized: list[dict] = []
    for item in query_param.conversation_history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})

    if max_messages > 0 and len(normalized) > max_messages:
        normalized = normalized[-max_messages:]
    return normalized


def _build_followup_faultcase_query(query: str, history_messages: list[dict]) -> str:
    last_user_query = next(
        (
            str(message.get("content") or "").strip()
            for message in reversed(history_messages)
            if message.get("role") == "user"
            and str(message.get("content") or "").strip()
        ),
        "",
    )
    last_assistant_reply = next(
        (
            str(message.get("content") or "").strip()
            for message in reversed(history_messages)
            if message.get("role") == "assistant"
            and str(message.get("content") or "").strip()
        ),
        "",
    )
    if not last_user_query and not last_assistant_reply:
        return query

    followup_parts = []
    if last_user_query:
        followup_parts.append(f"上一轮问题：{last_user_query}")
    if last_assistant_reply:
        followup_parts.append(f"上一轮回答：{last_assistant_reply}")
    followup_parts.append(f"当前追问：{query}")
    return "\n".join(followup_parts)


async def _build_faultcase_fast_context(
    query: str,
    knowledge_graph_inst: BaseGraphStorage,
    chunks_vdb: BaseVectorStorage,
    text_chunks_db: BaseKVStorage[TextChunkSchema],
    query_param: QueryParam,
    global_config: dict | None = None,
    alias_store=None,
    max_source_candidates: int | None = None,
):
    use_graph_seed = (
        query_param.mode == "graph_only"
        or (
            query_param.mode in {"graph_text_hybrid", "keyword_search"}
            and not query_param.text_only_retrieval
        )
    )

    if use_graph_seed:
        node_datas, source_ids = await _faultcase_fast_recall(
            query,
            knowledge_graph_inst,
            chunks_vdb,
            query_param,
            alias_store=alias_store,
        )
        if not node_datas and query_param.mode != "keyword_search":
            return None
    else:
        node_datas, source_ids = [], []

    entity_rows = [["entity", "entity_type", "score", "description"]]
    if node_datas:
        sorted_nodes = sorted(
            node_datas, key=lambda item: item.get("score", 0), reverse=True
        )
        sorted_nodes = truncate_list_by_token_size(
            sorted_nodes,
            key=lambda item: item.get("description", ""),
            max_token_size=query_param.max_token_for_node_context,
        )
        for node in sorted_nodes:
            entity_rows.append(
                [
                    node["entity_name"],
                    node.get("entity_type", ""),
                    round(node.get("score", 0), 4),
                    node.get("description", "UNKNOWN"),
                ]
            )
    entities_context = list_of_list_to_csv(entity_rows)

    if query_param.mode == "graph_only":
        graph_chunk_ids = source_ids[: max(1, query_param.top_k)]
        if hasattr(text_chunks_db, "get_by_ids"):
            payloads = await text_chunks_db.get_by_ids(graph_chunk_ids)
        else:
            payloads = await asyncio.gather(
                *[text_chunks_db.get_by_id(chunk_id) for chunk_id in graph_chunk_ids]
            )
        chunk_candidates = [
            {"id": chunk_id, "content": payload.get("content", "")}
            for chunk_id, payload in zip(graph_chunk_ids, payloads)
            if payload is not None
        ]
    elif query_param.mode == "keyword_search":
        lexical_candidate_ids = await collect_lexical_candidate_ids(
            source_ids,
            [],
            text_chunks_db,
            scan_limit=max(
                query_param.chunk_bm25_scan_limit,
                query_param.faultcase_chunk_lexical_scan_limit,
                len(source_ids) or 1,
                1000,
            ),
        )
        chunk_candidates = await recall_bm25_chunks(
            query,
            text_chunks_db,
            lexical_candidate_ids,
            top_k=query_param.chunk_bm25_top_k,
        )
    else:
        chunk_candidates = await hybrid_chunk_recall(
            query,
            source_ids,
            chunks_vdb,
            text_chunks_db,
            query_param,
        )
        chunk_candidates = await rerank_faultcase_candidates(
            query,
            chunk_candidates,
            query_param,
            global_config=global_config or {},
        )
        if max_source_candidates is not None:
            chunk_candidates = chunk_candidates[: max(1, int(max_source_candidates))]

    chunk_candidates = truncate_list_by_token_size(
        chunk_candidates,
        key=lambda item: item.get("content", ""),
        max_token_size=query_param.max_token_for_text_unit,
    )

    text_rows = [["id", "content"]]
    for candidate in chunk_candidates:
        text_rows.append([candidate["id"], candidate.get("content", "")])
    text_units_context = list_of_list_to_csv(text_rows)

    return f"""
-----Sources-----
```csv
{text_units_context}
```
-----Entities-----
```csv
{entities_context}
```
"""


async def faultcase_fast_query(
    query,
    knowledge_graph_inst: BaseGraphStorage,
    chunks_vdb: BaseVectorStorage,
    text_chunks_db: BaseKVStorage[TextChunkSchema],
    query_param: QueryParam,
    global_config: dict,
):
    use_model_func = global_config["llm_model_func"]
    response_type = query_param.response_type
    keyword_query = " ".join(
        [str(item).strip() for item in (query_param.keywords or []) if str(item).strip()]
    )
    # Temporarily disable multi-turn memory in the backend and force
    # single-turn retrieval/generation. Keeping history out of the
    # retrieval query avoids prior turns polluting the recall path.
    # history_messages = (
    #     []
    #     if query_param.mode == "keyword_search"
    #     else _normalize_conversation_history(query_param)
    # )
    history_messages: list[dict] = []
    retrieval_query = (
        keyword_query
        if query_param.mode == "keyword_search" and len(query_param.keywords or []) > 1
        else query
    )
    context = await _build_faultcase_fast_context(
        retrieval_query,
        knowledge_graph_inst,
        chunks_vdb,
        text_chunks_db,
        query_param,
        global_config=global_config,
        alias_store=global_config.get("faultcase_alias_store"),
        max_source_candidates=2 if history_messages else None,
    )

    if context is None:
        return PROMPTS["fail_response"]
    if query_param.only_need_context:
        return context

    sys_prompt_prefix = f"""你是船舶故障诊断助手，只能依据给定上下文作答。
规则：
- 处理类问题：给步骤。
- 原因类问题：列原因。
- 列举类问题：列当前命中的故障卡片。
- 注意事项类问题：答注意事项。
- 若上下文没有明确答案，就说“根据当前证据无法确定”。
- 优先简洁，除非步骤本身需要展开。
- 当前检索模式：{query_param.mode}

回答风格：{response_type}

上下文：
"""
    llm_context_window = int(global_config.get("llm_model_max_token_size", 32768) or 32768)
    reserved_completion_tokens = max(256, min(1024, int(llm_context_window * 0.2)))
    prompt_budget = max(512, llm_context_window - reserved_completion_tokens)
    prompt_prefix_tokens = len(encode_string_by_tiktoken(sys_prompt_prefix))
    available_context_tokens = max(256, prompt_budget - prompt_prefix_tokens)
    context_tokens = encode_string_by_tiktoken(context)

    if len(context_tokens) > available_context_tokens:
        logger.warning(
            "faultcase_fast context truncated from %s to %s tokens to fit model window %s",
            len(context_tokens),
            available_context_tokens,
            llm_context_window,
        )
        context = decode_tokens_by_tiktoken(
            context_tokens[:available_context_tokens]
        )

    sys_prompt = f"{sys_prompt_prefix}{context}\n"
    if query_param.stream:
        return await use_model_func(
            query,
            system_prompt=sys_prompt,
            history_messages=history_messages or None,
            stream=True,
        )
    return await use_model_func(
        query,
        system_prompt=sys_prompt,
        history_messages=history_messages or None,
    )
