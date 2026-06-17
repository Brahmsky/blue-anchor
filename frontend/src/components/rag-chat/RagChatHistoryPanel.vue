<script setup lang="ts">
import { Clock3, Download, Plus, Trash2 } from 'lucide-vue-next';

import type { SessionListItem } from '@/types/ragChat';

defineProps<{
  activeSessionId: string;
  exportingSessionId: string | null;
  sessionItems: SessionListItem[];
}>();

const emit = defineEmits<{
  deleteSession: [sessionId: string];
  exportSession: [sessionId: string];
  reset: [];
  selectSession: [sessionId: string];
}>();

function confirmDelete(session: SessionListItem) {
  if (window.confirm(`确定要删除对话 "${session.title}" 吗？`)) {
    emit('deleteSession', session.id);
  }
}
</script>

<template>
  <aside class="history-panel">
    <div class="history-panel__create">
      <button class="create-btn" type="button" @click="emit('reset')">
        <Plus :size="14" />
        <span>新建对话</span>
      </button>
    </div>

    <div class="history-panel__list scrollbar-thin">
      <div class="history-label">最近对话</div>
      <button
        v-for="session in sessionItems"
        :key="session.id"
        class="history-item"
        :class="{ active: activeSessionId === session.id }"
        type="button"
        @click="emit('selectSession', session.id)"
      >
        <div class="history-item__title">{{ session.title }}</div>
        <div class="history-item__meta">
          <span class="history-item__time">
            <Clock3 :size="9" />
            {{ session.updatedLabel }}
          </span>
          <div class="history-item__actions">
            <span class="history-item__count">{{ session.count }}条</span>
            <button
              class="history-inline-btn"
              type="button"
              :disabled="exportingSessionId === session.id"
              title="导出会话"
              @click.stop="emit('exportSession', session.id)"
            >
              <Download :size="10" />
            </button>
            <button
              class="history-inline-btn danger"
              type="button"
              title="删除会话"
              @click.stop="confirmDelete(session)"
            >
              <Trash2 :size="10" />
            </button>
          </div>
        </div>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.history-panel {
  min-height: 0;
  border-radius: 18px;
  border: 1px solid rgba(219, 234, 254, 0.95);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.9), rgba(248, 251, 255, 0.96)),
    radial-gradient(circle at top, rgba(96, 165, 250, 0.12), transparent 60%);
  box-shadow: 0 8px 24px rgba(59, 130, 246, 0.05);
  backdrop-filter: blur(14px);
}

.history-panel__create {
  padding: 10px;
  border-bottom: 1px solid rgba(239, 246, 255, 0.95);
}

.create-btn {
  width: 100%;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 10px;
  border: 1px solid rgba(191, 219, 254, 0.95);
  background: linear-gradient(180deg, rgba(239, 246, 255, 0.96), rgba(219, 234, 254, 0.9));
  color: var(--accent-primary);
  font-size: 12px;
  font-weight: 600;
}

.history-panel__list {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
}

.history-label {
  margin-bottom: 8px;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
}

.history-item {
  width: 100%;
  padding: 10px;
  border-radius: 10px;
  border: 1px solid transparent;
  background: transparent;
  text-align: left;
  margin-bottom: 6px;
}

.history-item.active,
.history-item:hover {
  background: rgba(239, 246, 255, 0.95);
  border-color: rgba(191, 219, 254, 0.95);
}

.history-item__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.history-item__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 4px;
  font-size: 10px;
  color: var(--text-muted);
}

.history-item__time {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.history-item__count {
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(219, 234, 254, 0.9);
  color: var(--accent-primary);
}

.history-item__actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.history-inline-btn {
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  background: rgba(255, 255, 255, 0.82);
}

.history-inline-btn:disabled {
  opacity: 0.5;
}

.history-inline-btn.danger:hover {
  color: #b91c1c;
}
</style>
