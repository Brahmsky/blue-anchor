import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "minirag" / "faultcase_alias_router.py"
TOOL_MODULE_PATH = ROOT / "tools" / "import_equipment_aliases.py"


def _load_module(name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


module = _load_module("faultcase_alias_router", MODULE_PATH)
tool_module = _load_module("import_equipment_aliases_tool", TOOL_MODULE_PATH)

FaultcaseAliasStore = module.FaultcaseAliasStore
route_faultcase_query = module.route_faultcase_query
import_equipment_aliases = module.import_equipment_aliases


def _write_diagnostic_records(records_root: Path, *equipment_names: str) -> None:
    records_dir = records_root / "demo-doc" / "records"
    records_dir.mkdir(parents=True)
    payload = {
        "records": [{"equipment": equipment_name} for equipment_name in equipment_names]
    }
    (records_dir / "diagnostic_records_llm.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_datasource_config(datasource_root: Path, datasource_id: str) -> None:
    (datasource_root / "source" / "raw").mkdir(parents=True)
    (datasource_root / "outputs" / "graph" / "workdir").mkdir(parents=True)
    (datasource_root / "datasource.yaml").write_text(
        "\n".join(
            [
                f"id: {datasource_id}",
                "name: Test datasource",
                "type: local_fs",
                "paths:",
                "  source_root: ./source/raw",
                "  staging_root: ./staging",
                "  output_root: ./outputs",
                "pipeline:",
                "  profile: ship_faultcase_graph",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_alias_store_crud_roundtrip(tmp_path: Path):
    store = FaultcaseAliasStore(str(tmp_path), datasource_id="datasource-demo")
    created = store.create_alias(
        datasource_id="datasource-demo",
        canonical_name="主机系统无法启动",
        entity_type="FAULTCASE",
        alias="主机打不着火",
        enabled=True,
        reviewed=False,
    )

    assert created["datasource_id"] == "datasource-demo"
    assert created["alias_norm"] == "主机打不着火"
    assert store.get_stats()["total"] == 1
    assert store.get_stats()["datasource_id"] == "datasource-demo"

    updated = store.update_alias(created["id"], reviewed=True, alias="主机无法点火")
    assert updated["reviewed"] is True
    assert updated["datasource_id"] == "datasource-demo"
    assert updated["alias_norm"] == "主机无法点火"

    listed = store.list_aliases(datasource_id="datasource-demo", query="点火")
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]
    assert listed[0]["datasource_id"] == "datasource-demo"

    store.delete_alias(created["id"])
    assert store.list_aliases() == []


def test_router_prefers_component_alias(tmp_path: Path):
    store = FaultcaseAliasStore(str(tmp_path), datasource_id="datasource-demo")
    store.create_alias(
        datasource_id="datasource-demo",
        canonical_name="燃油电磁阀",
        entity_type="COMPONENT",
        alias="电磁阀",
        enabled=True,
        reviewed=True,
    )

    routed = route_faultcase_query("电磁阀怎么检查", store)

    assert routed["datasource_id"] == "datasource-demo"
    assert routed["intent"] == "component"
    assert routed["preferred_entity_types"][0] == "COMPONENT"
    assert routed["alias_hits"][0]["canonical_name"] == "燃油电磁阀"


def test_import_equipment_aliases_creates_short_aliases(tmp_path: Path):
    datasource_id = "fixture-datasource"
    outputs_root = tmp_path / "datasource-root" / "staging" / "extracted" / "records"
    working_dir = tmp_path / "custom-workdir"
    _write_diagnostic_records(outputs_root, "船舶柴油机", "船用发电机", "柴油机")

    result = import_equipment_aliases(
        working_dir=str(working_dir),
        outputs_root=str(outputs_root),
        datasource_id=datasource_id,
        enabled=True,
        reviewed=True,
        reset=False,
    )

    persisted_events = [
        json.loads(line)
        for line in (working_dir / "faultcase_aliases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    reloaded_store = FaultcaseAliasStore(str(working_dir), datasource_id=datasource_id)

    assert result["created_count"] == 2
    assert result["stats"]["total"] == 2
    assert result["stats"]["datasource_id"] == datasource_id
    assert {event["datasource_id"] for event in persisted_events} == {datasource_id}
    assert {item["alias"] for item in reloaded_store.list_aliases()} == {
        "柴油机",
        "发电机",
    }


def test_import_equipment_aliases_tool_uses_resolved_datasource_id(
    tmp_path: Path, monkeypatch, capsys
):
    datasource_root = tmp_path / "datasource-root"
    datasource_id = "fixture-stable-id"
    working_dir = tmp_path / "alias-workdir"
    _write_datasource_config(datasource_root, datasource_id)
    _write_diagnostic_records(
        datasource_root / "staging" / "extracted" / "records",
        "船舶柴油机",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_equipment_aliases.py",
            "--datasource-root",
            str(datasource_root),
            "--working-dir",
            str(working_dir),
        ],
    )

    tool_module.main()

    result = json.loads(capsys.readouterr().out)
    persisted_events = [
        json.loads(line)
        for line in (working_dir / "faultcase_aliases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    reloaded_store = FaultcaseAliasStore(str(working_dir), datasource_id=datasource_id)

    assert result["stats"]["datasource_id"] == datasource_id
    assert {event["datasource_id"] for event in persisted_events} == {datasource_id}
    assert [item["alias"] for item in reloaded_store.list_aliases()] == ["柴油机"]
