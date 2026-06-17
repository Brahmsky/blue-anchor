# pyright: reportMissingImports=false

from __future__ import annotations

from pathlib import Path

import pytest

from minirag.datasource_resolver import (
    DatasourceResolutionError,
    resolve_datasource,
)


def _seed_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    datasource_root = repo_root / "datasources" / "local_ship_docs"
    (repo_root / "datasources").mkdir(parents=True)
    datasource_root.mkdir(parents=True)
    (repo_root / "data_pipeline" / "inputs").mkdir(parents=True)
    (repo_root / "data_pipeline" / "outputs" / "all_docs_demo_query_workdir").mkdir(
        parents=True
    )
    (repo_root / "output" / "all_docs_demo_query_workdir").mkdir(parents=True)
    (repo_root / "inputs").mkdir(parents=True)
    (repo_root / "datasources" / "registry.json").write_text(
        """
[
  {
    "id": "local_ship_docs",
    "name": "船舶维修资料库",
    "type": "local_fs",
    "path": "./datasources/local_ship_docs"
  }
]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (datasource_root / "datasource.yaml").write_text(
        """
id: local_ship_docs
name: 船舶维修资料库
type: local_fs
paths:
  source_root: ./source/raw
  staging_root: ./staging
  output_root: ./outputs
pipeline:
  profile: ship_faultcase_graph
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return repo_root


def test_canonical_id_and_root_resolve_to_same_datasource(tmp_path: Path):
    repo_root = _seed_repo(tmp_path)

    by_id = resolve_datasource(datasource_id="local_ship_docs", repo_root=repo_root)
    by_root = resolve_datasource(
        datasource_root="datasources/local_ship_docs",
        repo_root=repo_root,
    )

    expected = {
        "id": "local_ship_docs",
        "root": str((repo_root / "datasources" / "local_ship_docs").resolve()),
        "source_root": str(
            (repo_root / "datasources" / "local_ship_docs" / "source" / "raw").resolve()
        ),
        "staging_root": str(
            (repo_root / "datasources" / "local_ship_docs" / "staging").resolve()
        ),
        "output_root": str(
            (repo_root / "datasources" / "local_ship_docs" / "outputs").resolve()
        ),
        "working_dir": str(
            (
                repo_root
                / "datasources"
                / "local_ship_docs"
                / "outputs"
                / "graph"
                / "workdir"
            ).resolve()
        ),
    }

    assert by_id.to_dict() == expected
    assert by_root.to_dict() == expected


# Deprecated compatibility selector coverage: these legacy roots must still map
# to the canonical datasource during migration.
@pytest.mark.parametrize(
    ("selector_name", "selector_value"),
    [
        ("input_dir", "datasources/local_ship_docs/source/raw"),
        ("outputs_root", "datasources/local_ship_docs/outputs"),
        ("working_dir", "datasources/local_ship_docs/outputs/graph/workdir"),
        ("input_dir", "data_pipeline/inputs"),
        ("outputs_root", "data_pipeline/outputs"),
        ("working_dir", "data_pipeline/outputs/all_docs_demo_query_workdir"),
        ("input_dir", "inputs"),
        ("outputs_root", "output"),
        ("working_dir", "output/all_docs_demo_query_workdir"),
    ],
)
def test_legacy_selectors_map_to_canonical_datasource_with_warning(
    tmp_path: Path,
    selector_name: str,
    selector_value: str,
):
    repo_root = _seed_repo(tmp_path)

    with pytest.warns(DeprecationWarning):
        resolved = resolve_datasource(
            repo_root=repo_root,
            **{selector_name: selector_value},
        )

    assert resolved.id == "local_ship_docs"
    assert (
        resolved.working_dir
        == (
            repo_root
            / "datasources"
            / "local_ship_docs"
            / "outputs"
            / "graph"
            / "workdir"
        ).resolve()
    )


def test_conflicting_selectors_raise_explicit_conflict(tmp_path: Path):
    repo_root = _seed_repo(tmp_path)
    mismatched_root = tmp_path / "other_datasource"
    mismatched_root.mkdir()

    with pytest.raises(DatasourceResolutionError, match="conflict"):
        resolve_datasource(
            datasource_id="local_ship_docs",
            datasource_root=mismatched_root,
            repo_root=repo_root,
        )


def test_missing_datasource_is_rejected(tmp_path: Path):
    repo_root = _seed_repo(tmp_path)

    with pytest.raises(DatasourceResolutionError, match="No registered datasource"):
        resolve_datasource(datasource_id="missing", repo_root=repo_root)
