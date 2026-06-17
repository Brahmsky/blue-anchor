<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  AlertCircle,
  BarChart2,
  CheckCircle,
  Clock3,
  Eye,
  FolderOpen,
  GitBranch,
  Loader,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload
} from 'lucide-vue-next';

import {
  deleteDocument,
  fetchDocumentDetail,
  fetchDocumentSummary,
  fetchScanProgress,
  makeDocumentReadyToQuery,
  reindexDocument,
  reprocessDocument,
  triggerDocumentScan,
  uploadDocument
} from '@/api/documents';
import { fetchSystemConfig } from '@/api/system';
import {
  deleteRawChunk,
  editRawChunk,
  fetchRawChunkFileDetail,
  fetchRawChunkSummary,
  mergeRawChunks,
  reprocessRawChunkDocument,
  splitRawChunk
} from '@/api/rawChunks';
import type {
  DocumentDetailResponse,
  DocumentStatus,
  DocumentSummaryItem,
  DocumentSummaryResponse,
  SystemConfigResponse,
  RawChunkFileChunk,
  RawChunkFileResponse,
  RawChunkSummaryItem,
  RawChunkSummaryResponse
} from '@/types/api';

type KnowledgeSourceKey = 'input_dir' | 'pipeline';

interface DrawerNotice {
  tone: 'success' | 'warning' | 'error' | 'info';
  text: string;
}

interface ChunkEditForm {
  chunkId: string;
  breadcrumb: string;
  content: string;
  chunkType: string;
  metadataText: string;
}

interface ChunkSplitForm {
  chunkId: string;
  leftContent: string;
  rightContent: string;
}

const summary = ref<DocumentSummaryResponse | null>(null);
const systemConfig = ref<SystemConfigResponse | null>(null);
const rawChunkSummary = ref<RawChunkSummaryResponse | null>(null);
const detail = ref<DocumentDetailResponse | null>(null);
const loading = ref(true);
const detailLoading = ref(false);
const rawChunkLoading = ref(true);
const uploading = ref(false);
const scanning = ref(false);
const dragging = ref(false);
const error = ref('');
const detailError = ref('');
const rawChunkError = ref('');
const pendingFiles = ref<File[]>([]);
const search = ref('');
const selectedCategory = ref('全部');
const selectedDocKey = ref('');
const progressText = ref('');
const progressValue = ref(0);
const pageNotice = ref<DrawerNotice | null>(null);
const documentActionBusy = ref<'reindex' | 'reprocess' | 'ready' | 'delete' | ''>('');
const rawChunkDrawerOpen = ref(false);
const rawChunkDrawerItem = ref<RawChunkSummaryItem | null>(null);
const rawChunkFileDetail = ref<RawChunkFileResponse | null>(null);
const rawChunkFileLoading = ref(false);
const rawChunkFileError = ref('');
const rawChunkActionBusy = ref(false);
const rawChunkReprocessBusy = ref(false);
const activeChunkId = ref('');
const activeChunkMode = ref<'edit' | 'split'>('edit');
const chunkEditorError = ref('');
const drawerNotice = ref<DrawerNotice | null>(null);
const editForm = ref<ChunkEditForm>({
  chunkId: '',
  breadcrumb: '',
  content: '',
  chunkType: '',
  metadataText: '{}'
});
const splitForm = ref<ChunkSplitForm>({
  chunkId: '',
  leftContent: '',
  rightContent: ''
});
const editSnapshot = ref<ChunkEditForm | null>(null);
const splitSnapshot = ref<ChunkSplitForm | null>(null);

let pollTimer: number | null = null;
let detailRequestToken = 0;

const statusMeta: Record<
  DocumentStatus,
  { label: string; color: string; bg: string; border: string; icon: typeof CheckCircle }
> = {
  indexed: {
    label: '已索引',
    color: '#10b981',
    bg: 'rgba(16, 185, 129, 0.12)',
    border: 'rgba(16, 185, 129, 0.22)',
    icon: CheckCircle
  },
  processing: {
    label: '处理中',
    color: '#f59e0b',
    bg: 'rgba(245, 158, 11, 0.12)',
    border: 'rgba(245, 158, 11, 0.22)',
    icon: Loader
  },
  failed: {
    label: '失败',
    color: '#ef4444',
    bg: 'rgba(239, 68, 68, 0.12)',
    border: 'rgba(239, 68, 68, 0.2)',
    icon: AlertCircle
  },
  pending: {
    label: '待处理',
    color: '#64748b',
    bg: 'rgba(100, 116, 139, 0.1)',
    border: 'rgba(100, 116, 139, 0.18)',
    icon: Clock3
  }
};

const extensionColors: Record<string, string> = {
  xlsx: '#10b981',
  md: '#8b5cf6',
  txt: '#64748b'
};

const documents = computed(() => summary.value?.items ?? []);
const datasourceId = computed(
  () =>
    summary.value?.datasource_id
    || summary.value?.datasource?.datasource_id
    || systemConfig.value?.server.datasource_id
    || ''
);
const sourceDocuments = computed(() =>
  documents.value.filter((item) => documentSource(item) === 'input_dir')
);

const categories = computed(() => {
  const counts = new Map<string, number>();
  for (const item of sourceDocuments.value) {
    const key = normalizeType(item.type);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  return [
    { label: '全部', count: sourceDocuments.value.length },
    ...Array.from(counts.entries())
      .sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'))
      .map(([label, count]) => ({ label, count }))
  ];
});

const filteredDocuments = computed(() => {
  const query = search.value.trim().toLowerCase();
  return sourceDocuments.value.filter((item) => {
    const matchesCategory =
      selectedCategory.value === '全部' || normalizeType(item.type) === selectedCategory.value;
    const haystack =
      `${item.name} ${item.relative_path} ${item.content_summary ?? ''} ${item.error ?? ''}`.toLowerCase();
    const matchesQuery = !query || haystack.includes(query);
    return matchesCategory && matchesQuery;
  });
});

const processingQueue = computed(() =>
  sourceDocuments.value
    .filter((item) => item.status === 'processing' || item.status === 'pending')
    .slice(0, 3)
);

const selectedDoc = computed(() => {
  if (!selectedDocKey.value) return null;
  return documents.value.find((item) => docKey(item) === selectedDocKey.value) ?? null;
});

const statCards = computed(() => {
  const scopedDocuments = documents.value;
  const stats = {
    total: scopedDocuments.length,
    input: scopedDocuments.filter((item) => documentSource(item) === 'input_dir').length,
    pipeline: scopedDocuments.filter((item) => documentSource(item) === 'pipeline').length,
    indexed: scopedDocuments.filter((item) => item.status === 'indexed').length,
    processing: scopedDocuments.filter((item) => item.status === 'processing').length,
    pending: scopedDocuments.filter((item) => item.status === 'pending').length
  };
  return [
    { label: '总库存', value: stats.total, color: '#3b82f6' },
    { label: '源文档', value: stats.input, color: '#10b981' },
    { label: '派生记录', value: stats.pipeline, color: '#8b5cf6' },
    { label: '处理中 / 待处理', value: stats.processing + stats.pending, color: '#f59e0b' }
  ];
});

const drawerChunks = computed(() => rawChunkFileDetail.value?.chunks ?? []);

const selectedDocRawChunkItems = computed<RawChunkSummaryItem[]>(() => {
  if (!selectedDoc.value || documentSource(selectedDoc.value) !== 'input_dir') return [];

  const matchTokens = new Set(buildDocumentMatchTokens(selectedDoc.value));
  return [...(rawChunkSummary.value?.items ?? [])]
    .filter((item) => buildRawChunkItemMatchTokens(item).some((token) => matchTokens.has(token)))
    .sort((left, right) => left.chapter.localeCompare(right.chapter, 'zh-CN'));
});

const selectedDocRawChunkDocDir = computed(() => selectedDocRawChunkItems.value[0]?.doc_dir ?? '');
const selectedDocRawChunkDocName = computed(
  () => selectedDocRawChunkItems.value[0]?.doc_name || selectedDoc.value?.name || '当前文档'
);

const canMutateSelectedDoc = computed(() => {
  const doc = selectedDoc.value;
  if (!doc || documentSource(doc) !== 'input_dir') return false;
  return supportsDocumentAction(doc, 'reindexable') || supportsDocumentAction(doc, 'reprocessable');
});

const selectedDocReadyToQuery = computed(() => {
  const doc = selectedDoc.value;
  if (!doc || documentSource(doc) !== 'input_dir') return false;
  return Boolean(doc.ready_to_query || detail.value?.registry_snapshot.ready_to_query);
});

const detailSummary = computed(
  () => detail.value?.doc_status?.content_summary ?? detail.value?.registry_snapshot.content_summary ?? selectedDoc.value?.content_summary ?? ''
);

const activeChunk = computed(() => {
  if (!drawerChunks.value.length) return null;
  return drawerChunks.value.find((chunk) => chunk.chunk_id === activeChunkId.value) ?? drawerChunks.value[0];
});

const showChunkMetadataEditor = computed(() => {
  if (!canMutateSelectedDoc.value) return false;
  return hasMeaningfulChunkMetadata(activeChunk.value?.metadata);
});

function docKey(item: DocumentSummaryItem) {
  return item.relative_path || item.absolute_path || item.name;
}

function documentSource(item: DocumentSummaryItem): KnowledgeSourceKey {
  return item.resource_kind;
}

function supportsDocumentAction(
  item: DocumentSummaryItem,
  action: 'deletable' | 'reindexable' | 'reprocessable'
) {
  return Boolean(item.capabilities[action]);
}

function normalizeType(type: string) {
  return (type || '未知').toUpperCase();
}

function displayExtension(item: DocumentSummaryItem) {
  const raw = item.name || item.relative_path || item.absolute_path || item.doc_id || '';
  const match = raw.toLowerCase().match(/\.([a-z0-9]+)$/);
  return (match?.[1] || 'md').toLowerCase();
}

function formatBytes(size: number) {
  if (!size) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const exponent = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  return `${(size / 1024 ** exponent).toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

function formatTime(value?: string) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date);
}

function fileColor(type: string) {
  return extensionColors[(type || '').toLowerCase()] ?? '#64748b';
}

function statusInfo(status: DocumentStatus) {
  return statusMeta[status] ?? statusMeta.pending;
}

function inferredPageCount(item: DocumentSummaryItem) {
  if (!item.content_length) return '—';
  return Math.max(1, Math.round(item.content_length / 2200));
}

function inferredGraphCount(item: DocumentSummaryItem) {
  if (typeof item.graph_nodes === 'number') return item.graph_nodes;
  if (!item.chunks_count) return 0;
  return Math.max(0, Math.round(item.chunks_count * 0.42));
}

function inferredRelationCount(item: DocumentSummaryItem) {
  if (typeof item.graph_edges === 'number') return item.graph_edges;
  if (!item.chunks_count) return 0;
  return Math.max(0, Math.round(item.chunks_count * 0.78));
}

function statusProgress(item: DocumentSummaryItem) {
  if (item.status === 'indexed') return 100;
  if (item.status === 'failed') return 0;
  if (item.status === 'pending') return 12;
  const chunks = item.chunks_count ?? 0;
  return Math.min(84, Math.max(18, chunks ? Math.round(chunks / 8) : 42));
}

function previewText(content: string) {
  const collapsed = content.replace(/\s+/g, ' ').trim();
  if (collapsed.length <= 180) return collapsed || '（空内容）';
  return `${collapsed.slice(0, 180)}…`;
}

function normalizeMatchToken(value: string) {
  return value
    .toLowerCase()
    .replace(/^.*[\\/]/, '')
    .replace(/\.[^.]+$/, '')
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '');
}

function buildDocumentMatchTokens(item: DocumentSummaryItem) {
  return [item.name, item.relative_path, item.doc_id]
    .map((value) => normalizeMatchToken(value || ''))
    .filter(Boolean);
}

function buildRawChunkItemMatchTokens(item: RawChunkSummaryItem) {
  return [item.doc_name, item.doc_dir, item.relative_path, item.chapter]
    .map((value) => normalizeMatchToken(value))
    .filter(Boolean);
}

function hasMeaningfulChunkMetadata(metadata: Record<string, unknown> | null | undefined) {
  if (!metadata || typeof metadata !== 'object') return false;
  const ignoredKeys = new Set(['level']);

  return Object.entries(metadata).some(([key, value]) => {
    if (ignoredKeys.has(key)) return false;
    if (value === null || value === undefined || value === '') return false;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === 'object') return Object.keys(value as Record<string, unknown>).length > 0;
    return true;
  });
}

function resourceLabel(item: DocumentSummaryItem) {
  return documentSource(item) === 'input_dir' ? '源文档' : '派生记录';
}

function capabilityBadges(item: DocumentSummaryItem) {
  if (documentSource(item) === 'pipeline') {
    return ['详情'];
  }

  return [
    '详情',
    supportsDocumentAction(item, 'reindexable') ? '重建索引' : '',
    supportsDocumentAction(item, 'reprocessable') ? '重处理' : '',
    supportsDocumentAction(item, 'deletable') ? '删除' : ''
  ].filter(Boolean);
}

function detailStatusLabel() {
  const runtimeStatus = String(detail.value?.doc_status?.status ?? '').trim();
  if (!runtimeStatus) return '';
  if (runtimeStatus === 'processed') return '已处理';
  if (runtimeStatus === 'pending') return '待处理';
  if (runtimeStatus === 'processing') return '处理中';
  if (runtimeStatus === 'failed') return '失败';
  return runtimeStatus;
}

async function loadDocumentDetail(relativePath: string) {
  if (!datasourceId.value) return;

  const targetItem = documents.value.find((item) => item.relative_path === relativePath) ?? null;

  const currentToken = ++detailRequestToken;
  detailLoading.value = true;
  detailError.value = '';
  detail.value = null;

  try {
    const nextDetail = await fetchDocumentDetail(datasourceId.value, relativePath);
    if (currentToken !== detailRequestToken) return;
    detail.value = nextDetail;
  } catch (err) {
    if (currentToken !== detailRequestToken) return;
    if (targetItem && documentSource(targetItem) === 'pipeline') {
      detail.value = null;
      detailError.value = '';
      return;
    }
    detailError.value = err instanceof Error ? err.message : '文档详情加载失败';
  } finally {
    if (currentToken === detailRequestToken) {
      detailLoading.value = false;
    }
  }
}

async function refreshSelectedDocument() {
  if (!selectedDoc.value) return;
  await loadSummary(true);
  await loadDocumentDetail(selectedDoc.value.relative_path);
}

async function executeDocumentAction(action: 'reindex' | 'reprocess' | 'ready' | 'delete') {
  if (!selectedDoc.value || !datasourceId.value) return;

  const target = selectedDoc.value;
  documentActionBusy.value = action;
  pageNotice.value = {
    tone: 'info',
    text:
      action === 'reindex'
        ? `正在重建 ${target.name} 的索引...`
        : action === 'reprocess'
          ? `正在重新处理 ${target.name}...`
          : action === 'ready'
            ? `正在为 ${target.name} 生成故障卡并入图...`
            : `正在删除 ${target.name}...`
  };

  try {
    const response =
      action === 'reindex'
        ? await reindexDocument(datasourceId.value, target.relative_path)
        : action === 'reprocess'
          ? await reprocessDocument(datasourceId.value, target.relative_path)
          : action === 'ready'
            ? await makeDocumentReadyToQuery(datasourceId.value, target.relative_path)
            : await deleteDocument(datasourceId.value, target.relative_path);

    if (action === 'delete') {
      closeRawChunkDrawer();
      selectedDocKey.value = '';
      detail.value = null;
      detailError.value = '';
    }

    await Promise.all([loadSummary(true), loadRawChunkPipeline()]);

    if (action !== 'delete') {
      await loadDocumentDetail(target.relative_path);
    }

    pageNotice.value = {
      tone: 'success',
      text: response.message
    };
  } catch (err) {
    pageNotice.value = {
      tone: 'error',
      text: err instanceof Error ? err.message : '文档操作失败'
    };
  } finally {
    documentActionBusy.value = '';
  }
}

async function confirmDeleteSelectedDocument() {
  if (!selectedDoc.value) return;

  const expected = selectedDoc.value.name;
  const confirmation = window.prompt(
    `即将删除源文档「${selectedDoc.value.name}」。\n\n` +
      '删除后会立即移除源文件，并让该文档对应的索引、检索引用、图谱结果以及可精确匹配的 pipeline records 从当前 datasource 中消失。\n\n' +
      `这是不可撤销操作。请输入文档名 ${expected} 以确认删除：`
  );

  if (confirmation === null) return;

  if (confirmation.trim() !== expected) {
    pageNotice.value = {
      tone: 'warning',
      text: '删除已取消：确认输入与文档名不匹配。'
    };
    return;
  }

  await executeDocumentAction('delete');
}

function pickFiles(fileList: FileList | null) {
  if (!fileList) return;
  const existing = new Set(pendingFiles.value.map((file) => file.name));
  const next = Array.from(fileList).filter((file) => !existing.has(file.name));
  pendingFiles.value = pendingFiles.value.concat(next);
  if (next.length && !uploading.value) {
    void uploadFiles();
  }
}

function onDragOver(event: DragEvent) {
  event.preventDefault();
  dragging.value = true;
}

function onDragLeave() {
  dragging.value = false;
}

function onDrop(event: DragEvent) {
  event.preventDefault();
  dragging.value = false;
  pickFiles(event.dataTransfer?.files ?? null);
}

function selectDoc(item: DocumentSummaryItem) {
  selectedDocKey.value = docKey(item);
}

function clearPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

function syncDrawerItemFromSummary() {
  if (!rawChunkDrawerItem.value) return;
  const nextItem = rawChunkSummary.value?.items.find((item) => item.relative_path === rawChunkDrawerItem.value?.relative_path);
  if (nextItem) {
    rawChunkDrawerItem.value = nextItem;
  }
}

function resetChunkEditor() {
  activeChunkId.value = '';
  activeChunkMode.value = 'edit';
  chunkEditorError.value = '';
  editForm.value = {
    chunkId: '',
    breadcrumb: '',
    content: '',
    chunkType: '',
    metadataText: '{}'
  };
  splitForm.value = {
    chunkId: '',
    leftContent: '',
    rightContent: ''
  };
  editSnapshot.value = null;
  splitSnapshot.value = null;
}

function openEditMode(chunk: RawChunkFileChunk) {
  editSnapshot.value = {
    chunkId: chunk.chunk_id,
    breadcrumb: chunk.breadcrumb,
    content: chunk.content,
    chunkType: chunk.chunk_type,
    metadataText: JSON.stringify(chunk.metadata ?? {}, null, 2)
  };
  activeChunkId.value = chunk.chunk_id;
  activeChunkMode.value = 'edit';
  chunkEditorError.value = '';
  editForm.value = {
    chunkId: chunk.chunk_id,
    breadcrumb: chunk.breadcrumb,
    content: chunk.content,
    chunkType: chunk.chunk_type,
    metadataText: JSON.stringify(chunk.metadata ?? {}, null, 2)
  };
}

function openSplitMode(chunk: RawChunkFileChunk) {
  splitSnapshot.value = {
    chunkId: chunk.chunk_id,
    leftContent: chunk.content,
    rightContent: ''
  };
  activeChunkId.value = chunk.chunk_id;
  activeChunkMode.value = 'split';
  chunkEditorError.value = '';
  splitForm.value = {
    chunkId: chunk.chunk_id,
    leftContent: chunk.content,
    rightContent: ''
  };
}

function toggleChunkCard(chunk: RawChunkFileChunk) {
  if (activeChunkId.value === chunk.chunk_id) {
    resetChunkEditor();
    drawerNotice.value = null;
    return;
  }

  openEditMode(chunk);
}

function discardChunkChanges() {
  if (activeChunkMode.value === 'edit' && editSnapshot.value) {
    editForm.value = { ...editSnapshot.value };
  } else if (activeChunkMode.value === 'split' && splitSnapshot.value) {
    splitForm.value = { ...splitSnapshot.value };
  }
  resetChunkEditor();
  drawerNotice.value = null;
}

function parseMetadataObject() {
  try {
    const parsed = JSON.parse(editForm.value.metadataText || '{}');
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('metadata 必须是 JSON 对象');
    }
    return parsed as Record<string, unknown>;
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : 'metadata JSON 解析失败');
  }
}

async function loadSummary(preserveSelection = true) {
  const currentSelection = selectedDocKey.value;
  if (!datasourceId.value) {
    return;
  }
  loading.value = true;
  error.value = '';
  try {
    const nextSummary = await fetchDocumentSummary(datasourceId.value);
    summary.value = nextSummary;
    if (preserveSelection && currentSelection) {
      const exists = nextSummary.items.some((item) => docKey(item) === currentSelection);
      selectedDocKey.value = exists ? currentSelection : nextSummary.items[0] ? docKey(nextSummary.items[0]) : '';
    } else if (!preserveSelection) {
      selectedDocKey.value = '';
    } else if (nextSummary.items[0]) {
      selectedDocKey.value = docKey(nextSummary.items[0]);
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '知识库摘要加载失败';
  } finally {
    loading.value = false;
  }
}

async function loadSourceContext() {
  try {
    systemConfig.value = await fetchSystemConfig();
  } catch {
    systemConfig.value = null;
  }
}

async function loadRawChunkPipeline() {
  rawChunkLoading.value = true;
  rawChunkError.value = '';
  try {
    rawChunkSummary.value = await fetchRawChunkSummary();
    syncDrawerItemFromSummary();
  } catch (err) {
    rawChunkError.value = err instanceof Error ? err.message : '原始分块管线摘要加载失败';
  } finally {
    rawChunkLoading.value = false;
  }
}

async function refreshRawChunkSummaryInPlace() {
  try {
    rawChunkSummary.value = await fetchRawChunkSummary();
    rawChunkError.value = '';
    syncDrawerItemFromSummary();
  } catch (err) {
    rawChunkError.value = err instanceof Error ? err.message : '原始分块管线摘要刷新失败';
  }
}

async function loadRawChunkFile(relativePath: string, preferredChunkId?: string) {
  rawChunkFileLoading.value = true;
  rawChunkFileError.value = '';
  try {
    const detail = await fetchRawChunkFileDetail({ relative_path: relativePath });
    rawChunkFileDetail.value = detail;

    const nextChunk = (preferredChunkId
      ? detail.chunks.find((chunk) => chunk.chunk_id === preferredChunkId)
      : detail.chunks.find((chunk) => chunk.chunk_id === activeChunkId.value))
      ?? null;

    if (nextChunk) {
      openEditMode(nextChunk);
    } else {
      resetChunkEditor();
    }
  } catch (err) {
    rawChunkFileDetail.value = null;
    resetChunkEditor();
    rawChunkFileError.value = err instanceof Error ? err.message : '原始分块章节详情加载失败';
  } finally {
    rawChunkFileLoading.value = false;
  }
}

async function refreshSelectedRawChunkData(preferredChunkId?: string) {
  if (!rawChunkDrawerItem.value) return;
  await refreshRawChunkSummaryInPlace();
  await loadRawChunkFile(rawChunkDrawerItem.value.relative_path, preferredChunkId);
}

async function openRawChunkFile(item: RawChunkSummaryItem) {
  rawChunkDrawerOpen.value = true;
  rawChunkDrawerItem.value = item;
  drawerNotice.value = null;
  chunkEditorError.value = '';
  resetChunkEditor();
  await loadRawChunkFile(item.relative_path);
}

function closeRawChunkDrawer() {
  rawChunkDrawerOpen.value = false;
  rawChunkDrawerItem.value = null;
  rawChunkFileDetail.value = null;
  rawChunkFileError.value = '';
  drawerNotice.value = null;
  resetChunkEditor();
}

async function refreshKnowledgePage() {
  await loadSourceContext();
  await Promise.all([loadSummary(), loadRawChunkPipeline()]);
}

async function copyText(text: string, fallbackLabel: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    window.alert(`${fallbackLabel}：\n${text}`);
  }
}

async function uploadFiles() {
  if (!pendingFiles.value.length) return;
  uploading.value = true;
  error.value = '';
  try {
    const uploadedCount = pendingFiles.value.length;
    for (const file of pendingFiles.value) {
      await uploadDocument(file);
    }
    pendingFiles.value = [];
    await Promise.all([loadSummary(), loadRawChunkPipeline()]);
    pageNotice.value = {
      tone: 'success',
      text: `已上传 ${uploadedCount} 个源文档，并同步生成当前知识库所需的分块产物。`
    };
  } catch (err) {
    error.value = err instanceof Error ? err.message : '文件上传失败';
  } finally {
    uploading.value = false;
  }
}

function clearPendingFiles() {
  if (uploading.value) return;
  pendingFiles.value = [];
}

async function refreshScanProgress() {
  const progress = await fetchScanProgress();
  progressValue.value = progress.progress;
  progressText.value = progress.total_files
    ? `正在处理 ${progress.current_file}（${progress.indexed_count}/${progress.total_files}）`
    : '等待扫描结果...';

  if (!progress.is_scanning) {
    scanning.value = false;
    clearPolling();
    await loadSummary();
  }
}

async function startScan() {
  scanning.value = true;
  progressValue.value = 0;
  progressText.value = '正在启动扫描...';
  error.value = '';
  try {
    await triggerDocumentScan();
    await refreshScanProgress();
    clearPolling();
    pollTimer = window.setInterval(() => {
      void refreshScanProgress();
    }, 1000);
  } catch (err) {
    scanning.value = false;
    error.value = err instanceof Error ? err.message : '扫描失败';
  }
}

async function saveChunkChanges(chunk: RawChunkFileChunk) {
  const content = editForm.value.content.trim();
  if (!content) {
    chunkEditorError.value = 'Chunk 内容不能为空。';
    return;
  }

  let metadata: Record<string, unknown>;
  try {
    metadata = parseMetadataObject();
  } catch (err) {
    chunkEditorError.value = err instanceof Error ? err.message : 'metadata JSON 校验失败';
    return;
  }

  if (!rawChunkDrawerItem.value) return;

  rawChunkActionBusy.value = true;
  chunkEditorError.value = '';
  drawerNotice.value = null;

  try {
    const response = await editRawChunk({
      relative_path: rawChunkDrawerItem.value.relative_path,
      chunk_id: chunk.chunk_id,
      breadcrumb: editForm.value.breadcrumb,
      content,
      chunk_type: editForm.value.chunkType,
      metadata
    });

    await refreshSelectedRawChunkData(response.new_chunk_id);
    drawerNotice.value = {
      tone: 'success',
      text: 'Chunk 已保存。当前文档已标记 Dirty；只有手动重处理后才会刷新 raw chunk 路由产物。'
    };
  } catch (err) {
    chunkEditorError.value = err instanceof Error ? err.message : 'Chunk 保存失败';
  } finally {
    rawChunkActionBusy.value = false;
  }
}

async function runSplit(chunk: RawChunkFileChunk) {
  const leftContent = splitForm.value.leftContent.trim();
  const rightContent = splitForm.value.rightContent.trim();

  if (!leftContent || !rightContent) {
    chunkEditorError.value = '拆分后的左右两段都必须是非空内容。';
    return;
  }

  if (!rawChunkDrawerItem.value) return;

  rawChunkActionBusy.value = true;
  chunkEditorError.value = '';
  drawerNotice.value = null;

  try {
    const response = await splitRawChunk({
      relative_path: rawChunkDrawerItem.value.relative_path,
      chunk_id: chunk.chunk_id,
      left_content: leftContent,
      right_content: rightContent
    });

    await refreshSelectedRawChunkData(response.left_chunk_id);
    drawerNotice.value = {
      tone: 'success',
      text: 'Chunk 已拆分成两段，文件顺序已更新。当前仍为 Dirty 状态，需手动重处理后才会刷新 routing 结果。'
    };
  } catch (err) {
    chunkEditorError.value = err instanceof Error ? err.message : 'Chunk 拆分失败';
  } finally {
    rawChunkActionBusy.value = false;
  }
}

async function handleSplitButton(chunk: RawChunkFileChunk) {
  if (activeChunkId.value === chunk.chunk_id && activeChunkMode.value === 'split') {
    await runSplit(chunk);
    return;
  }

  openSplitMode(chunk);
  drawerNotice.value = null;
}

async function mergeWithNextChunk(chunk: RawChunkFileChunk, nextChunk: RawChunkFileChunk | null) {
  if (!nextChunk) {
    return;
  }

  if (!rawChunkDrawerItem.value) return;

  rawChunkActionBusy.value = true;
  chunkEditorError.value = '';
  drawerNotice.value = null;

  try {
    const response = await mergeRawChunks({
      relative_path: rawChunkDrawerItem.value.relative_path,
      first_chunk_id: chunk.chunk_id,
      second_chunk_id: nextChunk.chunk_id
    });

    await refreshSelectedRawChunkData(response.merged_chunk_id);
    drawerNotice.value = {
      tone: 'success',
      text: '当前 chunk 已与下一相邻 chunk 合并。文档仍保持 Dirty，需显式重处理后才会刷新 raw chunk routing。'
    };
  } catch (err) {
    chunkEditorError.value = err instanceof Error ? err.message : 'Chunk 合并失败';
  } finally {
    rawChunkActionBusy.value = false;
  }
}

async function deleteChunkFromFile(chunk: RawChunkFileChunk) {
  if (!rawChunkDrawerItem.value) return;

  const confirmed = window.confirm(
    `将要删除 Chunk ${chunk.chunk_index + 1}（${chunk.chunk_id}）。\n\n` +
      '该操作会直接写入章节文件，删除后不可自动恢复。是否继续？'
  );
  if (!confirmed) return;

  rawChunkActionBusy.value = true;
  chunkEditorError.value = '';
  drawerNotice.value = null;

  try {
    const response = await deleteRawChunk({
      relative_path: rawChunkDrawerItem.value.relative_path,
      chunk_id: chunk.chunk_id
    });

    await refreshSelectedRawChunkData(response.next_chunk_id || undefined);
    drawerNotice.value = {
      tone: 'success',
      text: 'Chunk 已删除并写入文件。如需刷新 routing 结果，请在外层点击“重新处理当前文档（仅分块）”。'
    };
  } catch (err) {
    chunkEditorError.value = err instanceof Error ? err.message : 'Chunk 删除失败';
  } finally {
    rawChunkActionBusy.value = false;
  }
}

async function reprocessSelectedDocumentRawChunks() {
  if (!selectedDocRawChunkDocDir.value) return;

  const confirmed = window.confirm(
    `即将对文档 ${selectedDocRawChunkDocName.value} 执行文档级 raw chunk 重处理。\n\n` +
    'v1 只会刷新 good_chunks / long_chunk / tables / images 这几个 routing 产物，不会自动执行后续 LLM 抽取、records 聚合或 graph build。\n\n是否继续？'
  );

  if (!confirmed) return;

  rawChunkReprocessBusy.value = true;
  chunkEditorError.value = '';
  pageNotice.value = {
    tone: 'info',
    text: '正在刷新当前文档的 raw chunk 路由产物...'
  };
  drawerNotice.value = null;

  try {
    await reprocessRawChunkDocument({ doc_dir: selectedDocRawChunkDocDir.value });
    await refreshRawChunkSummaryInPlace();
    if (rawChunkDrawerItem.value) {
      await loadRawChunkFile(rawChunkDrawerItem.value.relative_path, activeChunkId.value || undefined);
    }

    pageNotice.value = {
      tone: 'success',
      text: '文档级 raw chunk 重处理完成。已刷新 routing 产物；后续 LLM/graph 阶段仍需手动执行。'
    };
  } catch (err) {
    pageNotice.value = {
      tone: 'error',
      text: err instanceof Error ? err.message : '文档级 raw chunk 重处理失败'
    };
  } finally {
    rawChunkReprocessBusy.value = false;
  }
}

function previewDocument(item: DocumentSummaryItem) {
  void copyText(item.relative_path, '相对路径');
}

function copyAbsolutePath(item: DocumentSummaryItem) {
  void copyText(item.absolute_path, '绝对路径');
}

watch(filteredDocuments, (items) => {
  if (!items.length) {
    selectedDocKey.value = '';
    detail.value = null;
    detailError.value = '';
    return;
  }

  if (!selectedDocKey.value || !items.some((item) => docKey(item) === selectedDocKey.value)) {
    selectedDocKey.value = docKey(items[0]);
  }
});

watch(selectedDocKey, (key) => {
  if (!key) {
    detail.value = null;
    detailError.value = '';
    return;
  }

  const target = documents.value.find((item) => docKey(item) === key);
  if (!target) {
    detail.value = null;
    detailError.value = '';
    return;
  }

  void loadDocumentDetail(target.relative_path);
});

onMounted(() => {
  void refreshKnowledgePage();
});

onBeforeUnmount(() => {
  clearPolling();
});
</script>

<template>
  <div class="knowledge-layout">
    <aside class="knowledge-sidebar">
      <section class="kb-panel category-panel">
        <div class="kb-panel-title">当前视图内分类</div>
        <div class="category-list">
          <button
            v-for="category in categories"
            :key="category.label"
            class="category-item"
            :class="{ active: selectedCategory === category.label }"
            type="button"
            @click="selectedCategory = category.label"
          >
            <div class="category-item-main">
              <FolderOpen :size="12" />
              <span>{{ category.label }}</span>
            </div>
            <span class="category-count">{{ category.count }}</span>
          </button>
        </div>
      </section>

      <section class="upload-panel" :class="{ dragging }" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
        <label class="upload-dropzone">
          <input hidden multiple type="file" accept=".txt,.md,.xlsx" @change="pickFiles(($event.target as HTMLInputElement).files)" />
          <div class="upload-icon">
            <Upload :size="20" />
          </div>
          <div class="upload-title">上传源文档</div>
          <div class="upload-subtitle">支持 .txt、.md、.xlsx</div>
          <div class="upload-cta">
            <Plus :size="12" />
            <span>选择文件</span>
          </div>
        </label>
      </section>

      <section v-if="pendingFiles.length" class="kb-panel">
        <div class="kb-panel-head">
          <div class="kb-panel-title">{{ uploading ? '正在上传' : '待上传' }}</div>
          <span class="pill" :class="uploading ? 'pill-amber' : 'pill-blue'">{{ pendingFiles.length }}</span>
        </div>
        <div class="upload-file-list">
          <div v-for="file in pendingFiles" :key="file.name" class="upload-file-item">
            <div class="upload-file-name">{{ file.name }}</div>
            <div class="upload-file-size">{{ formatBytes(file.size) }}</div>
          </div>
        </div>
        <div class="detail-actions">
          <button class="detail-action detail-action-blue" type="button" :disabled="uploading" @click="uploadFiles">
            <Upload :size="13" />
            <span>{{ uploading ? '上传中...' : '重新上传' }}</span>
          </button>
          <button class="detail-action" type="button" :disabled="uploading" @click="clearPendingFiles">
            <Trash2 :size="13" />
            <span>{{ uploading ? '上传中不可清空' : '清空队列' }}</span>
          </button>
        </div>
      </section>

      <section class="kb-panel queue-panel">
        <div class="kb-panel-head">
          <div class="kb-panel-title">处理队列</div>
          <span class="pill" :class="processingQueue.length ? 'pill-amber' : 'pill-slate'">
            {{ processingQueue.length ? `${processingQueue.length} 进行中` : '空闲' }}
          </span>
        </div>
        <div v-if="processingQueue.length" class="queue-list">
          <div v-for="item in processingQueue" :key="docKey(item)" class="queue-item">
            <div class="queue-title">{{ item.name }}</div>
          </div>
        </div>
        <div v-else class="queue-empty">当前没有正在处理的文档。</div>
      </section>

      <div v-if="scanning" class="scan-banner">
        <div class="scan-banner-head">
          <span>{{ progressText }}</span>
          <strong>{{ Math.round(progressValue) }}%</strong>
        </div>
        <div class="scan-banner-track">
          <div class="scan-banner-bar" :style="{ width: `${progressValue}%` }"></div>
        </div>
      </div>
    </aside>

    <section class="knowledge-main" :class="{ 'detail-open': !!selectedDoc }">
      <header class="knowledge-toolbar">
        <div class="knowledge-search">
          <Search :size="13" />
          <input v-model="search" type="text" placeholder="搜索文档、路径或摘要..." />
        </div>

        <button class="toolbar-btn" type="button" @click="refreshKnowledgePage()">
          <RefreshCw :size="12" />
          <span>刷新库存</span>
        </button>

        <div class="toolbar-meta">源文档 · {{ filteredDocuments.length }} 条记录</div>
      </header>

      <div v-if="pageNotice" class="detail-notice" :class="pageNotice.tone">{{ pageNotice.text }}</div>
      <div v-if="error" class="toolbar-error">{{ error }}</div>

      <div class="knowledge-content">
        <div v-if="loading" class="loading-state">正在加载 datasource 文档库存...</div>
        <div v-else-if="!filteredDocuments.length" class="empty-state">当前 datasource 下没有符合条件的文档记录。</div>
        <div v-else class="document-table-wrap">
          <div class="document-table-head document-table-head--inventory">
            <div>文档</div>
            <div>来源</div>
            <div>大小</div>
            <div>类型</div>
            <div>更新时间</div>
            <div>状态</div>
          </div>

          <div
            v-for="item in filteredDocuments"
            :key="docKey(item)"
            class="document-row document-row--inventory"
            :class="{ active: selectedDocKey === docKey(item) }"
            data-testid="kb-document-row"
            :data-resource-kind="documentSource(item)"
            @click="selectDoc(item)"
          >
            <div class="document-main">
              <div class="document-badge" :style="{ background: fileColor(displayExtension(item)) }">
                {{ displayExtension(item) }}
              </div>
              <div class="document-main-text">
                <div class="document-name">{{ item.name }}</div>
                <div class="document-subtext">{{ item.chunks_count ?? 0 }} 块 · {{ inferredGraphCount(item) }} 节点 · {{ inferredRelationCount(item) }} 关系</div>
                <div v-if="item.status !== 'indexed'" class="document-progress">
                  <div class="document-progress-track">
                    <div class="document-progress-bar" :style="{ width: `${statusProgress(item)}%` }"></div>
                  </div>
                </div>
              </div>
            </div>

            <div class="table-cell">
              <span class="resource-pill" :class="`resource-pill--${documentSource(item)}`">{{ resourceLabel(item) }}</span>
            </div>

            <div class="table-cell">{{ formatBytes(item.size) }}</div>

            <div class="table-cell">
              <span class="type-pill">{{ normalizeType(item.type) }}</span>
            </div>

            <div class="table-cell">{{ formatTime(item.indexed_at || item.modified_at) }}</div>

            <div class="table-cell">
              <div class="status-inline" :style="{ color: statusInfo(item.status).color }">
                <component :is="statusInfo(item.status).icon" :size="12" :class="{ 'spin-icon': item.status === 'processing' }" />
                <span>{{ statusInfo(item.status).label }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <aside class="knowledge-detail" :class="{ open: !!selectedDoc }">
      <div class="detail-header">
        <div class="kb-panel-title">文档详情</div>
      </div>

      <div class="detail-body">
        <template v-if="selectedDoc">
          <div class="detail-file-card" data-testid="kb-document-detail">
            <div class="detail-file-badge" :style="{ background: fileColor(displayExtension(selectedDoc)) }">
              {{ displayExtension(selectedDoc) }}
            </div>
            <div class="detail-file-text">
              <div class="detail-file-name">{{ selectedDoc.name }}</div>
              <div class="detail-file-size">{{ formatBytes(selectedDoc.size) }}</div>
            </div>
          </div>

          <div class="detail-actions">
            <button class="detail-action detail-action-teal" type="button" :disabled="detailLoading" @click="refreshSelectedDocument">
              <RefreshCw :size="13" />
              <span>{{ detailLoading ? '刷新中...' : '刷新详情' }}</span>
            </button>
            <template v-if="documentSource(selectedDoc) === 'input_dir' && supportsDocumentAction(selectedDoc, 'reindexable')">
              <button class="detail-action detail-action-blue" type="button" data-testid="kb-reindex-button" :disabled="documentActionBusy !== ''" @click="executeDocumentAction('reindex')">
                <RefreshCw :size="13" />
                <span>{{ documentActionBusy === 'reindex' ? '重建中...' : '重建索引（全量）' }}</span>
              </button>
            </template>
            <template v-if="documentSource(selectedDoc) === 'input_dir' && selectedDocRawChunkDocDir">
              <button class="detail-action detail-action-teal" type="button" data-testid="kb-raw-chunk-reprocess-button" :disabled="rawChunkReprocessBusy || rawChunkLoading" @click="reprocessSelectedDocumentRawChunks">
                <Loader :size="13" :class="{ 'spin-icon': rawChunkReprocessBusy }" />
                <span>{{ rawChunkReprocessBusy ? '刷新分块中...' : '重新处理当前文档（仅分块）' }}</span>
              </button>
            </template>
            <template v-if="documentSource(selectedDoc) === 'input_dir' && !selectedDocReadyToQuery">
              <button class="detail-action detail-action-blue" type="button" data-testid="kb-ready-to-query-button" :disabled="documentActionBusy !== '' || rawChunkReprocessBusy" @click="executeDocumentAction('ready')">
                <GitBranch :size="13" />
                <span>{{ documentActionBusy === 'ready' ? '入图中...' : '生成故障卡并入图' }}</span>
              </button>
            </template>
            <template v-if="documentSource(selectedDoc) === 'input_dir' && supportsDocumentAction(selectedDoc, 'deletable')">
              <button class="detail-action detail-action-red" type="button" data-testid="kb-delete-button" :disabled="documentActionBusy !== ''" @click="confirmDeleteSelectedDocument">
                <Trash2 :size="13" />
                <span>{{ documentActionBusy === 'delete' ? '删除中...' : '删除源文档' }}</span>
              </button>
            </template>
          </div>

          <div v-if="detailLoading" class="loading-state detail-loading">正在加载文档详情...</div>
          <div v-if="detailError" class="detail-error">
            <AlertCircle :size="14" />
            <span>{{ detailError }}</span>
          </div>
          <template v-if="!detailLoading">

            <div v-if="detailSummary" class="detail-summary">
              <div class="detail-summary-title">内容摘要</div>
              <div class="detail-summary-text">{{ detailSummary }}</div>
            </div>

            <div v-if="selectedDoc.error" class="detail-error">
              <AlertCircle :size="14" />
              <span>{{ selectedDoc.error }}</span>
            </div>

            <section v-if="documentSource(selectedDoc) === 'input_dir'" class="raw-chunk-panel" data-testid="kb-detail-raw-chunk-panel">
              <div v-if="rawChunkLoading" class="loading-state raw-chunk-loading">正在加载原始分块摘要...</div>
              <div v-else-if="rawChunkError" class="raw-chunk-inline-error">{{ rawChunkError }}</div>
              <template v-else>
                <div v-if="!selectedDocRawChunkItems.length" class="empty-state raw-chunk-empty">当前文档还没有匹配到可维护的 raw chunk 章节文件。</div>
                <div v-else class="raw-chunk-file-table" data-testid="kb-raw-chunk-table">
                  <div class="raw-chunk-file-table-head">
                    <span>文档章节</span>
                    <span>Chunk 数</span>
                  </div>
                  <div class="raw-chunk-file-list">
                    <button
                      v-for="item in selectedDocRawChunkItems"
                      :key="item.relative_path"
                      class="raw-chunk-file-item"
                      type="button"
                      data-testid="open-raw-chunk-file"
                      @click="openRawChunkFile(item)"
                    >
                      <span class="raw-chunk-file-title">{{ item.chapter || '未命名章节' }}</span>
                      <span class="raw-chunk-file-count">{{ item.chunk_count }}</span>
                    </button>
                  </div>
                </div>
              </template>
            </section>
          </template>
        </template>
      </div>
    </aside>
  </div>

  <Teleport to="body">
    <div v-if="rawChunkDrawerOpen" class="drawer-backdrop" @click.self="closeRawChunkDrawer">
      <aside class="drawer-panel" @click.stop>
        <header class="drawer-header">
          <div>
            <div class="drawer-title">原始分块文件详情</div>
            <div class="drawer-subtitle">点击 chunk 卡片可展开或收起详情。</div>
          </div>
          <button class="drawer-close" type="button" aria-label="关闭原始分块抽屉" @click="closeRawChunkDrawer">
            ×
          </button>
        </header>

        <div class="drawer-body">
          <div class="drawer-card drawer-card--compact">
            <div class="drawer-meta-grid drawer-meta-grid--simple">
              <div class="drawer-meta-item drawer-meta-item--doc">
                <div class="drawer-meta-label">文档</div>
                <div class="drawer-meta-value drawer-meta-value--doc">{{ rawChunkDrawerItem?.doc_name || rawChunkFileDetail?.doc_name || '—' }}</div>
              </div>
              <div class="drawer-meta-item drawer-meta-item--chapter">
                <div class="drawer-meta-label">章节</div>
                <div class="drawer-meta-value drawer-meta-value--chapter">{{ rawChunkDrawerItem?.chapter || rawChunkFileDetail?.chapter || '—' }}</div>
              </div>
              <div class="drawer-meta-item drawer-meta-item--count">
                <div class="drawer-meta-label">Chunk 数</div>
                <div class="drawer-meta-value">{{ rawChunkFileDetail?.chunk_count ?? rawChunkDrawerItem?.chunk_count ?? 0 }}</div>
              </div>
            </div>
          </div>

          <div v-if="drawerNotice" class="drawer-notice" :class="drawerNotice.tone">{{ drawerNotice.text }}</div>
          <div v-if="rawChunkFileLoading" class="loading-state drawer-loading">正在加载章节详情...</div>
          <div v-else-if="rawChunkFileError" class="drawer-notice error">{{ rawChunkFileError }}</div>
          <div v-else-if="!drawerChunks.length" class="empty-state drawer-loading">当前章节文件没有可编辑的 chunk。</div>
          <div v-else class="chunk-list">
            <section
              v-for="(chunk, index) in drawerChunks"
              :key="chunk.chunk_id"
              class="chunk-card"
              :class="{ active: activeChunkId === chunk.chunk_id }"
              @click="toggleChunkCard(chunk)"
            >
              <div class="chunk-card__header">
                <div>
                  <div class="chunk-card__title">Chunk {{ chunk.chunk_index + 1 }}</div>
                  <div class="chunk-card__id">{{ chunk.chunk_id }}</div>
                </div>
                <div class="chunk-card__actions" @click.stop>
                  <button
                    type="button"
                    class="toolbar-btn raw-chunk-editor-btn raw-chunk-editor-btn--danger"
                    data-testid="chunk-delete-button"
                    :disabled="rawChunkActionBusy || rawChunkReprocessBusy"
                    @click.stop="deleteChunkFromFile(chunk)"
                  >
                    删除
                  </button>
                  <button
                    type="button"
                    class="toolbar-btn raw-chunk-editor-btn"
                    data-testid="chunk-split-button"
                    :disabled="rawChunkActionBusy || rawChunkReprocessBusy"
                    @click.stop="handleSplitButton(chunk)"
                  >
                    {{ activeChunkId === chunk.chunk_id && activeChunkMode === 'split' ? '确认拆分' : '拆分' }}
                  </button>
                  <button
                    type="button"
                    class="toolbar-btn raw-chunk-editor-btn"
                    data-testid="chunk-merge-next-button"
                    :disabled="rawChunkActionBusy || rawChunkReprocessBusy || !drawerChunks[index + 1]"
                    :title="drawerChunks[index + 1] ? '仅允许与下一相邻 chunk 合并' : '当前已是最后一个 chunk，不能向后合并'"
                    @click.stop="mergeWithNextChunk(chunk, drawerChunks[index + 1] ?? null)"
                  >
                    向后合并
                  </button>
                </div>
              </div>

              <div class="chunk-card__meta">
                <span class="status-chip info"><span class="status-dot"></span>{{ chunk.chunk_type || 'text' }}</span>
                <span class="chunk-card__breadcrumb">{{ chunk.breadcrumb || '无 breadcrumb' }}</span>
              </div>

              <p v-if="activeChunkId !== chunk.chunk_id" class="chunk-card__preview">{{ previewText(chunk.content) }}</p>

              <div v-else class="chunk-editor" @click.stop>
                <template v-if="activeChunkMode === 'edit'">
                  <div class="chunk-editor__grid">
                    <label class="chunk-editor__field">
                      <span class="chunk-editor__label">Breadcrumb</span>
                      <input v-model="editForm.breadcrumb" class="field-input" type="text" />
                    </label>
                    <label class="chunk-editor__field">
                      <span class="chunk-editor__label">Chunk Type</span>
                      <input v-model="editForm.chunkType" class="field-input" type="text" />
                    </label>
                  </div>

                  <label class="chunk-editor__field">
                    <span class="chunk-editor__label">Content</span>
                    <textarea
                      v-model="editForm.content"
                      class="field-textarea chunk-editor__textarea"
                      data-testid="chunk-editor-textarea"
                    ></textarea>
                  </label>

                  <label v-if="showChunkMetadataEditor" class="chunk-editor__field">
                    <span class="chunk-editor__label">Metadata JSON</span>
                    <textarea v-model="editForm.metadataText" class="field-textarea chunk-editor__metadata"></textarea>
                  </label>

                  <div v-if="chunkEditorError" class="drawer-notice error">{{ chunkEditorError }}</div>

                  <div class="chunk-editor__footer">
                    <button
                      type="button"
                      class="detail-action detail-action-blue drawer-footer-btn"
                      data-testid="chunk-save-button"
                      :disabled="rawChunkActionBusy || rawChunkReprocessBusy"
                      @click="saveChunkChanges(chunk)"
                    >
                      {{ rawChunkActionBusy ? '正在保存...' : '保存修改' }}
                    </button>
                    <button
                      type="button"
                      class="detail-action detail-action-slate drawer-footer-btn"
                      :disabled="rawChunkActionBusy || rawChunkReprocessBusy"
                      @click.stop="discardChunkChanges()"
                    >
                      放弃修改
                    </button>
                  </div>
                </template>

                <template v-else>
                  <div class="chunk-helper">
                    拆分会用当前 chunk 替换成两个新 chunk，并保持它们在当前文件中的顺序位置。
                  </div>

                  <div class="chunk-editor__split-grid">
                    <label class="chunk-editor__field">
                      <span class="chunk-editor__label">左侧内容</span>
                      <textarea v-model="splitForm.leftContent" class="field-textarea chunk-editor__textarea"></textarea>
                    </label>
                    <label class="chunk-editor__field">
                      <span class="chunk-editor__label">右侧内容</span>
                      <textarea v-model="splitForm.rightContent" class="field-textarea chunk-editor__textarea"></textarea>
                    </label>
                  </div>

                  <div v-if="chunkEditorError" class="drawer-notice error">{{ chunkEditorError }}</div>

                  <div class="chunk-editor__footer">
                    <button
                      type="button"
                      class="detail-action detail-action-teal drawer-footer-btn"
                      :disabled="rawChunkActionBusy || rawChunkReprocessBusy"
                      @click="openEditMode(chunk)"
                    >
                      返回编辑
                    </button>
                    <button
                      type="button"
                      class="detail-action detail-action-slate drawer-footer-btn"
                      :disabled="rawChunkActionBusy || rawChunkReprocessBusy"
                      @click.stop="discardChunkChanges()"
                    >
                      放弃修改
                    </button>
                  </div>
                </template>
              </div>
            </section>
          </div>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.source-entry {
  min-height: calc(100vh - var(--topbar-height));
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 24px;
}

.source-entry-panel {
  width: min(100%, 1040px);
  display: grid;
  gap: 20px;
}

.source-entry-header h2 {
  margin: 0;
  font-size: 28px;
  color: var(--text-sidebar-primary);
}

.source-entry-header p {
  margin: 10px 0 0;
  color: var(--text-sidebar-muted);
  line-height: 1.7;
}

.source-entry-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.source-entry-card {
  display: grid;
  gap: 16px;
  padding: 22px;
  border-radius: 22px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 16px 36px rgba(148, 163, 184, 0.12);
}

.source-entry-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.source-entry-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-sidebar-primary);
}

.source-entry-desc,
.source-current-desc,
.source-current-path,
.detail-note {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-sidebar-muted);
}

.source-entry-desc {
  margin-top: 8px;
}

.source-entry-status {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.source-entry-status--success {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
}

.source-entry-status--warning {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.source-entry-meta {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: var(--text-sidebar-secondary);
}

.document-subtext--path {
  font-size: 11px;
  line-height: 1.65;
  color: var(--text-sidebar-muted);
  word-break: break-word;
  overflow-wrap: anywhere;
}

.knowledge-layout {
  display: flex;
  height: 100%;
  min-height: 0;
  background: transparent;
  position: relative;
  overflow: hidden;
}

.knowledge-sidebar,
.knowledge-detail {
  flex-shrink: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  background: rgba(248, 251, 255, 0.88);
  box-sizing: border-box;
}

.knowledge-sidebar {
  width: 218px;
  min-width: 218px;
  max-width: 218px;
  border-right: 1px solid var(--border-sidebar);
  overflow-y: auto;
  overflow-x: hidden;
  align-items: stretch;

  box-sizing: border-box;
}

.knowledge-detail {
width: 296px;
  gap: 0;
  padding: 0;
  border-left: 1px solid var(--border-sidebar);
  background: linear-gradient(180deg, #f7fbff 0%, #eff6ff 100%);
  min-width: 0;
  overflow: hidden;
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  transform: translateX(100%);
  opacity: 0;
  pointer-events: none;
  transition:
    transform 0.24s ease,
    opacity 0.24s ease;
}

.knowledge-detail.open {
  transform: translateX(0);
  opacity: 1;
  pointer-events: auto;
}

.kb-panel {
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid var(--border-sidebar);
  border-radius: 16px;
  padding: 16px;
  box-shadow: 0 8px 24px rgba(148, 163, 184, 0.08);
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  width: 100%;
  box-sizing: border-box;
}

.queue-panel {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 0;
}

.kb-panel-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-sidebar-primary);
  letter-spacing: 0.02em;
}

.kb-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.toolbar-btn--compact {
  height: 28px;
  padding: 0 10px;
}

.source-current-title {
  margin-top: 8px;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-sidebar-primary);
}

.source-current-desc,
.source-current-path {
  margin-top: 8px;
}

.stats-mini-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.stats-mini-card {
  padding: 12px;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fbff 0%, #edf5ff 100%);
  border: 1px solid #e0ecfb;
}

.stats-mini-value {
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.stats-mini-label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-sidebar-muted);
}

.upload-panel {
  border-radius: 18px;
  border: 2px dashed rgba(59, 130, 246, 0.22);
  background: linear-gradient(180deg, rgba(239, 246, 255, 0.92) 0%, rgba(219, 234, 254, 0.72) 100%);
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease,
    transform 0.2s ease;
}

.upload-panel.dragging {
  border-color: rgba(59, 130, 246, 0.5);
  box-shadow: 0 0 30px rgba(59, 130, 246, 0.14);
  transform: translateY(-1px);
}

.upload-dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 22px 18px;
  cursor: pointer;
}

.upload-icon {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: rgba(59, 130, 246, 0.12);
  color: var(--accent-primary);
  margin-bottom: 12px;
}

.upload-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-sidebar-primary);
}

.upload-subtitle {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-sidebar-muted);
  line-height: 1.6;
}

.upload-cta {
  margin-top: 14px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: 10px;
  background: rgba(219, 234, 254, 0.88);
  border: 1px solid rgba(59, 130, 246, 0.18);
  color: var(--accent-primary);
  font-size: 12px;
  font-weight: 600;
}

.upload-file-list,
.category-list,
.queue-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
}

.queue-list {
  max-height: 180px;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 2px;
}

.category-panel {
  display: grid;
  gap: 12px;
  padding: 14px;
}

.category-panel .kb-panel-title {
  padding: 0 2px;
}

.category-panel .category-list {
  gap: 10px;
}

.upload-file-item {
  min-width: 0;
  width: 100%;
  max-width: 100%;
  padding: 10px 12px;
  background: #f3f8ff;
  border: 1px solid #dceafe;
  border-radius: 12px;
  box-sizing: border-box;
}

.upload-file-name {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-sidebar-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.upload-file-size,
.queue-text,
.queue-empty {
  font-size: 12px;
  color: var(--text-sidebar-muted);
}

.pill {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}

.pill-blue {
  background: rgba(59, 130, 246, 0.12);
  color: var(--accent-primary);
}

.pill-amber {
  background: rgba(245, 158, 11, 0.12);
  color: #f59e0b;
}

.pill-slate {
  background: rgba(148, 163, 184, 0.14);
  color: #64748b;
}

.category-item {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 14px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-sidebar-secondary);
  transition:
    background 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease;
}

.category-item:hover {
  background: rgba(239, 246, 255, 0.9);
}

.category-item.active {
  background: linear-gradient(135deg, rgba(219, 234, 254, 0.96) 0%, rgba(191, 219, 254, 0.74) 100%);
  border-color: rgba(59, 130, 246, 0.18);
  color: var(--accent-primary);
}

.category-item-main {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 600;
}

.category-count {
  min-width: 24px;
  padding: 3px 7px;
  border-radius: 999px;
  font-size: 11px;
  text-align: center;
  background: #edf4ff;
  color: var(--text-sidebar-muted);
}

.category-item.active .category-count {
  background: rgba(59, 130, 246, 0.14);
  color: var(--accent-primary);
}

.queue-item {
  display: block;
  align-items: center;
  min-width: 0;
  width: 100%;
  max-width: 100%;
  padding: 10px 12px;
  border-radius: 12px;
  background: #f6faff;
  border: 1px solid #deebfb;
  overflow: hidden;
  box-sizing: border-box;
}

.queue-title {
  display: block;
  flex: 1;
  min-width: 0;
  width: 100%;
  max-width: 100%;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-sidebar-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.document-progress-track,
.scan-banner-track {
  flex: 1;
  height: 6px;
  border-radius: 999px;
  overflow: hidden;
  background: #dce7f8;
}

.document-progress-bar,
.scan-banner-bar {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--accent-primary), var(--accent-secondary));
}

.scan-banner {
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid #deebfb;
}

.scan-banner-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--text-sidebar-secondary);
}

.scan-banner-head strong {
  color: var(--accent-primary);
}

.knowledge-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: margin-right 0.24s ease;
}

.knowledge-main.detail-open {
  margin-right: 296px;
}

.knowledge-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px 16px;
  border-bottom: 1px solid var(--border-sidebar);
  background: rgba(248, 251, 255, 0.78);
  backdrop-filter: blur(10px);
  flex-shrink: 0;
}

.knowledge-search {
  display: flex;
  align-items: center;
  gap: 8px;
  width: min(100%, 360px);
  height: 34px;
  padding: 0 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dce7f8;
  color: var(--text-sidebar-muted);
}

.knowledge-search input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 12px;
  color: var(--text-sidebar-primary);
}

.toolbar-btn,
.view-toggle-btn {
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.92);
  color: var(--text-sidebar-secondary);
  font-size: 12px;
  font-weight: 600;
}

.view-toggle {
  display: inline-flex;
  border-radius: 10px;
  border: 1px solid #dce7f8;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.92);
}

.view-toggle-btn {
  width: 34px;
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
}

.view-toggle-btn.active {
  background: rgba(219, 234, 254, 0.92);
  color: var(--accent-primary);
}

.toolbar-meta {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-sidebar-muted);
}

.danger-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid rgba(239, 68, 68, 0.2);
  background: rgba(239, 68, 68, 0.1);
  color: #ef4444;
  font-size: 12px;
  font-weight: 600;
}

.toolbar-error {
  margin: 14px 18px 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.14);
  color: #ef4444;
  font-size: 12px;
}

.knowledge-content {
  flex: 1;
  overflow: auto;
  padding: 18px;
}

.document-table-wrap {
  display: grid;
  gap: 8px;
  max-width: 980px;
}

.document-table-head,
.document-row {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) 96px 88px 88px 106px 92px;
  gap: 12px;
  align-items: center;
}

.document-table-head {
  padding: 0 16px 8px;
  border-bottom: 1px solid #dce7f8;
  color: var(--text-sidebar-muted);
  font-size: 11px;
  font-weight: 600;
}

.document-row {
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.88);
  cursor: pointer;
  transition:
    background 0.2s ease,
    border-color 0.2s ease,
    transform 0.2s ease;
}

.document-row:hover {
  background: rgba(247, 251, 255, 0.98);
}

.document-row.active {
  background: linear-gradient(180deg, rgba(239, 246, 255, 0.98) 0%, rgba(219, 234, 254, 0.74) 100%);
  border-color: rgba(59, 130, 246, 0.26);
  transform: translateX(4px);
}

.document-main {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.document-badge,
.document-card-badge,
.detail-file-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  color: white;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.document-badge {
  width: 30px;
  height: 30px;
  font-size: 9px;
  flex-shrink: 0;
}

.document-main-text {
  flex: 1;
  min-width: 0;
}

.document-name,
.document-card-title,
.detail-file-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-sidebar-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.document-subtext,
.document-card-meta,
.detail-file-size {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-sidebar-muted);
}

.document-progress {
  margin-top: 8px;
  width: 100%;
}

.resource-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-size: 11px;
  font-weight: 700;
}

.resource-pill--input_dir {
  color: #0f766e;
  background: rgba(20, 184, 166, 0.12);
  border-color: rgba(20, 184, 166, 0.18);
}

.resource-pill--pipeline {
  color: #7c3aed;
  background: rgba(139, 92, 246, 0.12);
  border-color: rgba(139, 92, 246, 0.18);
}

.capability-cell {
  align-self: stretch;
}

.capability-list,
.detail-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.capability-chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(241, 245, 249, 0.92);
  border: 1px solid #dce7f8;
  color: var(--text-sidebar-secondary);
  font-size: 10px;
  font-weight: 600;
}

.table-cell {
  font-size: 12px;
  color: var(--text-sidebar-secondary);
}

.node-cell {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #14b8a6;
}

.type-pill {
  display: inline-flex;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(219, 234, 254, 0.96);
  border: 1px solid rgba(59, 130, 246, 0.12);
  color: var(--accent-primary);
  font-size: 11px;
  font-weight: 600;
}

.status-inline {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
}

.spin-icon {
  animation: spin 1s linear infinite;
}

.document-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px;
  max-width: 980px;
}

.document-card {
  padding: 16px;
  border-radius: 18px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.9);
  cursor: pointer;
  transition:
    transform 0.2s ease,
    border-color 0.2s ease,
    box-shadow 0.2s ease;
}

.document-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 22px rgba(148, 163, 184, 0.08);
}

.document-card.active {
  border-color: rgba(59, 130, 246, 0.24);
  background: linear-gradient(180deg, rgba(247, 251, 255, 0.98) 0%, rgba(219, 234, 254, 0.66) 100%);
  transform: translateY(-2px);
}

.document-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.document-card-badge {
  width: 40px;
  height: 40px;
  font-size: 10px;
}

.status-chip-small {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 999px;
  border: 1px solid;
  font-size: 11px;
  font-weight: 600;
}

.document-card-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
  font-size: 11px;
  color: var(--text-sidebar-secondary);
}

.document-card-stats span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--border-sidebar);
}

.detail-close {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-sidebar-muted);
  font-size: 18px;
  line-height: 1;
}

.detail-body {
  padding: 12px;
  display: grid;
  gap: 12px;
  overflow: auto;
}

.detail-file-card,
.detail-graph-card,
.detail-summary {
  padding: 14px;
  border-radius: 16px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.88);
}

.detail-file-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 14px;
}

.detail-file-text {
  min-width: 0;
  flex: 1;
}

.detail-file-badge {
  width: 36px;
  height: 36px;
  font-size: 10px;
  flex-shrink: 0;
}

.detail-file-size {
  font-size: 12px;
  margin-top: 2px;
}

.detail-meta {
  display: grid;
  gap: 10px;
}

.detail-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  font-size: 12px;
}

.detail-row span {
  color: var(--text-sidebar-muted);
}

.detail-row strong {
  max-width: 164px;
  text-align: right;
  color: var(--text-sidebar-primary);
  font-weight: 600;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.truncate-text {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.detail-file-name {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.detail-graph-card {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.9) 0%, rgba(209, 250, 229, 0.85) 100%);
  border-color: rgba(20, 184, 166, 0.16);
}

.detail-graph-title,
.detail-summary-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-sidebar-primary);
  margin-bottom: 10px;
}

.detail-graph-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.detail-graph-value {
  font-size: 20px;
  font-weight: 700;
  color: #14b8a6;
}

.detail-graph-label,
.detail-summary-text {
  font-size: 12px;
  color: var(--text-sidebar-muted);
  line-height: 1.65;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.detail-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.14);
  color: #ef4444;
  font-size: 12px;
}

.detail-actions {
  display: grid;
  gap: 6px;
  padding: 10px;
  border-radius: 14px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.9);
}

.detail-note-card {
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid transparent;
  font-size: 12px;
  line-height: 1.7;
}

.detail-note-card--info {
  color: var(--accent-primary);
  background: rgba(219, 234, 254, 0.7);
  border-color: rgba(59, 130, 246, 0.18);
}

.detail-note-card--warning {
  color: #b45309;
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.22);
}

.detail-loading {
  min-height: 160px;
}

.detail-notice {
  margin: 14px 18px 0;
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid transparent;
  font-size: 12px;
  line-height: 1.6;
}

.detail-notice.success {
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border-color: rgba(16, 185, 129, 0.22);
}

.detail-notice.warning {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.1);
  border-color: rgba(245, 158, 11, 0.22);
}

.detail-notice.error {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.22);
}

.detail-notice.info {
  color: var(--accent-primary);
  background: rgba(219, 234, 254, 0.7);
  border-color: rgba(59, 130, 246, 0.18);
}

.detail-note {
  margin-top: 14px;
}

.detail-action {
  width: 100%;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 8px 11px;
  border-radius: 10px;
  border: 1px solid;
  background: rgba(255, 255, 255, 0.88);
  font-size: 12px;
  font-weight: 600;
}

.detail-action-blue {
  color: var(--accent-primary);
  border-color: rgba(59, 130, 246, 0.16);
}

.detail-action-teal {
  color: #14b8a6;
  border-color: rgba(20, 184, 166, 0.18);
}

.detail-action-slate {
  color: var(--text-sidebar-secondary);
  border-color: #dce7f8;
}

.detail-action-red {
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.16);
}

.raw-chunk-panel {
  margin-bottom: 8px;
  padding: 12px;
  border-radius: 14px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 8px 24px rgba(148, 163, 184, 0.08);
  display: grid;
  gap: 8px;
}

.drawer-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-sidebar-primary);
}

.drawer-subtitle {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-sidebar-muted);
}

.raw-chunk-loading,
.raw-chunk-empty {
  margin-top: 8px;
}

.raw-chunk-inline-error {
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.14);
  color: #ef4444;
  font-size: 12px;
}

.raw-chunk-file-table {
  display: grid;
  gap: 8px;
}

.raw-chunk-file-table-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 0 4px;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-sidebar-muted);
}

.raw-chunk-file-list {
  display: grid;
  gap: 6px;
  max-height: min(42vh, 300px);
  overflow-y: auto;
  padding-right: 2px;
}

.raw-chunk-file-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  text-align: left;
  border-radius: 10px;
  border: 1px solid #dce7f8;
  background: rgba(248, 251, 255, 0.86);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

.raw-chunk-file-item:hover {
  border-color: rgba(59, 130, 246, 0.28);
  box-shadow: 0 4px 14px rgba(59, 130, 246, 0.08);
}

.raw-chunk-file-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-sidebar-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.raw-chunk-file-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 58px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: #b45309;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.18);
}

.drawer-status-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.2;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: currentColor;
  opacity: 0.85;
}

.status-chip.success {
  color: #10b981;
  background: rgba(16, 185, 129, 0.12);
  border-color: rgba(16, 185, 129, 0.22);
}

.status-chip.warning {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.22);
}

.status-chip.error {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.2);
}

.status-chip.info {
  color: var(--accent-primary);
  background: rgba(59, 130, 246, 0.1);
  border-color: rgba(59, 130, 246, 0.18);
}

.raw-chunk-editor-btn {
  padding: 4px 10px;
  min-height: 28px;
  font-size: 11px;
  line-height: 1.2;
}

.raw-chunk-editor-btn:disabled {
  background: #f1f5f9;
  border-color: #dbe3ef;
  color: #94a3b8;
  cursor: not-allowed;
}

.raw-chunk-editor-btn--danger {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.24);
  background: rgba(254, 242, 242, 0.92);
}

.raw-chunk-editor-btn--danger:not(:disabled):hover {
  border-color: rgba(220, 38, 38, 0.38);
  background: rgba(254, 226, 226, 0.96);
}

.drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  justify-content: flex-end;
  background: rgba(15, 23, 42, 0.28);
}

.drawer-panel {
  width: min(780px, 100vw);
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: rgba(248, 251, 255, 0.98);
  border-left: 1px solid var(--border-sidebar);
  box-shadow: 0 24px 48px rgba(15, 23, 42, 0.18);
}

.drawer-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px;
  border-bottom: 1px solid #deebfb;
  background: rgba(255, 255, 255, 0.92);
}

.drawer-close {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.92);
  color: var(--text-sidebar-muted);
  font-size: 20px;
  line-height: 1;
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px 32px;
}

.drawer-card {
  padding: 18px;
  border-radius: 18px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.9);
}

.drawer-card--compact {
  padding: 14px 16px;
  border-radius: 14px;
}

.drawer-meta-grid,
.chunk-editor__grid,
.chunk-editor__split-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.drawer-meta-grid--simple {
  grid-template-columns: repeat(12, minmax(0, 1fr));
  align-items: start;
}

.drawer-meta-item--doc {
  grid-column: 1 / 5;
}

.drawer-meta-item--chapter {
  grid-column: 5 / 9;
  justify-self: center;
}

.drawer-meta-item--chapter .drawer-meta-label,
.drawer-meta-item--chapter .drawer-meta-value {
  text-align: center;
}

.drawer-meta-item--count {
  grid-column: 11 / 13;
  justify-self: end;
}

.drawer-meta-item--count .drawer-meta-label,
.drawer-meta-item--count .drawer-meta-value {
  text-align: center;
}

.drawer-meta-label,
.chunk-editor__label {
  display: block;
  margin-bottom: 6px;
  font-size: 11px;
  color: var(--text-sidebar-muted);
}

.drawer-meta-value {
  font-size: 13px;
  color: var(--text-sidebar-primary);
}

.drawer-meta-value--doc {
  font-weight: 600;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.drawer-meta-value--chapter {
  font-weight: 600;
}

.drawer-meta-value--path {
  word-break: break-all;
}

.drawer-status-row {
  margin-top: 16px;
}

.drawer-notice {
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid transparent;
  font-size: 12px;
  line-height: 1.6;
}

.drawer-notice.success {
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border-color: rgba(16, 185, 129, 0.22);
}

.drawer-notice.warning {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.1);
  border-color: rgba(245, 158, 11, 0.22);
}

.drawer-notice.error {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.22);
}

.drawer-notice.info {
  color: var(--accent-primary);
  background: rgba(219, 234, 254, 0.7);
  border-color: rgba(59, 130, 246, 0.18);
}

.drawer-loading {
  min-height: 180px;
}

.chunk-list {
  display: grid;
  gap: 14px;
  margin-top: 16px;
}

.chunk-card {
  padding: 18px;
  border-radius: 18px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.92);
  cursor: pointer;
}

.chunk-card.active {
  border-color: rgba(59, 130, 246, 0.3);
  box-shadow: 0 16px 32px rgba(59, 130, 246, 0.15), 0 4px 12px rgba(59, 130, 246, 0.1);
  background: #fff;
  cursor: default;
}

.chunk-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.chunk-card__title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-sidebar-primary);
}

.chunk-card__id {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-sidebar-muted);
  word-break: break-all;
}

.chunk-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.chunk-card__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}

.chunk-card__breadcrumb {
  font-size: 12px;
  color: var(--text-sidebar-secondary);
}

.chunk-card__preview {
  margin: 12px 0 0;
  padding-top: 12px;
  border-top: 1px solid #deebfb;
  font-size: 13px;
  line-height: 1.65;
  color: var(--text-sidebar-secondary);
  white-space: pre-wrap;
}

.chunk-editor {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #deebfb;
}

.chunk-editor__field {
  display: block;
}

.field-input,
.field-textarea {
  width: 100%;
  border-radius: 12px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.96);
  color: var(--text-sidebar-primary);
  font-size: 12px;
  outline: none;
}

.field-input {
  height: 36px;
  padding: 0 12px;
}

.field-textarea {
  padding: 10px 12px;
  line-height: 1.6;
  resize: vertical;
}

.chunk-editor__textarea {
  min-height: 180px;
}

.chunk-editor__metadata {
  min-height: 150px;
}

.chunk-editor__footer {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}

.drawer-footer-btn {
  width: auto;
}

.chunk-helper {
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(239, 246, 255, 0.92);
  color: var(--text-sidebar-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.detail-slide-enter-active,
.detail-slide-leave-active {
  transition:
    transform 0.24s ease,
    opacity 0.24s ease;
}

.detail-slide-enter-from,
.detail-slide-leave-to {
  transform: translateX(18px);
  opacity: 0;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1440px) {
  .source-entry-grid {
    grid-template-columns: 1fr;
  }

  .knowledge-sidebar {
    width: 218px;
  }

  .knowledge-detail {
    width: 256px;
  }

  .knowledge-main.detail-open {
    margin-right: 256px;
  }

  .document-table-head,
  .document-row {
    grid-template-columns: minmax(220px, 1fr) 90px 80px 82px 96px 82px;
  }
}

@media (max-width: 1180px) {
  .knowledge-layout {
    height: auto;
    overflow: visible;
    flex-direction: column;
  }

  .knowledge-main.detail-open {
    margin-right: 0;
  }

  .knowledge-sidebar,
  .knowledge-detail {
    width: 100%;
    border-left: none;
    border-right: none;
  }

  .knowledge-sidebar {
    border-bottom: 1px solid var(--border-sidebar);
  }

  .knowledge-detail {
    position: static;
    transform: none;
    opacity: 1;
    pointer-events: auto;
    border-top: 1px solid var(--border-sidebar);
  }
}

@media (max-width: 900px) {
  .knowledge-toolbar {
    flex-wrap: wrap;
  }

  .drawer-meta-grid,
  .chunk-editor__grid,
  .chunk-editor__split-grid {
    grid-template-columns: 1fr;
  }

  .chunk-card__header {
    flex-direction: column;
  }

  .chunk-card__actions {
    justify-content: flex-start;
  }

  .drawer-panel {
    width: 100vw;
  }

  .toolbar-meta {
    margin-left: 0;
  }

  .document-table-head,
  .document-row {
    grid-template-columns: minmax(220px, 1fr) 88px 88px 92px;
  }

  .document-table-head > :nth-child(4),
  .document-table-head > :nth-child(5),
  .document-table-head > :nth-child(7),
  .document-row > :nth-child(4),
  .document-row > :nth-child(5),
  .document-row > :nth-child(7) {
    display: none;
  }
}

@media (max-width: 640px) {
  .knowledge-content,
  .knowledge-sidebar,
  .detail-body {
    padding: 14px;
  }

  .raw-chunk-panel,
  .drawer-body,
  .drawer-header {
    padding-left: 14px;
    padding-right: 14px;
  }

  .document-grid {
    grid-template-columns: 1fr;
  }

  .stats-mini-grid {
    grid-template-columns: 1fr 1fr;
  }

}
</style>
