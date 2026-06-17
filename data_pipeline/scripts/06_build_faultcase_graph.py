import argparse
import asyncio
import importlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests

import minirag  # noqa: F401

from data_pipeline.script_runtime import (
    add_datasource_arguments,
    discover_doc_dirs as discover_doc_dirs_under_root,
    resolve_doc_dir_argument,
    resolve_records_root,
    resolve_working_dir,
)
from minirag.base import DocStatus
from minirag.minirag import MiniRAG
from minirag.operate import merge_extracted_candidates
from minirag.utils import (
    EmbeddingFunc,
    compute_mdhash_id,
    encode_string_by_tiktoken,
    get_content_summary,
    logger,
)
from minirag.llm.ollama import ollama_embed
from minirag.llm.openai import openai_embed


def _load_optional_module(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


dotenv_module = _load_optional_module("dotenv")
if dotenv_module is not None and hasattr(dotenv_module, "load_dotenv"):
    dotenv_module.load_dotenv(override=False)


def normalize_doc_name(doc_name: str) -> str:
    value = str(doc_name or "").strip().replace(".md", "")
    value = re.sub(r"_[^_]+_\d+$", "", value)
    return value


def infer_parent_name_from_context(title_text: str) -> str:
    normalized = str(title_text or "").strip()
    rules = [
        ("航政艇", "航政艇"),
        ("液压系统", "船舶液压系统"),
        ("液压设备", "船舶液压系统"),
        ("电气设备", "船舶电气设备"),
        ("柴油机", "船舶柴油机"),
        ("发动机", "船舶发动机"),
        ("舵机", "船舶舵机"),
        ("机械设备", "船舶机械设备"),
    ]
    for keyword, parent in rules:
        if keyword in normalized:
            return parent
    return normalized or "文档父节点"


def infer_doc_parent_name(doc_name: str) -> str:
    return infer_parent_name_from_context(normalize_doc_name(doc_name))


def is_large_manual_doc(doc_name: str) -> bool:
    normalized = normalize_doc_name(doc_name)
    return any(keyword in normalized for keyword in ["指南", "维护与修理"])


def parse_breadcrumb_parts(breadcrumb: str) -> list[str]:
    return [
        part.strip()
        for part in str(breadcrumb or "").replace(".md", "").split(">")
        if part.strip()
    ]


def resolve_parent_scope(
    doc_name: str,
    breadcrumb: str,
    scope_mode: str,
) -> dict[str, str]:
    parts = parse_breadcrumb_parts(breadcrumb)
    chapter = parts[1] if len(parts) > 1 else ""
    subsection = parts[2] if len(parts) > 2 else ""
    use_section_scope = scope_mode == "section" or (
        scope_mode == "auto" and is_large_manual_doc(doc_name)
    )
    if use_section_scope:
        scope_title = " > ".join(
            [part for part in [chapter, subsection] if part]
        ).strip() or normalize_doc_name(doc_name)
        scope_key = f"{normalize_doc_name(doc_name)}::{scope_title}"
        return {
            "scope_key": scope_key,
            "scope_title": scope_title,
            "chapter": chapter,
            "subsection": subsection,
            "scope_kind": "section",
        }

    return {
        "scope_key": normalize_doc_name(doc_name),
        "scope_title": normalize_doc_name(doc_name),
        "chapter": chapter,
        "subsection": subsection,
        "scope_kind": "doc",
    }


def build_parent_node_key(parent_name: str, scope: dict[str, str]) -> str:
    if scope.get("scope_kind") != "section":
        return parent_name
    suffix = " > ".join(
        [
            part
            for part in [scope.get("chapter", ""), scope.get("subsection", "")]
            if part
        ]
    ).strip() or scope.get("scope_title", "")
    return f"{parent_name}｜{suffix}"


def build_doc_parent_description(
    doc_name: str, parent_name: str, record_count: int, scope_title: str = ""
) -> str:
    normalized = normalize_doc_name(doc_name)
    if scope_title and scope_title != normalized:
        return f"{parent_name}，由文档《{normalized}》中范围“{scope_title}”归纳出的公共父节点，当前汇聚 {record_count} 条故障卡片记录。"
    return f"{parent_name}，由文档《{normalized}》归纳出的父级装备节点，当前汇聚 {record_count} 条故障卡片记录。"


def build_parent_llm_prompt(
    doc_name: str,
    equipment_names: list[str],
    chapter: str = "",
    subsection: str = "",
    scope_title: str = "",
) -> str:
    equipment_block = "；".join([name for name in equipment_names if name]) or "无"
    return f"""你是船舶故障图谱的父节点归一化助手。

任务：
根据文档标题、章节/小节范围和文档内出现的 equipment 名称，输出一个“公共父节点名称”。

要求：
1. 父节点名称要稳定、概括、适合跨文档复用。
2. 优先提取总装备或总系统名称，例如：
   - 航政艇
   - 船舶柴油机
   - 船舶液压系统
   - 船舶电气设备
   - 船舶舵机
   - 船舶发动机
   - 船舶机械设备
3. 不要保留“分析 / 解决方案 / 维修指南 / 维护与修理 / 常见故障 / 故障分析 / 探讨 / 排除方法 / 应对方法”等文档体裁词。
4. 如果“液压设备”和“液压系统”表达的是同一个总类，统一成“船舶液压系统”。
5. 如果“柴油机”相关文档表达的是同一个总类，统一成“船舶柴油机”。
6. 如果当前范围只是一本大部头中的一个章节/小节，则应优先依据该章节/小节的主题来命名父节点，而不是整本书的总标题。
7. 如果章节/小节明显对应某个公共装备簇，例如“发电机与配电板”“蓄电池与充放电”“舵机液压回路”，应返回这个范围下更合理的公共父节点。
8. 如果标题本身已经明确给出总装备名称，优先服从标题。
9. 只返回 JSON，不要返回解释文字。

输出格式：
{{
  "parent_name": "船舶柴油机",
  "confidence": 0.92,
  "reason": "标题直接指向柴油机总类"
}}

[文档标题]
{normalize_doc_name(doc_name)}

[当前范围]
{scope_title or "无"}

[章节]
{chapter or "无"}

[小节]
{subsection or "无"}

[equipment 列表]
{equipment_block}
"""


def infer_doc_parent_name_via_llm(
    doc_name: str,
    equipment_names: list[str],
    chapter: str,
    subsection: str,
    scope_title: str,
    llm_url: str,
    llm_model: str,
    llm_api_key: str,
    llm_timeout: int,
) -> tuple[str | None, dict[str, Any]]:
    payload = {
        "model": llm_model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": build_parent_llm_prompt(
                    doc_name,
                    equipment_names,
                    chapter=chapter,
                    subsection=subsection,
                    scope_title=scope_title,
                ),
            }
        ],
    }
    headers = {"Content-Type": "application/json"}
    if llm_api_key:
        headers["Authorization"] = f"Bearer {llm_api_key}"

    response = requests.post(
        llm_url, json=payload, headers=headers, timeout=llm_timeout
    )
    response.raise_for_status()
    data = response.json()
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if "```" in text:
        text = text.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(text)
    parent_name = str(parsed.get("parent_name", "") or "").strip()
    return parent_name or None, parsed


def resolve_doc_parent_name(
    doc_name: str,
    equipment_names: list[str],
    chapter: str,
    subsection: str,
    scope_title: str,
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any]]:
    context_title = (
        scope_title
        or " ".join([chapter or "", subsection or ""]).strip()
        or normalize_doc_name(doc_name)
    )
    rule_parent = infer_parent_name_from_context(context_title)
    normalized = scope_title or normalize_doc_name(doc_name)
    has_specific_rule = rule_parent != normalized

    mode = args.parent_node_mode
    if mode == "rule":
        return rule_parent, {
            "mode": "rule",
            "parent_name": rule_parent,
            "reason": "规则命中或标题清洗结果",
        }

    if mode == "hybrid" and has_specific_rule:
        return rule_parent, {
            "mode": "hybrid-rule",
            "parent_name": rule_parent,
            "reason": "已有明确规则命中，跳过 LLM",
        }

    try:
        parent_name, meta = infer_doc_parent_name_via_llm(
            doc_name=doc_name,
            equipment_names=equipment_names,
            chapter=chapter,
            subsection=subsection,
            scope_title=scope_title,
            llm_url=args.parent_llm_url,
            llm_model=args.parent_llm_model,
            llm_api_key=args.parent_llm_api_key,
            llm_timeout=args.parent_llm_timeout,
        )
        if parent_name:
            return parent_name, {
                "mode": f"{mode}-llm",
                "parent_name": parent_name,
                "reason": meta.get("reason", ""),
                "confidence": meta.get("confidence"),
            }
    except Exception as e:
        logger.warning(f"parent node llm fallback failed for {doc_name}: {e}")

    return rule_parent, {
        "mode": f"{mode}-fallback-rule",
        "parent_name": rule_parent,
        "reason": "LLM 不可用或失败，回退到规则",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a faultcase graph directly from datasource staging/extracted/records diagnostic_records_llm.json files."
    )
    add_datasource_arguments(parser)
    parser.add_argument(
        "--all-docs",
        action="store_true",
        help="Use every datasource document directory under staging/extracted/records that contains diagnostic_records_llm.json.",
    )
    parser.add_argument(
        "--doc-dir",
        action="append",
        dest="doc_dirs",
        help="Specific datasource records document directory. Can be passed multiple times.",
    )
    parser.add_argument(
        "--exclude-doc",
        action="append",
        default=[],
        help="Document directory names to exclude.",
    )
    parser.add_argument(
        "--working-dir",
        default=None,
        help="Target MiniRAG working directory. Defaults to the datasource outputs/graph/workdir.",
    )
    parser.add_argument(
        "--embedding-binding",
        default=os.getenv("EMBEDDING_BINDING", "openai"),
        choices=["ollama", "openai"],
        help="Embedding backend binding. Use openai for OpenAI-compatible APIs such as LM Studio.",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("EMBEDDING_MODEL", "text-embedding-qwen3-embedding-4b"),
        help="Embedding model name.",
    )
    parser.add_argument(
        "--embedding-host",
        default=os.getenv("EMBEDDING_BINDING_HOST", "http://127.0.0.1:1234/v1"),
        help="Embedding host URL. For openai binding, pass the OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--embedding-api-key",
        default=os.getenv(
            "EMBEDDING_BINDING_API_KEY", os.getenv("OPENAI_API_KEY", "lm-studio")
        ),
        help="Embedding API key. For LM Studio this can be any non-empty placeholder if auth is not enforced.",
    )
    parser.add_argument(
        "--embedding-dim", default=2560, type=int, help="Embedding dimension."
    )
    parser.add_argument(
        "--parent-node-mode",
        default=os.getenv("PARENT_NODE_MODE", "hybrid"),
        choices=["rule", "llm", "hybrid"],
        help="How to derive document-level parent nodes.",
    )
    parser.add_argument(
        "--parent-scope-mode",
        default=os.getenv("PARENT_SCOPE_MODE", "auto"),
        choices=["doc", "section", "auto"],
        help="Whether parent nodes are resolved per document or per chapter/subsection scope.",
    )
    parser.add_argument(
        "--parent-llm-url",
        default=os.getenv(
            "PARENT_LLM_URL", "http://127.0.0.1:1234/v1/chat/completions"
        ),
        help="OpenAI-compatible chat completions endpoint for parent node normalization.",
    )
    parser.add_argument(
        "--parent-llm-model",
        default=os.getenv("PARENT_LLM_MODEL", "qwen3.5-2b"),
        help="Model used for parent node normalization.",
    )
    parser.add_argument(
        "--parent-llm-api-key",
        default=os.getenv("PARENT_LLM_API_KEY", "lm-studio"),
        help="API key for parent node normalization.",
    )
    parser.add_argument(
        "--parent-llm-timeout",
        default=120,
        type=int,
        help="Timeout in seconds for parent node normalization LLM calls.",
    )
    parser.add_argument("--log-level", default="INFO", help="MiniRAG log level.")
    parser.add_argument(
        "--reset-working-dir",
        action="store_true",
        help="Delete working-dir before building.",
    )
    return parser


def discover_doc_dirs(outputs_root: str, exclude_docs: list[str]) -> list[Path]:
    root = Path(outputs_root).expanduser().resolve()
    return discover_doc_dirs_under_root(
        root,
        "diagnostic_records_llm.json",
        exclude_names=exclude_docs,
    )


def resolve_doc_dirs(args: argparse.Namespace) -> list[Path]:
    records_root = resolve_records_root(args)
    if args.doc_dirs:
        return [resolve_doc_dir_argument(item, records_root) for item in args.doc_dirs]
    if args.all_docs:
        return discover_doc_dirs(str(records_root), args.exclude_doc)
    raise ValueError("Provide --all-docs or at least one --doc-dir")


def build_rag(args) -> MiniRAG:
    async def dummy_llm(prompt, system_prompt=None, history_messages=None, **kwargs):
        return ""

    if args.embedding_binding == "openai":
        embed_func = lambda texts: openai_embed(
            texts,
            model=args.embedding_model,
            base_url=args.embedding_host,
            api_key=args.embedding_api_key,
        )
    else:
        embed_func = lambda texts: ollama_embed(
            texts,
            embed_model=args.embedding_model,
            host=args.embedding_host,
            api_key=args.embedding_api_key,
        )

    embedding_func = EmbeddingFunc(
        embedding_dim=args.embedding_dim,
        max_token_size=8192,
        func=embed_func,
    )

    working_dir = resolve_working_dir(args)
    if args.reset_working_dir and working_dir.exists():
        shutil.rmtree(working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)

    return MiniRAG(
        working_dir=str(working_dir),
        embedding_func=embedding_func,
        llm_model_func=dummy_llm,
        llm_model_name="faultcase-direct-builder",
        llm_model_max_async=1,
        max_parallel_insert=1,
        log_level=args.log_level,
        enable_llm_cache=False,
    )


def normalize_text(value: str | None) -> str:
    value = (value or "").strip()
    return value if value else "无"


def normalize_list(values) -> list[str]:
    if not values:
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def render_numbered_block(values: list[str]) -> str:
    if not values:
        return "无"
    return "\n".join(f"{index}. {item}" for index, item in enumerate(values, start=1))


def faultcase_node_name(record: dict) -> str:
    fault = normalize_text(record.get("fault"))
    equipment = normalize_text(record.get("equipment"))
    if fault == "无":
        return equipment
    if equipment == "无":
        return fault
    return f"{fault}（{equipment}）"


def build_equipment_description(record: dict) -> str:
    equipment = normalize_text(record.get("equipment"))
    fault = normalize_text(record.get("fault"))
    return f"{equipment}，本记录对应故障卡片：{fault}。"


def build_faultcase_description(record: dict) -> str:
    parts = [
        f"故障现象: {normalize_text(record.get('symptom') or record.get('fault'))}",
        f"可能原因: {'；'.join(normalize_list(record.get('causes'))) if normalize_list(record.get('causes')) else '无'}",
        f"处理步骤: {'；'.join(normalize_list(record.get('actions'))) if normalize_list(record.get('actions')) else '无'}",
        f"可能后果: {'；'.join(normalize_list(record.get('consequences'))) if normalize_list(record.get('consequences')) else '无'}",
        f"关键部件: {'；'.join(normalize_list(record.get('key_components'))) if normalize_list(record.get('key_components')) else '无'}",
        f"注意事项: {'；'.join(normalize_list(record.get('precautions'))) if normalize_list(record.get('precautions')) else '无'}",
        f"breadcrumb: {normalize_text(record.get('breadcrumb'))}",
        f"原始文本: {normalize_text(record.get('source_text'))}",
    ]
    return " ".join(parts)


def render_faultcase_chunk(record: dict) -> str:
    parts = [
        f"[记录ID] {normalize_text(record.get('record_id'))}",
        f"[设备] {normalize_text(record.get('equipment'))}",
        f"[故障卡片] {faultcase_node_name(record)}",
        f"[故障现象] {normalize_text(record.get('symptom') or record.get('fault'))}",
        "[可能原因]",
        render_numbered_block(normalize_list(record.get("causes"))),
        "[处理步骤]",
        render_numbered_block(normalize_list(record.get("actions"))),
        "[可能后果]",
        render_numbered_block(normalize_list(record.get("consequences"))),
        "[关键部件]",
        render_numbered_block(normalize_list(record.get("key_components"))),
        "[注意事项]",
        render_numbered_block(normalize_list(record.get("precautions"))),
        "[原始文本]",
        normalize_text(record.get("source_text")),
    ]
    return "\n".join(parts)


def chunk_id_for(doc_name: str, record_id: str) -> str:
    return compute_mdhash_id(f"{doc_name}::{record_id}", prefix="chunk-faultcase-")


async def build_graph(args) -> None:
    doc_dirs = resolve_doc_dirs(args)
    rag = build_rag(args)
    now = datetime.now().isoformat()

    maybe_nodes: dict[str, list[dict]] = {}
    maybe_edges: dict[tuple[str, str], list[dict]] = {}
    chunk_payload: dict[str, dict] = {}
    full_docs_payload: dict[str, dict] = {}
    doc_status_payload: dict[str, dict] = {}

    total_records = 0
    for doc_dir in doc_dirs:
        records_path = doc_dir / "diagnostic_records_llm.json"
        payload = json.loads(records_path.read_text(encoding="utf-8"))
        records = payload.get("records", [])
        doc_name = payload.get("doc_name") or doc_dir.name
        doc_id = compute_mdhash_id(doc_name, prefix="doc-")
        parent_groups: dict[str, dict[str, Any]] = {}

        rendered_chunks = []
        for index, record in enumerate(records):
            total_records += 1
            record_id = normalize_text(record.get("record_id") or f"llm_{index:03d}")
            chunk_id = chunk_id_for(doc_name, record_id)
            chunk_content = render_faultcase_chunk(record)
            rendered_chunks.append(chunk_content)
            chunk_payload[chunk_id] = {
                "tokens": len(
                    encode_string_by_tiktoken(
                        chunk_content, model_name=rag.tiktoken_model_name
                    )
                ),
                "content": chunk_content,
                "full_doc_id": doc_id,
                "chunk_order_index": index,
            }

            equipment_name = normalize_text(record.get("equipment"))
            fault_name = faultcase_node_name(record)
            maybe_nodes.setdefault(equipment_name, []).append(
                {
                    "entity_name": equipment_name,
                    "entity_type": "EQUIPMENT",
                    "description": build_equipment_description(record),
                    "source_id": chunk_id,
                }
            )
            maybe_nodes.setdefault(fault_name, []).append(
                {
                    "entity_name": fault_name,
                    "entity_type": "FAULTCASE",
                    "description": build_faultcase_description(record),
                    "source_id": chunk_id,
                }
            )
            maybe_edges.setdefault((equipment_name, fault_name), []).append(
                {
                    "src_id": equipment_name,
                    "tgt_id": fault_name,
                    "weight": 10.0,
                    "description": "该设备对应一条高密度故障卡片，包含故障现象、原因、处理步骤、注意事项、后果与原始文本证据。",
                    "keywords": "故障记录, 高密度诊断卡片",
                    "relation_type": "Has_FaultCase",
                    "source_id": chunk_id,
                }
            )

            scope = resolve_parent_scope(
                doc_name=doc_name,
                breadcrumb=record.get("breadcrumb", ""),
                scope_mode=args.parent_scope_mode,
            )
            group = parent_groups.setdefault(
                scope["scope_key"],
                {
                    "scope_title": scope["scope_title"],
                    "chapter": scope["chapter"],
                    "subsection": scope["subsection"],
                    "equipment_names": set(),
                    "source_ids": [],
                    "record_count": 0,
                },
            )
            group["equipment_names"].add(equipment_name)
            group["source_ids"].append(chunk_id)
            group["record_count"] += 1

        for group in parent_groups.values():
            parent_name, parent_meta = resolve_doc_parent_name(
                doc_name=doc_name,
                equipment_names=sorted(group["equipment_names"]),
                chapter=group["chapter"],
                subsection=group["subsection"],
                scope_title=group["scope_title"],
                args=args,
            )
            parent_node_key = build_parent_node_key(
                parent_name,
                {
                    "scope_kind": "section"
                    if args.parent_scope_mode == "section"
                    or (
                        args.parent_scope_mode == "auto"
                        and is_large_manual_doc(doc_name)
                    )
                    else "doc",
                    "scope_title": group["scope_title"],
                    "chapter": group["chapter"],
                    "subsection": group["subsection"],
                },
            )
            logger.info(
                "parent resolved | doc=%s | scope=%s | parent=%s | node_key=%s | mode=%s | reason=%s",
                doc_name,
                group["scope_title"],
                parent_name,
                parent_node_key,
                parent_meta.get("mode"),
                parent_meta.get("reason", ""),
            )
            parent_source_id = "<SEP>".join(dict.fromkeys(group["source_ids"]))
            maybe_nodes.setdefault(parent_node_key, []).append(
                {
                    "entity_name": parent_node_key,
                    "entity_type": "DOCROOT",
                    "description": build_doc_parent_description(
                        doc_name,
                        parent_name,
                        group["record_count"],
                        scope_title=group["scope_title"],
                    ),
                    "source_id": parent_source_id,
                }
            )
            for equipment_name in sorted(group["equipment_names"]):
                if equipment_name == parent_node_key:
                    continue
                maybe_edges.setdefault((parent_node_key, equipment_name), []).append(
                    {
                        "src_id": parent_node_key,
                        "tgt_id": equipment_name,
                        "weight": 5.0,
                        "description": "公共父节点归档到该范围内出现的具体设备节点。",
                        "keywords": "父节点归档, 设备聚类",
                        "relation_type": "Has_Equipment",
                        "source_id": parent_source_id,
                    }
                )

        full_doc_content = "\n\n".join(rendered_chunks)
        full_docs_payload[doc_id] = {"content": full_doc_content}
        doc_status_payload[doc_id] = {
            "content": full_doc_content,
            "content_summary": get_content_summary(full_doc_content),
            "content_length": len(full_doc_content),
            "status": DocStatus.PROCESSED,
            "chunks_count": len(records),
            "created_at": now,
            "updated_at": now,
            "metadata": {"doc_name": doc_name},
        }

    await asyncio.gather(
        rag.chunks_vdb.upsert(chunk_payload),
        rag.full_docs.upsert(full_docs_payload),
        rag.text_chunks.upsert(chunk_payload),
        rag.doc_status.upsert(doc_status_payload),
    )

    await merge_extracted_candidates(
        maybe_nodes,
        maybe_edges,
        rag.chunk_entity_relation_graph,
        rag.entities_vdb,
        rag.entity_name_vdb,
        rag.relationships_vdb,
        rag.__dict__,
    )
    await rag._insert_done()

    print(
        json.dumps(
            {
                "working_dir": str(resolve_working_dir(args)),
                "doc_count": len(doc_dirs),
                "record_count": total_records,
                "chunk_count": len(chunk_payload),
                "node_group_count": len(maybe_nodes),
                "edge_group_count": len(maybe_edges),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(build_graph(args))


if __name__ == "__main__":
    main()
