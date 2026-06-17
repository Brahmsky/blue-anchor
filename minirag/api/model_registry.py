from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


LLM_ROLE_INDEX = "index"
LLM_ROLE_QUERY = "query"
LLM_ROLE_BENCHMARK = "benchmark"
EMBEDDING_ROLE = "embedding"


class ResolvedModelConfig(BaseModel):
    id: str
    label: str
    binding: str
    host: str | None = None
    model: str
    roles: list[str] = Field(default_factory=list)
    api_key: str | None = Field(default=None, exclude=True)
    api_key_configured: bool = False


class RegisteredModelConfig(BaseModel):
    id: str
    label: str
    binding: str
    model: str
    host: str | None = None
    api_key: str | None = None
    binding_env: str | None = None
    model_env: str | None = None
    host_env: str | None = None
    api_key_env: str | None = None
    roles: list[str] = Field(default_factory=list)

    def _resolve_value(self, env_name: str | None, fallback: str | None) -> str | None:
        if env_name:
            env_value = os.getenv(env_name, "").strip()
            if env_value:
                return env_value
        return fallback

    def resolve(self) -> ResolvedModelConfig:
        resolved_api_key = self._resolve_value(self.api_key_env, self.api_key)
        return ResolvedModelConfig(
            id=self.id,
            label=self.label,
            binding=self._resolve_value(self.binding_env, self.binding) or self.binding,
            host=self._resolve_value(self.host_env, self.host),
            model=self._resolve_value(self.model_env, self.model) or self.model,
            roles=self.roles.copy(),
            api_key=resolved_api_key,
            api_key_configured=bool(resolved_api_key),
        )

    def supports_role(
        self,
        role: Literal["index", "query", "benchmark", "embedding"],
        *,
        default_roles: list[str],
    ) -> bool:
        allowed_roles = self.roles or default_roles
        return role in allowed_roles


class ModelRegistryDefaults(BaseModel):
    index_llm_id: str | None = None
    query_llm_id: str | None = None
    embedding_model_id: str | None = None


class ModelRegistryConfig(BaseModel):
    llm_models: list[RegisteredModelConfig] = Field(default_factory=list)
    embedding_models: list[RegisteredModelConfig] = Field(default_factory=list)
    defaults: ModelRegistryDefaults = Field(default_factory=ModelRegistryDefaults)

    def llm_options(
        self,
        role: Literal["index", "query", "benchmark"],
    ) -> list[RegisteredModelConfig]:
        if role == LLM_ROLE_INDEX:
            default_roles = [LLM_ROLE_INDEX]
        elif role == LLM_ROLE_QUERY:
            default_roles = [LLM_ROLE_QUERY, LLM_ROLE_BENCHMARK]
        else:
            default_roles = [LLM_ROLE_QUERY, LLM_ROLE_BENCHMARK]
        return [
            item
            for item in self.llm_models
            if item.supports_role(role, default_roles=default_roles)
        ]

    def resolve_llm(
        self,
        model_id: str,
        *,
        role: Literal["index", "query", "benchmark"],
    ) -> ResolvedModelConfig:
        for item in self.llm_options(role):
            if item.id == model_id:
                return item.resolve()
        raise KeyError(f"Unknown llm model id for role={role}: {model_id}")

    def resolve_embedding(self, model_id: str) -> ResolvedModelConfig:
        for item in self.embedding_models:
            if item.id == model_id:
                return item.resolve()
        raise KeyError(f"Unknown embedding model id: {model_id}")


class RuntimeModelSelection(BaseModel):
    index_llm_id: str
    query_llm_id: str
    embedding_model_id: str


class RuntimeModelCatalogResponse(BaseModel):
    selection: RuntimeModelSelection
    llm_models: list[ResolvedModelConfig]
    embedding_models: list[ResolvedModelConfig]

def build_default_model_registry(args) -> ModelRegistryConfig:
    return ModelRegistryConfig(
        llm_models=[
            RegisteredModelConfig(
                id="index-default",
                label="Index LLM (current args)",
                binding=args.llm_binding,
                host=args.llm_binding_host,
                api_key=args.llm_binding_api_key,
                model=args.llm_model,
                roles=[LLM_ROLE_INDEX],
            ),
            RegisteredModelConfig(
                id="query-default",
                label="Query LLM (current args)",
                binding=args.query_llm_binding,
                host=args.query_llm_binding_host,
                api_key=args.query_llm_binding_api_key,
                model=args.query_llm_model,
                roles=[LLM_ROLE_QUERY, LLM_ROLE_BENCHMARK],
            ),
        ],
        embedding_models=[
            RegisteredModelConfig(
                id="embedding-default",
                label="Embedding (current args)",
                binding=args.embedding_binding,
                host=args.embedding_binding_host,
                api_key=args.embedding_binding_api_key,
                model=args.embedding_model,
                roles=[EMBEDDING_ROLE],
            )
        ],
        defaults=ModelRegistryDefaults(
            index_llm_id="index-default",
            query_llm_id="query-default",
            embedding_model_id="embedding-default",
        ),
    )


def load_model_registry(args, registry_path: str | None) -> ModelRegistryConfig:
    registry = build_default_model_registry(args)
    if not registry_path:
        return registry

    path = Path(registry_path)
    if not path.exists():
        return registry

    payload = json.loads(path.read_text(encoding="utf-8"))
    configured = ModelRegistryConfig.model_validate(payload)
    default_index_id = configured.defaults.index_llm_id or (
        configured.llm_models[0].id if configured.llm_models else registry.defaults.index_llm_id
    )
    query_candidates = configured.llm_options(LLM_ROLE_QUERY)
    default_query_id = configured.defaults.query_llm_id or (
        query_candidates[0].id if query_candidates else registry.defaults.query_llm_id
    )
    default_embedding_id = configured.defaults.embedding_model_id or (
        configured.embedding_models[0].id
        if configured.embedding_models
        else registry.defaults.embedding_model_id
    )

    return ModelRegistryConfig(
        llm_models=configured.llm_models,
        embedding_models=configured.embedding_models,
        defaults=ModelRegistryDefaults(
            index_llm_id=default_index_id,
            query_llm_id=default_query_id,
            embedding_model_id=default_embedding_id,
        ),
    )


def resolve_runtime_selection(
    registry: ModelRegistryConfig,
    selection: RuntimeModelSelection | None = None,
) -> RuntimeModelSelection:
    index_options = registry.llm_options(LLM_ROLE_INDEX)
    query_options = registry.llm_options(LLM_ROLE_QUERY)
    next_selection = selection or RuntimeModelSelection(
        index_llm_id=registry.defaults.index_llm_id
        or (index_options[0].id if index_options else "index-default"),
        query_llm_id=registry.defaults.query_llm_id
        or (query_options[0].id if query_options else "query-default"),
        embedding_model_id=registry.defaults.embedding_model_id
        or (
            registry.embedding_models[0].id
            if registry.embedding_models
            else "embedding-default"
        ),
    )

    registry.resolve_llm(next_selection.index_llm_id, role=LLM_ROLE_INDEX)
    registry.resolve_llm(next_selection.query_llm_id, role=LLM_ROLE_QUERY)
    registry.resolve_embedding(next_selection.embedding_model_id)
    return next_selection


def build_runtime_model_catalog(
    registry: ModelRegistryConfig,
    selection: RuntimeModelSelection,
) -> RuntimeModelCatalogResponse:
    resolved_llm_models = [item.resolve() for item in registry.llm_models]
    resolved_embedding_models = [item.resolve() for item in registry.embedding_models]
    return RuntimeModelCatalogResponse(
        selection=selection,
        llm_models=resolved_llm_models,
        embedding_models=resolved_embedding_models,
    )
