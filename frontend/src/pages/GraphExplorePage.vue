<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import {
  ArrowLeft,
  BookOpen,
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  GitBranch,
  Info,
  Layers,
  RotateCcw,
  Search,
  Tag,
  X,
  ZoomIn,
  ZoomOut
} from 'lucide-vue-next';

import GraphCanvas from '@/components/GraphCanvas.vue';
import {
  fetchGraphByLabel,
  fetchGraphFull,
  fetchGraphLabels,
  fetchGraphNodeDetail,
  fetchGraphSummary
} from '@/api/graph';
import { fetchSystemConfig } from '@/api/system';
import type {
  GraphNode,
  GraphNodeDetail,
  GraphResponse,
  GraphSummaryResponse,
  SystemConfigResponse
} from '@/types/api';
import { renderRichText, renderRichTextInline } from '@/utils/richText';

type GraphViewMode = 'full' | 'label';
type PageMode = 'explore' | 'read';

interface HoverNode {
  id: string;
  label: string;
  entityType: string;
}

interface NarrativeSection {
  label: string;
  key: string;
  paragraphs: string[];
}

interface RelationPreviewItem {
  label: string;
  type: string;
  direction: string;
}

interface FaultcardFieldConfig {
  section: string;
  propertyKeys: string[];
  aliases: string[];
}

interface EmptyStateCopy {
  title: string;
  subtitle: string;
}

interface GraphLabelEntry {
  label: string;
  entity_type: string;
}

interface SyntheticFaultPropertyNode extends GraphNode {
  id: string;
  label: string;
  labels?: string[];
  entity_type: 'FAULT_PROPERTY';
  description: string;
  source_id: string;
  faultcase_id: string;
  faultcase_label: string;
  property_label: string;
}

const defaultRootEntityTypes = ['DOCROOT', 'CHUNK'] as const;
const searchableRootEntityTypes = ['DOCROOT', 'CHUNK', 'EQUIPMENT', 'COMPONENT'] as const;
const rootTypePriority = new Map<string, number>(
  [...searchableRootEntityTypes].map((type, index) => [type, index])
);

const labels = ref<GraphLabelEntry[]>([]);
const graphSummary = ref<GraphSummaryResponse | null>(null);
const systemConfig = ref<SystemConfigResponse | null>(null);
const graph = ref<GraphResponse>({ nodes: [], edges: [] });
const graphMode = ref<GraphViewMode>('full');
const pageMode = ref<PageMode>('explore');
const currentRootLabel = ref('');
const activeNodeLabel = ref('');
const focusedNodeLabel = ref('');
const detailPanelVisible = ref(false);
const selectedNode = ref<GraphNodeDetail | null>(null);
const hoveredNode = ref<HoverNode | null>(null);
const loadingSummary = ref(true);
const loadingGraph = ref(false);
const loadingNodeDetail = ref(false);
const graphError = ref('');
const detailError = ref('');
const summaryWarning = ref('');
const rootSearch = ref('');
const selectedTypes = ref<string[]>([]);
const canvasRef = ref<InstanceType<typeof GraphCanvas> | null>(null);
const expandedRelations = ref(false);
const expandedSectionKeys = ref<string[]>([]);

let graphRequestToken = 0;
let detailRequestToken = 0;

const typeColorMap: Record<string, string> = {
  EQUIPMENT: '#3B82F6',
  COMPONENT: '#60A5FA',
  FAULTCASE: '#F59E0B',
  FAULT_PROPERTY: '#10B981',
  FAULT: '#EF4444',
  CAUSE: '#9333EA',
  ACTION: '#10B981',
  FAULTCODE: '#8B5CF6',
  DOCROOT: '#64748B',
  CHUNK: '#64748B',
  UNKNOWN: '#94A3B8'
};

const typeSurfaceMap: Record<string, string> = {
  EQUIPMENT: 'var(--accent-lighter)',
  COMPONENT: 'var(--accent-light)',
  FAULTCASE: 'var(--warning-light)',
  FAULT_PROPERTY: 'var(--success-light)',
  FAULT: 'var(--error-light)',
  CAUSE: 'var(--purple-light)',
  ACTION: 'var(--success-light)',
  FAULTCODE: 'var(--purple-light)',
  DOCROOT: 'var(--bg-tertiary)',
  CHUNK: 'var(--bg-tertiary)',
  UNKNOWN: 'var(--border-light)'
};

const typeLabelMap: Record<string, string> = {
  EQUIPMENT: '设备',
  COMPONENT: '部件',
  FAULTCASE: '故障卡',
  FAULT_PROPERTY: '故障属性',
  FAULT: '故障',
  CAUSE: '原因',
  ACTION: '处理建议',
  FAULTCODE: '故障码',
  DOCROOT: '文档',
  CHUNK: '证据块',
  UNKNOWN: '其他'
};

const hiddenPropertyKeys = new Set([
  'entity_name',
  'source_id',
  'source-id',
  'source id',
  'source_doc_id',
  'source_doc_ids',
  'chunk_id',
  'chunk_ids',
  'file_path',
  'debug',
  'debug_info'
]);

const narrativeFieldGroups = [
  {
    label: '故障现象',
    keys: ['phenomenon', 'symptom', 'symptoms', 'fault_phenomenon', '故障现象', '现象']
  },
  {
    label: '原因',
    keys: ['cause', 'causes', 'reason', 'fault_cause', '故障原因', '原因']
  },
  {
    label: '处理建议',
    keys: ['action', 'actions', 'solution', 'repair', 'repair_method', '维修方法', '处理', '处理方法']
  },
  {
    label: '注意事项',
    keys: ['notice', 'caution', 'precaution', '注意事项', '注意']
  },
  {
    label: '后果',
    keys: ['consequence', 'effect', 'impact', '后果']
  },
  {
    label: '关键部件',
    keys: ['component', 'components', 'part', 'parts', '关键部件', '部件']
  },
  {
    label: '日常维护',
    keys: ['maintenance', 'maintain', 'daily_maintenance', '日常维护', '维护方法']
  }
] as const;

const faultcardFieldConfigs: FaultcardFieldConfig[] = [
  { section: '记录ID', propertyKeys: ['record_id', 'recordid'], aliases: ['记录ID'] },
  { section: '来源路径', propertyKeys: ['breadcrumb'], aliases: ['来源路径', 'breadcrumb'] },
  { section: '章节标题', propertyKeys: ['record_title', 'recordtitle', '章节标题'], aliases: ['章节标题'] },
  { section: '装备', propertyKeys: ['equipment', '装备'], aliases: ['装备'] },
  { section: '故障卡片', propertyKeys: ['fault_card', 'faultcard', 'fault', '故障卡片'], aliases: ['故障卡片'] },
  { section: '故障现象', propertyKeys: ['symptom', 'fault_phenomenon', 'phenomenon', '故障现象'], aliases: ['故障现象', '现象'] },
  { section: '可能原因', propertyKeys: ['causes', 'cause', 'reason', '可能原因', '原因'], aliases: ['可能原因', '原因'] },
  { section: '处理步骤', propertyKeys: ['actions', 'action', 'repair_method', '处理步骤', '处理建议'], aliases: ['处理步骤', '处理建议', '维修方法'] },
  { section: '可能后果', propertyKeys: ['consequences', 'consequence', 'effect', 'impact', '可能后果', '后果'], aliases: ['可能后果', '后果'] },
  { section: '注意事项', propertyKeys: ['precautions', 'precaution', 'notice', '注意事项'], aliases: ['注意事项', '注意'] },
  { section: '关键部件', propertyKeys: ['key_components', 'keycomponents', 'components', 'component', '关键部件'], aliases: ['关键部件'] },
  { section: '原始文本', propertyKeys: ['source_text', 'sourcetext', '原始文本'], aliases: ['原始文本'] }
];

const expandableFaultPropertyLabels = new Set([
  '故障现象',
  '可能原因',
  '处理步骤',
  '注意事项',
  '关键部件'
]);

function normalizeType(entityType: unknown) {
  return String(entityType ?? 'UNKNOWN').replace(/"/g, '').toUpperCase();
}

function displayTypeLabel(type: string) {
  return typeLabelMap[type] ?? type;
}

function displayTypeColor(type: string) {
  return typeColorMap[type] ?? typeColorMap.UNKNOWN;
}

function displayTypeSurface(type: string) {
  return typeSurfaceMap[type] ?? typeSurfaceMap.UNKNOWN;
}

function graphNodeId(node: GraphResponse['nodes'][number], fallback = '') {
  return String(node.id ?? node.labels?.[0] ?? fallback);
}

function graphNodeLabel(node: GraphResponse['nodes'][number], fallback = '') {
  return String(node.labels?.[0] ?? node.id ?? fallback);
}

function graphNodeKeys(node: GraphResponse['nodes'][number], fallback = '') {
  return [...new Set([graphNodeId(node, fallback), graphNodeLabel(node, fallback)].filter(Boolean))];
}

function findGraphNodeByKey(nextGraph: GraphResponse, key: string) {
  if (!key) {
    return null;
  }

  return (
    nextGraph.nodes.find((node, index) => graphNodeKeys(node, `node-${index}`).includes(key)) ?? null
  );
}

function normalizePropertyKey(key: string) {
  return key.trim().toLowerCase().replace(/[_\-\s]+/g, '');
}

function isHiddenPropertyKey(key: string) {
  return hiddenPropertyKeys.has(key.trim().toLowerCase());
}

function isMeaningfulPropertyValue(value: unknown): boolean {
  if (value === null || value === undefined) {
    return false;
  }
  if (typeof value === 'string') {
    const normalized = value.trim();
    return normalized.length > 0 && !['无', '暂无', '未提供', 'N/A', 'None', 'null'].includes(normalized);
  }
  if (Array.isArray(value)) {
    return value.some((item) => isMeaningfulPropertyValue(item));
  }
  return true;
}

function formatPropertyValue(value: unknown) {
  if (Array.isArray(value)) {
    return value.filter((item) => item !== null && item !== undefined && String(item).trim()).join('；');
  }
  if (typeof value === 'object' && value !== null) {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  return String(value).trim();
}

function buildDescriptionSections(text: string) {
  return text
    .split(/\n+|；|。/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildStructuredFieldParagraphs(text: string) {
  return text
    .split(/\n+/)
    .map((item) => item.trim())
    .filter((item) => isMeaningfulPropertyValue(item));
}

function isLongDetailSection(section: NarrativeSection) {
  const totalLength = section.paragraphs.join(' ').length;
  const lineBudget = section.label === '原始文本' ? 2 : section.label === '处理步骤' ? 5 : 4;
  const charBudget = section.label === '原始文本' ? 180 : section.label === '处理步骤' ? 320 : 220;
  return section.paragraphs.length > lineBudget || totalLength > charBudget;
}

function isSectionExpanded(sectionKey: string) {
  return expandedSectionKeys.value.includes(sectionKey);
}

function visibleSectionParagraphs(section: NarrativeSection) {
  if (!isLongDetailSection(section) || isSectionExpanded(section.key)) {
    return section.paragraphs;
  }

  const lineBudget = section.label === '原始文本' ? 2 : section.label === '处理步骤' ? 5 : 4;
  const previewParagraphs = section.paragraphs.slice(0, lineBudget);
  if (previewParagraphs.length === 1) {
    const previewText = previewParagraphs[0];
    const charBudget = section.label === '原始文本' ? 120 : section.label === '处理步骤' ? 220 : 160;
    if (previewText.length > charBudget) {
      return [`${previewText.slice(0, charBudget).trim()}…`];
    }
  }
  return previewParagraphs;
}

function toggleSectionExpanded(sectionKey: string) {
  expandedSectionKeys.value = isSectionExpanded(sectionKey)
    ? expandedSectionKeys.value.filter((key) => key !== sectionKey)
    : expandedSectionKeys.value.concat(sectionKey);
}

function normalizeFieldParagraphs(value: unknown) {
  if (Array.isArray(value)) {
    return value
      .filter((item) => isMeaningfulPropertyValue(item))
      .map((item, index) => `${index + 1}. ${String(item).trim()}`);
  }
  return buildStructuredFieldParagraphs(formatPropertyValue(value));
}

function parseBracketedCardSections(text: string): NarrativeSection[] {
  const normalizedText = String(text ?? '').trim();
  if (!normalizedText.includes('[') || !normalizedText.includes(']')) {
    return [];
  }

  const regex = /\[([^\]]+)\]\s*\n?([\s\S]*?)(?=\n\s*\[[^\]]+\]\s*\n?|$)/g;
  const sections: NarrativeSection[] = [];

  for (const match of normalizedText.matchAll(regex)) {
    const rawLabel = match[1]?.trim();
    const rawValue = match[2]?.trim();
    if (!rawLabel || !rawValue) {
      continue;
    }

    const field = faultcardFieldConfigs.find((item) => item.aliases.some((alias) => alias === rawLabel));
    if (!field) {
      continue;
    }

    sections.push({
      label: field.section,
      key: `faultcard:block:${field.section}`,
      paragraphs: buildStructuredFieldParagraphs(rawValue)
    });
  }

  return sections;
}

function parseStructuredDescriptionSections(text: string): NarrativeSection[] {
  const normalizedText = text.replace(/\s+/g, ' ').trim();
  if (!normalizedText) {
    return [];
  }

  const labelPattern = faultcardFieldConfigs
    .flatMap((field) => field.aliases)
    .map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .sort((left, right) => right.length - left.length)
    .join('|');

  const regex = new RegExp(`(${labelPattern})\\s*[:：]\\s*([\\s\\S]*?)(?=(?:${labelPattern})\\s*[:：]|$)`, 'g');
  const sections: NarrativeSection[] = [];

  for (const match of normalizedText.matchAll(regex)) {
    const rawLabel = match[1]?.trim();
    const rawValue = match[2]?.trim();
    if (!rawLabel || !rawValue) {
      continue;
    }

    const field = faultcardFieldConfigs.find((item) => item.aliases.some((alias) => alias === rawLabel));
    if (!field) {
      continue;
    }

    sections.push({
      label: field.section,
      key: `description:${field.section}`,
      paragraphs: buildStructuredFieldParagraphs(rawValue)
    });
  }

  return sections;
}

function extractExpandableFaultSections(text: string): Array<{ label: string; value: string }> {
  const normalizedText = String(text ?? '').trim();
  if (!normalizedText) {
    return [];
  }

  const parsedSections = parseBracketedCardSections(normalizedText).length
    ? parseBracketedCardSections(normalizedText)
    : parseStructuredDescriptionSections(normalizedText);

  return parsedSections
    .filter((section) => expandableFaultPropertyLabels.has(section.label))
    .map((section) => ({
      label: section.label,
      value: section.paragraphs.join('\n').trim()
    }))
    .filter((section) => isMeaningfulPropertyValue(section.value));
}

function sanitizeFileName(value: string) {
  return value.replace(/[\\/:*?"<>|]/g, '_');
}

function ensureVisibleType(type: string) {
  if (!type || selectedTypes.value.includes(type)) {
    return;
  }
  selectedTypes.value = selectedTypes.value.concat(type);
}

function availableTypesFromGraph(nextGraph: GraphResponse) {
  return [...new Set(nextGraph.nodes.map((node) => normalizeType(node.entity_type)))];
}

function buildSyntheticFaultPropertyId(faultcaseId: string, propertyLabel: string) {
  return `fault-property::${faultcaseId}::${propertyLabel}`;
}

function buildExpandedFaultPropertyGraph(nextGraph: GraphResponse): GraphResponse {
  const syntheticNodes: SyntheticFaultPropertyNode[] = [];
  const syntheticEdges: GraphResponse['edges'] = [];

  nextGraph.nodes.forEach((node, index) => {
    if (normalizeType(node.entity_type) !== 'FAULTCASE') {
      return;
    }

    const faultcaseId = graphNodeId(node, `faultcase-${index}`);
    const faultcaseLabel = graphNodeLabel(node, faultcaseId);
    const description = String(node.description ?? '').trim();
    if (!description) {
      return;
    }

    extractExpandableFaultSections(description).forEach((section) => {
      const syntheticId = buildSyntheticFaultPropertyId(faultcaseId, section.label);
      syntheticNodes.push({
        id: syntheticId,
        label: section.value,
        labels: [section.value],
        entity_type: 'FAULT_PROPERTY',
        description: section.value,
        source_id: String(node.source_id ?? ''),
        faultcase_id: faultcaseId,
        faultcase_label: faultcaseLabel,
        property_label: section.label
      });
      syntheticEdges.push({
        source: faultcaseId,
        target: syntheticId,
        type: section.label,
        direction: 'out'
      });
    });
  });

  if (!syntheticNodes.length) {
    return nextGraph;
  }

  return {
    ...nextGraph,
    nodes: nextGraph.nodes.concat(syntheticNodes),
    edges: nextGraph.edges.concat(syntheticEdges)
  };
}

function findNodeLabel(nextGraph: GraphResponse, label: string) {
  if (!label) {
    return '';
  }

  const matchedNode = nextGraph.nodes.find((node, index) =>
    graphNodeKeys(node, `node-${index}`).includes(label)
  );
  return matchedNode ? graphNodeLabel(matchedNode, label) : '';
}

function dedupeLabelEntries(entries: GraphLabelEntry[]) {
  const seen = new Set<string>();
  const result: GraphLabelEntry[] = [];

  entries.forEach((item) => {
    const label = String(item.label ?? '').trim();
    if (!label || seen.has(label)) {
      return;
    }
    seen.add(label);
    result.push({
      label,
      entity_type: String(item.entity_type ?? 'UNKNOWN').trim() || 'UNKNOWN'
    });
  });

  return result;
}

function sortRootEntries(entries: GraphLabelEntry[]) {
  return [...entries].sort((left, right) => {
    const leftType = normalizeType(left.entity_type);
    const rightType = normalizeType(right.entity_type);
    const leftPriority = rootTypePriority.get(leftType) ?? Number.MAX_SAFE_INTEGER;
    const rightPriority = rootTypePriority.get(rightType) ?? Number.MAX_SAFE_INTEGER;

    if (leftPriority !== rightPriority) {
      return leftPriority - rightPriority;
    }

    return left.label.localeCompare(right.label, 'zh-CN');
  });
}

function defaultRootEntries(entries: GraphLabelEntry[]) {
  const normalizedEntries = dedupeLabelEntries(entries);
  const grayEntries = normalizedEntries.filter((item) =>
    defaultRootEntityTypes.includes(normalizeType(item.entity_type) as (typeof defaultRootEntityTypes)[number])
  );
  if (grayEntries.length) {
    return sortRootEntries(grayEntries);
  }

  return sortRootEntries(normalizedEntries);
}

function preferredInitialRootLabel(entries: GraphLabelEntry[]) {
  const normalizedEntries = dedupeLabelEntries(entries);
  const docEntry = normalizedEntries.find((item) => normalizeType(item.entity_type) === 'DOCROOT');
  if (docEntry) {
    return docEntry.label;
  }

  const chunkEntry = normalizedEntries.find((item) => normalizeType(item.entity_type) === 'CHUNK');
  if (chunkEntry) {
    return chunkEntry.label;
  }

  return normalizedEntries[0]?.label ?? '';
}

function searchCandidateEntries(entries: GraphLabelEntry[]) {
  const normalizedEntries = dedupeLabelEntries(entries);
  const matched = normalizedEntries.filter((item) =>
    searchableRootEntityTypes.includes(normalizeType(item.entity_type) as (typeof searchableRootEntityTypes)[number])
  );
  return matched.length ? sortRootEntries(matched) : sortRootEntries(normalizedEntries);
}

function relationActionLabel(label: string) {
  if (graphMode.value === 'label' && allLabelNames.value.includes(label)) {
    return '跳转到该子图';
  }
  return '查看该节点';
}

const datasourceId = computed(
  () =>
    systemConfig.value?.server.datasource_id ||
    graph.value.datasource_id ||
    graph.value.datasource?.datasource_id ||
    graphSummary.value?.datasource_id ||
    graphSummary.value?.datasource.datasource_id ||
    systemConfig.value?.server.datasource_id ||
    ''
);

const datasourceOutputRoot = computed(
  () =>
    graph.value.datasource?.output_root ||
    graphSummary.value?.datasource.output_root ||
    ''
);

const graphWorkingDir = computed(
  () =>
    graph.value.datasource?.working_dir ||
    graphSummary.value?.datasource.working_dir ||
    systemConfig.value?.server.working_dir ||
    ''
);

function currentDatasourceIdOrThrow() {
  const scopeId = datasourceId.value.trim();
  if (!scopeId) {
    throw new Error('当前 datasource_id 尚未加载完成');
  }
  return scopeId;
}

const stageTitle = computed(() =>
  graphMode.value === 'full'
    ? 'datasource 总图谱'
    : currentRootLabel.value
      ? `围绕「${currentRootLabel.value}」展开的子图`
      : '围绕单个节点展开的子图探索'
);

const stageSubtitle = computed(() =>
  graphMode.value === 'full'
    ? '展示当前 datasource 已生成的图谱节点与关系，可直接过滤类型、查看详情并导出当前视图。'
    : '从一个起点进入后，仍可查看详情、沿关系跳转、继续过滤类型并导出当前结果。'
);

const allLabelNames = computed(() => labels.value.map((item) => item.label));
const displayedRootEntries = computed(() => defaultRootEntries(labels.value));

const searchedLabels = computed(() => {
  const query = rootSearch.value.trim().toLowerCase();
  const roots = displayedRootEntries.value;
  const searchCandidates = searchCandidateEntries(labels.value);
  const base = query
    ? searchCandidates.filter((item) => item.label.toLowerCase().includes(query))
    : roots;

  if (!currentRootLabel.value || !base.some((item) => item.label === currentRootLabel.value)) {
    return base;
  }

  const currentEntry =
    labels.value.find((item) => item.label === currentRootLabel.value) ?? {
      label: currentRootLabel.value,
      entity_type: 'UNKNOWN'
    };
  return [currentEntry, ...base.filter((item) => item.label !== currentRootLabel.value)];
});

const searchResultMeta = computed(() => {
  const query = rootSearch.value.trim();
  const total = query ? searchCandidateEntries(labels.value).length : displayedRootEntries.value.length;
  const matched = searchedLabels.value.length;

  if (!total) {
    return '';
  }

  if (query) {
    return `已匹配 ${matched} / ${total} 个灰色或蓝色节点`;
  }

  return `默认展示 ${matched} 个灰色节点`;
});

const hasGraphArtifacts = computed(() => {
  if (graph.value.graph_state === 'ready') {
    return true;
  }

  if (graph.value.nodes.length || graph.value.edges.length || labels.value.length) {
    return true;
  }

  return Boolean(
    graphSummary.value && (graphSummary.value.total_nodes > 0 || graphSummary.value.total_edges > 0)
  );
});

const filtersHideAllNodes = computed(() => {
  if (!graph.value.nodes.length) {
    return false;
  }

  return !filteredGraph.value.nodes.length;
});

const graphEmptyState = computed<EmptyStateCopy>(() => {
  if (filtersHideAllNodes.value) {
    return {
      title: '当前筛选后没有可显示内容',
      subtitle: '恢复实体类型过滤，或切换到其他视图继续浏览当前 datasource 的图谱输出。'
    };
  }

  if (!hasGraphArtifacts.value) {
    const location = graphWorkingDir.value || datasourceOutputRoot.value;
    return {
      title: '当前 datasource 还没有可浏览的图谱产物',
      subtitle: location
        ? `后端已识别当前 datasource，但 ${location} 里还没有可浏览的节点与关系。请先生成图谱产物后再返回此页。`
        : '后端已识别当前 datasource，但还没有可浏览的节点与关系。请先生成图谱产物后再返回此页。'
    };
  }

  if (graphMode.value === 'label') {
    return {
      title: '当前子图没有可显示内容',
      subtitle: '请重新选择一个子图入口，或恢复被隐藏的实体类型。'
    };
  }

  return {
    title: '当前图谱输出暂时为空',
    subtitle: '可以恢复类型过滤，或稍后刷新查看最新生成的节点与关系。'
  };
});

const expandedGraphForMetrics = computed<GraphResponse>(() =>
  buildExpandedFaultPropertyGraph(graph.value)
);

const displayGraph = computed<GraphResponse>(() =>
  graphMode.value === 'label' ? expandedGraphForMetrics.value : graph.value
);

const visibleTypeCounts = computed(() => {
  if (displayGraph.value.nodes.length) {
    const counts = new Map<string, number>();
    displayGraph.value.nodes.forEach((node) => {
      const entityType = normalizeType(node.entity_type);
      counts.set(entityType, (counts.get(entityType) ?? 0) + 1);
    });
    return [...counts.entries()]
      .map(([type, count]) => ({ type, count }))
      .sort((left, right) => right.count - left.count);
  }

  return (graphSummary.value?.type_counts ?? []).map((item) => ({
    type: normalizeType(item.type),
    count: item.count
  }));
});

const syntheticFaultPropertyDetails = computed(() => {
  const entries = new Map<string, GraphNodeDetail>();

  displayGraph.value.nodes.forEach((node, index) => {
    if (normalizeType(node.entity_type) !== 'FAULT_PROPERTY') {
      return;
    }

    const id = graphNodeId(node, `synthetic-${index}`);
    const label = graphNodeLabel(node, id);
    const description = String(node.description ?? '').trim();
    const syntheticNode = node as unknown as SyntheticFaultPropertyNode;
    const faultcaseLabel = String(syntheticNode.faultcase_label ?? '').trim();
    const propertyLabel = String(syntheticNode.property_label ?? label).trim();

    entries.set(id, {
      label,
      entity_type: 'FAULT_PROPERTY',
      degree: 1,
      properties: {
        description,
        属性名称: propertyLabel,
        来源故障卡: faultcaseLabel
      },
      relationships: faultcaseLabel
        ? [
            {
              label: faultcaseLabel,
              entity_type: 'FAULTCASE',
              direction: 'in',
              type: propertyLabel,
              properties: {}
            }
          ]
        : []
    });
  });

  return entries;
});

const filteredGraph = computed<GraphResponse>(() => {
  if (!selectedTypes.value.length) {
    return { ...displayGraph.value, nodes: [], edges: [] };
  }

  const nodes = displayGraph.value.nodes.filter((node) =>
    selectedTypes.value.includes(normalizeType(node.entity_type))
  );
  const allowedIds = new Set(nodes.flatMap((node, index) => graphNodeKeys(node, `node-${index}`)));
  const edges = displayGraph.value.edges.filter(
    (edge) => allowedIds.has(String(edge.source)) && allowedIds.has(String(edge.target))
  );

  return {
    ...displayGraph.value,
    nodes,
    edges
  };
});

const graphMetrics = computed(() => {
  const metricsGraph =
    graphMode.value === 'full' ? expandedGraphForMetrics.value : filteredGraph.value;
  const totalNodes = metricsGraph.nodes.length;
  const totalEdges = metricsGraph.edges.length;

  return [
    { label: '节点', value: totalNodes, color: '#3B82F6' },
    { label: '关系', value: totalEdges, color: '#10B981' }
  ];
});

const legendTypes = computed(() =>
  visibleTypeCounts.value
    .filter((item) => selectedTypes.value.includes(item.type))
    .map((item) => ({
      type: item.type,
      label: displayTypeLabel(item.type),
      color: displayTypeColor(item.type)
    }))
);

const exportBaseName = computed(() => {
  const datasourceLabel = datasourceId.value || 'graph-explore';
  if (graphMode.value === 'full') {
    return sanitizeFileName(`${datasourceLabel}-完整图谱`);
  }
  return sanitizeFileName(`${datasourceLabel}-${currentRootLabel.value || '子图探索'}`);
});

const narrativeSections = computed<NarrativeSection[]>(() => {
  if (!selectedNode.value) {
    return [];
  }

  const selectedEntityType = normalizeType(selectedNode.value.entity_type);
  const entries = Object.entries(selectedNode.value.properties).filter(
    ([key, value]) => !isHiddenPropertyKey(key) && isMeaningfulPropertyValue(value)
  );

  const sections: NarrativeSection[] = [];
  if (selectedEntityType === 'FAULTCASE') {
    const sectionMap = new Map<string, NarrativeSection>();

    const descriptionEntry = entries.find(([key]) => normalizePropertyKey(key) === 'description');
    const descriptionText = descriptionEntry ? formatPropertyValue(descriptionEntry[1]) : '';

    const blockSections = parseBracketedCardSections(descriptionText);
    blockSections.forEach((section) => sectionMap.set(section.label, section));

    const descriptionSections = blockSections.length ? [] : parseStructuredDescriptionSections(descriptionText);
    descriptionSections.forEach((section) => sectionMap.set(section.label, section));

    faultcardFieldConfigs.forEach((field) => {
      if (sectionMap.has(field.section)) {
        return;
      }

      const propertyMatch = entries.find(([key, value]) => {
        if (!isMeaningfulPropertyValue(value)) {
          return false;
        }
        const normalizedKey = normalizePropertyKey(key);
        return field.propertyKeys.some((candidate) => normalizePropertyKey(candidate) === normalizedKey);
      });

      if (!propertyMatch) {
        return;
      }

      sectionMap.set(field.section, {
        label: field.section,
        key: `property:${field.section}`,
        paragraphs: normalizeFieldParagraphs(propertyMatch[1])
      });
    });

    faultcardFieldConfigs.forEach((field) => {
      const section = sectionMap.get(field.section);
      if (section?.paragraphs.length) {
        sections.push(section);
      }
    });

    if (!sections.length && descriptionText) {
      sections.push({
        label: '描述',
        key: 'description:fallback',
        paragraphs: buildDescriptionSections(descriptionText)
      });
    }
  } else {
    const descriptionEntry = entries.find(([key]) => normalizePropertyKey(key) === 'description');
    if (descriptionEntry) {
      sections.push({
        label: '描述',
        key: 'description:fallback',
        paragraphs: buildDescriptionSections(formatPropertyValue(descriptionEntry[1]))
      });
    }
  }

  for (const group of narrativeFieldGroups) {
    const match = entries.find(([key]) =>
      group.keys.some((candidate) => normalizePropertyKey(key) === normalizePropertyKey(candidate))
    );

    if (!match) {
      continue;
    }

    const text = formatPropertyValue(match[1]);
    if (!text) {
      continue;
    }

    sections.push({
      label: group.label,
      key: match[0],
      paragraphs: buildDescriptionSections(text)
    });
  }

  return sections;
});

const visibleRelations = computed<RelationPreviewItem[]>(() => {
  const relations = selectedNode.value?.relationships ?? [];
  return expandedRelations.value ? relations : relations.slice(0, 8);
});

const selectedNodeType = computed(() =>
  selectedNode.value ? normalizeType(selectedNode.value.entity_type) : ''
);

const useFaultcardNarrativeLayout = computed(() => selectedNodeType.value === 'FAULTCASE');
const faultcardMetaLabels = new Set(['记录ID', '来源路径', '章节标题', '装备', '故障卡片']);

const faultcardMetaSections = computed(() =>
  useFaultcardNarrativeLayout.value
    ? narrativeSections.value.filter((section) => faultcardMetaLabels.has(section.label))
    : []
);

const faultcardBodySections = computed(() =>
  useFaultcardNarrativeLayout.value
    ? narrativeSections.value.filter((section) => !faultcardMetaLabels.has(section.label))
    : narrativeSections.value
);

function detailSectionTone(label: string) {
  if (!useFaultcardNarrativeLayout.value) {
    return 'detail-stack-section detail-stack-section--generic';
  }

  if (label === '故障现象' || label === '关键部件') {
    return 'detail-stack-section detail-stack-section--symptom';
  }
  if (label === '可能原因' || label === '可能后果') {
    return 'detail-stack-section detail-stack-section--cause';
  }
  if (label === '处理步骤') {
    return 'detail-stack-section detail-stack-section--action';
  }
  if (label === '注意事项') {
    return 'detail-stack-section detail-stack-section--warning';
  }
  return 'detail-stack-section detail-stack-section--meta';
}

const hiddenRelationCount = computed(() => {
  const total = selectedNode.value?.relationships.length ?? 0;
  return Math.max(0, total - visibleRelations.value.length);
});

const showDetailPanel = computed(
  () =>
    pageMode.value === 'read' ||
    detailPanelVisible.value ||
    loadingNodeDetail.value ||
    Boolean(detailError.value)
);

const selectedNodeProperties = computed(() => {
  if (!selectedNode.value) {
    return [];
  }

  const consumedKeys = new Set(narrativeSections.value.map((item) => item.key));
  if (narrativeSections.value.some((item) => item.key.startsWith('description:'))) {
    consumedKeys.add('description');
  }

  return Object.entries(selectedNode.value.properties).filter(
    ([key, value]) =>
      !consumedKeys.has(key) && !isHiddenPropertyKey(key) && isMeaningfulPropertyValue(value)
  );
});

async function selectNode(label: string) {
  if (!label) {
    activeNodeLabel.value = '';
    selectedNode.value = null;
    return;
  }

  const syntheticDetail = syntheticFaultPropertyDetails.value.get(label);
  if (syntheticDetail) {
    activeNodeLabel.value = label;
    focusedNodeLabel.value = label;
    detailPanelVisible.value = true;
    selectedNode.value = syntheticDetail;
    expandedRelations.value = false;
    expandedSectionKeys.value = [];
    detailError.value = '';
    loadingNodeDetail.value = false;
    ensureVisibleType(normalizeType(syntheticDetail.entity_type));
    return;
  }

  const resolvedNode = findGraphNodeByKey(displayGraph.value, label);
  const resolvedLabel = resolvedNode ? graphNodeLabel(resolvedNode, label) : label;
  activeNodeLabel.value = label;
  focusedNodeLabel.value = label;
  detailPanelVisible.value = true;
  const scopeId = currentDatasourceIdOrThrow();
  const requestToken = ++detailRequestToken;
  loadingNodeDetail.value = true;
  detailError.value = '';

  try {
    const detail = await fetchGraphNodeDetail(resolvedLabel, scopeId);
    if (requestToken !== detailRequestToken) {
      return;
    }

    selectedNode.value = detail;
    activeNodeLabel.value = detail.label;
    focusedNodeLabel.value = detail.label;
    expandedRelations.value = false;
    expandedSectionKeys.value = [];
    ensureVisibleType(normalizeType(detail.entity_type));
  } catch (reason) {
    if (requestToken !== detailRequestToken) {
      return;
    }

    selectedNode.value = null;
    detailError.value = reason instanceof Error ? reason.message : '节点详情加载失败';
  } finally {
    if (requestToken === detailRequestToken) {
      loadingNodeDetail.value = false;
    }
  }
}

async function loadGraphData(
  mode: GraphViewMode,
  rootLabel?: string,
  options: {
    autoSelect?: boolean;
    preferredFocusLabel?: string;
  } = {}
) {
  const scopeId = currentDatasourceIdOrThrow();
  const requestToken = ++graphRequestToken;
  const previousSelectedLabel =
    selectedNode.value?.label ?? activeNodeLabel.value ?? focusedNodeLabel.value ?? '';
  const nextRootLabel = (rootLabel ?? currentRootLabel.value ?? '').trim();
  const preferredFocusLabel = (options.preferredFocusLabel ?? '').trim();
  const shouldAutoSelect = options.autoSelect ?? (mode === 'label' || pageMode.value === 'read');
  const pendingFocusLabel =
    preferredFocusLabel || (mode === 'label' ? nextRootLabel : previousSelectedLabel);

  graphMode.value = mode;
  graphError.value = '';
  detailRequestToken += 1;
  detailError.value = '';
  detailPanelVisible.value = false;
  hoveredNode.value = null;
  activeNodeLabel.value = shouldAutoSelect ? pendingFocusLabel : '';
  focusedNodeLabel.value = shouldAutoSelect ? pendingFocusLabel : '';
  selectedNode.value = null;
  loadingGraph.value = true;

  if (mode === 'label') {
    currentRootLabel.value = nextRootLabel;
  }

  try {
    const nextGraph =
      mode === 'full'
        ? await fetchGraphFull(scopeId)
        : await fetchGraphByLabel(nextRootLabel, scopeId);

    if (requestToken !== graphRequestToken) {
      return;
    }

    const fallbackLabel = nextGraph.nodes.length
      ? graphNodeLabel(nextGraph.nodes[0], 'graph-node-0')
      : '';
    const preferredLabel =
      mode === 'label'
        ? findNodeLabel(nextGraph, preferredFocusLabel || nextRootLabel) ||
          findNodeLabel(nextGraph, previousSelectedLabel)
        : findNodeLabel(nextGraph, preferredFocusLabel || previousSelectedLabel);
    const nextFocusLabel = shouldAutoSelect ? preferredLabel || fallbackLabel : '';

    activeNodeLabel.value = nextFocusLabel;
    focusedNodeLabel.value = nextFocusLabel;
    graph.value = nextGraph;
    selectedTypes.value = availableTypesFromGraph(
      graphMode.value === 'label' ? buildExpandedFaultPropertyGraph(nextGraph) : nextGraph
    );

    if (!nextGraph.nodes.length || !shouldAutoSelect) {
      return;
    }

    void selectNode(nextFocusLabel || fallbackLabel);
  } catch (reason) {
    if (requestToken !== graphRequestToken) {
      return;
    }

    graph.value = { nodes: [], edges: [] };
    selectedTypes.value = [];
    graphError.value =
      reason instanceof Error
        ? reason.message
        : mode === 'full'
          ? '完整图谱加载失败'
          : '子图探索加载失败';
  } finally {
    if (requestToken === graphRequestToken) {
      loadingGraph.value = false;
    }
  }
}

async function switchGraphMode(
  mode: GraphViewMode,
  rootLabel?: string,
  options: {
    preferredFocusLabel?: string;
  } = {}
) {
  if (mode === 'label') {
    const nextRoot = (
      rootLabel ??
      currentRootLabel.value ??
      preferredInitialRootLabel(labels.value) ??
      labels.value[0]?.label ??
      ''
    ).trim();

    if (!nextRoot) {
      graphMode.value = 'label';
      graph.value = { nodes: [], edges: [] };
      selectedTypes.value = [];
      selectedNode.value = null;
      graphError.value = '当前 datasource 暂无可用的子图入口，请先生成图谱节点后再探索';
      return;
    }

    await loadGraphData('label', nextRoot, {
      autoSelect: true,
      preferredFocusLabel: options.preferredFocusLabel ?? nextRoot
    });
    return;
  }

  await loadGraphData('full', undefined, {
    autoSelect: pageMode.value === 'read',
    preferredFocusLabel: options.preferredFocusLabel
  });
}

async function initializePage() {
  loadingSummary.value = true;
  summaryWarning.value = '';
  graphError.value = '';

  try {
    const configData = await fetchSystemConfig();
    const scopeId = configData.server.datasource_id?.trim();
    if (!scopeId) {
      throw new Error('系统未返回 datasource_id');
    }

    systemConfig.value = configData;

    const [labelResult, summaryResult] = await Promise.allSettled([
      fetchGraphLabels(scopeId),
      fetchGraphSummary(scopeId)
    ]);

    const warnings: string[] = [];

    if (labelResult.status === 'fulfilled') {
      labels.value = labelResult.value;
      currentRootLabel.value = preferredInitialRootLabel(labelResult.value);
    } else {
      warnings.push(
        labelResult.reason instanceof Error
          ? labelResult.reason.message
          : '子图探索入口暂时不可用'
      );
    }

    if (summaryResult.status === 'fulfilled') {
      graphSummary.value = summaryResult.value;
    } else {
      warnings.push('图谱概览暂时不可用，页面将直接使用实时图数据。');
    }

    summaryWarning.value = warnings.join(' ');
    await switchGraphMode('full');
  } catch (reason) {
    graphError.value = reason instanceof Error ? reason.message : '图谱视图初始化失败';
  } finally {
    loadingSummary.value = false;
  }
}

function toggleType(type: string) {
  selectedTypes.value = selectedTypes.value.includes(type)
    ? selectedTypes.value.filter((item) => item !== type)
    : selectedTypes.value.concat(type);
}

function resetTypeFilter() {
  selectedTypes.value = visibleTypeCounts.value.map((item) => item.type);
}

function handleCanvasSelect(label: string) {
  if (!label) {
    clearSelectedNode();
    return;
  }
  activeNodeLabel.value = label;
  focusedNodeLabel.value = label;
  detailPanelVisible.value = true;
  void selectNode(label);
}

function clearSelectedNode() {
  detailRequestToken += 1;
  detailError.value = '';
  activeNodeLabel.value = '';
  selectedNode.value = null;
  expandedRelations.value = false;
  expandedSectionKeys.value = [];
}

function closeDetailPanel() {
  detailPanelVisible.value = false;
}

function handleFullGraphModeClick() {
  if (loadingSummary.value || loadingGraph.value) {
    return;
  }

  if (graphMode.value === 'full') {
    canvasRef.value?.replaySimulation({ resetView: true });
    return;
  }

  void switchGraphMode('full');
}

function jumpToRelation(label: string) {
  if (!label) {
    return;
  }

  if (graphMode.value === 'label' && allLabelNames.value.includes(label)) {
    void switchGraphMode('label', label, { preferredFocusLabel: label });
    return;
  }

  const existsInCurrentGraph = graph.value.nodes.some((node, index) =>
    graphNodeKeys(node, `node-${index}`).includes(label)
  );

  if (existsInCurrentGraph) {
    void selectNode(label);
    return;
  }

  if (allLabelNames.value.includes(label)) {
    void switchGraphMode('label', label, { preferredFocusLabel: label });
    return;
  }

  void selectNode(label);
}

function exportGraphJson() {
  const blob = new Blob([JSON.stringify(filteredGraph.value, null, 2)], {
    type: 'application/json'
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${exportBaseName.value}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

function exportGraphSnapshot() {
  canvasRef.value?.downloadSnapshot(`${exportBaseName.value}.png`);
}

watch(
  () => graph.value.nodes,
  (nextNodes) => {
    if (!nextNodes.length) {
      selectedTypes.value = [];
    }
  }
);

onMounted(() => {
  void initializePage();
});
</script>

<template>
  <div class="graph-explore-page" :class="[`is-${pageMode}-mode`, { 'has-detail-panel': showDetailPanel }]">
    <aside class="graph-side-panel">
      <section class="panel-section panel-section--overview">
        <div class="section-title-row">
          <h3 class="section-title">概览</h3>
        </div>

        <div class="metric-grid">
          <div v-for="item in graphMetrics" :key="item.label" class="metric-card">
            <div class="metric-value" :style="{ color: item.color }">{{ item.value }}</div>
            <div class="metric-label">{{ item.label }}</div>
          </div>
        </div>
      </section>

      <section class="panel-section">
        <div class="section-title-row">
          <h3 class="section-title">浏览模式</h3>
        </div>

        <div class="mode-switch-grid">
          <button
            type="button"
            class="mode-card"
            :class="{ active: graphMode === 'full' }"
            @click="handleFullGraphModeClick"
          >
            <div class="mode-card-icon"><Layers :size="16" /></div>
            <div class="mode-card-copy">
              <strong>完整图谱</strong>
            </div>
          </button>

          <button
            type="button"
            class="mode-card"
            :class="{ active: graphMode === 'label' }"
            @click="switchGraphMode('label')"
          >
            <div class="mode-card-icon"><GitBranch :size="16" /></div>
            <div class="mode-card-copy">
              <strong>子图探索</strong>
            </div>
          </button>
        </div>
      </section>

      <section class="panel-section panel-section--search">
        <div class="section-title-row">
          <h3 class="section-title">搜索与起点</h3>
        </div>
        <div v-if="searchResultMeta" class="search-result-meta">{{ searchResultMeta }}</div>

        <label class="graph-search-shell">
          <Search :size="13" class="graph-search-icon" />
          <input
            v-model="rootSearch"
            class="graph-search-input"
            placeholder="搜索设备或文档节点名称..."
          />
          <button v-if="rootSearch" class="graph-clear-btn" type="button" @click="rootSearch = ''">
            <X :size="12" />
          </button>
        </label>

        <div v-if="searchedLabels.length" class="search-result-list">
          <button
            v-for="entry in searchedLabels"
            :key="entry.label"
            type="button"
            class="search-result-item"
            :class="{ active: currentRootLabel === entry.label && graphMode === 'label' }"
            @click="switchGraphMode('label', entry.label)"
          >
            <span
              class="search-result-dot"
              :style="{ background: displayTypeColor(normalizeType(entry.entity_type)) }"
            ></span>
            <span class="search-result-text">{{ entry.label }}</span>
          </button>
        </div>

        <div v-else class="search-empty-state">
          {{ rootSearch ? '无匹配节点' : '暂无探索入口' }}
        </div>
      </section>

      <section class="panel-section">
        <div class="section-title-row">
          <h3 class="section-title">节点类型</h3>
          <button class="section-link" type="button" @click="resetTypeFilter">全显示</button>
        </div>

        <div class="type-filter-list">
          <button
            v-for="item in visibleTypeCounts"
            :key="item.type"
            type="button"
            class="type-filter-item"
            :class="{ active: selectedTypes.includes(item.type) }"
            @click="toggleType(item.type)"
          >
            <div class="type-filter-main">
              <span class="type-filter-dot" :style="{ background: displayTypeColor(item.type) }"></span>
              <span class="type-filter-label">{{ displayTypeLabel(item.type) }}</span>
            </div>
            <span class="type-filter-count">{{ item.count }}</span>
            <component
              :is="selectedTypes.includes(item.type) ? Eye : EyeOff"
              :size="11"
              class="type-filter-icon"
            />
          </button>
        </div>
      </section>
    </aside>

    <section class="graph-stage-panel">
      <div class="graph-stage-header">
        <div v-if="pageMode === 'explore'">
          <div class="graph-stage-title">{{ stageTitle }}</div>
          <div class="graph-stage-subtitle">{{ stageSubtitle }}</div>
          <div v-if="summaryWarning" class="graph-stage-warning">{{ summaryWarning }}</div>
        </div>
        <div v-else>
          <div class="graph-stage-title">局部导航</div>
        </div>

        <div class="graph-stage-meta" v-if="pageMode === 'explore'">
          <div class="graph-stage-actions">
            <button
              v-if="activeNodeLabel || selectedNode"
              type="button"
              class="stage-action-btn"
              @click="clearSelectedNode"
            >
              <X :size="13" />
              <span>取消选择</span>
            </button>
            <button type="button" class="stage-action-btn" @click="exportGraphJson">
              <Download :size="13" />
              <span>导出 JSON</span>
            </button>
            <button type="button" class="stage-action-btn" @click="exportGraphSnapshot">
              <Download :size="13" />
              <span>导出 PNG</span>
            </button>
          </div>
        </div>
        <div v-else class="graph-stage-meta graph-stage-meta--read">
          <button type="button" class="stage-action-btn stage-action-btn--readback" @click="pageMode = 'explore'">
            <ArrowLeft :size="13" />
            <span>返回大图</span>
          </button>
        </div>
      </div>

      <div class="graph-stage-body">
        <div v-if="loadingSummary || loadingGraph" class="graph-stage-empty graph-stage-empty--loading">
          <div class="graph-loading-shell">
            <div class="graph-loading-visual" aria-hidden="true">
              <span class="graph-loading-link graph-loading-link--a"></span>
              <span class="graph-loading-link graph-loading-link--b"></span>
              <span class="graph-loading-link graph-loading-link--c"></span>
              <span class="graph-loading-node graph-loading-node--a"></span>
              <span class="graph-loading-node graph-loading-node--b"></span>
              <span class="graph-loading-node graph-loading-node--c"></span>
              <span class="graph-loading-node graph-loading-node--d"></span>
            </div>
            <div class="graph-loading-copy">
            <div class="graph-stage-empty-title">正在加载图谱视图…</div>
            <div class="graph-stage-empty-subtitle">正在读取当前 datasource 的图谱数据与节点详情。</div>
            </div>
          </div>
        </div>

        <div v-else-if="graphError" class="graph-stage-empty graph-stage-empty--error">
          <div>
            <div class="graph-stage-empty-title">当前视图暂时不可用</div>
            <div class="graph-stage-empty-subtitle">{{ graphError }}</div>
          </div>
        </div>

        <div v-else-if="!filteredGraph.nodes.length" class="graph-stage-empty">
          <div>
            <div class="graph-stage-empty-title">{{ graphEmptyState.title }}</div>
            <div class="graph-stage-empty-subtitle">{{ graphEmptyState.subtitle }}</div>
          </div>
        </div>

        <template v-else>
          <div class="graph-floating-toolbar">
            <button type="button" class="floating-tool-btn" title="放大" @click="canvasRef?.zoomIn()">
              <ZoomIn :size="13" />
            </button>
            <button type="button" class="floating-tool-btn" title="缩小" @click="canvasRef?.zoomOut()">
              <ZoomOut :size="13" />
            </button>
            <button type="button" class="floating-tool-btn" title="重置视图" @click="canvasRef?.resetView()">
              <RotateCcw :size="13" />
            </button>
          </div>

          <div v-if="hoveredNode && !selectedNode" class="graph-hover-chip">
            <span class="graph-hover-dot" :style="{ background: displayTypeColor(hoveredNode.entityType) }"></span>
            <strong>{{ hoveredNode.label }}</strong>
            <span>{{ displayTypeLabel(hoveredNode.entityType) }}</span>
          </div>

          <GraphCanvas
            ref="canvasRef"
            :graph="filteredGraph"
            :selected-node-id="activeNodeLabel || selectedNode?.label"
            :focus-node-id="focusedNodeLabel || activeNodeLabel || selectedNode?.label"
            :mode="graphMode"
            :viewport-mode="pageMode === 'read' ? 'local' : 'global'"
            :current-root-label="currentRootLabel"
            @select="handleCanvasSelect"
            @hover="hoveredNode = $event"
          />

          <div class="graph-legend-bar" v-if="pageMode === 'explore'">
            <div v-for="item in legendTypes" :key="item.type" class="graph-legend-item">
              <span class="graph-legend-dot" :style="{ background: item.color }"></span>
              <span>{{ item.label }}</span>
            </div>
          </div>
        </template>
      </div>
    </section>

    <section v-if="pageMode === 'read'" class="graph-reading-panel">
      <header class="reading-header">
        <button type="button" class="back-to-explore-btn" @click="pageMode = 'explore'">
          <ArrowLeft :size="14" />
          返回图谱探索
        </button>
      </header>
      
      <div class="reading-content">
        <h1 class="reading-title">{{ selectedNode?.label }}</h1>
        
        <div class="reading-meta-strip">
          <span class="reading-badge" :style="{ background: displayTypeSurface(normalizeType(selectedNode?.entity_type)), color: displayTypeColor(normalizeType(selectedNode?.entity_type)) }">
            {{ displayTypeLabel(normalizeType(selectedNode?.entity_type)) }}
          </span>
          <span class="reading-meta-item">连接度 {{ selectedNode?.degree }}</span>
          <span class="reading-meta-item">关系数 {{ selectedNode?.relationships.length }}</span>
        </div>

        <div class="reading-body">
          <article
            v-for="section in faultcardBodySections"
            :key="section.label"
            class="reading-section"
          >
            <h2 class="reading-section-title">{{ section.label }}</h2>
            <div class="reading-section-content">
              <div
                v-for="paragraph in section.paragraphs"
                :key="paragraph"
                class="reading-section-paragraph rich-text-content"
                v-html="renderRichText(paragraph)"
              ></div>
            </div>
          </article>
        </div>
      </div>
    </section>

    <aside v-if="showDetailPanel" class="graph-detail-panel">
      <div class="detail-header">
        <div>
          <div class="detail-title">节点详情</div>
        </div>
        <button
          v-if="pageMode === 'explore'"
          type="button"
          class="detail-close-btn"
          @click="closeDetailPanel"
        >
          <X :size="14" />
        </button>
      </div>

      <div class="detail-body">
        <div v-if="loadingNodeDetail" class="detail-empty">
          <div>
            <div class="detail-empty-title">正在读取节点详情…</div>
            <div class="detail-empty-subtitle">请稍候，系统正在返回该节点的属性与关系。</div>
          </div>
        </div>

        <div v-else-if="detailError" class="detail-empty">
          <div>
            <div class="detail-empty-title">节点详情暂不可用</div>
            <div class="detail-empty-subtitle">{{ detailError }}</div>
          </div>
        </div>

        <div v-else-if="!selectedNode" class="detail-empty">
          <div>
            <div class="detail-empty-title">尚未选中节点</div>
            <div class="detail-empty-subtitle">点击画布节点，或先通过子图探索选一个起点进入。</div>
          </div>
        </div>

        <template v-else>
          <section class="detail-hero">
            <div
              class="detail-avatar"
              :style="{
                background: displayTypeSurface(normalizeType(selectedNode.entity_type)),
                color: displayTypeColor(normalizeType(selectedNode.entity_type)),
                borderColor: displayTypeColor(normalizeType(selectedNode.entity_type))
              }"
            >
              <GitBranch :size="16" />
            </div>
            <div class="detail-hero-copy">
              <div class="detail-hero-title">{{ selectedNode.label }}</div>
              <div
                class="detail-hero-badge"
                :style="{
                  background: displayTypeSurface(normalizeType(selectedNode.entity_type)),
                  color: displayTypeColor(normalizeType(selectedNode.entity_type))
                }"
              >
                {{ displayTypeLabel(normalizeType(selectedNode.entity_type)) }}
              </div>
            </div>
          </section>

          <section class="detail-metric-strip">
            <div class="detail-metric-card">
              <span>连接度</span>
              <strong>{{ selectedNode.degree }}</strong>
            </div>
            <div class="detail-metric-card">
              <span>关系数</span>
              <strong>{{ selectedNode.relationships.length }}</strong>
            </div>
          </section>

          <section class="detail-section detail-section--inspector">
            <div class="detail-section-head">
              <Tag :size="11" />
              <span>{{ useFaultcardNarrativeLayout ? '故障卡详情' : '属性' }}</span>
            </div>

            <section v-if="useFaultcardNarrativeLayout && faultcardMetaSections.length" class="faultcard-meta-block">
              <div v-for="section in faultcardMetaSections" :key="section.label" class="faultcard-meta-row">
                <span class="faultcard-meta-key">{{ section.label }}</span>
                <strong class="faultcard-meta-value rich-text-content" v-html="renderRichTextInline(section.paragraphs.join(' '))"></strong>
              </div>
            </section>

            <div v-if="faultcardBodySections.length && pageMode === 'explore'" class="detail-read-mode-cta">
              <button type="button" class="read-mode-btn" @click="pageMode = 'read'">
                <BookOpen :size="16" />
                <div class="read-mode-btn-copy">
                  <strong>阅读完整节点内容</strong>
                  <span>包含该节点的长文本与详细属性</span>
                </div>
                <ChevronRight :size="16" class="read-mode-btn-arrow" />
              </button>
            </div>

            <div
              v-if="selectedNodeProperties.length"
              :class="useFaultcardNarrativeLayout ? 'property-list property-list--secondary' : 'detail-section-stack detail-section-stack--generic'"
            >
              <template v-if="useFaultcardNarrativeLayout">
                <div v-for="[key, value] in selectedNodeProperties" :key="key" class="property-item">
                  <span>{{ key }}</span>
                  <strong class="rich-text-content" v-html="renderRichTextInline(formatPropertyValue(value))"></strong>
                </div>
              </template>
              <template v-else>
                <article v-for="[key, value] in selectedNodeProperties" :key="key" class="detail-stack-section detail-stack-section--generic">
                  <div class="detail-stack-rail"></div>
                  <div class="detail-stack-content">
                    <div class="detail-stack-label">{{ key }}</div>
                    <div
                      class="detail-stack-value detail-stack-value--generic rich-text-content"
                      v-html="renderRichText(formatPropertyValue(value))"
                    ></div>
                  </div>
                </article>
              </template>
            </div>

            <div v-else-if="!faultcardBodySections.length" class="detail-note">当前节点暂无可展示的业务属性。</div>
          </section>

          <section class="detail-section detail-section--relations">
            <div class="detail-section-head">
              <Layers :size="11" />
              <span>关联关系 ({{ selectedNode.relationships.length }})</span>
            </div>
            <div class="detail-note">
              {{ graphMode === 'label' ? '在子图探索中，点击会优先跳到该节点；若它也是子图入口，会切到对应子图并保持当前焦点方式。' : '在完整图谱中，点击会直接定位到关联节点，并尽量保持当前视角习惯。' }}
            </div>

            <div class="relation-list relation-list--bounded">
              <button
                v-for="relation in visibleRelations"
                :key="`${relation.label}-${relation.type}-${relation.direction}`"
                type="button"
                class="relation-item"
                @click="jumpToRelation(relation.label)"
              >
                <div class="relation-main">
                  <strong>{{ relation.label }}</strong>
                  <span>{{ relation.direction }} · {{ relation.type }}</span>
                </div>
                <div class="relation-meta">
                  <span class="relation-tag">{{ relationActionLabel(relation.label) }}</span>
                  <ChevronRight :size="12" class="relation-arrow" />
                </div>
              </button>
            </div>

            <button
              v-if="hiddenRelationCount > 0"
              type="button"
              class="relation-toggle-btn"
              @click="expandedRelations = true"
            >
              展开剩余 {{ hiddenRelationCount }} 个关联节点
            </button>
            <button
              v-else-if="selectedNode.relationships.length > 8"
              type="button"
              class="relation-toggle-btn"
              @click="expandedRelations = false"
            >
              收起关联节点
            </button>
          </section>
        </template>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.graph-explore-page {
  display: grid;
  gap: 0;
  height: 100%;
  max-height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--bg-primary);
}

.graph-explore-page.is-explore-mode {
  grid-template-columns: 240px minmax(0, 1fr);
  grid-template-rows: 1fr;
  grid-template-areas: "side stage";
}

.graph-explore-page.is-explore-mode.has-detail-panel {
  grid-template-columns: 240px minmax(0, 1fr) 300px;
  grid-template-rows: 1fr;
  grid-template-areas: "side stage detail";
}

.graph-explore-page.is-read-mode {
  grid-template-columns: 240px minmax(0, 1fr) 320px;
  grid-template-rows: clamp(320px, 38vh, 400px) minmax(0, 1fr);
  grid-template-areas: 
    "side reading stage"
    "side reading detail";
}

.graph-side-panel {
  grid-area: side;
  grid-row: 1 / -1;
  border-right: 1px solid var(--border-light);
  padding: 7px 10px;
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 0;
  overflow-y: auto;
  background: var(--bg-primary);
}

.graph-stage-panel {
  grid-area: stage;
  position: relative;
  min-width: 0;
  min-height: 0;
  background: var(--bg-primary);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.graph-reading-panel {
  grid-area: reading;
  background: var(--bg-primary);
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border-light);
  min-width: 0;
  min-height: 0;
}

.reading-header {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border-light);
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.back-to-explore-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  padding: 6px 12px;
  border-radius: 6px;
  margin-left: -12px;
  transition: all 0.2s;
}

.back-to-explore-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.reading-content {
  flex: 1;
  overflow-y: auto;
  padding: 32px 48px 64px;
  margin: 0 auto;
  width: 100%;
  max-width: 800px;
}

.reading-title {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.3;
}

.reading-meta-strip {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border-light);
}

.reading-badge {
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.reading-meta-item {
  color: var(--text-tertiary);
  font-size: 13px;
}

.reading-body {
  margin-top: 32px;
  display: grid;
  gap: 32px;
}

.reading-section-title {
  margin: 0 0 12px;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.reading-section-content {
  color: var(--text-secondary);
  font-size: 15px;
  line-height: 1.8;
}

.reading-section-paragraph + .reading-section-paragraph {
  margin-top: 1em;
}

.detail-read-mode-cta {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border-light);
}

.read-mode-btn {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  background: var(--accent-lighter);
  border: 1px solid var(--border-accent);
  border-radius: 12px;
  color: var(--accent-primary);
  text-align: left;
  transition: all 0.2s ease;
  cursor: pointer;
}

.read-mode-btn:hover {
  background: var(--accent-light);
}

.read-mode-btn-copy {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 4px;
}

.read-mode-btn-copy strong {
  font-size: 14px;
  font-weight: 600;
}

.read-mode-btn-copy span {
  font-size: 12px;
  color: var(--accent-primary);
  opacity: 0.8;
  line-height: 1.4;
}

.read-mode-btn-arrow {
  flex-shrink: 0;
}

.graph-detail-panel {
  grid-area: detail;
  border-left: 1px solid var(--border-light);
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--bg-primary);
}

.detail-close-btn {
  width: 30px;
  height: 30px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.9);
  color: var(--text-tertiary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.detail-close-btn:hover {
  color: var(--accent-primary);
  border-color: var(--border-accent);
  background: var(--bg-hover);
}

.is-read-mode .graph-stage-panel {
  border-bottom: 1px solid var(--border-light);
  border-left: 1px solid var(--border-light);
}

.is-read-mode .graph-stage-header {
  padding: 8px 12px;
}

.is-read-mode .graph-stage-title {
  font-size: 12px;
}

.is-read-mode .graph-stage-body {
  padding: 12px 16px 16px;
}

.is-read-mode .graph-floating-toolbar {
  top: 12px;
  right: 12px;
  transform: scale(0.85);
  transform-origin: top right;
}

.is-read-mode .graph-hover-chip {
  top: 12px;
}

.is-read-mode .graph-stage-empty-title {
  font-size: 14px;
}

.is-read-mode .graph-stage-empty-subtitle {
  font-size: 12px;
}

.panel-section,
.detail-section,
.detail-hero,
.detail-metric-strip {
  border: none;
  background: transparent;
  border-radius: 0;
  box-shadow: none;
  border-bottom: 1px solid var(--border-light);
  padding: 16px 0;
}

.panel-section:last-child {
  border-bottom: none;
}

.panel-section--overview {
  background: transparent;
}

.section-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.section-title,
.detail-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.graph-stage-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.detail-subtitle {
  margin: 4px 0 0;
  color: var(--text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.graph-stage-subtitle {
  display: none;
}

.graph-stage-subtitle--visible {
  display: block;
  margin-top: 6px;
  color: var(--text-tertiary);
  font-size: 12px;
  line-height: 1.5;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.metric-card {
  border-radius: 8px;
  border: 1px solid var(--border-light);
  background: var(--bg-card);
  padding: 10px;
  display: grid;
  gap: 2px;
}

.metric-value {
  color: var(--text-primary);
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.metric-label {
  color: var(--text-tertiary);
  font-size: 11px;
}

.mode-switch-grid,
.search-result-list,
.type-filter-list,
.relation-list,
.property-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.mode-card,
.search-result-item,
.type-filter-item,
.relation-item,
.stage-action-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border-radius: 8px;
  border: 1px solid var(--border-light);
  background: var(--bg-card);
  color: var(--text-secondary);
  transition: all 0.2s ease;
}

.mode-card,
.search-result-item,
.type-filter-item,
.relation-item {
  padding: 8px 10px;
}

.mode-card:hover,
.search-result-item:hover,
.type-filter-item:hover,
.relation-item:hover,
.stage-action-btn:hover {
  border-color: var(--border-accent);
  background: var(--bg-hover);
}

.mode-card.active,
.search-result-item.active,
.type-filter-item.active {
  background: var(--accent-lighter);
  border-color: var(--border-accent);
}

.mode-card-icon {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--accent-light);
  color: var(--accent-primary);
  flex-shrink: 0;
}

.mode-card-copy {
  min-width: 0;
  display: grid;
  gap: 2px;
  text-align: left;
}

.mode-card-copy strong {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 700;
}

.detail-note,
.search-empty-state {
  color: var(--text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.search-result-meta {
  margin-top: 8px;
  color: var(--text-tertiary);
  font-size: 11px;
  line-height: 1.5;
}

.search-empty-state,
.detail-note {
  margin-top: 10px;
}

.graph-search-shell {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 36px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-card);
}

.graph-search-shell:focus-within {
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 3px var(--accent-light);
}

.graph-search-icon,
.graph-clear-btn {
  color: var(--text-muted);
}

.graph-search-input {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  outline: none;
}

.graph-clear-btn {
  border: none;
  background: transparent;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.search-result-list {
  margin-top: 12px;
  max-height: 248px;
  overflow-y: auto;
}

.search-result-dot,
.type-filter-dot,
.graph-hover-dot,
.graph-legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  flex-shrink: 0;
}

.search-result-dot {
  background: var(--accent-primary);
}

.search-result-text {
  min-width: 0;
  text-align: left;
  font-size: 12px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.current-root-text {
  margin-top: 10px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.current-root-text strong {
  color: var(--text-primary);
  font-weight: 600;
}

.section-link,
.legend-export-btn {
  border: none;
  background: transparent;
  color: var(--accent-primary);
  font-size: 12px;
  font-weight: 600;
}

.type-filter-main,
.relation-main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.relation-main {
  display: grid;
  gap: 4px;
  text-align: left;
}

.type-filter-label,
.relation-main span {
  font-size: 12px;
  color: var(--text-secondary);
}

.type-filter-count,
.relation-tag {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg-tertiary);
  color: var(--text-muted);
  font-size: 11px;
  white-space: nowrap;
}

.type-filter-icon,
.relation-arrow {
  color: var(--text-muted);
  flex-shrink: 0;
}

.detail-section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.graph-stage-header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: transparent;
  flex-shrink: 0;
}

.graph-stage-meta {
  display: grid;
  gap: 12px;
  justify-items: end;
}

.graph-stage-warning {
  margin-top: 10px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 999px;
  background: var(--warning-light);
  color: var(--warning);
  font-size: 11px;
  font-weight: 600;
}

.graph-stage-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.stage-action-btn {
  width: auto;
  padding: 6px 10px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 6px;
}

.stage-action-btn--readback {
  align-self: flex-start;
}

.graph-stage-body {
  flex: 1;
  position: relative;
  overflow: hidden;
  padding: 16px;
  display: flex;
  flex-direction: column;
}

.graph-stage-empty {
  display: grid;
  place-items: center;
  flex: 1;
  min-height: 0;
  border-radius: 28px;
  border: 1px solid var(--border);
  background: linear-gradient(180deg, var(--bg-card) 0%, var(--accent-lighter) 100%);
  text-align: center;
  box-shadow: var(--shadow-sm);
}

.graph-stage-empty--loading {
  background:
    radial-gradient(circle at 20% 18%, rgba(59, 130, 246, 0.16), transparent 26%),
    radial-gradient(circle at 80% 20%, rgba(16, 185, 129, 0.12), transparent 24%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.96) 0%, rgba(239, 246, 255, 0.96) 100%);
}

.graph-stage-empty--error {
  border-color: rgba(239, 68, 68, 0.22);
}

.graph-loading-shell {
  display: grid;
  gap: 18px;
  justify-items: center;
}

.graph-loading-visual {
  position: relative;
  width: 220px;
  height: 132px;
}

.graph-loading-node,
.graph-loading-link {
  position: absolute;
  display: block;
}

.graph-loading-node {
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.95), rgba(125, 211, 252, 0.95));
  box-shadow: 0 0 0 8px rgba(59, 130, 246, 0.08);
  animation: graph-loading-node-pulse 1.8s ease-in-out infinite;
}

.graph-loading-node--a {
  top: 10px;
  left: 22px;
}

.graph-loading-node--b {
  top: 26px;
  right: 24px;
  animation-delay: 0.18s;
}

.graph-loading-node--c {
  bottom: 18px;
  left: 58px;
  animation-delay: 0.32s;
}

.graph-loading-node--d {
  bottom: 12px;
  right: 44px;
  animation-delay: 0.48s;
}

.graph-loading-link {
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(148, 163, 184, 0.18), rgba(59, 130, 246, 0.48), rgba(148, 163, 184, 0.18));
  transform-origin: left center;
  animation: graph-loading-link-pulse 1.8s ease-in-out infinite;
}

.graph-loading-link--a {
  top: 28px;
  left: 38px;
  width: 136px;
  transform: rotate(6deg);
}

.graph-loading-link--b {
  top: 62px;
  left: 46px;
  width: 84px;
  transform: rotate(36deg);
  animation-delay: 0.22s;
}

.graph-loading-link--c {
  top: 82px;
  left: 112px;
  width: 62px;
  transform: rotate(-26deg);
  animation-delay: 0.4s;
}

.graph-loading-copy {
  display: grid;
  gap: 6px;
  justify-items: center;
}

.graph-stage-empty-title,
.detail-empty-title {
  color: var(--text-primary);
  font-size: 18px;
  font-weight: 600;
}

.graph-stage-empty-subtitle,
.detail-empty-subtitle {
  margin-top: 8px;
  color: var(--text-tertiary);
  font-size: 13px;
}

@keyframes graph-loading-node-pulse {
  0%,
  100% {
    opacity: 0.42;
    transform: scale(0.96);
  }
  50% {
    opacity: 1;
    transform: scale(1.04);
  }
}

@keyframes graph-loading-link-pulse {
  0%,
  100% {
    opacity: 0.26;
  }
  50% {
    opacity: 0.9;
  }
}

.graph-floating-toolbar {
  position: absolute;
  top: 36px;
  right: 40px;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.floating-tool-btn {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.9);
  color: var(--text-tertiary);
  backdrop-filter: blur(8px);
  transition: all 0.2s ease;
  box-shadow: var(--shadow-sm);
}

.floating-tool-btn:hover {
  color: var(--accent-primary);
  border-color: var(--border-accent);
}

.graph-hover-chip {
  position: absolute;
  top: 36px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-height: 38px;
  padding: 0 16px;
  border-radius: 14px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.94);
  color: var(--text-secondary);
  backdrop-filter: blur(8px);
  box-shadow: var(--shadow-lg);
}

.graph-hover-chip strong {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
}

.graph-legend-bar {
  position: absolute;
  left: 40px;
  right: 40px;
  bottom: 26px;
  z-index: 2;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  min-height: 40px;
  padding: 0 16px;
  border-radius: 14px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(8px);
  box-shadow: var(--shadow-sm);
}

.graph-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.detail-header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-light);
  background: transparent;
  flex-shrink: 0;
}

.detail-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 16px 24px;
  display: flex;
  flex-direction: column;
}

.detail-empty {
  display: grid;
  place-items: center;
  min-height: min(360px, 100%);
  text-align: center;
}

.detail-hero {
  padding: 16px 0;
  display: flex;
  align-items: center;
  gap: 14px;
}

.detail-avatar {
  width: 42px;
  height: 42px;
  border-radius: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid currentColor;
  flex-shrink: 0;
}

.detail-hero-copy {
  min-width: 0;
}

.detail-hero-title {
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.3;
}

.detail-hero-badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  margin-top: 8px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}

.detail-metric-strip {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
  padding: 12px 0;
}

.detail-metric-card {
  border-radius: 8px;
  border: 1px solid var(--border-light);
  background: var(--bg-card);
  padding: 10px;
}

.detail-metric-card span {
  display: block;
  color: var(--text-tertiary);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.detail-metric-card strong {
  display: block;
  margin-top: 6px;
  color: var(--text-primary);
  font-size: 18px;
  font-weight: 700;
}

.detail-section {
  margin-top: 12px;
  padding: 12px 0;
}

.detail-section--inspector {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
}

.detail-section--relations {
  flex: 0 0 auto;
}

.faultcard-meta-block {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding: 14px 0 18px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
}

.faultcard-meta-row {
  display: grid;
  gap: 4px;
}

.faultcard-meta-key {
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.faultcard-meta-value {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 500;
  line-height: 1.6;
}

.detail-section-stack {
  display: grid;
  gap: 18px;
  margin-top: 14px;
}

.detail-content-zones {
  display: grid;
  gap: 0;
  min-height: 0;
  flex: 1;
}

.detail-primary-zone {
  padding-right: 0;
}

.detail-section-stack--generic {
  gap: 16px;
}

.property-summary-card {
  border: 1px solid var(--border-light);
  border-radius: 8px;
  background: var(--bg-card);
  padding: 0;
  display: grid;
  gap: 0;
  overflow: hidden;
}

.property-summary-title {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 10px 12px 8px;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary);
  background: rgba(248, 250, 252, 0.96);
  border-bottom: 1px solid var(--border-light);
}

.property-summary-body {
  display: grid;
  gap: 6px;
  max-height: 168px;
  overflow-y: auto;
  padding: 10px 12px 12px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.7;
}

.property-summary-body p {
  margin: 0;
}

.detail-stack-section {
  display: grid;
  grid-template-columns: 4px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  padding-bottom: 18px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
}

.detail-stack-section:last-child {
  padding-bottom: 0;
  border-bottom: none;
}

.detail-stack-rail {
  width: 4px;
  min-height: 100%;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.22);
}

.detail-stack-section--symptom .detail-stack-rail {
  background: rgba(245, 158, 11, 0.75);
}

.detail-stack-section--cause .detail-stack-rail {
  background: rgba(239, 68, 68, 0.72);
}

.detail-stack-section--action .detail-stack-rail {
  background: rgba(16, 185, 129, 0.72);
}

.detail-stack-section--warning .detail-stack-rail {
  background: rgba(234, 179, 8, 0.72);
}

.detail-stack-section--meta .detail-stack-rail,
.detail-stack-section--generic .detail-stack-rail {
  background: rgba(148, 163, 184, 0.3);
}

.detail-stack-content {
  min-width: 0;
  display: grid;
  gap: 8px;
}

.detail-stack-label {
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.detail-stack-value {
  display: grid;
  gap: 8px;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.75;
}

.detail-stack-value p {
  margin: 0;
}

.detail-stack-value--collapsed {
  position: relative;
  max-height: 168px;
  overflow: hidden;
}

.detail-stack-value--collapsed::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 32px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0) 0%, var(--bg-primary) 100%);
  pointer-events: none;
}

.detail-stack-value--expanded {
  max-height: none;
}

.detail-inline-toggle {
  border: none;
  background: transparent;
  color: var(--accent-primary);
  font-size: 12px;
  font-weight: 600;
  padding: 0;
  text-align: left;
}

.detail-stack-value--generic {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.7;
}

.property-list {
  margin-top: 14px;
}

.property-list--secondary {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid rgba(148, 163, 184, 0.18);
}

.property-item {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-light);
}

.property-item:last-child {
  border-bottom: none;
}

.property-item span {
  color: var(--text-tertiary);
  font-size: 12px;
}

.property-item strong {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 500;
  text-align: right;
  word-break: break-word;
}

.relation-meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.relation-toggle-btn {
  margin-top: 12px;
  border: none;
  background: transparent;
  color: var(--accent-primary);
  font-size: 12px;
  font-weight: 600;
  padding: 0;
  text-align: left;
}

.relation-list--bounded {
  max-height: 220px;
  overflow-y: auto;
  padding-right: 4px;
}

.relation-main strong {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 1200px) {
  .graph-explore-page {
    grid-template-columns: 240px minmax(0, 1fr);
    height: auto;
    overflow: visible;
  }

  .graph-detail-panel {
    grid-column: 1 / -1;
    border-left: none;
    border-top: 1px solid var(--border);
  }

  .relation-list--bounded,
  .detail-stack-value--raw-expanded {
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }
}

@media (max-width: 1040px) {
  .graph-explore-page {
    grid-template-columns: 1fr;
  }

  .graph-side-panel,
  .graph-detail-panel {
    border: none;
    border-bottom: 1px solid var(--border);
  }

  .graph-stage-header {
    flex-direction: column;
  }

  .graph-stage-meta {
    width: 100%;
    justify-items: stretch;
  }

  .graph-stage-actions {
    justify-content: flex-start;
  }

  .graph-stage-body {
    padding: 16px 16px 68px;
  }

  .graph-floating-toolbar {
    right: 24px;
  }

  .graph-legend-bar {
    left: 24px;
    right: 24px;
    bottom: 20px;
  }

  .property-summary-grid,
  .overview-grid {
    grid-template-columns: 1fr;
  }
}
</style>
