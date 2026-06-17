import argparse
import csv
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert benchmark query CSV to JSON."
    )
    parser.add_argument(
        "--input",
        default="datasources/local_ship_docs/outputs/benchmark/query_set.csv",
        help="Path to the source CSV file.",
    )
    parser.add_argument(
        "--output",
        default="datasources/local_ship_docs/outputs/benchmark/query_set.json",
        help="Path to the output JSON file.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    normalized_rows = []
    for row in rows:
        normalized_rows.append(
            {
                "Question": (row.get("Question") or "").strip(),
                "Gold Answer": (row.get("Gold Answer") or "").strip(),
                "Evidence": (row.get("Evidence") or "").strip(),
                "Type": (row.get("Type") or "").strip(),
            }
        )

    with output_path.open("w", encoding="utf-8-sig") as f:
        json.dump(normalized_rows, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(normalized_rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
