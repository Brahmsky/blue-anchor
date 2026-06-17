from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DatasourceResolutionError(ValueError):
    """Raised when datasource selectors cannot be resolved consistently."""


@dataclass(frozen=True)
class ResolvedDatasource:
    id: str
    root: Path
    source_root: Path
    staging_root: Path
    output_root: Path
    working_dir: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "root": str(self.root),
            "source_root": str(self.source_root),
            "staging_root": str(self.staging_root),
            "output_root": str(self.output_root),
            "working_dir": str(self.working_dir),
        }


def resolve_datasource(
    *,
    datasource_id: str | None = None,
    datasource_root: str | Path | None = None,
    input_dir: str | Path | None = None,
    outputs_root: str | Path | None = None,
    working_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> ResolvedDatasource:
    resolved_repo_root = _resolve_repo_root(repo_root)
    catalog = _load_catalog(resolved_repo_root, registry_path)
    explicit_root_datasource: ResolvedDatasource | None = None
    ordered_selectors = [
        ("datasource_id", datasource_id),
        ("datasource_root", datasource_root),
        ("input_dir", input_dir),
        ("outputs_root", outputs_root),
        ("working_dir", working_dir),
    ]

    if not any(value is not None for _, value in ordered_selectors):
        raise DatasourceResolutionError(
            "No datasource selector provided; expected datasource_id, datasource_root, or a legacy path selector."
        )

    matches: list[tuple[str, str]] = []
    unresolved: list[tuple[str, str]] = []

    if datasource_id is not None:
        selector_id = str(datasource_id).strip()
        if not selector_id:
            raise DatasourceResolutionError("datasource_id cannot be empty.")
        if selector_id in catalog:
            matches.append(("datasource_id", selector_id))
        else:
            unresolved.append(("datasource_id", selector_id))

    if datasource_root is not None:
        selector_path = _resolve_path(resolved_repo_root, datasource_root)
        matched = _match_exact_root(catalog, selector_path)
        if matched is None:
            try:
                explicit_root_datasource = _build_resolved_datasource(selector_path)
            except DatasourceResolutionError:
                unresolved.append(("datasource_root", str(selector_path)))
            else:
                matches.append(("datasource_root", explicit_root_datasource.id))
        else:
            matches.append(("datasource_root", matched.id))

    for selector_name, selector_value in (
        ("input_dir", input_dir),
        ("outputs_root", outputs_root),
        ("working_dir", working_dir),
    ):
        if selector_value is None:
            continue
        selector_path = _resolve_path(resolved_repo_root, selector_value)
        matched = _match_legacy_selector(catalog, selector_name, selector_path)
        if matched is None:
            unresolved.append((selector_name, str(selector_path)))
        else:
            matches.append((selector_name, matched.id))

    matched_ids = {matched_id for _, matched_id in matches}
    if len(matched_ids) > 1:
        raise DatasourceResolutionError(_format_conflict(matches, unresolved))

    if unresolved and matches:
        raise DatasourceResolutionError(_format_conflict(matches, unresolved))

    if unresolved and not matches:
        selector_name, selector_value = unresolved[0]
        raise DatasourceResolutionError(
            f"No registered datasource matches {selector_name}={selector_value!r}."
        )

    if (
        explicit_root_datasource is not None
        and datasource_id is not None
        and datasource_id.strip() in catalog
        and catalog[datasource_id.strip()].root != explicit_root_datasource.root
    ):
        raise DatasourceResolutionError(
            _format_conflict(
                matches,
                [("datasource_root", str(explicit_root_datasource.root))],
            )
        )

    selected_id = _select_id_by_precedence(ordered_selectors, matches)
    if (
        explicit_root_datasource is not None
        and selected_id == explicit_root_datasource.id
    ):
        datasource = explicit_root_datasource
    else:
        datasource = catalog[selected_id]

    if datasource_id is None and datasource_root is None:
        warnings.warn(
            "Resolving datasources from legacy input_dir/outputs_root/working_dir is deprecated; use datasource_id or datasource_root instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    return datasource


def _resolve_repo_root(repo_root: str | Path | None) -> Path:
    base = Path(__file__).resolve().parents[1]
    if repo_root is None:
        return base
    return Path(repo_root).expanduser().resolve()


def _load_catalog(
    repo_root: Path,
    registry_path: str | Path | None,
) -> dict[str, ResolvedDatasource]:
    resolved_registry_path = _resolve_registry_path(repo_root, registry_path)
    try:
        raw_registry = json.loads(resolved_registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatasourceResolutionError(
            f"Datasource registry not found at {resolved_registry_path}."
        ) from exc
    except json.JSONDecodeError as exc:
        raise DatasourceResolutionError(
            f"Datasource registry at {resolved_registry_path} is invalid JSON: {exc}"
        ) from exc

    if not isinstance(raw_registry, list):
        raise DatasourceResolutionError(
            f"Datasource registry at {resolved_registry_path} must be a JSON array."
        )

    catalog: dict[str, ResolvedDatasource] = {}
    for index, entry in enumerate(raw_registry):
        if not isinstance(entry, dict):
            raise DatasourceResolutionError(
                f"Datasource registry entry #{index} must be an object."
            )
        entry_id = entry.get("id")
        entry_root = entry.get("path")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise DatasourceResolutionError(
                f"Datasource registry entry #{index} is missing a valid 'id'."
            )
        if not isinstance(entry_root, str) or not entry_root.strip():
            raise DatasourceResolutionError(
                f"Datasource registry entry {entry_id!r} is missing a valid 'path'."
            )
        if entry_id in catalog:
            raise DatasourceResolutionError(
                f"Datasource registry contains duplicate id {entry_id!r}."
            )

        root = _resolve_path(repo_root, entry_root)
        catalog[entry_id] = _build_resolved_datasource(root, expected_id=entry_id)

    if not catalog:
        raise DatasourceResolutionError(
            f"Datasource registry at {resolved_registry_path} is empty."
        )

    return catalog


def _resolve_registry_path(repo_root: Path, registry_path: str | Path | None) -> Path:
    if registry_path is None:
        return (repo_root / "datasources" / "registry.json").resolve()
    return _resolve_path(repo_root, registry_path)


def _resolve_path(base: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _resolve_config_path(datasource_root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DatasourceResolutionError(
            f"Datasource config under {datasource_root} contains a non-string path value."
        )
    return _resolve_path(datasource_root, value)


def _load_minimal_yaml(file_path: Path) -> dict[str, Any]:
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise DatasourceResolutionError(
            f"Datasource config not found at {file_path}."
        ) from exc

    root: dict[str, Any] = {}
    stack: list[dict[str, Any]] = [root]

    for line_number, raw_line in enumerate(lines, start=1):
        stripped_line = raw_line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2 != 0:
            raise DatasourceResolutionError(
                f"Unsupported indentation in {file_path}:{line_number}; expected 2-space YAML nesting."
            )
        level = indent // 2
        if level >= len(stack):
            raise DatasourceResolutionError(
                f"Invalid nesting in {file_path}:{line_number}."
            )
        stack = stack[: level + 1]
        container = stack[-1]

        key, separator, raw_value = stripped_line.partition(":")
        if separator == "":
            raise DatasourceResolutionError(
                f"Invalid YAML mapping in {file_path}:{line_number}."
            )

        key = key.strip()
        value = raw_value.strip()
        if not key:
            raise DatasourceResolutionError(
                f"Empty YAML key in {file_path}:{line_number}."
            )

        if not value:
            child: dict[str, Any] = {}
            container[key] = child
            stack.append(child)
            continue

        if (
            value.startswith(("'", '"'))
            and value.endswith(("'", '"'))
            and len(value) >= 2
        ):
            value = value[1:-1]
        container[key] = value

    return root


def _build_resolved_datasource(
    datasource_root: Path,
    *,
    expected_id: str | None = None,
) -> ResolvedDatasource:
    root = datasource_root.expanduser().resolve()
    yaml_payload = _load_minimal_yaml(root / "datasource.yaml")
    yaml_id = str(yaml_payload.get("id") or "").strip()
    resolved_id = expected_id or yaml_id or root.name
    if expected_id is not None and yaml_id and yaml_id != expected_id:
        raise DatasourceResolutionError(
            f"Datasource config id mismatch for {expected_id!r}: datasource.yaml declares {yaml_id!r}."
        )

    try:
        paths_payload = yaml_payload["paths"]
        source_root = _resolve_config_path(root, paths_payload["source_root"])
        staging_root = _resolve_config_path(root, paths_payload["staging_root"])
        output_root = _resolve_config_path(root, paths_payload["output_root"])
    except KeyError as exc:
        raise DatasourceResolutionError(
            f"Datasource config at {root / 'datasource.yaml'} is missing paths.{exc.args[0]}."
        ) from exc

    return ResolvedDatasource(
        id=resolved_id,
        root=root,
        source_root=source_root,
        staging_root=staging_root,
        output_root=output_root,
        working_dir=(output_root / "graph" / "workdir").resolve(),
    )


def _match_exact_root(
    catalog: dict[str, ResolvedDatasource],
    selector_path: Path,
) -> ResolvedDatasource | None:
    for datasource in catalog.values():
        if selector_path == datasource.root:
            return datasource
    return None


def _match_legacy_selector(
    catalog: dict[str, ResolvedDatasource],
    selector_name: str,
    selector_path: Path,
) -> ResolvedDatasource | None:
    attribute_name = {
        "input_dir": "source_root",
        "outputs_root": "output_root",
        "working_dir": "working_dir",
    }[selector_name]
    matches = [
        datasource
        for datasource in catalog.values()
        if any(
            _path_matches_selector(selector_path, candidate_root)
            for candidate_root in _legacy_match_roots(
                datasource=datasource,
                selector_name=selector_name,
                attribute_name=attribute_name,
            )
        )
    ]
    if len(matches) > 1:
        raise DatasourceResolutionError(
            f"Datasource selector conflict: {selector_name}={str(selector_path)!r} matches multiple registered datasources."
        )
    return matches[0] if matches else None


def _legacy_match_roots(
    *,
    datasource: ResolvedDatasource,
    selector_name: str,
    attribute_name: str,
) -> tuple[Path, ...]:
    repo_root = datasource.root.parents[1]
    roots = [getattr(datasource, attribute_name)]

    if selector_name == "input_dir":
        roots.extend(
            [
                (repo_root / "data_pipeline" / "inputs").resolve(),
                (repo_root / "inputs").resolve(),
            ]
        )
    elif selector_name == "outputs_root":
        roots.extend(
            [
                (repo_root / "data_pipeline" / "outputs").resolve(),
                (repo_root / "output").resolve(),
            ]
        )
    elif selector_name == "working_dir":
        roots.extend(
            [
                (
                    repo_root
                    / "data_pipeline"
                    / "outputs"
                    / "all_docs_demo_query_workdir"
                ).resolve(),
                (repo_root / "output" / "all_docs_demo_query_workdir").resolve(),
            ]
        )

    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root not in seen:
            deduped.append(root)
            seen.add(root)
    return tuple(deduped)


def _path_matches_selector(selector_path: Path, target_root: Path) -> bool:
    return selector_path == target_root or selector_path.is_relative_to(target_root)


def _select_id_by_precedence(
    ordered_selectors: list[tuple[str, object | None]],
    matches: list[tuple[str, str]],
) -> str:
    match_lookup = dict(matches)
    for selector_name, selector_value in ordered_selectors:
        if selector_value is not None and selector_name in match_lookup:
            return match_lookup[selector_name]
    raise DatasourceResolutionError("Datasource resolution failed unexpectedly.")


def _format_conflict(
    matches: list[tuple[str, str]],
    unresolved: list[tuple[str, str]],
) -> str:
    match_text = ", ".join(
        f"{selector_name} -> {matched_id!r}" for selector_name, matched_id in matches
    )
    unresolved_text = ", ".join(
        f"{selector_name} -> {selector_value!r} (unresolved)"
        for selector_name, selector_value in unresolved
    )
    details = "; ".join(part for part in (match_text, unresolved_text) if part)
    return f"Datasource selector conflict: {details}"
