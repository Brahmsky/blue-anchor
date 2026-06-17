<script setup lang="ts">
import {
  ArrowUpRight,
  BarChart3,
  Bug,
  Copy,
  Database,
  GitBranch,
  Info,
  MessageSquare,
  Plug,
  Search,
  Terminal,
  Check
} from 'lucide-vue-next';
import { computed, onBeforeUnmount, ref } from 'vue';
import { RouterLink } from 'vue-router';

type HelpSection = {
  id: string;
  title: string;
  subtitle: string;
  keywords: string[];
};

const query = ref('');
const normalizedQuery = computed(() => query.value.trim().toLowerCase());

const sections: HelpSection[] = [
  {
    id: 'tasks',
    title: '常见任务',
    subtitle: '从文档、图谱到问答、评测：每件事在哪里做、怎么做。',
    keywords: ['知识库', '图谱', '问答', 'benchmark', 'alias', '扫描', '上传', '运行']
  },
  {
    id: 'api',
    title: '接口速查',
    subtitle: '用 curl 快速确认后端状态、数据源绑定与图谱输出。',
    keywords: ['curl', 'health', 'system/config', 'graphs', 'graph/summary', 'query']
  }
];

const visibleSections = computed(() => {
  const q = normalizedQuery.value;
  if (!q) {
    return sections;
  }
  return sections.filter((section) => {
    const haystack = [section.title, section.subtitle, ...section.keywords].join(' ').toLowerCase();
    return haystack.includes(q);
  });
});

const copiedKey = ref('');
let copiedTimer: number | null = null;

async function copyText(text: string, key: string) {
  try {
    await navigator.clipboard.writeText(text);
    copiedKey.value = key;
    if (copiedTimer !== null) {
      window.clearTimeout(copiedTimer);
    }
    copiedTimer = window.setTimeout(() => {
      copiedKey.value = '';
      copiedTimer = null;
    }, 1200);
  } catch {
    // Clipboard might be blocked in some environments; we keep UI silent.
  }
}

onBeforeUnmount(() => {
  if (copiedTimer !== null) {
    window.clearTimeout(copiedTimer);
  }
});

const cmdBackendById = 'uv run python -m minirag.api.minirag_server --port 9733 --datasource-id local_ship_docs';
const cmdBackendByRoot = 'uv run python -m minirag.api.minirag_server --port 9733 --datasource-root ./datasources/local_ship_docs';
const cmdFrontendDev = 'cd frontend\npnpm install\npnpm run dev';
const cmdFrontendProxyTo9733 = 'cd frontend\nVITE_PROXY_TARGET=http://127.0.0.1:9733 pnpm run dev';
const cmdHealth = 'curl -sS http://127.0.0.1:9733/health';
const cmdSystemConfig = 'curl -sS http://127.0.0.1:9733/system/config';
const cmdGraphSummary = 'curl -sS "http://127.0.0.1:9733/graph/summary?datasource_id=local_ship_docs"';
const cmdGraphFull = 'curl -sS "http://127.0.0.1:9733/graphs?datasource_id=local_ship_docs&mode=full"';
</script>

<template>
  <div class="help-shell">
    <section class="card help-hero">
      <div class="help-hero__grid">
        <div class="help-hero__main">
          <p class="help-hero__eyebrow">帮助中心</p>
          <h2 class="help-hero__title">智锚·索引深蓝 </h2>
          <p class="help-hero__lead">
            船舶故障维修问答平台操作手册：启动、目录、任务入口、排障顺序、接口速查。
          </p>
          <div class="help-hero__cta">
            <RouterLink class="help-cta" to="/knowledge-base">
              <Database :size="16" />
              <span>去知识库</span>
              <ArrowUpRight :size="14" class="help-cta__arrow" />
            </RouterLink>
            <RouterLink class="help-cta" to="/graph-explore">
              <GitBranch :size="16" />
              <span>去图谱视图</span>
              <ArrowUpRight :size="14" class="help-cta__arrow" />
            </RouterLink>
            <RouterLink class="help-cta" to="/rag-chat">
              <MessageSquare :size="16" />
              <span>去 RAG 问答</span>
              <ArrowUpRight :size="14" class="help-cta__arrow" />
            </RouterLink>
            <RouterLink class="help-cta" to="/benchmark">
              <BarChart3 :size="16" />
              <span>去 Benchmark</span>
              <ArrowUpRight :size="14" class="help-cta__arrow" />
            </RouterLink>
            <RouterLink class="help-cta help-cta--muted" to="/system-config">
              <Plug :size="16" />
              <span>去数据源管理</span>
              <ArrowUpRight :size="14" class="help-cta__arrow" />
            </RouterLink>
          </div>
        </div>

        <div class="help-hero__side">
          <div class="help-kpi">
            <div class="help-kpi__item">
              <div class="help-kpi__label">运行方式</div>
              <div class="help-kpi__value">datasource-based</div>
            </div>
            <div class="help-kpi__item">
              <div class="help-kpi__label">默认数据源</div>
              <div class="help-kpi__value">local_ship_docs</div>
            </div>
            <div class="help-kpi__item">
              <div class="help-kpi__label">关键目录</div>
              <div class="help-kpi__value">outputs/graph/workdir</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <div class="help-grid">
      <main class="help-main">
        <section v-if="visibleSections.some((s) => s.id === 'tasks')" id="tasks" class="card help-card">
          <header class="card-header">
            <div>
              <div class="card-title">常见任务</div>
            </div>
          </header>
          <div class="card-body help-body">
            <div class="help-task-grid">
              <article class="help-task">
                <div class="help-task__title">
                  <Database :size="16" />
                  <span>上传 / 扫描文档</span>
                </div>
                <p class="help-task__text">
                  入口在 <RouterLink class="help-link" to="/knowledge-base">知识库</RouterLink>。
                  列表出现文档，状态进入 indexed/processing，打开详情看到摘要/分块信息。
                </p>
              </article>
              <article class="help-task">
                <div class="help-task__title">
                  <GitBranch :size="16" />
                  <span>浏览总图 / 子图</span>
                </div>
                <p class="help-task__text">
                  入口在 <RouterLink class="help-link" to="/graph-explore">图谱视图</RouterLink>。
                  看到节点/关系数量，点击节点能加载详情与关系预览。
                </p>
              </article>
              <article class="help-task">
                <div class="help-task__title">
                  <MessageSquare :size="16" />
                  <span>发起一次问答</span>
                </div>
                <p class="help-task__text">
                  入口在 <RouterLink class="help-link" to="/rag-chat">RAG 问答</RouterLink>。
                  回答可读、引用可展开、引用内容来自当前 datasource 的文档证据。
                </p>
              </article>
              <article class="help-task">
                <div class="help-task__title">
                  <BarChart3 :size="16" />
                  <span>跑一轮 Benchmark</span>
                </div>
                <p class="help-task__text">
                  入口在 <RouterLink class="help-link" to="/benchmark">Benchmark 评测</RouterLink>。
                  完成一次运行并返回统计；失败时在页面看到明确错误信息。
                </p>
              </article>
              <article class="help-task">
                <div class="help-task__title">
                  <Plug :size="16" />
                  <span>确认数据源与 Alias</span>
                </div>
                <p class="help-task__text">
                  入口在 <RouterLink class="help-link" to="/system-config">数据源管理</RouterLink>。
                  看到 datasource 根路径、工作目录、以及 alias 统计与启用状态。
                </p>
              </article>
            </div>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<style scoped>
.help-shell {
  display: grid;
  gap: 20px;
}

.help-hero {
  padding: 22px 22px 20px;
  background:
    radial-gradient(circle at 14% 12%, rgba(191, 219, 254, 0.55), transparent 34%),
    radial-gradient(circle at 90% 30%, rgba(219, 234, 254, 0.7), transparent 38%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.95), rgba(255, 255, 255, 0.88));
}

.help-hero__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 18px;
  align-items: start;
}

.help-hero__eyebrow {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent-primary);
}

.help-hero__title {
  margin: 0;
  font-size: 28px;
  line-height: 1.18;
}

.help-hero__lead {
  margin: 10px 0 0;
  color: var(--text-secondary);
  line-height: 1.75;
  max-width: 760px;
}

.help-hero__cta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}

.help-cta {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: var(--shadow-sm);
  color: var(--text-primary);
  transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
}

.help-cta:hover {
  transform: translateY(-1px);
  border-color: var(--border-accent);
  box-shadow: var(--shadow-glow);
}

.help-cta--muted {
  background: rgba(239, 246, 255, 0.65);
}

.help-cta__arrow {
  color: var(--text-tertiary);
}

.help-hero__side {
  display: grid;
  gap: 14px;
}

.help-search {
  position: relative;
  display: flex;
  align-items: center;
}

.help-search__icon {
  position: absolute;
  left: 12px;
  color: var(--text-muted);
}

.help-search__input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 10px 12px 10px 36px;
  background: rgba(255, 255, 255, 0.9);
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.help-search__input:focus {
  border-color: var(--border-accent);
  box-shadow: var(--shadow-glow);
}

.help-kpi {
  display: grid;
  gap: 10px;
}

.help-kpi__item {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
}

.help-kpi__label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.help-kpi__value {
  margin-top: 4px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.help-grid {
  display: block;
}

.help-main {
  display: grid;
  gap: 20px;
  min-width: 0;
}

.help-card :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
}

.help-body {
  display: grid;
  gap: 16px;
}

.help-split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
}

.help-block {
  border: 1px solid var(--border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  padding: 16px;
  display: grid;
  gap: 12px;
}

.help-block__title {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  color: var(--text-primary);
}

.help-code {
  position: relative;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(248, 251, 255, 0.95), rgba(239, 246, 255, 0.78));
  padding: 12px 12px 12px;
  overflow: hidden;
}

.help-code--tight {
  padding: 10px 10px 10px;
}

.help-code pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  padding-right: 74px;
  color: #0f172a;
  line-height: 1.65;
}

.help-copy {
  position: absolute;
  top: 10px;
  right: 10px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.95);
  border-radius: 12px;
  padding: 7px 10px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 650;
  color: var(--text-secondary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease, color 0.15s ease;
}

.help-copy:hover {
  border-color: var(--border-accent);
  box-shadow: var(--shadow-glow);
  color: var(--text-primary);
}

.help-muted {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.75;
}

.help-callout {
  border: 1px solid var(--border-accent);
  background: rgba(239, 246, 255, 0.9);
  border-radius: 18px;
  padding: 14px 16px;
}

.help-callout--soft {
  background: rgba(248, 251, 255, 0.92);
  border-color: var(--border);
}

.help-callout__title {
  font-size: 13px;
  font-weight: 800;
  color: var(--text-primary);
}

.help-callout__text {
  margin: 8px 0 0;
  color: var(--text-secondary);
  line-height: 1.75;
}

.help-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.help-fact {
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  display: grid;
  gap: 6px;
}

.help-fact__k {
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 650;
}

.help-fact__v {
  font-size: 13px;
  font-weight: 800;
  color: var(--text-primary);
}

.help-fact__d {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.help-task-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.help-task {
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  display: grid;
  gap: 10px;
}

.help-task__title {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-weight: 800;
  color: var(--text-primary);
}

.help-task__text {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.75;
}

.help-link {
  color: var(--accent-primary);
  font-weight: 700;
  text-decoration: underline;
  text-decoration-color: rgba(59, 130, 246, 0.35);
  text-underline-offset: 3px;
}

.help-accordion {
  display: grid;
  gap: 10px;
}

.help-detail {
  border: 1px solid var(--border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  padding: 4px 0;
  overflow: hidden;
}

.help-detail__summary {
  list-style: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  font-weight: 850;
  color: var(--text-primary);
}

.help-detail__summary::-webkit-details-marker {
  display: none;
}

.help-detail__body {
  padding: 0 14px 14px;
  display: grid;
  gap: 10px;
}

.help-api-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.help-api {
  border: 1px solid var(--border);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  padding: 14px;
  display: grid;
  gap: 10px;
}

.help-api__title {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-weight: 850;
  color: var(--text-primary);
}

.help-nav {
  display: grid;
  gap: 20px;
  position: sticky;
  top: 18px;
}

.help-nav__card {
  padding: 14px;
}

.help-nav__card--mini {
  padding: 16px;
}

.help-nav__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 2px 12px;
  border-bottom: 1px solid var(--border-light);
}

.help-nav__title {
  font-weight: 900;
  color: var(--text-primary);
  letter-spacing: 0.02em;
}

.help-nav__hint {
  font-size: 12px;
  color: var(--text-muted);
}

.help-nav__list {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.help-nav__item {
  padding: 10px 10px;
  border-radius: 14px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.92);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  display: grid;
  gap: 4px;
}

.help-nav__item:hover {
  border-color: var(--border-accent);
  box-shadow: var(--shadow-glow);
}

.help-nav__name {
  font-size: 13px;
  font-weight: 850;
  color: var(--text-primary);
}

.help-nav__sub {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.help-mini__title {
  font-weight: 900;
  color: var(--text-primary);
  margin-bottom: 10px;
}

.help-mini__list {
  display: grid;
  gap: 10px;
}

.help-check {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  color: var(--text-secondary);
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .help-hero__grid {
    grid-template-columns: 1fr;
  }

  .help-grid {
    display: block;
  }

  .help-nav {
    position: static;
  }
}

@media (max-width: 860px) {
  .help-split,
  .help-task-grid,
  .help-api-grid,
  .help-facts {
    grid-template-columns: 1fr;
  }
}
</style>
