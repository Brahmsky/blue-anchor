<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import {
  ChevronDown,
  ChevronRight,
  Cloud,
  Cpu,
  FileText,
  GitBranch,
  RefreshCw,
  Workflow
} from 'lucide-vue-next';
import { useRoute } from 'vue-router';

import AliasManagementPanel from '@/components/alias/AliasManagementPanel.vue';
import { fetchDocumentSummary, fetchScanProgress, triggerDocumentScan } from '@/api/documents';
import { fetchHealth, fetchSystemCapabilities, fetchSystemConfig } from '@/api/system';
import type {
  DocumentSummaryResponse,
  HealthResponse,
  ScanProgressResponse,
  SystemCapabilities,
  SystemConfigResponse
} from '@/types/api';

type ConnectorStatus = 'connected' | 'error' | 'paused';

interface ConnectorCard {
  id: string;
  name: string;
  type: string;
  status: ConnectorStatus;
  icon: any;
  color: string;
  lastSync: string;
  nextSync: string;
  docCount: number;
  metricLabel: string;
  syncInterval: string;
  actionLabel: string;
  footerNote?: string;
  config: Array<{ label: string; value: string }>;
  syncSettings: Array<{ label: string; value: string }>;
}

const route = useRoute();
const expandedConnector = ref<string | null>(null);
const aliasPanelAnchor = ref<HTMLElement | null>(null);
const aliasPanelRequested = ref(false);
const loading = ref(true);
const scanning = ref(false);
const error = ref('');

const health = ref<HealthResponse | null>(null);
const capabilities = ref<SystemCapabilities | null>(null);
const systemConfig = ref<SystemConfigResponse | null>(null);
const documentSummary = ref<DocumentSummaryResponse | null>(null);
const scanProgress = ref<ScanProgressResponse | null>(null);

function requireDatasourceId(config: SystemConfigResponse) {
  const datasourceId = config.server.datasource_id?.trim();
  if (!datasourceId) {
    throw new Error('系统未返回 datasource_id');
  }
  return datasourceId;
}

function routeFocus() {
  return typeof route.query.focus === 'string' ? route.query.focus : null;
}

function applyRouteState() {
  const focus = routeFocus();
  if (focus === 'alias-store' || focus === 'alias-panel') {
    expandedConnector.value = 'alias-store';
    aliasPanelRequested.value = true;
  } else if (focus && connectors.value.some((connector) => connector.id === focus)) {
    expandedConnector.value = focus;
  }

  void nextTick(() => {
    if (aliasPanelRequested.value && aliasPanelAnchor.value) {
      aliasPanelAnchor.value.scrollIntoView({ behavior: 'smooth', block: 'start' });
      aliasPanelRequested.value = false;
    }
  });
}

const connectors = computed<ConnectorCard[]>(() => {
  if (!systemConfig.value || !documentSummary.value) return [];

  const cfg = systemConfig.value;
  const summary = documentSummary.value;
  const baseStatus: ConnectorStatus = health.value?.status === 'healthy' ? 'connected' : 'error';
  const latestItem = summary.items[0];
  const ds = summary.datasource;

  return [
    {
      id: 'source',
      name: 'Source Documents (源文档)',
      type: 'source',
      status: summary.stats.total > 0 ? baseStatus : 'paused',
      icon: FileText,
      color: '#4f46e5',
      lastSync: latestItem?.modified_at ? formatRelativeTime(latestItem.modified_at) : '未知',
      nextSync: '手动触发',
      docCount: summary.stats.total,
      metricLabel: '源文档',
      syncInterval: '按需构建',
      actionLabel: '执行源目录同步',
      config: [
        { label: 'Datasource ID', value: ds.datasource_id },
        { label: 'Source Root', value: ds.source_root },
        { label: 'Input Dir（兼容字段）', value: ds.input_dir }
      ],
      syncSettings: [
        { label: '就绪状态', value: summary.stats.total > 0 ? '已就绪' : '缺失源文件' },
        { label: '文件总数', value: String(summary.stats.total) }
      ]
    },
    {
      id: 'pipeline-runtime',
      name: '索引与运行产物',
      type: 'pipeline-runtime',
      status: summary.stats.processing > 0 ? 'connected' : (summary.stats.indexed > 0 ? 'connected' : 'paused'),
      icon: Workflow,
      color: '#f59e0b',
      lastSync: latestItem?.indexed_at ? formatRelativeTime(latestItem.indexed_at) : '未知',
      nextSync: '随索引与构建刷新',
      docCount: summary.stats.indexed,
      metricLabel: '已索引文档',
      syncInterval: '索引 + 构建',
      actionLabel: '刷新运行状态',
      config: [
        { label: 'Staging Root', value: ds.staging_root },
        { label: 'Output Root', value: ds.output_root },
        { label: '文件过滤', value: 'diagnostic_records_llm.json' }
      ],
      syncSettings: [
        { label: '并发数', value: String(cfg.chunking.max_parallel_insert) },
        { label: '运行产物状态', value: summary.stats.indexed > 0 ? '图谱与向量可用' : '运行时产物缺失' },
        { label: 'Top K', value: String(cfg.query.top_k) },
        { label: 'Default Mode', value: cfg.query.default_mode }
      ]
    },
    {
      id: 'graph-storage',
      name: '图数据库配置',
      type: 'graph-storage',
      status: summary.stats.indexed > 0 ? baseStatus : 'paused',
      icon: GitBranch,
      color: '#0f766e',
      lastSync: latestItem?.indexed_at ? formatRelativeTime(latestItem.indexed_at) : '未知',
      nextSync: '随图谱构建刷新',
      docCount: summary.stats.indexed,
      metricLabel: '图谱产物',
      syncInterval: '图谱重建',
      actionLabel: '刷新图谱状态',
      config: [
        { label: '数据库类型', value: cfg.storage.graph_storage },
        { label: '工作目录', value: cfg.server.working_dir },
        { label: 'KV Storage', value: cfg.storage.kv_storage }
      ],
      syncSettings: [
        { label: 'Doc Status', value: cfg.storage.doc_status_storage },
        { label: 'Output Root', value: ds.output_root },
        { label: '图谱状态', value: summary.stats.indexed > 0 ? '图谱可浏览' : '等待构建' }
      ]
    },
    {
      id: 'embedding',
      name: 'Embedding 服务',
      type: 'embedding',
      status: baseStatus,
      icon: Cloud,
      color: '#0284c7',
      lastSync: '运行时连接',
      nextSync: '实时',
      docCount: summary.stats.total,
      metricLabel: '向量接入',
      syncInterval: '即时',
      actionLabel: '刷新服务状态',
      config: [
        { label: 'Binding', value: cfg.embedding.binding },
        { label: 'Host', value: cfg.embedding.binding_host ?? '未配置' },
        { label: 'Model', value: cfg.embedding.model },
        { label: 'Vector Storage', value: cfg.storage.vector_storage }
      ],
      syncSettings: [
        { label: '向量维度', value: String(cfg.embedding.dimension) },
        { label: '最大 Token', value: String(cfg.embedding.max_embed_tokens) },
        { label: '索引模型', value: cfg.llm.model },
        { label: '状态', value: health.value?.status === 'healthy' ? '在线' : '异常' }
      ]
    },
    {
      id: 'query-llm',
      name: '查询 LLM',
      type: 'llm',
      status: baseStatus,
      icon: Cpu,
      color: '#7c3aed',
      lastSync: '运行时连接',
      nextSync: '实时',
      docCount: 1,
      metricLabel: '当前模型',
      syncInterval: '即时',
      actionLabel: '刷新模型状态',
      config: [
        { label: 'Query Binding', value: cfg.llm.query_binding },
        { label: 'Query Host', value: cfg.llm.query_binding_host ?? '未配置' },
        { label: 'Query Model', value: cfg.llm.query_model },
        { label: '索引模型', value: cfg.llm.model }
      ],
      syncSettings: [
        { label: '模型提供商', value: cfg.llm.query_binding },
        { label: '模型版本', value: cfg.llm.query_model },
        { label: 'Max Async', value: String(cfg.llm.max_async) },
        { label: 'Max Tokens', value: String(cfg.llm.max_tokens) },
        { label: 'History Turns', value: String(cfg.llm.history_turns) }
      ]
    }
  ];
});

async function loadPage() {
  loading.value = true;
  error.value = '';
  try {
    const [healthData, capabilityData, configData] = await Promise.all([
      fetchHealth(),
      fetchSystemCapabilities(),
      fetchSystemConfig(),
    ]);

    const datasourceId = requireDatasourceId(configData);
    const [docSummaryData, scanData] = await Promise.all([
      fetchDocumentSummary(datasourceId),
      fetchScanProgress().catch(() => null)
    ]);
    health.value = healthData;
    capabilities.value = capabilityData;
    systemConfig.value = configData;
    documentSummary.value = docSummaryData;
    scanProgress.value = scanData;
  } catch (err) {
    error.value = err instanceof Error ? err.message : '数据源管理信息加载失败';
  } finally {
    loading.value = false;
  }
}

async function runRealScan() {
  scanning.value = true;
  try {
    await triggerDocumentScan();
    await loadPage();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '扫描触发失败';
  } finally {
    scanning.value = false;
  }
}

function formatRelativeTime(value?: string) {
  if (!value) return '未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffMin = Math.round((Date.now() - date.getTime()) / 60000);
  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  return `${Math.round(diffHour / 24)} 天前`;
}

function connectorStatusMeta(status: ConnectorStatus) {
  if (status === 'connected') return { label: '● 已连接', color: '#10b981', bg: 'rgba(16,185,129,0.1)' };
  if (status === 'error') return { label: '● 错误', color: '#ef4444', bg: 'rgba(239,68,68,0.1)' };
  return { label: '● 已暂停', color: '#f59e0b', bg: 'rgba(245,158,11,0.1)' };
}

function toggleConnector(id: string) {
  expandedConnector.value = expandedConnector.value === id ? null : id;
}

onMounted(() => {
  void loadPage().then(() => {
    applyRouteState();
  });
});

watch(
  () => [route.query.focus, connectors.value.length],
  () => {
    applyRouteState();
  }
);
</script>

<template>
  <div class="data-sources-page">
    <div v-if="error" class="page-error">{{ error }}</div>
    <div v-else-if="loading" class="page-loading">正在读取数据源管理信息...</div>

    <template v-else>
      <section class="page-section">
        <div class="connector-list">
          <article
            v-for="connector in connectors"
            :key="connector.id"
            class="connector-card"
            :class="{ expanded: expandedConnector === connector.id }"
          >
            <button class="connector-head" type="button" @click="toggleConnector(connector.id)">
              <div class="connector-icon" :style="{ background: `${connector.color}20`, borderColor: `${connector.color}40` }">
                <component :is="connector.icon" :size="18" :style="{ color: connector.color }" />
              </div>

              <div class="connector-main">
                <div class="connector-title-row">
                  <span class="connector-title">{{ connector.name }}</span>
                  <span
                    class="connector-status"
                    :style="{
                      color: connectorStatusMeta(connector.status).color,
                      background: connectorStatusMeta(connector.status).bg
                    }"
                  >
                    {{ connectorStatusMeta(connector.status).label }}
                  </span>
                </div>
                <div class="connector-meta">
                  <span>{{ connector.docCount }} {{ connector.metricLabel }}</span>
                  <span>上次同步: {{ connector.lastSync }}</span>
                  <span>下次同步: {{ connector.nextSync }}</span>
                </div>
              </div>

              <div class="connector-actions">
                <button
                  class="mini-btn"
                  type="button"
                  @click.stop="connector.id === 'source' ? runRealScan() : loadPage()"
                >
                  <RefreshCw :size="11" />
                  <span>{{ connector.id === 'source' && scanning ? '同步中' : connector.actionLabel }}</span>
                </button>
                <component
                  :is="expandedConnector === connector.id ? ChevronDown : ChevronRight"
                  :size="16"
                  class="expand-indicator"
                />
              </div>
            </button>

            <div v-if="expandedConnector === connector.id" class="connector-body">
              <div class="connector-columns">
                <div>
                  <div class="section-subtitle">连接配置</div>
                  <div class="kv-list">
                    <div v-for="row in connector.config" :key="row.label" class="kv-row">
                      <span class="kv-label">{{ row.label }}</span>
                      <div class="kv-value">{{ row.value }}</div>
                    </div>
                  </div>
                </div>
                <div>
                  <div class="section-subtitle">同步设置</div>
                  <div class="kv-list">
                    <div v-for="row in connector.syncSettings" :key="row.label" class="kv-row">
                      <span class="kv-label">{{ row.label }}</span>
                      <div class="kv-value">{{ row.value }}</div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="connector-footer">
                <div class="connector-note">
                  {{ connector.footerNote || '' }}
                </div>
                <button
                  class="mini-btn"
                  type="button"
                  @click="connector.id === 'source' ? runRealScan() : loadPage()"
                >
                  <RefreshCw :size="11" />
                  <span>{{ connector.id === 'source' && scanning ? '同步中' : connector.actionLabel }}</span>
                </button>
              </div>
            </div>
          </article>
        </div>

        <div ref="aliasPanelAnchor">
          <article
            class="connector-card alias-shell"
            :class="{ expanded: expandedConnector === 'alias-store' }"
          >
            <button class="connector-head" type="button" @click="toggleConnector('alias-store')">
              <div class="connector-icon" style="background: rgba(20, 184, 166, 0.12); border-color: rgba(20, 184, 166, 0.22);">
                <Workflow :size="18" style="color: #14b8a6;" />
              </div>

              <div class="connector-main">
                <div class="connector-title-row">
                  <span class="connector-title">Alias 管理</span>
                  <span
                    class="connector-status"
                    style="color: #14b8a6; background: rgba(20, 184, 166, 0.1);"
                  >
                    ● {{ capabilities?.alias_store?.total ?? systemConfig?.alias_store?.total ?? 0 }} 条规则
                  </span>
                </div>
                <div class="connector-meta">
                  <span>作用域: {{ systemConfig?.server.datasource_id }}</span>
                  <span>已启用: {{ capabilities?.alias_store?.enabled ?? systemConfig?.alias_store?.enabled ?? 0 }}</span>
                  <span>已复核: {{ capabilities?.alias_store?.reviewed ?? systemConfig?.alias_store?.reviewed ?? 0 }}</span>
                </div>
              </div>

              <div class="connector-actions">
                <component
                  :is="expandedConnector === 'alias-store' ? ChevronDown : ChevronRight"
                  :size="16"
                  class="expand-indicator"
                />
              </div>
            </button>

            <div v-if="expandedConnector === 'alias-store'" class="connector-body alias-shell-body">
              <AliasManagementPanel
                v-if="systemConfig"
                :datasource-id="systemConfig.server.datasource_id"
                title="Alias 作用域能力面板"
                subtitle="在当前数据源实例下管理别名、复核状态与路由预览。"
                compact
              />
            </div>
          </article>
        </div>

        <div class="page-note">本页集中展示当前系统参数，便于核对运行环境与关键配置。</div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.data-sources-page {
  display: grid;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.data-sources-page > .page-error,
.data-sources-page > .page-loading,
.data-sources-page > .page-section {
  min-height: 0;
  overflow-y: scroll;
  overflow-x: hidden;
  scrollbar-gutter: stable;
}

.read-only-pill {
  display: inline-flex;
  align-items: center;
  height: 34px;
  padding: 0 14px;
  border: 1px solid rgba(59, 130, 246, 0.28);
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(219, 234, 254, 0.95) 0%, rgba(239, 246, 255, 1) 100%);
  color: var(--accent-primary);
  font-size: 12px;
  font-weight: 700;
}

.page-error,
.page-loading {
  margin: 0 20px;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px solid #dce7f8;
  background: rgba(255, 255, 255, 0.92);
  color: var(--text-secondary);
  font-size: 13px;
}

.page-error {
  border-color: rgba(239, 68, 68, 0.16);
  background: rgba(254, 242, 242, 0.92);
  color: #dc2626;
}

.page-section {
  display: grid;
  gap: 14px;
  align-content: start;
  padding: 16px 20px 20px;
  width: 100%;
  max-width: none;
}

.connector-card {
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid #dce7f8;
  border-radius: 18px;
  box-shadow: 0 10px 24px rgba(148, 163, 184, 0.06);
}

.connector-card:hover {
  box-shadow: 0 14px 30px rgba(148, 163, 184, 0.08);
}

.connector-list {
  display: grid;
  gap: 12px;
  align-content: start;
}

.connector-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 16px;
  min-height: 88px;
  border: none;
  background: transparent;
  text-align: left;
  box-sizing: border-box;
}

.connector-icon {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  border: 1px solid transparent;
  flex-shrink: 0;
}

.connector-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.connector-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  min-height: 24px;
}

.connector-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.connector-status {
  display: inline-flex;
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.connector-meta {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
  color: var(--text-muted);
  font-size: 11px;
}

.connector-meta span {
  padding: 4px 8px;
  background: rgba(248, 250, 252, 0.8);
  border: 1px solid rgba(226, 232, 240, 0.6);
  border-radius: 6px;
  font-weight: 500;
  white-space: nowrap;
  color: var(--text-secondary);
}

.connector-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 180px;
  justify-content: flex-end;
  align-self: stretch;
}

.mini-btn,
.icon-btn,
.reset-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border-radius: 10px;
  border: 1px solid #dce7f8;
  background: #f8fbff;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 700;
}

.icon-btn {
  width: 30px;
  padding: 0;
}

.mini-btn.danger {
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.2);
  background: rgba(239, 68, 68, 0.06);
}

.expand-indicator {
  color: #64748b;
}

.connector-body {
  padding: 0 16px 16px;
  border-top: 1px solid #e8eef9;
}

.alias-shell-body {
  padding-top: 16px;
}

.connector-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.section-subtitle,
.panel-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 700;
}

.section-subtitle {
  margin-bottom: 12px;
  color: var(--accent-primary);
  font-size: 12px;
}

.kv-list {
  display: grid;
  gap: 10px;
}

.kv-row {
  display: grid;
  gap: 4px;
}

.kv-label {
  color: var(--text-muted);
  font-size: 11px;
}

.kv-value {
  width: 100%;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid #e3ecfb;
  background: #f7fbff;
  color: var(--text-primary);
  font-size: 12px;
  word-break: break-word;
}

.connector-footer {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #e8eef9;
}

.connector-note,
.page-note {
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-muted);
}

.connector-note {
  flex: 1;
  min-width: 220px;
}

.reset-btn {
  background: #f8fbff;
}

@media (max-width: 1180px) {
  .connector-columns {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .connector-columns {
    grid-template-columns: 1fr;
  }

  .connector-head {
    align-items: flex-start;
    min-height: 0;
    flex-wrap: wrap;
  }

  .connector-actions {
    flex: 1 1 100%;
    justify-content: space-between;
  }

  .connector-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    overflow: visible;
  }
}
</style>
