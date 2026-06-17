# pyright: reportMissingImports=false, reportGeneralTypeIssues=false, reportAttributeAccessIssue=false, reportAssignmentType=false, reportArgumentType=false, reportReturnType=false, reportCallIssue=false, reportOptionalMemberAccess=false, reportPossiblyUnboundVariable=false, reportOperatorIssue=false, reportOptionalOperand=false
import asyncio
import os
from collections import deque
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Type, cast, Any, Optional
from dotenv import load_dotenv
import networkx as nx


from .operate import (
    chunking_by_token_size,
    extract_entities,
    faultcase_fast_query,
)

from .utils import (
    EmbeddingFunc,
    compute_mdhash_id,
    limit_async_func_call,
    convert_response_to_json,
    logger,
    clean_text,
    get_content_summary,
    set_logger,
    logger,
)
from .base import (
    BaseGraphStorage,
    BaseKVStorage,
    BaseVectorStorage,
    StorageNameSpace,
    QueryParam,
    DocStatus,
)


STORAGES = {
    "NetworkXStorage": ".kg.networkx_impl",
    "Neo4JStorage": ".kg.neo4j_impl",
    "JsonKVStorage": ".kg.json_kv_impl",
    "NanoVectorDBStorage": ".kg.nano_vector_db_impl",
    "JsonDocStatusStorage": ".kg.jsondocstatus_impl",
}

load_dotenv(dotenv_path=".env", override=False)


def lazy_external_import(module_name: str, class_name: str):
    """Lazily import a class from an external module based on the package of the caller."""

    # Get the caller's module and package
    import inspect

    caller_frame = inspect.currentframe().f_back
    module = inspect.getmodule(caller_frame)
    package = module.__package__ if module else None

    def import_class(*args, **kwargs):
        import importlib

        module = importlib.import_module(module_name, package=package)
        cls = getattr(module, class_name)
        return cls(*args, **kwargs)

    return import_class


def always_get_an_event_loop() -> asyncio.AbstractEventLoop:
    """
    Ensure that there is always an event loop available.

    This function tries to get the current event loop. If the current event loop is closed or does not exist,
    it creates a new event loop and sets it as the current event loop.

    Returns:
        asyncio.AbstractEventLoop: The current or newly created event loop.
    """
    try:
        # Try to get the current event loop
        current_loop = asyncio.get_event_loop()
        if current_loop.is_closed():
            raise RuntimeError("Event loop is closed.")
        return current_loop

    except RuntimeError:
        # If no event loop exists or it is closed, create a new one
        logger.info("Creating a new event loop in main thread.")
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        return new_loop


@dataclass
class MiniRAG:
    working_dir: str = field(
        default_factory=lambda: (
            f"./minirag_cache_{datetime.now().strftime('%Y-%m-%d-%H:%M:%S')}"
        )
    )
    datasource_id: str = ""

    # RAGmode: str = 'minirag'

    kv_storage: str = field(default="JsonKVStorage")
    vector_storage: str = field(default="NanoVectorDBStorage")
    graph_storage: str = field(default="NetworkXStorage")

    current_log_level = logger.level
    log_level: str = field(default=current_log_level)

    # text chunking
    chunk_token_size: int = 1200
    chunk_overlap_token_size: int = 100
    tiktoken_model_name: str = "gpt-4o-mini"
    faultcase_rerank_model: str = field(
        default_factory=lambda: os.getenv("FAULTCASE_RERANK_MODEL", "")
    )
    faultcase_rerank_base_url: str = field(
        default_factory=lambda: os.getenv("FAULTCASE_RERANK_BASE_URL", "")
    )
    faultcase_rerank_device: str = field(
        default_factory=lambda: os.getenv("FAULTCASE_RERANK_DEVICE", "cpu")
    )
    faultcase_rerank_precision: str = field(
        default_factory=lambda: os.getenv("FAULTCASE_RERANK_PRECISION", "fp32")
    )

    # entity extraction
    entity_extract_max_gleaning: int = 1
    entity_summary_to_max_tokens: int = 500

    # node embedding
    node_embedding_algorithm: str = "node2vec"
    node2vec_params: dict = field(
        default_factory=lambda: {
            "dimensions": 1536,
            "num_walks": 10,
            "walk_length": 40,
            "window_size": 2,
            "iterations": 3,
            "random_seed": 3,
        }
    )

    embedding_func: EmbeddingFunc = None
    embedding_batch_num: int = 32
    embedding_func_max_async: int = 16

    # LLM
    llm_model_func: callable = None
    llm_model_name: str = "meta-llama/Llama-3.2-1B-Instruct"  #'meta-llama/Llama-3.2-1B'#'google/gemma-2-2b-it'
    llm_model_max_token_size: int = 32768
    llm_model_max_async: int = 16
    llm_model_kwargs: dict = field(default_factory=dict)

    # storage
    vector_db_storage_cls_kwargs: dict = field(default_factory=dict)

    enable_llm_cache: bool = True

    # extension
    addon_params: dict = field(default_factory=dict)
    convert_response_to_json_func: callable = convert_response_to_json

    # Add new field for document status storage type
    doc_status_storage: str = field(default="JsonDocStatusStorage")
    faultcase_alias_store: Optional[Any] = field(default=None, repr=False)

    # Custom Chunking Function
    chunking_func: callable = chunking_by_token_size
    chunking_func_kwargs: dict = field(default_factory=dict)

    max_parallel_insert: int = field(default=int(os.getenv("MAX_PARALLEL_INSERT", 2)))

    def __post_init__(self):
        log_file = os.path.join(self.working_dir, "minirag.log")
        set_logger(log_file)
        logger.setLevel(self.log_level)

        logger.info(f"Logger initialized for working directory: {self.working_dir}")
        if not os.path.exists(self.working_dir):
            logger.info(f"Creating working directory {self.working_dir}")
            os.makedirs(self.working_dir)

        # show config
        global_config = asdict(self)
        _print_config = ",\n  ".join([f"{k} = {v}" for k, v in global_config.items()])
        logger.debug(f"MiniRAG init with param:\n  {_print_config}\n")

        # @TODO: should move all storage setup here to leverage initial start params attached to self.

        self.key_string_value_json_storage_cls: Type[BaseKVStorage] = (
            self._get_storage_class(self.kv_storage)
        )
        self.vector_db_storage_cls: Type[BaseVectorStorage] = self._get_storage_class(
            self.vector_storage
        )
        self.graph_storage_cls: Type[BaseGraphStorage] = self._get_storage_class(
            self.graph_storage
        )

        self.key_string_value_json_storage_cls = partial(
            self.key_string_value_json_storage_cls, global_config=global_config
        )

        self.vector_db_storage_cls = partial(
            self.vector_db_storage_cls, global_config=global_config
        )

        self.graph_storage_cls = partial(
            self.graph_storage_cls, global_config=global_config
        )
        self.json_doc_status_storage = self.key_string_value_json_storage_cls(
            namespace="json_doc_status_storage",
            embedding_func=None,
        )

        if not os.path.exists(self.working_dir):
            logger.info(f"Creating working directory {self.working_dir}")
            os.makedirs(self.working_dir)

        self.llm_response_cache = (
            self.key_string_value_json_storage_cls(
                namespace="llm_response_cache",
                global_config=asdict(self),
                embedding_func=None,
            )
            if self.enable_llm_cache
            else None
        )

        self.embedding_func = limit_async_func_call(self.embedding_func_max_async)(
            self.embedding_func
        )

        ####
        # add embedding func by walter
        ####
        self.full_docs = self.key_string_value_json_storage_cls(
            namespace="full_docs",
            global_config=asdict(self),
            embedding_func=self.embedding_func,
        )
        self.text_chunks = self.key_string_value_json_storage_cls(
            namespace="text_chunks",
            global_config=asdict(self),
            embedding_func=self.embedding_func,
        )
        self.chunk_entity_relation_graph = self.graph_storage_cls(
            namespace="chunk_entity_relation",
            global_config=asdict(self),
            embedding_func=self.embedding_func,
        )
        ####
        # add embedding func by walter over
        ####

        self.entities_vdb = self.vector_db_storage_cls(
            namespace="entities",
            global_config=asdict(self),
            embedding_func=self.embedding_func,
            meta_fields={"entity_name"},
        )
        global_config = asdict(self)

        self.entity_name_vdb = self.vector_db_storage_cls(
            namespace="entities_name",
            global_config=asdict(self),
            embedding_func=self.embedding_func,
            meta_fields={"entity_name"},
        )

        self.relationships_vdb = self.vector_db_storage_cls(
            namespace="relationships",
            global_config=asdict(self),
            embedding_func=self.embedding_func,
            meta_fields={"src_id", "tgt_id"},
        )
        self.chunks_vdb = self.vector_db_storage_cls(
            namespace="chunks",
            global_config=asdict(self),
            embedding_func=self.embedding_func,
        )

        self.llm_model_func = limit_async_func_call(self.llm_model_max_async)(
            partial(
                self.llm_model_func,
                hashing_kv=self.llm_response_cache,
                **self.llm_model_kwargs,
            )
        )
        # Initialize document status storage
        self.doc_status_storage_cls = self._get_storage_class(self.doc_status_storage)
        self.doc_status = self.doc_status_storage_cls(
            namespace="doc_status",
            global_config=global_config,
            embedding_func=None,
        )

    def _get_storage_class(self, storage_name: str) -> dict:
        import_path = STORAGES[storage_name]
        storage_class = lazy_external_import(import_path, storage_name)
        return storage_class

    def set_storage_client(self, db_client):
        # Now only tested on Oracle Database
        for storage in [
            self.vector_db_storage_cls,
            self.graph_storage_cls,
            self.doc_status,
            self.full_docs,
            self.text_chunks,
            self.llm_response_cache,
            self.key_string_value_json_storage_cls,
            self.chunks_vdb,
            self.relationships_vdb,
            self.entities_vdb,
            self.graph_storage_cls,
            self.chunk_entity_relation_graph,
            self.llm_response_cache,
        ]:
            # set client
            storage.db = db_client

    def insert(self, string_or_strings):
        loop = always_get_an_event_loop()
        return loop.run_until_complete(self.ainsert(string_or_strings))

    async def ainsert(
        self,
        input: str | list[str],
        split_by_character: str | None = None,
        split_by_character_only: bool = False,
        ids: str | list[str] | None = None,
    ) -> None:
        if isinstance(input, str):
            input = [input]
        if isinstance(ids, str):
            ids = [ids]

        await self.apipeline_enqueue_documents(input, ids)
        await self.apipeline_process_enqueue_documents(
            split_by_character, split_by_character_only
        )

        # Perform additional entity extraction as per original ainsert logic
        inserting_chunks = {
            compute_mdhash_id(dp["content"], prefix="chunk-"): {
                **dp,
                "full_doc_id": doc_id,
            }
            for doc_id, status_doc in (
                await self.doc_status.get_docs_by_status(DocStatus.PROCESSED)
            ).items()
            for dp in self.chunking_func(
                status_doc.content,
                self.chunk_overlap_token_size,
                self.chunk_token_size,
                self.tiktoken_model_name,
            )
        }

        if inserting_chunks:
            logger.info("Performing entity extraction on newly processed chunks")
            await extract_entities(
                inserting_chunks,
                knowledge_graph_inst=self.chunk_entity_relation_graph,
                entity_vdb=self.entities_vdb,
                entity_name_vdb=self.entity_name_vdb,
                relationships_vdb=self.relationships_vdb,
                global_config=asdict(self),
            )

        await self._insert_done()

    async def apipeline_enqueue_documents(
        self, input: str | list[str], ids: list[str] | None = None
    ) -> None:
        """
        Pipeline for Processing Documents

        1. Validate ids if provided or generate MD5 hash IDs
        2. Remove duplicate contents
        3. Generate document initial status
        4. Filter out already processed documents
        5. Enqueue document in status
        """
        if isinstance(input, str):
            input = [input]
        if isinstance(ids, str):
            ids = [ids]

        if ids is not None:
            if len(ids) != len(input):
                raise ValueError("Number of IDs must match the number of documents")
            if len(ids) != len(set(ids)):
                raise ValueError("IDs must be unique")
            contents = {id_: doc for id_, doc in zip(ids, input)}
        else:
            input = list(set(clean_text(doc) for doc in input))
            contents = {compute_mdhash_id(doc, prefix="doc-"): doc for doc in input}

        unique_contents = {
            id_: content
            for content, id_ in {
                content: id_ for id_, content in contents.items()
            }.items()
        }
        new_docs: dict[str, Any] = {
            id_: {
                "content": content,
                "content_summary": get_content_summary(content),
                "content_length": len(content),
                "status": DocStatus.PENDING,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            for id_, content in unique_contents.items()
        }

        all_new_doc_ids = set(new_docs.keys())
        unique_new_doc_ids = await self.doc_status.filter_keys(all_new_doc_ids)

        new_docs = {
            doc_id: new_docs[doc_id]
            for doc_id in unique_new_doc_ids
            if doc_id in new_docs
        }
        if not new_docs:
            logger.info("No new unique documents were found.")
            return

        await self.doc_status.upsert(new_docs)
        logger.info(f"Stored {len(new_docs)} new unique documents")

    async def apipeline_process_enqueue_documents(
        self,
        split_by_character: str | None = None,
        split_by_character_only: bool = False,
    ) -> None:
        """
        Process pending documents by splitting them into chunks, processing
        each chunk for entity and relation extraction, and updating the
        document status.
        """
        processing_docs, failed_docs, pending_docs = await asyncio.gather(
            self.doc_status.get_docs_by_status(DocStatus.PROCESSING),
            self.doc_status.get_docs_by_status(DocStatus.FAILED),
            self.doc_status.get_docs_by_status(DocStatus.PENDING),
        )

        to_process_docs: dict[str, Any] = {
            **processing_docs,
            **failed_docs,
            **pending_docs,
        }
        if not to_process_docs:
            logger.info("No documents to process")
            return

        docs_batches = [
            list(to_process_docs.items())[i : i + self.max_parallel_insert]
            for i in range(0, len(to_process_docs), self.max_parallel_insert)
        ]
        logger.info(f"Number of batches to process: {len(docs_batches)}")

        for batch_idx, docs_batch in enumerate(docs_batches):
            for doc_id, status_doc in docs_batch:
                chunks = {
                    compute_mdhash_id(dp["content"], prefix="chunk-"): {
                        **dp,
                        "full_doc_id": doc_id,
                    }
                    for dp in self.chunking_func(
                        status_doc.content,
                        self.chunk_overlap_token_size,
                        self.chunk_token_size,
                        self.tiktoken_model_name,
                    )
                }
                await asyncio.gather(
                    self.chunks_vdb.upsert(chunks),
                    self.full_docs.upsert({doc_id: {"content": status_doc.content}}),
                    self.text_chunks.upsert(chunks),
                )
                await self.doc_status.upsert(
                    {
                        doc_id: {
                            "status": DocStatus.PROCESSED,
                            "chunks_count": len(chunks),
                            "content": status_doc.content,
                            "content_summary": status_doc.content_summary,
                            "content_length": status_doc.content_length,
                            "created_at": status_doc.created_at,
                            "updated_at": datetime.now().isoformat(),
                        }
                    }
                )
        logger.info("Document processing pipeline completed")

    async def _insert_done(self):
        tasks = []
        for storage_inst in [
            self.full_docs,
            self.text_chunks,
            self.llm_response_cache,
            self.entities_vdb,
            self.entity_name_vdb,
            self.relationships_vdb,
            self.chunks_vdb,
            self.chunk_entity_relation_graph,
        ]:
            if storage_inst is None:
                continue
            tasks.append(cast(StorageNameSpace, storage_inst).index_done_callback())
        await asyncio.gather(*tasks)

    async def _chunk_ids_for_doc(self, doc_id: str) -> list[str]:
        if not hasattr(self.text_chunks, "all_keys"):
            return []

        chunk_ids = await self.text_chunks.all_keys()
        if not chunk_ids:
            return []

        if hasattr(self.text_chunks, "get_by_ids"):
            payloads = await self.text_chunks.get_by_ids(chunk_ids)
        else:
            payloads = await asyncio.gather(
                *[self.text_chunks.get_by_id(chunk_id) for chunk_id in chunk_ids]
            )

        return [
            chunk_id
            for chunk_id, payload in zip(chunk_ids, payloads)
            if isinstance(payload, dict) and payload.get("full_doc_id") == doc_id
        ]

    async def apurge_document(self, doc_id: str) -> None:
        chunk_ids = await self._chunk_ids_for_doc(doc_id)
        tasks = []
        for storage, ids in [
            (self.doc_status, [doc_id]),
            (self.full_docs, [doc_id]),
            (self.text_chunks, chunk_ids),
            (self.chunks_vdb, chunk_ids),
            (self.entities_vdb, [doc_id]),
            (self.entity_name_vdb, [doc_id]),
            (self.relationships_vdb, [doc_id]),
        ]:
            if storage is None or not ids:
                continue
            if hasattr(storage, "delete"):
                tasks.append(storage.delete(ids))
        if tasks:
            await asyncio.gather(*tasks)

    async def areindex_document(
        self,
        content: str,
        *,
        new_doc_id: str | None = None,
        purge_doc_id: str | None = None,
    ) -> str:
        if purge_doc_id:
            await self.apurge_document(purge_doc_id)
        new_doc_id = new_doc_id or compute_mdhash_id(clean_text(content), prefix="doc-")
        await self.ainsert(content, ids=new_doc_id)
        return new_doc_id

    async def areprocess_document(
        self,
        content: str,
        *,
        new_doc_id: str | None = None,
        purge_doc_id: str | None = None,
    ) -> str:
        return await self.areindex_document(
            content,
            new_doc_id=new_doc_id,
            purge_doc_id=purge_doc_id,
        )

    async def adelete_document(self, doc_id: str) -> int:
        await self.apurge_document(doc_id)
        return 0

    def query(self, query: str, param: QueryParam = QueryParam()):
        loop = always_get_an_event_loop()
        return loop.run_until_complete(self.aquery(query, param))

    async def aquery(self, query: str, param: QueryParam = QueryParam()):
        if param.mode not in {
            "graph_text_hybrid",
            "graph_only",
            "text_only",
            "keyword_search",
            "faultcase_fast",
        }:
            raise ValueError(f"Unknown mode {param.mode}")
        global_config = {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "faultcase_alias_store"
        }
        global_config["faultcase_alias_store"] = self.faultcase_alias_store
        response = await faultcase_fast_query(
            query,
            self.chunk_entity_relation_graph,
            self.chunks_vdb,
            self.text_chunks,
            param,
            global_config,
        )
        await self._query_done()
        return response

    def _resolve_graph_datasource_id(self, datasource_id: str | None) -> str:
        expected = str(self.datasource_id or "").strip()
        requested = str(datasource_id or "").strip()

        if expected and requested and requested != expected:
            raise ValueError(
                f"Datasource mismatch: requested {requested}, current {expected}"
            )

        return requested or expected

    async def get_graph_labels(
        self, datasource_id: str | None = None, limit: int | None = None
    ) -> list[str]:
        self._resolve_graph_datasource_id(datasource_id)
        graph = cast(Any, self.chunk_entity_relation_graph)

        if hasattr(graph, "get_all_labels"):
            return await graph.get_all_labels(limit=limit)

        if hasattr(graph, "_graph"):
            labels = sorted(str(node_id) for node_id in graph._graph.nodes())
            return labels[:limit] if limit is not None and limit > 0 else labels

        return []

    async def get_graph_label_entries(
        self, datasource_id: str | None = None, limit: int | None = None
    ) -> list[dict[str, str]]:
        self._resolve_graph_datasource_id(datasource_id)
        graph = cast(Any, self.chunk_entity_relation_graph)

        if hasattr(graph, "get_all_label_entries"):
            entries = await graph.get_all_label_entries(limit=limit)
            normalized_entries = [
                {
                    "label": str(item.get("label", "")).strip(),
                    "entity_type": self._normalize_entity_type(
                        item.get("entity_type")
                    ),
                }
                for item in entries
                if str(item.get("label", "")).strip()
            ]
            return (
                normalized_entries[:limit]
                if limit is not None and limit > 0
                else normalized_entries
            )

        if hasattr(graph, "_graph"):
            entries: list[dict[str, str]] = []
            for node_id, node_data in graph._graph.nodes(data=True):
                label = str(node_id).strip()
                if not label:
                    continue
                entries.append(
                    {
                        "label": label,
                        "entity_type": self._normalize_entity_type(
                            node_data.get("entity_type")
                        ),
                    }
                )
            sorted_entries = sorted(entries, key=lambda item: item["label"])
            return (
                sorted_entries[:limit]
                if limit is not None and limit > 0
                else sorted_entries
            )

        if hasattr(graph, "get_graph"):
            payload = await graph.get_graph()
            entries = []
            for node in payload.get("nodes", []):
                label = (
                    str((node.get("labels") or [node.get("id")])[0] or "").strip()
                )
                if not label:
                    continue
                entries.append(
                    {
                        "label": label,
                        "entity_type": self._normalize_entity_type(
                            node.get("entity_type")
                        ),
                    }
                )
            sorted_entries = sorted(entries, key=lambda item: item["label"])
            return (
                sorted_entries[:limit]
                if limit is not None and limit > 0
                else sorted_entries
            )

        return [
            {"label": label, "entity_type": "UNKNOWN"}
            for label in await self.get_graph_labels(
                datasource_id=datasource_id,
                limit=limit,
            )
        ]

    async def get_graps(
        self,
        nodel_label: str,
        max_depth: int = 5,
        datasource_id: str | None = None,
    ):
        self._resolve_graph_datasource_id(datasource_id)
        graph = cast(Any, self.chunk_entity_relation_graph)

        if hasattr(graph, "get_knowledge_graph"):
            return await graph.get_knowledge_graph(nodel_label, max_depth=max_depth)

        return await self._build_generic_knowledge_graph(nodel_label, max_depth)

    async def get_graph_full(
        self,
        datasource_id: str | None = None,
        max_nodes: int = 1000,
        max_edges: int = 5000,
    ) -> dict[str, list[dict[str, Any]]]:
        self._resolve_graph_datasource_id(datasource_id)
        graph = cast(Any, self.chunk_entity_relation_graph)

        if hasattr(graph, "get_graph"):
            payload = await graph.get_graph()
        elif hasattr(graph, "_graph"):
            payload = {
                "nodes": [
                    {
                        **node_data,
                        "id": str(node_id),
                        "labels": [str(node_id)],
                    }
                    for node_id, node_data in graph._graph.nodes(data=True)
                ],
                "edges": [
                    {
                        **edge_data,
                        "source": str(source),
                        "target": str(target),
                        "type": self._relationship_type_from_edge(edge_data),
                    }
                    for source, target, edge_data in graph._graph.edges(data=True)
                ],
            }
        else:
            payload = {"nodes": [], "edges": []}

        nodes = payload.get("nodes", [])
        edges = payload.get("edges", [])

        if len(nodes) > max_nodes or len(edges) > max_edges:
            raise ValueError(
                f"Full graph too large for datasource export: {len(nodes)} nodes, {len(edges)} edges"
            )

        if not nodes and not edges:
            fallback_payload = self._load_graphml_full_graph_fallback()
            if fallback_payload["nodes"] or fallback_payload["edges"]:
                nodes = fallback_payload["nodes"]
                edges = fallback_payload["edges"]
            else:
                return {"nodes": [], "edges": []}

        if len(nodes) > max_nodes or len(edges) > max_edges:
            raise ValueError(
                f"Full graph too large for datasource export: {len(nodes)} nodes, {len(edges)} edges"
            )

        return {"nodes": nodes, "edges": edges}

    def _load_graphml_full_graph_fallback(self) -> dict[str, list[dict[str, Any]]]:
        graphml_path = Path(self.working_dir) / "graph_chunk_entity_relation.graphml"
        if not graphml_path.exists():
            return {"nodes": [], "edges": []}

        try:
            graph = nx.read_graphml(graphml_path)
        except Exception as exc:
            logger.warning(
                "full graph fallback failed to read graphml %s: %s",
                graphml_path,
                exc,
            )
            return {"nodes": [], "edges": []}

        logger.warning(
            "full graph storage returned empty payload; falling back to graphml snapshot %s",
            graphml_path,
        )
        return {
            "nodes": [
                {**node_data, "id": str(node_id), "labels": [str(node_id)]}
                for node_id, node_data in graph.nodes(data=True)
            ],
            "edges": [
                {
                    **edge_data,
                    "source": str(source),
                    "target": str(target),
                    "type": self._relationship_type_from_edge(edge_data),
                }
                for source, target, edge_data in graph.edges(data=True)
            ],
        }

    async def get_graph_summary(
        self, datasource_id: str | None = None
    ) -> dict[str, Any]:
        self._resolve_graph_datasource_id(datasource_id)
        graph = cast(Any, self.chunk_entity_relation_graph)

        if hasattr(graph, "get_graph_summary"):
            return await graph.get_graph_summary()

        if hasattr(graph, "_graph"):
            type_counts: dict[str, int] = {}
            for _, node_data in graph._graph.nodes(data=True):
                node_type = self._normalize_entity_type(node_data.get("entity_type"))
                type_counts[node_type] = type_counts.get(node_type, 0) + 1

            return {
                "total_nodes": graph._graph.number_of_nodes(),
                "total_edges": graph._graph.number_of_edges(),
                "type_counts": [
                    {"type": node_type, "count": count}
                    for node_type, count in sorted(type_counts.items())
                ],
            }

        labels = await self.get_graph_labels()
        return {
            "total_nodes": len(labels),
            "total_edges": 0,
            "type_counts": [],
        }

    async def get_graph_node_detail(
        self,
        node_label: str,
        max_relationships: int = 20,
        datasource_id: str | None = None,
    ) -> Any:
        self._resolve_graph_datasource_id(datasource_id)
        graph = cast(Any, self.chunk_entity_relation_graph)

        if hasattr(graph, "get_node_detail"):
            return await graph.get_node_detail(
                node_label, max_relationships=max_relationships
            )

        node_key = self._normalize_node_label(node_label)
        node = await graph.get_node(node_key)
        if not node:
            return None

        edges = await graph.get_node_edges(node_key) or []
        relationships = []
        for source, target in edges[:max_relationships]:
            edge_data = await graph.get_edge(source, target) or await graph.get_edge(
                target, source
            )
            other_label = target if source == node_key else source
            relationships.append(
                {
                    "label": self._normalize_node_label(other_label),
                    "direction": "outgoing" if source == node_key else "incoming",
                    "type": self._relationship_type_from_edge(edge_data),
                    "properties": edge_data or {},
                }
            )

        return {
            "label": node_key,
            "entity_type": self._normalize_entity_type(node.get("entity_type")),
            "degree": await graph.node_degree(node_key) or len(edges),
            "properties": node,
            "relationships": relationships,
        }

    async def _query_done(self):
        tasks = []
        for storage_inst in [self.llm_response_cache]:
            if storage_inst is None:
                continue
            tasks.append(cast(StorageNameSpace, storage_inst).index_done_callback())
        await asyncio.gather(*tasks)

    def delete_by_entity(self, entity_name: str):
        loop = always_get_an_event_loop()
        return loop.run_until_complete(self.adelete_by_entity(entity_name))

    async def adelete_by_entity(self, entity_name: str):
        entity_name = f'"{entity_name.upper()}"'

        try:
            await self.entities_vdb.delete_entity(entity_name)
            await self.relationships_vdb.delete_relation(entity_name)
            await self.chunk_entity_relation_graph.delete_node(entity_name)

            logger.info(
                f"Entity '{entity_name}' and its relationships have been deleted."
            )
            await self._delete_by_entity_done()
        except Exception as e:
            logger.error(f"Error while deleting entity '{entity_name}': {e}")

    async def _delete_by_entity_done(self):
        tasks = []
        for storage_inst in [
            self.entities_vdb,
            self.relationships_vdb,
            self.chunk_entity_relation_graph,
        ]:
            if storage_inst is None:
                continue
            tasks.append(cast(StorageNameSpace, storage_inst).index_done_callback())
        await asyncio.gather(*tasks)

    async def _build_generic_knowledge_graph(
        self, node_label: str, max_depth: int
    ) -> dict[str, list[dict[str, Any]]]:
        graph = self.chunk_entity_relation_graph
        start_label = self._normalize_node_label(node_label)
        if not await graph.has_node(start_label):
            return {"nodes": [], "edges": []}

        result: dict[str, list[dict[str, Any]]] = {"nodes": [], "edges": []}
        seen_nodes: set[str] = set()
        seen_edges: set[tuple[str, str]] = set()
        queue: deque[tuple[str, int]] = deque([(start_label, 0)])

        while queue:
            current_label, depth = queue.popleft()
            if current_label in seen_nodes:
                continue

            node = await graph.get_node(current_label)
            if not node:
                continue

            seen_nodes.add(current_label)
            result["nodes"].append(
                {
                    **node,
                    "id": current_label,
                    "labels": [current_label],
                }
            )

            if depth >= max_depth:
                continue

            for source, target in await graph.get_node_edges(current_label) or []:
                source_label = self._normalize_node_label(source)
                target_label = self._normalize_node_label(target)
                edge_key = (source_label, target_label)
                if edge_key not in seen_edges:
                    edge_data = await graph.get_edge(
                        source_label, target_label
                    ) or await graph.get_edge(target_label, source_label)
                    result["edges"].append(
                        {
                            **(edge_data or {}),
                            "source": source_label,
                            "target": target_label,
                            "type": self._relationship_type_from_edge(edge_data),
                        }
                    )
                    seen_edges.add(edge_key)

                next_label = (
                    target_label if source_label == current_label else source_label
                )
                if next_label not in seen_nodes:
                    queue.append((next_label, depth + 1))

        return result

    @staticmethod
    def _normalize_node_label(node_label: str) -> str:
        return str(node_label).strip('"')

    @staticmethod
    def _normalize_entity_type(entity_type: Any) -> str:
        if entity_type is None:
            return "UNKNOWN"
        if isinstance(entity_type, list):
            entity_type = entity_type[0] if entity_type else "UNKNOWN"
        return str(entity_type).strip('"') or "UNKNOWN"

    @staticmethod
    def _relationship_type_from_edge(edge_data: Any) -> str:
        if not edge_data:
            return "RELATED_TO"

        if isinstance(edge_data, dict):
            return (
                edge_data.get("type")
                or edge_data.get("keywords")
                or edge_data.get("label")
                or "RELATED_TO"
            )

        return "RELATED_TO"
