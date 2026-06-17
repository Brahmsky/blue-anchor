from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SUPPORTED_ALIAS_ENTITY_TYPES = {"EQUIPMENT", "FAULTCASE", "COMPONENT"}

FAULTCASE_QUERY_INTENT_RULES = {
    "procedure": ["怎么", "如何", "排查", "检查", "处理", "解决", "步骤", "维修"],
    "cause": ["原因", "为何", "为什么", "导致", "引起"],
    "caution": ["注意", "注意事项", "避免", "预防"],
    "component": [
        "部件",
        "组件",
        "阀",
        "泵",
        "滤器",
        "滤芯",
        "传感器",
        "继电器",
        "开关",
        "轴承",
        "线圈",
        "喷油器",
        "电磁阀",
    ],
    "faultcase": ["故障", "异常", "报警", "报错", "现象", "问题", "失效"],
}

EQUIPMENT_ALIAS_PREFIXES = ("船舶", "船用")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_alias_text(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[（(].*?[）)]", "", value)
    value = re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)
    return value.lower()


def normalize_entity_type(entity_type: str) -> str:
    normalized = (entity_type or "").strip().upper()
    if normalized not in SUPPORTED_ALIAS_ENTITY_TYPES:
        raise ValueError(f"unsupported entity_type: {entity_type}")
    return normalized


def _derive_equipment_aliases(canonical_name: str) -> list[str]:
    canonical = str(canonical_name or "").strip()
    if not canonical:
        return []

    aliases: list[str] = []
    for prefix in EQUIPMENT_ALIAS_PREFIXES:
        if canonical.startswith(prefix):
            candidate = canonical[len(prefix) :].strip()
            if candidate and candidate != canonical:
                aliases.append(candidate)

    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        normalized = normalize_alias_text(alias)
        if not normalized or normalized == normalize_alias_text(canonical):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(alias)
    return deduped


def import_equipment_aliases(
    working_dir: str,
    outputs_root: str,
    *,
    datasource_id: str | None = None,
    enabled: bool = True,
    reviewed: bool = False,
    reset: bool = False,
) -> dict[str, Any]:
    outputs_path = Path(outputs_root)
    store = FaultcaseAliasStore(working_dir, datasource_id=datasource_id)

    if reset and store.file_path.exists():
        store.file_path.unlink()
        store = FaultcaseAliasStore(working_dir, datasource_id=datasource_id)

    scanned_files = 0
    scanned_records = 0
    created_count = 0
    skipped_count = 0

    for json_file in sorted(outputs_path.rglob("diagnostic_records_llm.json")):
        scanned_files += 1
        with json_file.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)

        for record in payload.get("records", []):
            scanned_records += 1
            canonical_name = str(record.get("equipment") or "").strip()
            if not canonical_name:
                continue

            aliases = _derive_equipment_aliases(canonical_name)
            if not aliases:
                skipped_count += 1
                continue

            for alias in aliases:
                try:
                    store.create_alias(
                        canonical_name=canonical_name,
                        entity_type="EQUIPMENT",
                        alias=alias,
                        enabled=enabled,
                        reviewed=reviewed,
                    )
                    created_count += 1
                except ValueError:
                    skipped_count += 1

    return {
        "working_dir": str(Path(working_dir).resolve()),
        "outputs_root": str(outputs_path.resolve()),
        "scanned_files": scanned_files,
        "scanned_records": scanned_records,
        "created_count": created_count,
        "skipped_count": skipped_count,
        "stats": store.get_stats(),
    }


def infer_faultcase_query_intent(query: str) -> dict[str, Any]:
    stripped_query = (query or "").strip()
    matched_rule = "general"

    for rule_name in ("component", "procedure", "cause", "caution", "faultcase"):
        if any(
            keyword in stripped_query
            for keyword in FAULTCASE_QUERY_INTENT_RULES[rule_name]
        ):
            matched_rule = rule_name
            break

    if matched_rule == "component":
        preferred_entity_types = ["COMPONENT", "EQUIPMENT", "FAULTCASE"]
    elif matched_rule in {"procedure", "cause", "caution", "faultcase"}:
        preferred_entity_types = ["FAULTCASE", "EQUIPMENT", "COMPONENT"]
    else:
        preferred_entity_types = ["EQUIPMENT", "FAULTCASE", "COMPONENT"]

    return {
        "intent": matched_rule,
        "preferred_entity_types": preferred_entity_types,
    }


@dataclass
class FaultcaseAliasRecord:
    datasource_id: str
    id: str
    canonical_name: str
    entity_type: str
    alias: str
    alias_norm: str
    enabled: bool
    reviewed: bool
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FaultcaseAliasStore:
    def __init__(
        self,
        working_dir: str,
        file_name: str = "faultcase_aliases.jsonl",
        datasource_id: str | None = None,
    ):
        self.file_path = Path(working_dir) / file_name
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.datasource_id = (
            datasource_id or str(self.file_path.parent.resolve())
        ).strip()
        self._records: dict[str, FaultcaseAliasRecord] = {}
        self._load()

    def _load(self) -> None:
        self._records = {}
        if not self.file_path.exists():
            return

        with self.file_path.open("r", encoding="utf-8") as file_obj:
            for raw_line in file_obj:
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                record_id = payload["id"]
                if payload.get("deleted"):
                    self._records.pop(record_id, None)
                    continue
                self._records[record_id] = self._build_record(payload)

    def _build_record(self, payload: dict[str, Any]) -> FaultcaseAliasRecord:
        canonical_name = (payload.get("canonical_name") or "").strip()
        alias = (payload.get("alias") or "").strip()
        if not canonical_name:
            raise ValueError("canonical_name is required")
        if not alias:
            raise ValueError("alias is required")

        entity_type = normalize_entity_type(payload.get("entity_type", ""))
        alias_norm = normalize_alias_text(alias)
        if not alias_norm:
            raise ValueError("alias_norm cannot be empty")

        datasource_id = str(payload.get("datasource_id") or self.datasource_id).strip()
        if not datasource_id:
            raise ValueError("datasource_id is required")
        if datasource_id != self.datasource_id:
            raise ValueError("datasource_id does not match alias store scope")

        now = utc_now_iso()
        return FaultcaseAliasRecord(
            datasource_id=datasource_id,
            id=payload.get("id") or uuid.uuid4().hex,
            canonical_name=canonical_name,
            entity_type=entity_type,
            alias=alias,
            alias_norm=alias_norm,
            enabled=bool(payload.get("enabled", True)),
            reviewed=bool(payload.get("reviewed", False)),
            created_at=payload.get("created_at") or now,
            updated_at=payload.get("updated_at") or now,
        )

    def _append_event(self, payload: dict[str, Any]) -> None:
        with self.file_path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _ensure_unique(
        self,
        candidate: FaultcaseAliasRecord,
        *,
        exclude_id: Optional[str] = None,
    ) -> None:
        for record in self._records.values():
            if exclude_id and record.id == exclude_id:
                continue
            if (
                record.entity_type == candidate.entity_type
                and record.canonical_name == candidate.canonical_name
                and record.alias_norm == candidate.alias_norm
            ):
                raise ValueError("alias already exists for the same canonical entity")

    def list_aliases(
        self,
        *,
        datasource_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        enabled: Optional[bool] = None,
        reviewed: Optional[bool] = None,
        query: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        normalized_datasource_id = (datasource_id or self.datasource_id).strip()
        if normalized_datasource_id != self.datasource_id:
            return []

        normalized_query = normalize_alias_text(query or "")
        filtered: list[FaultcaseAliasRecord] = []
        normalized_entity_type = entity_type.strip().upper() if entity_type else None

        for record in self._records.values():
            if normalized_entity_type and record.entity_type != normalized_entity_type:
                continue
            if enabled is not None and record.enabled != enabled:
                continue
            if reviewed is not None and record.reviewed != reviewed:
                continue
            if normalized_query:
                search_text = (
                    f"{record.alias_norm}{normalize_alias_text(record.canonical_name)}"
                )
                if normalized_query not in search_text:
                    continue
            filtered.append(record)

        filtered.sort(
            key=lambda item: (
                item.entity_type,
                item.canonical_name,
                item.alias_norm,
                item.updated_at,
            )
        )
        return [record.to_dict() for record in filtered]

    def create_alias(
        self,
        *,
        datasource_id: Optional[str] = None,
        canonical_name: str,
        entity_type: str,
        alias: str,
        enabled: bool = True,
        reviewed: bool = False,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        normalized_datasource_id = (datasource_id or self.datasource_id).strip()
        record = self._build_record(
            {
                "id": uuid.uuid4().hex,
                "datasource_id": normalized_datasource_id,
                "canonical_name": canonical_name,
                "entity_type": entity_type,
                "alias": alias,
                "enabled": enabled,
                "reviewed": reviewed,
                "created_at": now,
                "updated_at": now,
            }
        )
        self._ensure_unique(record)
        self._records[record.id] = record
        self._append_event(record.to_dict())
        return record.to_dict()

    def update_alias(self, alias_id: str, **updates: Any) -> dict[str, Any]:
        existing = self._records.get(alias_id)
        if not existing:
            raise KeyError(alias_id)

        payload = existing.to_dict()
        payload.update(
            {key: value for key, value in updates.items() if value is not None}
        )
        payload["datasource_id"] = self.datasource_id
        payload["id"] = alias_id
        payload["updated_at"] = utc_now_iso()
        record = self._build_record(payload)
        self._ensure_unique(record, exclude_id=alias_id)
        self._records[alias_id] = record
        self._append_event(record.to_dict())
        return record.to_dict()

    def delete_alias(self, alias_id: str) -> None:
        if alias_id not in self._records:
            raise KeyError(alias_id)
        self._records.pop(alias_id, None)
        self._append_event(
            {
                "id": alias_id,
                "deleted": True,
                "updated_at": utc_now_iso(),
            }
        )

    def get_alias(self, alias_id: str) -> Optional[dict[str, Any]]:
        record = self._records.get(alias_id)
        return record.to_dict() if record else None

    def match_query(
        self,
        query: str,
        *,
        preferred_entity_types: Optional[list[str]] = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        normalized_query = normalize_alias_text(query)
        if not normalized_query:
            return []

        type_priority = {
            entity_type: len(preferred_entity_types or []) - index
            for index, entity_type in enumerate(preferred_entity_types or [])
        }

        matches: list[dict[str, Any]] = []
        for record in self._records.values():
            if not record.enabled or not record.alias_norm:
                continue

            if record.alias_norm in normalized_query:
                base_score = 5000 + len(record.alias_norm) * 20
            elif normalized_query in record.alias_norm and len(normalized_query) >= 2:
                base_score = 2000 + len(normalized_query) * 10
            else:
                continue

            score = (
                base_score
                + type_priority.get(record.entity_type, 0) * 25
                + (80 if record.reviewed else 0)
            )
            matches.append(
                {
                    **record.to_dict(),
                    "score": score,
                    "match_reason": "alias_norm_contains",
                }
            )

        matches.sort(
            key=lambda item: (
                item["score"],
                item["reviewed"],
                len(item["alias_norm"]),
            ),
            reverse=True,
        )
        return matches[:limit]

    def get_stats(self) -> dict[str, Any]:
        type_counts: dict[str, int] = {}
        enabled_count = 0
        reviewed_count = 0

        for record in self._records.values():
            type_counts[record.entity_type] = type_counts.get(record.entity_type, 0) + 1
            if record.enabled:
                enabled_count += 1
            if record.reviewed:
                reviewed_count += 1

        return {
            "datasource_id": self.datasource_id,
            "total": len(self._records),
            "enabled": enabled_count,
            "reviewed": reviewed_count,
            "file_path": str(self.file_path),
            "type_counts": type_counts,
        }


def route_faultcase_query(
    query: str,
    alias_store: Optional[FaultcaseAliasStore] = None,
) -> dict[str, Any]:
    intent_info = infer_faultcase_query_intent(query)
    alias_hits = (
        alias_store.match_query(
            query,
            preferred_entity_types=intent_info["preferred_entity_types"],
        )
        if alias_store is not None
        else []
    )

    ranked_entity_types: list[str] = []
    for hit in alias_hits:
        entity_type = hit["entity_type"]
        if entity_type not in ranked_entity_types:
            ranked_entity_types.append(entity_type)
    for entity_type in intent_info["preferred_entity_types"]:
        if entity_type not in ranked_entity_types:
            ranked_entity_types.append(entity_type)

    return {
        "datasource_id": alias_store.datasource_id if alias_store else "",
        "query": query,
        "query_norm": normalize_alias_text(query),
        "intent": intent_info["intent"],
        "preferred_entity_types": ranked_entity_types,
        "alias_hits": alias_hits,
    }
