<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { CheckCircle2, Link2, RefreshCw, Search, ShieldCheck, Trash2 } from 'lucide-vue-next';

import {
  createAlias,
  deleteAlias,
  fetchAliases,
  resolveAliasQuery,
  updateAlias,
  type AliasPayload
} from '@/api/aliases';
import { fetchSystemConfig } from '@/api/system';
import type { AliasRecord, AliasResolveResponse } from '@/types/api';

interface Props {
  datasourceId?: string;
  title?: string;
  subtitle?: string;
  compact?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  title: 'Alias 管理',
  subtitle: '管理 Alias 规则、启用状态和命中结果。',
  compact: false
});

const entityType = ref<'ALL' | 'EQUIPMENT' | 'FAULTCASE' | 'COMPONENT'>('ALL');
const search = ref('');
const loading = ref(false);
const saving = ref(false);
const resolveLoading = ref(false);
const currentPage = ref(1);
const pageSize = ref<10 | 20 | 50>(10);
const records = ref<AliasRecord[]>([]);
const resolveQueryText = ref('');
const resolveResult = ref<AliasResolveResponse | null>(null);
const editingId = ref<string | null>(null);
const scopeId = ref(props.datasourceId?.trim() ?? '');
const scopeLoading = ref(false);
const feedback = ref('');
const feedbackTone = ref<'info' | 'success' | 'error'>('info');
const stats = ref<{
  datasource_id: string;
  total: number;
  enabled: number;
  reviewed: number;
  file_path: string;
  type_counts: Record<string, number>;
} | null>(null);

const resolvePayloadText = computed(() =>
  resolveResult.value ? JSON.stringify(resolveResult.value, null, 2) : ''
);
const feedbackIsError = computed(() => feedback.value && feedbackTone.value === 'error');
const totalPages = computed(() => Math.max(1, Math.ceil(records.value.length / pageSize.value)));
const paginatedRecords = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return records.value.slice(start, start + pageSize.value);
});
const pageSummary = computed(() => {
  if (!records.value.length) return '0 - 0 / 0';
  const start = (currentPage.value - 1) * pageSize.value + 1;
  const end = Math.min(currentPage.value * pageSize.value, records.value.length);
  return `${start} - ${end} / ${records.value.length}`;
});
const visiblePageNumbers = computed(() => {
  const pages: number[] = [];
  const start = Math.max(1, currentPage.value - 2);
  const end = Math.min(totalPages.value, start + 4);
  const adjustedStart = Math.max(1, end - 4);
  for (let page = adjustedStart; page <= end; page += 1) {
    pages.push(page);
  }
  return pages;
});

const form = reactive<AliasPayload>({
  datasource_id: '',
  canonical_name: '',
  entity_type: 'FAULTCASE',
  alias: '',
  enabled: true,
  reviewed: false
});

const cards = computed(() => {
  if (!stats.value) return [];
  return [
    ['Alias 总数', stats.value.total],
    ['已启用', stats.value.enabled],
    ['已复核', stats.value.reviewed],
    ['当前 datasource', scopeId.value || stats.value.datasource_id]
  ];
});

const scopeLabel = computed(() => scopeId.value || '正在解析...');

function setFeedback(message: string, tone: 'info' | 'success' | 'error' = 'info') {
  feedback.value = message;
  feedbackTone.value = tone;
}

function formatAliasError(err: unknown, fallbackMessage: string) {
  const message = err instanceof Error ? err.message : fallbackMessage;
  if (/alias already exists for the same canonical entity/i.test(message)) {
    return '重复 alias 冲突：当前 canonical_name + alias 组合已存在。';
  }
  if (/datasource_id does not match alias store scope/i.test(message)) {
    return '保存失败：当前 datasource scope 不匹配。';
  }
  return message || fallbackMessage;
}

function resetForm() {
  form.datasource_id = scopeId.value;
  form.canonical_name = '';
  form.entity_type = 'FAULTCASE';
  form.alias = '';
  form.enabled = true;
  form.reviewed = false;
  editingId.value = null;
}

function syncScope(datasourceId: string) {
  scopeId.value = datasourceId.trim();
  form.datasource_id = scopeId.value;
}

async function resolveScope() {
  if (props.datasourceId?.trim()) {
    syncScope(props.datasourceId);
    return;
  }

  scopeLoading.value = true;
  try {
    const config = await fetchSystemConfig();
    syncScope(config.server.datasource_id);
  } catch (err) {
    setFeedback(err instanceof Error ? err.message : '无法读取当前 datasource_id', 'error');
  } finally {
    scopeLoading.value = false;
  }
}

async function loadAliases() {
  if (!scopeId.value) {
    await resolveScope();
    if (!scopeId.value) return;
  }

  loading.value = true;
  try {
    const response = await fetchAliases({
      datasourceId: scopeId.value,
      entityType: entityType.value === 'ALL' ? undefined : entityType.value,
      query: search.value
    });
    records.value = response.items;
    stats.value = response.stats;
    currentPage.value = 1;
    setFeedback(`已加载 ${response.total} 条 alias。`, 'success');
  } catch (err) {
    setFeedback(err instanceof Error ? err.message : 'Alias 列表读取失败', 'error');
  } finally {
    loading.value = false;
  }
}

async function submitAlias() {
  if (!scopeId.value) {
    setFeedback('当前 datasource_id 未解析完成，无法保存。', 'error');
    return;
  }

  if (!form.canonical_name.trim() || !form.alias.trim()) {
    setFeedback('请先填写 canonical_name 和 alias。', 'error');
    return;
  }

  form.datasource_id = scopeId.value;
  saving.value = true;
  try {
    const payload = {
      datasource_id: scopeId.value,
      canonical_name: form.canonical_name.trim(),
      entity_type: form.entity_type,
      alias: form.alias.trim(),
      enabled: form.enabled,
      reviewed: form.reviewed
    } satisfies AliasPayload;

    if (editingId.value) {
      await updateAlias(editingId.value, payload);
      setFeedback('Alias 已更新。', 'success');
    } else {
      await createAlias(payload);
      setFeedback('Alias 已创建。', 'success');
    }

    resetForm();
    await loadAliases();
  } catch (err) {
    setFeedback(formatAliasError(err, '保存 alias 失败'), 'error');
  } finally {
    saving.value = false;
  }
}

function startEdit(record: AliasRecord) {
  editingId.value = record.id;
  form.datasource_id = record.datasource_id;
  form.canonical_name = record.canonical_name;
  form.entity_type = record.entity_type;
  form.alias = record.alias;
  form.enabled = record.enabled;
  form.reviewed = record.reviewed;
  setFeedback(`正在编辑 ${record.alias}。`, 'info');
}

function cancelEdit() {
  resetForm();
  setFeedback('已取消编辑。', 'info');
}

async function patchAlias(record: AliasRecord, payload: Partial<AliasPayload>) {
  try {
    await updateAlias(record.id, {
      datasource_id: scopeId.value,
      ...payload
    });
    setFeedback(`已更新 ${record.alias}。`, 'success');
    await loadAliases();
  } catch (err) {
    setFeedback(formatAliasError(err, '更新 alias 失败'), 'error');
  }
}

async function removeAlias(record: AliasRecord) {
  if (!window.confirm(`删除 alias「${record.alias}」?`)) return;
  try {
    await deleteAlias(record.id, scopeId.value);
    setFeedback(`已删除 ${record.alias}。`, 'success');
    await loadAliases();
  } catch (err) {
    setFeedback(formatAliasError(err, '删除 alias 失败'), 'error');
  }
}

async function resolveQueryPreview() {
  if (!resolveQueryText.value.trim()) {
    resolveResult.value = null;
    setFeedback('请输入 query 后再预览路由。', 'error');
    return;
  }

  if (!scopeId.value) {
    await resolveScope();
    if (!scopeId.value) return;
  }

  resolveLoading.value = true;
  try {
    resolveResult.value = await resolveAliasQuery(resolveQueryText.value.trim(), scopeId.value);
    setFeedback('已生成路由预览。', 'success');
  } catch (err) {
    resolveResult.value = null;
    setFeedback(formatAliasError(err, '命中预览失败'), 'error');
  } finally {
    resolveLoading.value = false;
  }
}

function formatTypeCounts() {
  if (!stats.value) return '未加载';
  const entries = Object.entries(stats.value.type_counts);
  return entries.length ? entries.map(([key, value]) => `${key}:${value}`).join(' · ') : '空';
}

function goToPage(page: number) {
  currentPage.value = Math.min(Math.max(page, 1), totalPages.value);
}

watch(pageSize, () => {
  currentPage.value = 1;
});

watch(totalPages, (next) => {
  if (currentPage.value > next) {
    currentPage.value = next;
  }
});

onMounted(async () => {
  await resolveScope();
  if (!form.datasource_id) {
    form.datasource_id = scopeId.value;
  }
  await loadAliases();
});

watch(
  () => props.datasourceId,
  async (next) => {
    if (!next?.trim()) return;
    syncScope(next);
    await loadAliases();
  }
);
</script>

<template>
  <div class="alias-panel" :class="{ compact: props.compact }">
    <div v-if="!props.compact" class="alias-summary">
      <div v-for="[label, value] in cards" :key="label" class="card">
        <div class="card-body alias-metric">
          <span>{{ label }}</span>
          <strong>{{ value }}</strong>
        </div>
      </div>
    </div>

    <div v-if="!props.compact" class="card" :class="{ 'card-flat': props.compact }">
      <div class="card-header">
        <div>
          <div class="card-title">{{ title }}</div>
          <div class="card-subtitle">{{ subtitle }}</div>
        </div>
        <div class="scope-pill">
          <span>datasource_id</span>
          <strong>{{ scopeLabel }}</strong>
        </div>
      </div>
      <div class="card-body alias-meta-row">
        <div class="meta-chip">存储文件：{{ stats?.file_path ?? '等待加载...' }}</div>
        <div class="meta-chip">类型分布：{{ formatTypeCounts() }}</div>
        <div class="meta-chip">状态：{{ scopeLoading ? '读取中...' : '已连接' }}</div>
      </div>
    </div>

    <div class="card" :class="{ 'card-flat': props.compact }">
      <div class="card-header">
        <div>
          <div class="card-title">{{ editingId ? '编辑 Alias' : '新增 Alias' }}</div>
          <div class="card-subtitle">填写别名、标准名称和类型。</div>
        </div>
        <button v-if="editingId" class="ghost-btn" type="button" @click="cancelEdit">取消编辑</button>
      </div>
      <div class="card-body alias-form-grid">
        <div>
          <label class="field-label">canonical_name</label>
          <input v-model="form.canonical_name" class="field-input" placeholder="图中实体名" />
        </div>
        <div>
          <label class="field-label">entity_type</label>
          <select v-model="form.entity_type" class="field-input">
            <option value="FAULTCASE">FAULTCASE</option>
            <option value="EQUIPMENT">EQUIPMENT</option>
            <option value="COMPONENT">COMPONENT</option>
          </select>
        </div>
        <div>
          <label class="field-label">alias</label>
          <input v-model="form.alias" class="field-input" placeholder="用户常问叫法" />
        </div>
        <label class="toggle-line">
          <input v-model="form.enabled" type="checkbox" />
          启用
        </label>
        <label class="toggle-line">
          <input v-model="form.reviewed" type="checkbox" />
          已复核
        </label>
        <button class="primary-btn alias-submit" type="button" :disabled="saving" @click="submitAlias">
          <CheckCircle2 :size="14" />
          <span>{{ saving ? '提交中' : editingId ? '保存修改' : '新增 Alias' }}</span>
        </button>
        <div v-if="feedbackIsError" class="alias-inline-alert alias-inline-alert-error">
          <strong>提交失败</strong>
          <span>{{ feedback }}</span>
        </div>
      </div>
    </div>

    <div class="alias-main-grid">
      <div class="card alias-list-card" :class="{ 'card-flat': props.compact }">
        <div class="card-header">
          <div class="alias-section-head">
            <div class="card-title">Alias 列表</div>
            <div class="card-subtitle">{{ stats?.file_path ?? 'Alias Store' }}</div>
          </div>
          <div class="alias-section-badge">
            <span>共 {{ records.length }} 条</span>
          </div>
        </div>
        <div class="card-body alias-list-body">
          <div class="alias-toolbar">
            <div class="alias-search">
              <Search :size="14" />
              <input v-model="search" class="field-input alias-search-input" placeholder="搜索 Alias / Canonical" @keyup.enter="loadAliases" />
            </div>
            <select v-model="entityType" class="field-input alias-filter" @change="loadAliases">
              <option value="ALL">全部类型</option>
              <option value="FAULTCASE">FAULTCASE</option>
              <option value="EQUIPMENT">EQUIPMENT</option>
              <option value="COMPONENT">COMPONENT</option>
            </select>
            <button class="ghost-btn" type="button" @click="loadAliases">
              <RefreshCw :size="13" />
              刷新
            </button>
          </div>

          <div v-if="loading" class="loading-state">正在读取 Alias 列表...</div>
          <div v-else class="alias-table-shell">
            <table class="alias-table">
              <thead>
                <tr>
                  <th>Alias</th>
                  <th>Canonical</th>
                  <th>Type</th>
                  <th>scope</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="record in paginatedRecords" :key="record.id">
                  <td>{{ record.alias }}</td>
                  <td>{{ record.canonical_name }}</td>
                  <td>{{ record.entity_type }}</td>
                  <td class="alias-norm">{{ record.datasource_id }}</td>
                  <td>
                    <div class="alias-statuses">
                      <label class="alias-switch">
                        <input
                          :checked="record.enabled"
                          type="checkbox"
                          @change="patchAlias(record, { enabled: !record.enabled })"
                        />
                        <span class="alias-switch-track"></span>
                        <span class="alias-switch-label">启用</span>
                      </label>
                      <label class="alias-switch">
                        <input
                          :checked="record.reviewed"
                          type="checkbox"
                          @change="patchAlias(record, { reviewed: !record.reviewed })"
                        />
                        <span class="alias-switch-track"></span>
                        <span class="alias-switch-label">复核</span>
                      </label>
                    </div>
                  </td>
                  <td>
                    <div class="alias-actions">
                      <button class="ghost-btn compact-btn alias-edit-btn" type="button" @click="startEdit(record)">编辑</button>
                      <button class="ghost-btn compact-btn alias-delete-btn" type="button" @click="removeAlias(record)" aria-label="删除 alias">
                        <Trash2 :size="14" />
                      </button>
                    </div>
                  </td>
                </tr>
                <tr v-if="!records.length">
                  <td colspan="6" class="empty-inline">当前没有命中记录。</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="records.length" class="alias-pagination">
            <div class="alias-pagination-summary">显示 {{ pageSummary }}</div>
            <div class="alias-pagination-controls">
              <label class="alias-page-size">
                <span>每页</span>
                <select v-model="pageSize" class="field-input alias-page-size-select">
                  <option :value="10">10 条</option>
                  <option :value="20">20 条</option>
                  <option :value="50">50 条</option>
                </select>
              </label>

              <div class="alias-page-buttons">
                <button class="ghost-btn compact-btn" type="button" :disabled="currentPage === 1" @click="goToPage(1)">首页</button>
                <button class="ghost-btn compact-btn" type="button" :disabled="currentPage === 1" @click="goToPage(currentPage - 1)">上一页</button>
                <button
                  v-for="page in visiblePageNumbers"
                  :key="page"
                  class="ghost-btn compact-btn alias-page-number"
                  :class="{ active: page === currentPage }"
                  type="button"
                  @click="goToPage(page)"
                >
                  {{ page }}
                </button>
                <button class="ghost-btn compact-btn" type="button" :disabled="currentPage === totalPages" @click="goToPage(currentPage + 1)">下一页</button>
                <button class="ghost-btn compact-btn" type="button" :disabled="currentPage === totalPages" @click="goToPage(totalPages)">尾页</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="card alias-preview-card" :class="{ 'card-flat': props.compact }">
        <div class="card-header">
          <div class="alias-section-head">
            <div class="card-title">路由预览</div>
            <div class="card-subtitle">输入查询并查看 Alias 命中结果。</div>
          </div>
          <div class="alias-preview-badge">
            <ShieldCheck :size="14" />
            <span>{{ resolveResult ? `${resolveResult.alias_hits.length} 个命中` : '等待查询' }}</span>
          </div>
        </div>
        <div class="card-body alias-preview-body">
          <div class="alias-preview-input">
            <input
              v-model="resolveQueryText"
              class="field-input"
              placeholder="输入查询"
              @keyup.enter="resolveQueryPreview"
            />
            <button class="primary-btn" type="button" :disabled="resolveLoading" @click="resolveQueryPreview">
              <Link2 :size="14" />
              <span>{{ resolveLoading ? '解析中' : '查看命中' }}</span>
            </button>
          </div>

          <div v-if="feedbackIsError" class="alias-inline-alert alias-inline-alert-error alias-preview-alert">
            <strong>预览失败</strong>
            <span>{{ feedback }}</span>
          </div>

          <div v-if="resolveResult" class="alias-preview-result">
            <div class="preview-meta">
              <span>datasource_id: <strong>{{ resolveResult.datasource_id }}</strong></span>
              <span>intent: <strong>{{ resolveResult.intent }}</strong></span>
              <span>query_norm: <code>{{ resolveResult.query_norm || '--' }}</code></span>
            </div>
            <div class="preview-priority">
              <ShieldCheck :size="14" />
              <span>{{ resolveResult.preferred_entity_types.join(' → ') }}</span>
            </div>
            <div class="preview-hit-list">
              <div v-for="hit in resolveResult.alias_hits" :key="hit.id" class="preview-hit">
                <strong>{{ hit.alias }}</strong>
                <span>{{ hit.canonical_name }}</span>
                <span>{{ hit.entity_type }}</span>
              </div>
              <div v-if="!resolveResult.alias_hits.length" class="empty-inline">当前 query 没有命中已启用 alias。</div>
            </div>
            <div class="preview-payload-block">
              <div class="preview-payload-title">完整 resolve payload</div>
              <pre class="preview-payload">{{ resolvePayloadText }}</pre>
            </div>
          </div>
          <div v-else class="alias-preview-empty"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.alias-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.alias-panel.compact {
  gap: 16px;
}

.alias-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.compact-summary {
  gap: 12px;
}

.card-flat {
  border-radius: 16px;
  box-shadow: none;
}

.alias-metric {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.alias-metric span {
  color: var(--text-muted);
  font-size: 12px;
}

.alias-metric strong {
  font-size: 28px;
}

.scope-pill {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 999px;
  border: 1px solid rgba(59, 130, 246, 0.18);
  background: rgba(239, 246, 255, 0.92);
  color: var(--text-secondary);
  font-size: 12px;
}

.scope-pill strong {
  color: var(--accent-primary);
}

.alias-meta-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.meta-chip {
  padding: 8px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 12px;
}

.alias-inline-alert {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border-radius: 12px;
  font-size: 12px;
}

.alias-inline-alert-error {
  border: 1px solid rgba(239, 68, 68, 0.18);
  background: rgba(254, 242, 242, 0.95);
  color: #b91c1c;
}

.alias-form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  align-items: end;
}

.field-label {
  display: block;
  margin-bottom: 6px;
  color: var(--text-muted);
  font-size: 12px;
}

.toggle-line {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  color: var(--text-secondary);
}

.toggle-line.compact {
  min-height: auto;
  font-size: 12px;
}

.alias-submit {
  justify-content: center;
}

.alias-main-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr);
  gap: 20px;
  align-items: start;
}

.alias-list-card,
.alias-preview-card {
  overflow: hidden;
}

.alias-section-head {
  min-width: 0;
}

.alias-section-badge,
.alias-preview-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

.alias-section-badge {
  color: #0f766e;
  background: rgba(20, 184, 166, 0.1);
  border: 1px solid rgba(20, 184, 166, 0.18);
}

.alias-preview-badge {
  color: #1d4ed8;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.16);
}

.alias-list-body,
.alias-preview-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.alias-list-body {
  background:
    linear-gradient(180deg, rgba(248, 250, 252, 0.82) 0%, rgba(255, 255, 255, 0.98) 100%);
}

.alias-preview-body {
  background:
    radial-gradient(circle at top right, rgba(191, 219, 254, 0.26), transparent 36%),
    linear-gradient(180deg, rgba(239, 246, 255, 0.78) 0%, rgba(255, 255, 255, 0.98) 100%);
}

.alias-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
}

.alias-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-tertiary);
}

.alias-search-input {
  border: none;
  background: transparent;
}

.alias-filter {
  width: 170px;
}

.alias-table-shell {
  overflow-x: auto;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.96);
}

.alias-table {
  width: 100%;
  border-collapse: collapse;
}

.alias-table th,
.alias-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: middle;
  font-size: 13px;
}

.alias-table th {
  color: var(--text-muted);
  font-weight: 600;
  background: rgba(248, 250, 252, 0.92);
}

.alias-table tbody tr {
  transition: background 0.18s ease;
}

.alias-table tbody tr:hover {
  background: rgba(239, 246, 255, 0.62);
}

.alias-norm {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--accent-primary);
  font-size: 11px;
  word-break: break-all;
}

.alias-statuses,
.alias-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: nowrap;
}

.alias-statuses {
  min-width: 140px;
}

.alias-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  padding: 2px 0;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.alias-switch input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.alias-switch-track {
  position: relative;
  width: 34px;
  height: 20px;
  border-radius: 999px;
  background: #dbe7f5;
  transition: background 0.2s ease;
  flex: 0 0 auto;
}

.alias-switch-track::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.18);
  transition: transform 0.2s ease;
}

.alias-switch input:checked + .alias-switch-track {
  background: linear-gradient(135deg, #2563eb 0%, #60a5fa 100%);
}

.alias-switch input:checked + .alias-switch-track::after {
  transform: translateX(14px);
}

.alias-switch-label {
  font-size: 12px;
  font-weight: 600;
}

.compact-btn {
  height: 26px;
  border-radius: 6px;
  font-size: 11px;
  justify-content: center;
  padding: 0 10px;
}

.primary-btn.compact-btn,
.ghost-btn.compact-btn {
  height: 26px;
  font-size: 11px;
  padding: 0 10px;
}

.alias-edit-btn {
  min-width: 52px;
  background: rgba(248, 250, 252, 0.98);
}

.alias-delete-btn {
  width: 28px;
  min-width: 28px;
  height: 28px;
  padding: 0;
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.16);
  background: rgba(254, 242, 242, 0.8);
}

.alias-delete-btn:hover:not(:disabled) {
  color: #fff;
  border-color: transparent;
  background: #ef4444;
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.25);
}

.alias-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding-top: 4px;
}

.alias-pagination-summary {
  color: var(--text-muted);
  font-size: 12px;
}

.alias-pagination-controls {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.alias-page-size {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.alias-page-size-select {
  min-width: 80px;
  height: 26px;
  padding: 0 8px;
  border-radius: 6px;
}

.alias-page-buttons {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.alias-page-number {
  min-width: 26px;
  padding: 0 6px;
}

.alias-page-number.active {
  color: #fff;
  background: linear-gradient(135deg, #2563eb 0%, #60a5fa 100%);
  border-color: transparent;
  box-shadow: 0 4px 10px rgba(37, 99, 235, 0.2);
}

.alias-preview-input {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  padding: 14px;
  border: 1px solid rgba(59, 130, 246, 0.14);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.78);
}

.alias-preview-result {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.alias-preview-alert {
  margin-top: -2px;
}

.preview-meta,
.preview-priority {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--text-secondary);
  font-size: 12px;
  padding: 12px 14px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.8);
}

.preview-hit-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.preview-hit {
  display: grid;
  gap: 4px;
  padding: 14px;
  border: 1px solid rgba(59, 130, 246, 0.12);
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(239, 246, 255, 0.86) 0%, rgba(255, 255, 255, 0.96) 100%);
  box-shadow: 0 8px 18px rgba(148, 163, 184, 0.08);
}

.preview-hit strong {
  color: var(--text-primary);
  font-size: 13px;
}

.preview-hit span {
  color: var(--text-secondary);
}

.preview-payload-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
}

.preview-payload-title {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
}

.preview-payload {
  margin: 0;
  padding: 14px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.84);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.alias-preview-empty {
  display: none;
}

@media (max-width: 1100px) {
  .alias-summary,
  .alias-main-grid,
  .alias-form-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 1080px) {
  .alias-summary,
  .compact-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .alias-form-grid,
  .alias-main-grid {
    grid-template-columns: 1fr;
  }

  .alias-preview-input {
    grid-template-columns: 1fr;
  }

  .alias-pagination,
  .alias-pagination-controls {
    align-items: stretch;
  }
}

@media (max-width: 640px) {
  .alias-summary,
  .compact-summary {
    grid-template-columns: 1fr;
  }
}

.primary-btn,
.ghost-btn {
  height: 38px;
  border-radius: 12px;
  font-family: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  border: 1px solid transparent;
  padding: 0 16px;
  white-space: nowrap;
  transition: transform 0.16s ease, box-shadow 0.2s ease, background 0.2s ease, border-color 0.2s ease;
}

.primary-btn {
  color: #fff;
  background: linear-gradient(135deg, #2563eb 0%, #60a5fa 100%);
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.2);
}

.ghost-btn {
  color: var(--text-secondary);
  background: rgba(248, 250, 252, 0.96);
  border-color: rgba(148, 163, 184, 0.18);
}

.primary-btn:hover:not(:disabled),
.ghost-btn:hover:not(:disabled) {
  transform: translateY(-1px);
}

.primary-btn:hover:not(:disabled) {
  box-shadow: 0 12px 28px rgba(37, 99, 235, 0.35);
  background: linear-gradient(135deg, #1d4ed8 0%, #3b82f6 100%);
}

.ghost-btn:hover:not(:disabled) {
  background: #fff;
  border-color: #cbd5e1;
  color: #0f172a;
}

.primary-btn:disabled,
.ghost-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.primary-btn:disabled {
  background: #94a3b8;
  box-shadow: none;
}

.primary-btn.compact-btn,
.ghost-btn.compact-btn {
  height: 26px;
  font-size: 11px;
  padding: 0 10px;
  border-radius: 6px;
  min-width: unset;
}

.ghost-btn.compact-btn.alias-page-number {
  min-width: 26px;
  padding: 0 6px;
  border-radius: 6px;
}
</style>
