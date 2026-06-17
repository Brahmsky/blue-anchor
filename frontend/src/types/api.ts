export type DocumentStatus = 'indexed' | 'processing' | 'failed' | 'pending';

export type ResourceKind = 'input_dir' | 'pipeline';

export interface ResourceCapabilities {
  mutable: boolean;
  deletable: boolean;
  reindexable: boolean;
  reprocessable: boolean;
  read_only: boolean;
}

export interface DatasourceScope {
  datasource_id: string;
  datasource_root: string;
  source_root: string;
  staging_root: string;
  output_root: string;
  working_dir: string;
  input_dir: string;
}

export interface DocumentSummaryItem {
  datasource_id: string;
  resource_kind: ResourceKind;
  capabilities: ResourceCapabilities;
  name: string;
  relative_path: string;
  absolute_path: string;
  type: string;
  source_kind?: string;
  size: number;
  modified_at: string;
  status: DocumentStatus;
  doc_id?: string;
  chunks_count?: number;
  graph_nodes?: number;
  graph_edges?: number;
  content_length?: number;
  content_summary?: string;
  error?: string | null;
  indexed_at?: string;
  ready_to_query?: boolean;
  pipeline_record_count?: number;
  pipeline_relative_path?: string;
  pipeline_modified_at?: string;
}

export interface DocumentSummaryResponse {
  datasource_id: string;
  datasource: DatasourceScope;
  supported_extensions: string[];
  stats: {
    total: number;
    indexed: number;
    processing: number;
    failed: number;
    pending: number;
  };
  items: DocumentSummaryItem[];
}

export interface DocumentFileStats {
  exists: boolean;
  size?: number;
  modified_at?: string;
}

export interface DocumentRuntimeStatus {
  content?: string;
  content_summary?: string;
  content_length?: number;
  status?: string;
  created_at?: string;
  updated_at?: string;
  chunks_count?: number;
  [key: string]: unknown;
}

export interface DocumentDetailResponse {
  datasource_id: string;
  resource_kind: ResourceKind;
  capabilities: ResourceCapabilities;
  registry_snapshot: DocumentSummaryItem;
  file_stats: DocumentFileStats;
  doc_status: DocumentRuntimeStatus | null;
}

export interface DocumentMutationResponse {
  status: string;
  message: string;
  document_count: number;
}

export interface ScanProgressResponse {
  is_scanning: boolean;
  current_file: string;
  indexed_count: number;
  total_files: number;
  progress: number;
}

export type RawChunkReprocessStatus = 'idle' | 'running' | 'success' | 'failed';

export type RawChunkDownstreamState = 'stale' | 'routing_refreshed';

export interface RawChunkSummaryItem {
  relative_path: string;
  doc_dir: string;
  doc_name: string;
  chapter: string;
  chunk_count: number;
  dirty: boolean;
  last_edited_at: string | null;
  last_reprocessed_at: string | null;
  last_reprocess_status: RawChunkReprocessStatus;
  downstream_state: RawChunkDownstreamState;
}

export interface RawChunkSummaryResponse {
  datasource_id?: string;
  outputs_root: string;
  stats: {
    total: number;
    dirty: number;
    clean: number;
    reprocessing: number;
    downstream_stale: number;
    downstream_refreshed: number;
  };
  items: RawChunkSummaryItem[];
}

export interface RawChunkFileDetailParams {
  relative_path: string;
}

export interface RawChunkFileChunk {
  chunk_index: number;
  chunk_id: string;
  breadcrumb: string;
  content: string;
  chunk_type: string;
  metadata: Record<string, unknown>;
}

export interface RawChunkFileRegistry {
  dirty: boolean;
  last_edited_at: string | null;
  last_reprocessed_at: string | null;
  last_reprocess_status: RawChunkReprocessStatus;
  downstream_state: RawChunkDownstreamState;
}

export interface RawChunkFileResponse {
  relative_path: string;
  doc_name: string;
  chapter: string;
  chunk_count: number;
  chunks: RawChunkFileChunk[];
  registry: RawChunkFileRegistry | null;
}

export interface RawChunkEditRequest {
  relative_path: string;
  chunk_id: string;
  content: string;
  breadcrumb?: string;
  chunk_type?: string;
  metadata?: Record<string, unknown>;
}

export interface RawChunkEditResponse {
  relative_path: string;
  old_chunk_id: string;
  new_chunk_id: string;
  success: boolean;
}

export interface RawChunkSplitRequest {
  relative_path: string;
  chunk_id: string;
  left_content: string;
  right_content: string;
}

export interface RawChunkSplitResponse {
  relative_path: string;
  original_chunk_id: string;
  left_chunk_id: string;
  right_chunk_id: string;
  success: boolean;
}

export interface RawChunkMergeRequest {
  relative_path: string;
  first_chunk_id: string;
  second_chunk_id: string;
}

export interface RawChunkMergeResponse {
  relative_path: string;
  first_chunk_id: string;
  second_chunk_id: string;
  merged_chunk_id: string;
  success: boolean;
}

export interface RawChunkReprocessRequest {
  doc_dir: string;
}

export interface RawChunkRoutingCounts {
  good_chunks: number;
  long_chunk: number;
  tables: number;
  images: number;
}

export interface RawChunkScanSummary {
  chunk_count: number;
  avg_tokens: number;
  median_tokens: number;
  max_tokens: number;
  min_tokens: number;
  long_chunk_count: number;
  empty_chunk_count: number;
  html_table_count: number;
  html_image_count: number;
  markdown_image_count: number;
}

export interface RawChunkReprocessResponse {
  doc_dir: string;
  report_path: string;
  summary: RawChunkScanSummary;
  routing: RawChunkRoutingCounts;
  last_reprocess_status: 'success';
  downstream_state: 'routing_refreshed';
}

export interface QueryResponse {
  response: string;
}

export interface GraphNode {
  id?: string;
  labels?: string[];
  entity_type?: string;
  description?: string;
  source_id?: string;
  [key: string]: unknown;
}

export interface GraphEdge {
  source: string;
  target: string;
  type?: string;
  direction?: string;
  [key: string]: unknown;
}

export interface GraphResponse {
  datasource_id?: string;
  datasource?: DatasourceScope;
  graph_mode?: 'full' | 'label';
  graph_state?: 'empty' | 'ready';
  label?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphSummaryResponse {
  datasource_id: string;
  datasource: DatasourceScope;
  total_nodes: number;
  total_edges: number;
  type_counts: Array<{
    type: string;
    count: number;
  }>;
}

export interface GraphNodeDetail {
  datasource_id?: string;
  label: string;
  entity_type: string;
  degree: number;
  properties: Record<string, unknown>;
  relationships: Array<{
    label: string;
    entity_type?: string;
    direction: string;
    type: string;
    properties: Record<string, unknown>;
  }>;
}

export interface HealthResponse {
  status: string;
  working_directory: string;
  source_root: string;
  input_directory?: string;
  indexed_files: string[];
  indexed_files_count: number;
  configuration: Record<string, string>;
}

export interface SystemCapabilities {
  datasource_id: string;
  datasource: DatasourceScope;
  supported_modes: string[];
  default_mode: string;
  recommended_mode: string;
  current_demo_mainline: string;
  supports_stream: boolean;
  supports_only_need_context: boolean;
  stream_protocol: string;
  recommended_query_endpoint: string;
  frontend_rules: string[];
  top_k: number;
  batch_insert_size: number;
  max_tokens: number;
  query_model: string;
  embedding_model: string;
  equipment_catalog?: Array<{
    name: string;
    faultcase_count: number;
    document_count: number;
    documents: string[];
  }>;
  storage: {
    graph: string;
    vector: string;
    doc_status: string;
  };
  alias_store?: AliasStoreStats;
}

export interface AliasRecord {
  datasource_id: string;
  id: string;
  canonical_name: string;
  entity_type: 'EQUIPMENT' | 'FAULTCASE' | 'COMPONENT';
  alias: string;
  alias_norm: string;
  enabled: boolean;
  reviewed: boolean;
  created_at: string;
  updated_at: string;
}

export interface AliasStoreStats {
  datasource_id: string;
  total: number;
  enabled: number;
  reviewed: number;
  file_path: string;
  type_counts: Record<string, number>;
}

export interface AliasListResponse {
  datasource_id: string;
  items: AliasRecord[];
  total: number;
  stats: AliasStoreStats;
}

export interface AliasResolveResponse {
  datasource_id: string;
  query: string;
  query_norm: string;
  intent: string;
  preferred_entity_types: string[];
  alias_hits: AliasRecord[];
}

export interface SystemConfigResponse {
  server: {
    host: string;
    port: number;
    working_dir: string;
    datasource_id: string;
    input_dir: string;
    log_level: string;
    auto_scan_at_startup: boolean;
  };
  llm: {
    binding: string;
    binding_host: string | null;
    model: string;
    query_binding: string;
    query_binding_host: string | null;
    query_model: string;
    max_async: number;
    max_tokens: number;
    history_turns: number;
  };
  embedding: {
    binding: string;
    binding_host: string | null;
    model: string;
    dimension: number;
    max_embed_tokens: number;
  };
  chunking: {
    chunk_size: number;
    chunk_overlap_size: number;
    batch_insert_size: number;
    max_parallel_insert: number;
  };
  query: {
    default_mode: string;
    available_modes: string[];
    top_k: number;
    cosine_threshold: number;
    stream_protocol: string;
  };
  storage: {
    kv_storage: string;
    doc_status_storage: string;
    graph_storage: string;
    vector_storage: string;
  };
  alias_store?: AliasStoreStats;
  notes: string[];
}

export type EvidenceStatus = 'idle' | 'loading' | 'ready' | 'partial' | 'unavailable' | 'error';

export interface EvidenceItem {
  id: string;
  title: string;
  snippet: string;
  raw: string;
  page?: string;
  score?: string;
  sourceId?: string;
}

export interface ContextEntityItem {
  id: string;
  name: string;
  score?: string;
  description: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
  error?: boolean;
}

export interface ConversationMessage extends ChatMessage {
  timestamp: string;
  query?: string;
  mode?: string;
  endpoint?: string;
  ttftMs?: number;
  latencyMs?: number;
  datasourceId?: string;
  evidenceStatus?: EvidenceStatus;
  evidenceItems?: EvidenceItem[];
  contextSources?: string[];
  contextEntities?: ContextEntityItem[];
  contextRaw?: string;
  evidenceNote?: string;
}

export interface ChatSessionItem {
  id: string;
  title: string;
  updatedAt: number;
  manualTitle?: boolean;
  messages: ConversationMessage[];
}

export interface ChatStateResponse {
  datasource_id: string;
  active_session_id?: string | null;
  sessions: ChatSessionItem[];
}

export interface ChatStatePayload {
  datasource_id?: string;
  active_session_id?: string | null;
  sessions: ChatSessionItem[];
}

export interface ChatExportResponse {
  status: string;
  datasource_id: string;
  file_name: string;
  path: string;
  relative_path: string;
  content: string;
}

// ============================================
// Benchmark V1 Contract Types
// ============================================

/** Benchmark lifecycle state. */
export type BenchmarkState =
  | 'idle'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'completed'
  | 'failed';

/**
 * Raw score to label mapping (canonical, single source of truth from backend).
 * - 1: 正确 (correct)
 * - 0: 回避或不足 (partial/avoided)
 * - -1: 错误 (wrong)
 */
export const BENCHMARK_SCORE_LABELS: { 1: string; 0: string; '-1': string } = {
  1: '正确',
  0: '回避或不足',
  '-1': '错误',
};

/** Single question result item. */
export interface BenchmarkResultItem {
  question_id: string;
  question_type?: string;
  question: string;
  gold_answer: string;
  model_answer: string;
  raw_score: number;
  status_label: string;
  response_time_ms: number;
  completed_at: string;
  error_message?: string;
  primary_mode?: string;
  mode_answers?: Record<string, string>;
  mode_scores?: Record<string, number>;
  mode_status_labels?: Record<string, string>;
  mode_response_time_ms?: Record<string, number>;
  mode_recall_rates?: Record<string, number>;
}

export interface BenchmarkModeSummary {
  completed: number;
  correct_count: number;
  partial_count: number;
  wrong_count: number;
  accuracy_percent: number;
  avg_response_time_ms: number;
  avg_recall_rate?: number;
}

/** Aggregated benchmark run summary. */
export interface BenchmarkSummary {
  total: number;
  completed: number;
  correct_count: number;
  partial_count: number;
  wrong_count: number;
  accuracy_percent: number;
  avg_response_time_ms: number;
  avg_recall_rate?: number;
  primary_mode?: string;
  mode_summaries?: Record<string, BenchmarkModeSummary>;
}

/** Model option for benchmark selection. */
export interface BenchmarkModelOption {
  id: string;
  label: string;
}

/** Snapshot for frontend page state - normalized for /benchmark/status endpoint. */
export interface BenchmarkPageSnapshot {
  state: BenchmarkState;
  run_id?: string;
  progress_percent: number;
  summary?: BenchmarkSummary;
  recent_results: BenchmarkResultItem[];
  available_models: BenchmarkModelOption[];
  selected_model: string;
  can_start: boolean;
  can_stop: boolean;
  can_reset: boolean;
  error_message?: string;
}
