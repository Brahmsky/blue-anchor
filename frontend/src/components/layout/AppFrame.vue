<script setup lang="ts">
import {
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Database,
  FolderOpen,
  GitBranch,
  HelpCircle,
  MessageSquare,
  Plug,
  RefreshCw,
  Search,
  Sparkles,
  Zap
} from 'lucide-vue-next';
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router';

import { getApiKey, setApiKey } from '@/api/http';
import { fetchHealth } from '@/api/system';

const route = useRoute();
const router = useRouter();

const sidebarCollapsed = ref(false);
const healthStatus = ref('unknown');
let intervalId: number | null = null;
let startupRetryId: number | null = null;
let startupRetryCount = 0;

const pageTitle = computed(() => String(route.meta.title ?? '智锚·索引深蓝'));
const pageSubtitle = computed(() => String(route.meta.description ?? ''));
const searchPlaceholder = computed(() => String(route.meta.searchPlaceholder ?? '搜索知识库、实体、对话...'));
const healthStatusMeta = computed(() => {
  if (healthStatus.value === 'healthy') {
    return {
      label: '系统运行中',
      tone: 'online'
    } as const;
  }

  return {
    label: '状态未知',
    tone: 'unknown'
  } as const;
});

const mainNavItems = [
  {
    to: '/knowledge-base',
    label: '知识库',
    icon: Database
  },
  {
    to: '/graph-explore',
    label: '图谱视图',
    icon: GitBranch
  },
  {
    to: '/rag-chat',
    label: 'RAG 问答',
    icon: MessageSquare
  },
  {
    to: '/benchmark',
    label: 'Benchmark 评测',
    icon: BarChart3
  }
] as const;

const systemNavItems = [
  {
    to: '/system-config',
    label: '数据源管理',
    icon: Plug
  }
] as const;

async function refreshHealth() {
  try {
    const data = await fetchHealth();
    healthStatus.value = data.status;
  } catch {
    healthStatus.value = 'unknown';
  }
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value;
}

function editApiKey() {
  const nextValue = window.prompt('请输入 MiniRAG API Key（留空则移除）', getApiKey()) ?? '';
  setApiKey(nextValue.trim());
  void refreshHealth();
}

function showHelp() {
  void router.push('/help');
}

function goToChat() {
  void router.push('/rag-chat');
}

onMounted(() => {
  void refreshHealth();
  startupRetryId = window.setInterval(() => {
    startupRetryCount += 1;
    void refreshHealth();
    if (healthStatus.value === 'healthy' || startupRetryCount >= 20) {
      if (startupRetryId !== null) {
        window.clearInterval(startupRetryId);
        startupRetryId = null;
      }
    }
  }, 1000);
  intervalId = window.setInterval(() => {
    void refreshHealth();
  }, 5000);
});

onBeforeUnmount(() => {
  if (startupRetryId !== null) {
    window.clearInterval(startupRetryId);
  }
  if (intervalId !== null) {
    window.clearInterval(intervalId);
  }
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ collapsed: sidebarCollapsed }">
      <div class="sidebar-header">
        <div class="logo-icon">
          <Zap :size="18" color="white" />
        </div>
        <div v-if="!sidebarCollapsed">
          <div class="logo-title">智锚·索引深蓝</div>
          <div class="logo-subtitle">船舶装备故障诊断智能问答系统</div>
        </div>
      </div>

      <button class="sidebar-toggle" type="button" :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" @click="toggleSidebar">
        <ChevronRight v-if="sidebarCollapsed" :size="14" />
        <ChevronLeft v-else :size="14" />
      </button>

      <nav class="sidebar-nav scrollbar-thin">
        <div class="nav-section">
          <div v-if="!sidebarCollapsed" class="nav-label">主菜单</div>
          <div class="nav-items">
            <RouterLink
              v-for="item in mainNavItems"
              :key="item.to"
              class="nav-item"
              :class="{ active: route.path === item.to }"
              :to="item.to"
            >
              <component :is="item.icon" class="nav-icon" :size="18" />
              <span v-if="!sidebarCollapsed" class="nav-text">{{ item.label }}</span>
              <div v-if="route.path === item.to && !sidebarCollapsed" class="nav-indicator"></div>
            </RouterLink>
          </div>
        </div>

        <div class="nav-section">
          <div v-if="!sidebarCollapsed" class="nav-label">系统</div>
          <div class="nav-items">
            <RouterLink
              v-for="item in systemNavItems"
              :key="item.to"
              class="nav-item"
              :class="{ active: route.path === item.to }"
              :to="item.to"
            >
              <component :is="item.icon" class="nav-icon" :size="18" />
              <span v-if="!sidebarCollapsed" class="nav-text">{{ item.label }}</span>
              <div v-if="route.path === item.to && !sidebarCollapsed" class="nav-indicator"></div>
            </RouterLink>

            <button class="nav-item" type="button" @click="showHelp">
              <HelpCircle class="nav-icon" :size="18" />
              <span v-if="!sidebarCollapsed" class="nav-text">帮助中心</span>
            </button>
          </div>
        </div>
      </nav>

        <div class="sidebar-footer">
          <div class="user-profile">
            <div class="user-avatar">US</div>
            <div v-if="!sidebarCollapsed" class="user-info">
              <div class="user-name">USER</div>
              <div class="user-role">船舶装备故障诊断智能问答系统</div>
            </div>
          </div>
        </div>
    </aside>

    <div class="main-column">
      <header class="top-bar">
        <div class="page-title">
          <h1>{{ pageTitle }}</h1>
          <p>{{ pageSubtitle }}</p>
        </div>

        <label class="search-bar">
          <Search class="search-icon" :size="16" />
          <input class="search-input" :placeholder="searchPlaceholder" />
          <kbd class="search-shortcut">⌘K</kbd>
        </label>

        <div class="top-actions">
          <div class="status-badge" :class="`status-badge--${healthStatusMeta.tone}`" aria-live="polite">
            <span class="status-dot" aria-hidden="true"></span>
            <span>{{ healthStatusMeta.label }}</span>
          </div>
          <button class="action-btn" type="button" title="刷新" @click="refreshHealth">
            <RefreshCw :size="14" />
          </button>
          <button class="action-btn" type="button" title="API Key" @click="editApiKey">
            <FolderOpen :size="14" />
          </button>
          <button class="primary-btn" type="button" @click="goToChat">
            <Sparkles :size="14" />
            <span>新建查询</span>
          </button>
        </div>
      </header>

      <main class="page-content scrollbar-thin">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background: linear-gradient(180deg, #f8fbff 0%, #f3f8ff 48%, #eef5ff 100%);
  border-right: 1px solid var(--border-sidebar);
  display: flex;
  flex-direction: column;
  position: relative;
  transition:
    width 0.25s ease,
    background 0.25s ease;
  flex-shrink: 0;
}

.sidebar.collapsed {
  width: var(--sidebar-collapsed);
}

.sidebar-header {
  height: var(--topbar-height);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border-sidebar);
  flex-shrink: 0;
}

.logo-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-secondary) 100%);
  box-shadow: 0 0 16px rgba(59, 130, 246, 0.3);
  flex-shrink: 0;
}

.logo-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--text-sidebar-primary);
}

.logo-subtitle {
  font-size: 11px;
  color: var(--text-sidebar-muted);
  margin-top: -1px;
}

.sidebar-toggle {
  position: absolute;
  right: -12px;
  top: 64px;
  width: 24px;
  height: 24px;
  padding: 0;
  background: var(--bg-sidebar-muted);
  border: 1px solid var(--border-sidebar);
  border-radius: 50%;
  color: var(--text-tertiary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 0;
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    color 0.2s ease;
}

.sidebar-toggle :deep(svg) {
  display: block;
}

.sidebar-toggle:hover {
  color: var(--accent-primary);
  border-color: rgba(59, 130, 246, 0.35);
}

.sidebar-nav {
  flex: 1;
  min-height: 0;
  padding: 16px 14px 12px;
  overflow-y: auto;
}

.sidebar.collapsed .sidebar-header {
  justify-content: center;
  padding: 0;
}

.sidebar.collapsed .sidebar-nav {
  padding: 16px 10px 12px;
}

.nav-section {
  margin-bottom: 22px;
}

.nav-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-sidebar-muted);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  margin-bottom: 10px;
  padding-left: 10px;
}

.nav-items {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sidebar.collapsed .nav-items {
  align-items: center;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  padding: 10px 14px;
  border-radius: 12px;
  color: var(--text-tertiary);
  border: 1px solid transparent;
  background: transparent;
  transition:
    background 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease;
}

.sidebar.collapsed .nav-item {
  width: 48px;
  min-height: 48px;
  padding: 0;
  border-radius: 16px;
  justify-content: center;
  gap: 0;
}

.nav-item:hover {
  background: rgba(59, 130, 246, 0.08);
  color: var(--text-sidebar-secondary);
}

.nav-item.active {
  background: linear-gradient(135deg, rgba(219, 234, 254, 0.98) 0%, rgba(191, 219, 254, 0.82) 100%);
  border-color: rgba(59, 130, 246, 0.22);
  color: var(--accent-primary);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.75);
}

.nav-item--disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.nav-item--disabled:hover {
  background: transparent;
  color: var(--text-tertiary);
}

.nav-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent-primary);
  margin-left: auto;
  box-shadow: 0 0 10px rgba(59, 130, 246, 0.45);
}

.nav-icon {
  flex-shrink: 0;
}

.nav-text {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
}

.sidebar-footer {
  margin-top: auto;
  padding: 14px;
  border-top: 1px solid var(--border-sidebar);
  flex-shrink: 0;
}

.user-profile {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 54px;
  padding: 10px 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(239, 246, 255, 0.96) 100%);
  border: 1px solid #dce7f8;
  border-radius: 14px;
  transition: background 0.2s ease;
}

.user-profile:hover {
  background: rgba(219, 234, 254, 0.95);
}

.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent-primary) 0%, #14b8a6 100%);
  display: grid;
  place-items: center;
  color: white;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.user-info {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-sidebar-primary);
}

.user-role {
  font-size: 11px;
  color: var(--text-sidebar-muted);
}

.user-notification {
  color: var(--text-sidebar-muted);
  flex-shrink: 0;
}

.main-column {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

.page-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.top-bar {
  height: var(--topbar-height);
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 22px;
  flex-shrink: 0;
}

.page-title {
  min-width: 0;
  margin-right: 16px;
  width: 250px;
}

.page-title h1,
.page-title p {
  margin: 0;
}

.page-title h1 {
  font-size: 17px;
  font-weight: 700;
}

.page-title p {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 0;
}

.search-bar {
  flex: 1;
  max-width: 430px;
  display: flex;
  align-items: center;
  gap: 10px;
  background: linear-gradient(180deg, rgba(248, 251, 255, 0.98) 0%, rgba(240, 247, 255, 0.92) 100%);
  border: 1px solid #dce7f8;
  border-radius: 14px;
  padding: 0 12px;
  height: 40px;
}

.search-bar:focus-within {
  background: var(--bg-secondary);
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 3px var(--accent-light);
}

.search-icon {
  color: var(--text-muted);
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 13px;
  color: var(--text-primary);
}

.search-shortcut {
  font-size: 10px;
  padding: 2px 5px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-muted);
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.98) 0%, rgba(241, 245, 249, 0.92) 100%);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 12px;
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 500;
  white-space: nowrap;
}

.status-badge--online {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.98) 0%, rgba(209, 250, 229, 0.92) 100%);
  border-color: rgba(16, 185, 129, 0.2);
  color: var(--success);
}

.status-badge--unknown {
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.98) 0%, rgba(241, 245, 249, 0.92) 100%);
  border-color: rgba(148, 163, 184, 0.2);
  color: var(--text-tertiary);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.primary-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 14px;
  height: 36px;
  border: none;
  border-radius: var(--radius);
  background: linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-secondary) 100%);
  color: white;
  font-size: 12px;
  font-weight: 600;
  box-shadow: 0 0 16px rgba(59, 130, 246, 0.3);
}

.primary-btn:hover {
  box-shadow: 0 0 24px rgba(59, 130, 246, 0.5);
  transform: translateY(-1px);
}

@media (max-width: 768px) {
  .sidebar {
    display: none;
  }

  .search-bar {
    display: none;
  }
}
</style>
