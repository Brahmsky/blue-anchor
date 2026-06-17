import argparse
import json
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export diagnostic records into readable fault-case txt cards."
    )
    parser.add_argument("--records-json", required=True, help="Path to diagnostic records json")
    parser.add_argument("--output-dir", required=True, help="Directory to write txt outputs")
    return parser.parse_args()


def normalize_list(values: list[str]) -> list[str]:
    return [str(item).strip() for item in values or [] if str(item).strip()]


def sanitize_stem(name: str) -> str:
    name = str(name or "").strip()
    name = name.replace(".md", "")
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name or "faultcase_cards"


def format_section(title: str, values: list[str]) -> str:
    values = normalize_list(values)
    if not values:
        return f"[{title}]\n无"
    lines = [f"{idx}. {item}" for idx, item in enumerate(values, start=1)]
    return f"[{title}]\n" + "\n".join(lines)


def build_card_text(record: dict) -> str:
    equipment = record.get("equipment", "")
    fault = record.get("fault", "")
    fault_card = f"{fault}（{equipment}）" if equipment and fault else fault or equipment
    parts = [
        f"[记录ID]\n{record.get('record_id', '')}",
        f"[来源路径]\n{record.get('breadcrumb', '')}",
        f"[章节标题]\n{record.get('record_title', '')}",
        f"[装备]\n{equipment}",
        f"[故障卡片]\n{fault_card}",
        f"[故障现象]\n{record.get('symptom', '')}",
        format_section("可能原因", record.get("causes", [])),
        format_section("处理步骤", record.get("actions", [])),
        format_section("可能后果", record.get("consequences", [])),
        format_section("注意事项", record.get("precautions", [])),
        format_section("关键部件", record.get("key_components", [])),
        f"[原始文本]\n{record.get('source_text', '')}",
    ]
    return "\n\n".join(parts).strip() + "\n"


def build_overview_text(payload: dict) -> str:
    records = payload.get("records", [])
    lines = [
        "[文档名称]",
        str(payload.get("doc_name", "")),
        "",
        "[文档类型]",
        str(payload.get("doc_type", "")),
        "",
        "[导出说明]",
        "这份 txt 用于把 diagnostic records 转成更直观的“故障卡片”展示稿。",
        "它对齐当前项目约定的图谱本体结构：Equipment 作为入口，FaultCase 作为高密度故障卡片。",
        "原因、处理步骤、可能后果、注意事项、关键部件和原始文本都作为故障卡片内容层承载。",
        "",
        "[结构约定]",
        "1. 装备 -> 故障卡片",
        "2. 故障卡片内部展开：故障现象 / 可能原因 / 处理步骤 / 可能后果 / 注意事项 / 关键部件 / 原始文本",
        "",
        "[记录总数]",
        str(len(records)),
        "",
        "[记录索引]",
    ]
    for record in records:
        lines.append(
            f"- {record.get('record_id', '')}: {record.get('equipment', '')} -> {record.get('fault', '')}"
        )
    return "\n".join(lines).strip() + "\n"


def export_faultcase_txt_cards(records_path: Path, output_dir: Path) -> dict[str, str | int]:
    cards_dir = output_dir / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(records_path.read_text(encoding="utf-8"))
    records = payload.get("records", [])

    export_stem = sanitize_stem(payload.get("doc_name") or records_path.stem)
    overview_path = output_dir / f"{export_stem}_faultcase_cards_overview.txt"
    combined_path = output_dir / f"{export_stem}_faultcase_cards.txt"

    overview_text = build_overview_text(payload)
    overview_path.write_text(overview_text, encoding="utf-8")

    combined_parts = [overview_text, "\n" + "=" * 80 + "\n"]
    for index, record in enumerate(records, start=1):
        card_text = build_card_text(record)
        card_path = cards_dir / f"{index:02d}_{record.get('record_id', 'record')}.txt"
        card_path.write_text(card_text, encoding="utf-8")
        combined_parts.append(card_text)
        combined_parts.append("\n" + "-" * 80 + "\n")

    combined_path.write_text("".join(combined_parts).rstrip() + "\n", encoding="utf-8")
    return {
        "overview_path": str(overview_path),
        "combined_path": str(combined_path),
        "cards_dir": str(cards_dir),
        "record_count": len(records),
    }


def main() -> None:
    args = parse_args()
    summary = export_faultcase_txt_cards(Path(args.records_json), Path(args.output_dir))
    print(summary["combined_path"])


if __name__ == "__main__":
    main()
