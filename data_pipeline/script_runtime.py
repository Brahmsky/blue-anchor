from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from minirag.datasource_resolver import ResolvedDatasource, resolve_datasource


DEFAULT_DATASOURCE_ID = "local_ship_docs"
REPO_ROOT = Path(__file__).resolve().parents[1]


def add_datasource_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--datasource-id",
        default=None,
        help="Datasource id from datasources/registry.json. Defaults to local_ship_docs when omitted.",
    )
    parser.add_argument(
        "--datasource-root",
        default=None,
        help="Path to a datasource root directory such as ./datasources/local_ship_docs.",
    )


def resolve_cli_datasource(
    args: argparse.Namespace,
    *,
    default_datasource_id: str = DEFAULT_DATASOURCE_ID,
) -> ResolvedDatasource:
    datasource_id = _clean_optional(getattr(args, "datasource_id", None))
    datasource_root = _clean_optional(getattr(args, "datasource_root", None))
    if datasource_id is None and datasource_root is None:
        datasource_id = default_datasource_id
    return resolve_datasource(
        datasource_id=datasource_id,
        datasource_root=datasource_root,
        repo_root=REPO_ROOT,
    )


def resolve_source_root(args: argparse.Namespace) -> Path:
    return resolve_cli_datasource(args).source_root.resolve()


def resolve_chunks_root(args: argparse.Namespace) -> Path:
    return (resolve_cli_datasource(args).staging_root / "chunks").resolve()


def resolve_extracted_root(args: argparse.Namespace) -> Path:
    return (resolve_cli_datasource(args).staging_root / "extracted").resolve()


def resolve_records_root(args: argparse.Namespace) -> Path:
    return (resolve_extracted_root(args) / "records").resolve()


def resolve_working_dir(args: argparse.Namespace) -> Path:
    working_dir = _clean_optional(getattr(args, "working_dir", None))
    if working_dir is not None:
        return resolve_user_path(working_dir)
    return resolve_cli_datasource(args).working_dir.resolve()


def resolve_user_path(value: str | Path, *, relative_to: Path | None = None) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    if relative_to is not None:
        base_candidate = (relative_to / candidate).resolve()
        if base_candidate.exists():
            return base_candidate
    return (Path.cwd() / candidate).resolve()


def resolve_doc_dir_argument(doc_dir: str, docs_root: Path) -> Path:
    requested = Path(doc_dir).expanduser()
    if requested.is_absolute():
        return requested.resolve()
    docs_candidate = (docs_root / requested).resolve()
    if docs_candidate.exists():
        return docs_candidate
    return (Path.cwd() / requested).resolve()


def discover_doc_dirs(
    root: Path,
    required_relative_path: str,
    *,
    exclude_names: Iterable[str] | None = None,
) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Root does not exist: {root}")

    excluded = {name.strip() for name in (exclude_names or []) if str(name).strip()}
    doc_dirs: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in excluded:
            continue
        if (child / required_relative_path).exists():
            doc_dirs.append(child)
    return doc_dirs


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
