from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.script_runtime import (
    add_datasource_arguments,
    resolve_chunks_root,
    resolve_doc_dir_argument,
    resolve_records_root,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate datasource staging/chunks accepted_records into datasource staging/extracted/records diagnostic records."
    )
    add_datasource_arguments(parser)
    parser.add_argument(
        "--doc-dir",
        required=True,
        help="Path or name of one datasource document directory under staging/chunks.",
    )
    parser.add_argument(
        "--accepted-folder",
        default="accepted_records",
        help="Folder name under the chunk document directory that stores accepted extracted records.",
    )
    parser.add_argument(
        "--good-folder",
        default="good_chunks",
        help="Folder name under the chunk document directory that stores source chunk json files.",
    )
    parser.add_argument(
        "--write-review",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to write staging/extracted/records/<doc>/diagnostic_records_llm_review.json.",
    )
    parser.add_argument(
        "--cleanup-intermediates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Whether to remove accepted_records, rejected_chunks and llm_extract_report.json from staging/chunks after records are built.",
    )
    return parser


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_dir_if_exists(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            remove_dir_if_exists(child)
    path.rmdir()


def normalize_list(values: list[str]) -> list[str]:
    return [str(item).strip() for item in values or [] if str(item).strip()]


def extract_record_title_from_breadcrumb(breadcrumb: str) -> str:
    parts = [
        part.strip()
        for part in str(breadcrumb or "").replace(".md", "").split(">")
        if part.strip()
    ]
    return parts[-1] if parts else ""


def derive_section_group(chapter: str) -> str:
    chapter = str(chapter or "").strip()
    return chapter.split(" ", 1)[-1] if " " in chapter else chapter


def derive_equipment_hint(record_title: str) -> str:
    title = str(record_title or "").strip()
    title = re.sub(r"^\d+(\.\d+)*\s*", "", title)
    title = title.replace("主、辅机", "主辅机")
    for suffix in ["常见问题", "故障", "异常", "问题"]:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
            break
    title = title.replace("系统供水", "供水系统")
    title = title.replace("系统供油", "供油系统")
    title = title.replace("系统供电", "供电系统")
    if any(
        title.endswith(suffix) for suffix in ["供水", "供油", "供电"]
    ) and not title.endswith("系统"):
        title = title + "系统"
    return title.strip()


def normalize_equipment_name(
    value: str, breadcrumb: str, source_text: str, record_title: str
) -> tuple[str, list[str]]:
    notes: list[str] = []
    equipment = str(value or "").strip()
    if not equipment:
        return "", notes

    original = equipment
    equipment_hint = derive_equipment_hint(record_title)
    equipment = equipment.replace("、", "")
    equipment = equipment.replace("系统供水系统", "供水系统")
    equipment = equipment.replace("系统供油系统", "供油系统")
    equipment = equipment.replace("系统供电系统", "供电系统")
    equipment = equipment.rstrip("的")

    if equipment == "舱口盖" and "平式舱口盖" in source_text:
        equipment = "平式舱口盖"
    if equipment == "门窗" and "门窗边框" in breadcrumb:
        equipment = "门窗边框"
    if equipment == "消防系统供水系统":
        equipment = "消防供水系统"
    if equipment_hint:
        compact_equipment = equipment.replace("、", "")
        compact_hint = equipment_hint.replace("、", "")
        if not equipment:
            equipment = equipment_hint
        elif compact_equipment in compact_hint and len(compact_equipment) < len(
            compact_hint
        ):
            equipment = equipment_hint

    if equipment != original:
        notes.append(f"equipment normalized: {original} -> {equipment}")
    return equipment, notes


def load_source_chunk(
    doc_dir: Path, good_folder: str, source_chunk_file: str
) -> dict[str, Any]:
    return read_json(doc_dir / good_folder / source_chunk_file)


def extract_chunk_order(doc_dir: Path, chapter_file: str, chunk_id: str) -> int:
    chapter_path = doc_dir / "raw_chunks" / chapter_file
    if not chapter_path.exists():
        return 10**9
    payload = read_json(chapter_path)
    for index, chunk in enumerate(payload.get("chunks", [])):
        if chunk.get("chunk_id") == chunk_id:
            return index
    return 10**9


def assess_quality(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not record.get("equipment", "").strip():
        reasons.append("missing_equipment")
    if not record.get("fault", "").strip():
        reasons.append("missing_fault")
    if not record.get("symptom", "").strip():
        reasons.append("missing_symptom")

    nonempty_support_lists = sum(
        1
        for field in ["causes", "actions", "precautions", "consequences"]
        if normalize_list(record.get(field, []))
    )
    if nonempty_support_lists == 0:
        reasons.append("no_supporting_fields")

    equipment = str(record.get("equipment", "") or "").strip()
    if equipment.endswith("的") or equipment in {"设备", "系统", "部件", "故障"}:
        reasons.append("over_generic_equipment")
    return reasons


def build_record(
    *,
    accepted_payload: dict[str, Any],
    source_chunk: dict[str, Any],
    index: int,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    record = accepted_payload["record"]
    chunk = source_chunk.get("chunk", source_chunk)
    breadcrumb = chunk.get("breadcrumb", "").replace(".md", "")
    record_title = extract_record_title_from_breadcrumb(breadcrumb)
    section_group = derive_section_group(source_chunk.get("chapter", ""))
    record_id = f"llm_{index:03d}"

    equipment, notes = normalize_equipment_name(
        record.get("equipment", ""),
        breadcrumb,
        record.get("source_text", ""),
        record_title,
    )

    final_record = {
        "record_id": record_id,
        "section_group": section_group,
        "record_title": record_title,
        "breadcrumb": breadcrumb,
        "equipment": equipment,
        "fault": str(record.get("fault", "") or "").strip(),
        "symptom": str(record.get("symptom", "") or "").strip(),
        "causes": normalize_list(record.get("causes", [])),
        "actions": normalize_list(record.get("actions", [])),
        "consequences": normalize_list(record.get("consequences", [])),
        "precautions": normalize_list(record.get("precautions", [])),
        "key_components": normalize_list(record.get("key_components", [])),
        "source_text": str(record.get("source_text", "") or "").strip(),
    }
    review = {
        "record_id": record_id,
        "breadcrumb": breadcrumb,
        "source_chunk_id": accepted_payload.get("source_chunk_id", ""),
        "source_chunk_file": accepted_payload.get("source_chunk_file", ""),
        "notes": notes,
        "quality_reasons": assess_quality(final_record),
        "raw_record": record,
    }
    return final_record, review, chunk.get("chapter_file", "")


def aggregate_one_doc(args: argparse.Namespace) -> dict[str, Any]:
    chunks_root = resolve_chunks_root(args)
    records_root = resolve_records_root(args)
    doc_dir = resolve_doc_dir_argument(args.doc_dir, chunks_root)
    accepted_dir = doc_dir / args.accepted_folder
    accepted_files = sorted(accepted_dir.glob("*.json"))

    prepared = []
    for index, accepted_file in enumerate(accepted_files, start=1):
        accepted_payload = read_json(accepted_file)
        source_chunk = load_source_chunk(
            doc_dir, args.good_folder, accepted_payload["source_chunk_file"]
        )
        record, review, chapter_file = build_record(
            accepted_payload=accepted_payload,
            source_chunk=source_chunk,
            index=index,
        )
        prepared.append(
            {
                "record": record,
                "review": review,
                "chapter_file": chapter_file,
                "source_chunk_id": accepted_payload.get("source_chunk_id", ""),
            }
        )

    prepared.sort(
        key=lambda item: (
            item["record"]["section_group"],
            extract_chunk_order(doc_dir, item["chapter_file"], item["source_chunk_id"]),
            item["source_chunk_id"],
        )
    )

    final_records = []
    review_items = []
    for item in prepared:
        record = item["record"]
        review = item["review"]
        if review["quality_reasons"]:
            review["decision"] = "drop"
        else:
            review["decision"] = "keep"
            final_records.append(record)
        review_items.append(review)

    doc_name = doc_dir.name
    record_doc_dir = records_root / doc_name
    llm_payload = {
        "doc_name": doc_name,
        "doc_type": "diagnostic_records_llm",
        "record_count": len(final_records),
        "records": final_records,
    }
    llm_path = record_doc_dir / "diagnostic_records_llm.json"
    write_json(llm_path, llm_payload)

    print(f"final_llm: {llm_path}")
    if args.write_review:
        review_path = record_doc_dir / "diagnostic_records_llm_review.json"
        write_json(
            review_path,
            {
                "summary": {
                    "accepted_record_count": len(prepared),
                    "kept_record_count": len(final_records),
                    "dropped_record_count": len(prepared) - len(final_records),
                },
                "items": review_items,
            },
        )
        print(f"review: {review_path}")

    if args.cleanup_intermediates:
        remove_dir_if_exists(doc_dir / args.accepted_folder)
        remove_dir_if_exists(doc_dir / "rejected_chunks")
        llm_report = doc_dir / "llm_extract_report.json"
        if llm_report.exists():
            llm_report.unlink()

    return {
        "datasource_records_root": str(records_root),
        "records_path": str(llm_path),
        "accepted_record_count": len(prepared),
        "kept_record_count": len(final_records),
        "dropped_record_count": len(prepared) - len(final_records),
    }


def main() -> None:
    summary = aggregate_one_doc(build_parser().parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
