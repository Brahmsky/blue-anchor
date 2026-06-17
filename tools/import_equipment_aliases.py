from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_pipeline.script_runtime import (
    add_datasource_arguments,
    resolve_cli_datasource,
    resolve_records_root,
    resolve_working_dir,
)
from minirag.faultcase_alias_router import import_equipment_aliases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import EQUIPMENT aliases from datasource staging/extracted/records diagnostic_records_llm.json files."
    )
    add_datasource_arguments(parser)
    parser.add_argument(
        "--working-dir",
        default=None,
        help="Alias store working directory. Defaults to the datasource outputs/graph/workdir.",
    )
    parser.add_argument(
        "--reviewed",
        action="store_true",
        help="Mark imported aliases as reviewed",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing alias store file before importing",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    datasource = resolve_cli_datasource(args)
    working_dir = resolve_working_dir(args)
    records_root = resolve_records_root(args)
    result = import_equipment_aliases(
        working_dir=str(working_dir),
        outputs_root=str(records_root),
        datasource_id=datasource.id,
        enabled=True,
        reviewed=args.reviewed,
        reset=args.reset,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
