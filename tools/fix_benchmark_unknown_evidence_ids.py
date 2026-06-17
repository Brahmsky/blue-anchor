import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


ANSWER_FIELD_BY_TYPE = {
    "cause_explanation": "causes",
    "repair_method": "actions",
    "precautions": "precautions",
    "key_components": "key_components",
    "consequences": "consequences",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replace unknown benchmark evidence ids with matched record ids."
    )
    parser.add_argument(
        "--csv",
        default="datasources/local_ship_docs/outputs/benchmark/query_set.csv",
        help="Benchmark CSV path to rewrite in place.",
    )
    parser.add_argument(
        "--json",
        default="datasources/local_ship_docs/outputs/benchmark/query_set.json",
        help="Benchmark JSON path to rewrite after CSV is updated.",
    )
    parser.add_argument(
        "--records-root",
        default="datasources/local_ship_docs/staging/extracted/records",
        help="Root directory containing diagnostic_records_llm.json files.",
    )
    return parser


def normalize_joined_answer(record: dict, question_type: str) -> str:
    answer_field = ANSWER_FIELD_BY_TYPE.get(question_type, "")
    values = record.get(answer_field) or []
    return "；".join(str(item).strip() for item in values if str(item).strip())


def load_records(records_root: Path) -> dict[str, list[dict]]:
    records_by_doc: dict[str, list[dict]] = defaultdict(list)
    for record_path in sorted(records_root.rglob("diagnostic_records_llm.json")):
        doc_name = record_path.parent.name
        payload = json.load(record_path.open("r", encoding="utf-8"))
        for record in payload.get("records", []):
            records_by_doc[doc_name].append(record)
    return records_by_doc


def resolve_record_id(row: dict[str, str], records_by_doc: dict[str, list[dict]]) -> str | None:
    evidence = (row.get("Evidence") or "").strip()
    doc_name = evidence.split("|", 1)[0].split("::unknown_id", 1)[0].strip()
    candidates = [
        record
        for record in records_by_doc.get(doc_name, [])
        if normalize_joined_answer(record, (row.get("Type") or "").strip())
        == (row.get("Gold Answer") or "").strip()
    ]
    if len(candidates) != 1:
        return None
    record_id = str(candidates[0].get("record_id") or candidates[0].get("id") or "").strip()
    return record_id or None


def rewrite_json_from_rows(rows: list[dict[str, str]], output_path: Path) -> None:
    normalized_rows = [
        {
            "Question": (row.get("Question") or "").strip(),
            "Gold Answer": (row.get("Gold Answer") or "").strip(),
            "Evidence": (row.get("Evidence") or "").strip(),
            "Type": (row.get("Type") or "").strip(),
        }
        for row in rows
    ]
    with output_path.open("w", encoding="utf-8-sig") as handle:
        json.dump(normalized_rows, handle, ensure_ascii=False, indent=2)


def main() -> None:
    args = build_parser().parse_args()
    csv_path = Path(args.csv)
    json_path = Path(args.json)
    records_root = Path(args.records_root)

    records_by_doc = load_records(records_root)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0].keys()) if rows else ["Question", "Gold Answer", "Evidence", "Type"]

    unresolved_rows: list[tuple[int, str, str]] = []
    updated_count = 0
    for index, row in enumerate(rows, start=1):
        evidence = (row.get("Evidence") or "").strip()
        if "::unknown_id" not in evidence:
            continue
        record_id = resolve_record_id(row, records_by_doc)
        if record_id is None:
            unresolved_rows.append(
                (index, row.get("Question", "").strip(), evidence)
            )
            continue
        row["Evidence"] = evidence.replace("::unknown_id", f"::{record_id}", 1)
        updated_count += 1

    if unresolved_rows:
        print("Unresolved rows:")
        for index, question, evidence in unresolved_rows:
            print(f"- row={index} evidence={evidence} question={question}")
        raise SystemExit(1)

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    rewrite_json_from_rows(rows, json_path)
    print(f"Updated {updated_count} benchmark rows in {csv_path}")
    print(f"Rewrote {json_path}")


if __name__ == "__main__":
    main()
