import argparse
import csv
import glob
import json
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_pipeline.script_runtime import add_datasource_arguments, resolve_records_root


DEFAULT_OUTPUT_CSV = (
    ROOT / "dataset" / "Ship-Repair-Benchmark" / "qa" / "query_set_balanced.csv"
)
DEFAULT_SUMMARY_JSON = (
    ROOT
    / "dataset"
    / "Ship-Repair-Benchmark"
    / "qa"
    / "query_set_balanced_summary.json"
)

CATEGORY_TARGETS = {
    "cause_explanation": 55,
    "repair_method": 45,
    "maintenance": 20,
    "precaution": 10,
    "consequence": 10,
    "key_components": 15,
    "cause_to_fault": 25,
    "fault_distinction": 8,
    "similar_faults": 7,
    "common_faults": 5,
}

DOC_CAP_CANDIDATES = [40, 45, 50, 55, None]


@dataclass(frozen=True)
class Candidate:
    question: str
    gold_answer: str
    evidence: str
    question_type: str
    docs: tuple[str, ...]
    record_keys: tuple[str, ...]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a balanced Ship-Repair benchmark QA set from diagnostic records."
    )
    add_datasource_arguments(parser)
    parser.add_argument(
        "--records-glob",
        default=None,
        help="Optional glob pattern used to locate diagnostic_records_llm.json files. Defaults to the datasource staging/extracted/records tree.",
    )
    parser.add_argument(
        "--output-csv",
        default=str(DEFAULT_OUTPUT_CSV),
        help="Output path for the generated balanced CSV.",
    )
    parser.add_argument(
        "--summary-json",
        default=str(DEFAULT_SUMMARY_JSON),
        help="Output path for the generation summary JSON.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260415,
        help="Random seed used for stable candidate ordering.",
    )
    return parser


def clean_text(value: str) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ").strip(" \t。；;，,、.")
    text = re.sub(r"^(?:[（(]?\d+[）).、]\s*|\.\s+)", "", text)
    return text.strip(" \t。；;，,、.")


def listify(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_text(str(item)) for item in value if clean_text(str(item))]
    if isinstance(value, str):
        text = clean_text(value)
        return [text] if text else []
    return []


def join_items(items: list[str]) -> str:
    if not items:
        return ""
    return "；".join(item.rstrip("。") for item in items) + "。"


def bullet_sentence(prefix: str, items: list[str]) -> str:
    if not items:
        return ""
    return f"{prefix}{join_items(items)}"


def load_records(records_glob: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for raw_path in sorted(glob.glob(records_glob, recursive=True)):
        path = Path(raw_path).resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        doc_name = str(payload.get("doc_name") or path.parts[-3])
        for record in payload.get("records", []):
            record_id = clean_text(str(record.get("record_id") or ""))
            equipment = clean_text(str(record.get("equipment") or ""))
            fault = clean_text(str(record.get("fault") or ""))
            if not record_id or not equipment or not fault:
                continue
            records.append(
                {
                    "doc_name": doc_name,
                    "record_id": record_id,
                    "record_key": f"{doc_name}::{record_id}",
                    "equipment": equipment,
                    "fault": fault,
                    "symptom": clean_text(str(record.get("symptom") or "")),
                    "causes": listify(record.get("causes")),
                    "actions": listify(record.get("actions")),
                    "precautions": listify(record.get("precautions")),
                    "consequences": listify(record.get("consequences")),
                    "key_components": listify(record.get("key_components")),
                }
            )
    return records


def resolve_records_glob(args: argparse.Namespace) -> str:
    if args.records_glob:
        raw_pattern = Path(args.records_glob).expanduser()
        if raw_pattern.is_absolute():
            return str(raw_pattern)
        return str((Path.cwd() / raw_pattern).resolve())
    return str(resolve_records_root(args) / "*" / "diagnostic_records_llm.json")


def single_record_evidence(doc_name: str, record_id: str) -> str:
    return f"{doc_name}::{record_id} | 依据 {record_id} 对应故障卡与原始表述整理。"


def multi_record_evidence(groups: list[tuple[str, list[str]]]) -> str:
    parts = []
    record_ids: list[str] = []
    for doc_name, doc_record_ids in groups:
        joined = ",".join(doc_record_ids)
        parts.append(f"{doc_name}::{joined}")
        record_ids.extend(doc_record_ids)
    unique_ids = ",".join(record_ids)
    return f"{'; '.join(parts)} | 依据 {unique_ids} 对应故障卡与原始表述整理。"


def candidate_from_record(
    record: dict[str, object], question: str, answer: str, question_type: str
) -> Candidate:
    doc_name = str(record["doc_name"])
    record_id = str(record["record_id"])
    return Candidate(
        question=question,
        gold_answer=answer,
        evidence=single_record_evidence(doc_name, record_id),
        question_type=question_type,
        docs=(doc_name,),
        record_keys=(str(record["record_key"]),),
    )


def build_single_record_candidates(
    records: list[dict[str, object]],
) -> dict[str, list[Candidate]]:
    output: dict[str, list[Candidate]] = defaultdict(list)
    for record in records:
        equipment = str(record["equipment"])
        fault = str(record["fault"])
        causes = listify(record.get("causes"))
        actions = listify(record.get("actions"))
        precautions = listify(record.get("precautions"))
        consequences = listify(record.get("consequences"))
        key_components = listify(record.get("key_components"))

        if causes:
            output["cause_explanation"].append(
                candidate_from_record(
                    record,
                    f"{equipment}出现“{fault}”时，常见原因有哪些？",
                    bullet_sentence("常见原因包括：", causes),
                    "cause_explanation",
                )
            )
            for cause in causes[:2]:
                output["cause_to_fault"].append(
                    candidate_from_record(
                        record,
                        f"{cause}可能导致{equipment}出现什么故障？",
                        f"可能导致“{fault}”。",
                        "cause_to_fault",
                    )
                )

        if actions:
            output["repair_method"].append(
                candidate_from_record(
                    record,
                    f"{equipment}出现“{fault}”时，应如何处理？",
                    bullet_sentence("可按以下步骤处理：", actions),
                    "repair_method",
                )
            )

        if precautions:
            maintenance_items = precautions + [
                item for item in actions[:2] if item not in precautions
            ]
            output["maintenance"].append(
                candidate_from_record(
                    record,
                    f"平时维护{equipment}时，为避免“{fault}”，应注意什么？",
                    bullet_sentence("维护时应注意：", maintenance_items),
                    "maintenance",
                )
            )
            output["precaution"].append(
                candidate_from_record(
                    record,
                    f"处理{equipment}的“{fault}”时，有哪些注意事项？",
                    bullet_sentence("注意事项包括：", precautions),
                    "precaution",
                )
            )

        if consequences:
            output["consequence"].append(
                candidate_from_record(
                    record,
                    f"{equipment}出现“{fault}”后，可能造成什么后果？",
                    bullet_sentence("可能造成的后果包括：", consequences),
                    "consequence",
                )
            )

        if key_components:
            output["key_components"].append(
                candidate_from_record(
                    record,
                    f"处理{equipment}的“{fault}”时，重点涉及哪些关键部件？",
                    bullet_sentence("重点涉及的关键部件包括：", key_components),
                    "key_components",
                )
            )

    return output


def group_by_equipment(
    records: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[str(record["equipment"])].append(record)
    return grouped


def summarize_record(
    record: dict[str, object], cause_limit: int = 2, action_limit: int = 2
) -> str:
    parts: list[str] = []
    symptom = str(record["symptom"])
    if symptom:
        parts.append(f"表现偏向“{symptom.rstrip('。')}”")
    causes = listify(record.get("causes"))[:cause_limit]
    if causes:
        parts.append(f"常见原因包括{join_items(causes).rstrip('。')}")
    actions = listify(record.get("actions"))[:action_limit]
    if actions:
        parts.append(f"处理时可先{join_items(actions).rstrip('。')}")
    return "；".join(parts) + "。"


def build_pair_candidates(
    records: list[dict[str, object]], question_type: str
) -> list[Candidate]:
    grouped = group_by_equipment(records)
    output: list[Candidate] = []
    for equipment, equipment_records in grouped.items():
        if len(equipment_records) < 2:
            continue
        ordered = sorted(
            equipment_records,
            key=lambda item: (str(item["fault"]), str(item["record_id"])),
        )
        for idx, left in enumerate(ordered):
            for right in ordered[idx + 1 :]:
                if left["fault"] == right["fault"]:
                    continue
                left_fault = str(left["fault"])
                right_fault = str(right["fault"])
                left_summary = summarize_record(left)
                right_summary = summarize_record(right)
                evidence = multi_record_evidence(
                    [
                        (str(left["doc_name"]), [str(left["record_id"])]),
                        (str(right["doc_name"]), [str(right["record_id"])]),
                    ]
                )
                if question_type == "fault_distinction":
                    question = (
                        f"{equipment}中，“{left_fault}”与“{right_fault}”有什么区别？"
                    )
                    answer = (
                        f"主要区别如下："
                        f"“{left_fault}”{left_summary}"
                        f"“{right_fault}”{right_summary}"
                    )
                else:
                    question = f"排查{equipment}时，“{left_fault}”和“{right_fault}”容易混淆，应重点看哪些区别？"
                    answer = (
                        f"区分时可重点看："
                        f"“{left_fault}”{left_summary}"
                        f"“{right_fault}”{right_summary}"
                    )
                output.append(
                    Candidate(
                        question=question,
                        gold_answer=answer,
                        evidence=evidence,
                        question_type=question_type,
                        docs=(str(left["doc_name"]), str(right["doc_name"])),
                        record_keys=(str(left["record_key"]), str(right["record_key"])),
                    )
                )
    return output


def build_common_fault_candidates(records: list[dict[str, object]]) -> list[Candidate]:
    grouped = group_by_equipment(records)
    output: list[Candidate] = []
    for equipment, equipment_records in grouped.items():
        unique_faults = []
        seen_faults: set[str] = set()
        for record in sorted(
            equipment_records, key=lambda item: str(item["record_id"])
        ):
            fault = str(record["fault"])
            if fault not in seen_faults:
                unique_faults.append((fault, record))
                seen_faults.add(fault)
        if len(unique_faults) < 2:
            continue
        top_faults = unique_faults[: min(4, len(unique_faults))]
        fault_names = [fault for fault, _ in top_faults]
        doc_groups: dict[str, list[str]] = defaultdict(list)
        for _, record in top_faults:
            doc_groups[str(record["doc_name"])].append(str(record["record_id"]))
        output.append(
            Candidate(
                question=f"{equipment}有什么常见故障？",
                gold_answer=bullet_sentence("常见故障包括：", fault_names),
                evidence=multi_record_evidence(sorted(doc_groups.items())),
                question_type="common_faults",
                docs=tuple(
                    sorted({str(record["doc_name"]) for _, record in top_faults})
                ),
                record_keys=tuple(
                    str(record["record_key"]) for _, record in top_faults
                ),
            )
        )
    return output


def select_candidates(
    candidates: list[Candidate],
    target: int,
    rng: random.Random,
    selected_questions: set[str],
    doc_load: Counter[str],
    record_load: Counter[str],
    max_record_reuse: int = 3,
    max_doc_questions: int | None = None,
) -> list[Candidate]:
    ordered = candidates[:]
    rng.shuffle(ordered)
    ordered.sort(
        key=lambda item: (
            max(doc_load[doc] for doc in item.docs),
            sum(doc_load[doc] for doc in item.docs),
            max(record_load[key] for key in item.record_keys),
            len(item.docs),
            item.question,
        )
    )

    chosen: list[Candidate] = []
    for candidate in ordered:
        if len(chosen) >= target:
            break
        if candidate.question in selected_questions:
            continue
        if max_doc_questions is not None and any(
            doc_load[doc] >= max_doc_questions for doc in candidate.docs
        ):
            continue
        if any(record_load[key] >= max_record_reuse for key in candidate.record_keys):
            continue
        chosen.append(candidate)
        selected_questions.add(candidate.question)
        for doc in candidate.docs:
            doc_load[doc] += 1
        for key in candidate.record_keys:
            record_load[key] += 1

    if len(chosen) != target:
        raise RuntimeError(
            f"Unable to satisfy target={target} for category with only {len(chosen)} selected from {len(candidates)} candidates."
        )
    return chosen


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["Question", "Gold Answer", "Evidence", "Type"]
        )
        writer.writeheader()
        writer.writerows(rows)


def generate_balanced_set(
    records: list[dict[str, object]], rng: random.Random
) -> tuple[list[dict[str, str]], dict[str, object]]:
    single_candidates = build_single_record_candidates(records)
    pair_candidates = {
        "fault_distinction": build_pair_candidates(records, "fault_distinction"),
        "similar_faults": build_pair_candidates(records, "similar_faults"),
        "common_faults": build_common_fault_candidates(records),
    }

    selected_by_category: dict[str, list[Candidate]] | None = None
    doc_load: Counter[str] = Counter()
    record_load: Counter[str] = Counter()
    used_doc_cap: int | None = None

    last_error: Exception | None = None
    for doc_cap in DOC_CAP_CANDIDATES:
        try:
            current_selected_questions: set[str] = set()
            current_doc_load: Counter[str] = Counter()
            current_record_load: Counter[str] = Counter()
            current_selected_by_category: dict[str, list[Candidate]] = {}

            for category, target in CATEGORY_TARGETS.items():
                pool = (
                    pair_candidates.get(category)
                    or single_candidates.get(category)
                    or []
                )
                current_selected_by_category[category] = select_candidates(
                    pool,
                    target,
                    rng,
                    current_selected_questions,
                    current_doc_load,
                    current_record_load,
                    max_doc_questions=doc_cap,
                )

            selected_by_category = current_selected_by_category
            doc_load = current_doc_load
            record_load = current_record_load
            used_doc_cap = doc_cap
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    if selected_by_category is None:
        raise RuntimeError(
            f"Balanced set generation failed under all document cap settings: {last_error}"
        )

    rows: list[dict[str, str]] = []
    for category in CATEGORY_TARGETS:
        for candidate in selected_by_category[category]:
            rows.append(
                {
                    "Question": candidate.question,
                    "Gold Answer": candidate.gold_answer,
                    "Evidence": candidate.evidence,
                    "Type": candidate.question_type,
                }
            )

    summary = {
        "total_questions": len(rows),
        "category_counts": dict(Counter(row["Type"] for row in rows)),
        "document_counts": dict(sorted(doc_load.items())),
        "record_reuse_top10": record_load.most_common(10),
        "generation_targets": CATEGORY_TARGETS,
        "source_record_count": len(records),
        "selected_doc_cap": used_doc_cap,
    }
    return rows, summary


def main() -> None:
    args = build_parser().parse_args()
    rng = random.Random(args.seed)
    records = load_records(resolve_records_glob(args))
    rows, summary = generate_balanced_set(records, rng)

    output_csv = Path(args.output_csv)
    summary_json = Path(args.summary_json)
    write_csv(output_csv, rows)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(rows)} rows to {output_csv}")
    print(f"Wrote summary to {summary_json}")


if __name__ == "__main__":
    main()
