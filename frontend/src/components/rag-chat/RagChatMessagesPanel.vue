<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import {
  BookOpen,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  FileText,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  ThumbsDown,
  ThumbsUp
} from 'lucide-vue-next';

import type { ConversationMessage, EvidenceSelection } from '@/types/ragChat';

const props = defineProps<{
  canSend: boolean;
  composerPlaceholder?: string;
  draft: string;
  exportingMessageId: string | null;
  expandedEvidenceIds: string[];
  messages: ConversationMessage[];
  quickQuestions: readonly string[];
  regeneratingMessageId: string | null;
  renderMarkdown: (content: string) => string;
  selectedEvidence: EvidenceSelection | null;
  selectedMessageId: string | null;
  shouldShowEvidenceToggle: (message: ConversationMessage) => boolean;
  getEvidenceToggleLabel: (message: ConversationMessage) => string;
  sending: boolean;
}>();

const emit = defineEmits<{
  copyMessage: [message: ConversationMessage];
  exportMessage: [message: ConversationMessage];
  regenerateMessage: [message: ConversationMessage];
  selectEvidence: [selection: EvidenceSelection];
  selectMessage: [messageId: string];
  send: [];
  toggleEvidence: [messageId: string];
  'update:draft': [value: string];
}>();

const composerRef = ref<HTMLTextAreaElement | null>(null);
const messagesEndRef = ref<HTMLDivElement | null>(null);

const draftModel = computed({
  get: () => props.draft,
  set: (value: string) => emit('update:draft', value)
});

function resizeComposer() {
  if (!composerRef.value) return;
  composerRef.value.style.height = '0px';
  composerRef.value.style.height = `${Math.min(composerRef.value.scrollHeight, 124)}px`;
}

async function scrollToBottom() {
  await nextTick();
  messagesEndRef.value?.scrollIntoView({ behavior: 'smooth' });
}

watch(
  () => props.draft,
  () => nextTick(() => resizeComposer())
);

watch(
  () => props.messages.map((message) => `${message.id}:${message.content.length}:${message.streaming ? '1' : '0'}`).join('|'),
  () => {
    void scrollToBottom();
  }
);

onMounted(() => {
  resizeComposer();
  void scrollToBottom();
});
</script>

<template>
  <main class="chat-main">
    <div class="messages-area scrollbar-thin">
      <div v-if="!messages.length" class="empty-chat-placeholder">
        <div class="empty-chat-inner">
          <Sparkles class="empty-icon" :size="28" />
          <p>围绕船舶设备故障、图谱实体关系发起提问。</p>
        </div>
      </div>

      <article
        v-for="message in messages"
        :key="message.id"
        class="message-row"
        :class="[message.role, { active: selectedMessageId === message.id }]"
        @click="emit('selectMessage', message.id)"
      >
        <div class="message-avatar">
          <span v-if="message.role === 'user'">你</span>
          <Sparkles v-else :size="14" />
        </div>

        <div class="message-card">
          <div class="message-card__body">
            <div
              v-if="message.role === 'assistant' && message.streaming && !message.content && !message.error"
              class="typing-card"
            >
              <span></span>
              <span></span>
              <span></span>
              <em>正在整理回答...</em>
            </div>
            <div
              v-else-if="message.role === 'assistant'"
              class="message-content markdown rich-text-content"
              v-html="renderMarkdown(message.content)"
            ></div>
            <div v-else class="message-content">{{ message.content }}</div>
          </div>

          <button
            v-if="shouldShowEvidenceToggle(message)"
            class="message-evidence-toggle"
            type="button"
            @click.stop="emit('toggleEvidence', message.id)"
          >
            <div class="message-evidence-toggle__left">
              <BookOpen :size="11" />
              <span>{{ getEvidenceToggleLabel(message) }}</span>
            </div>
            <div class="message-evidence-toggle__right">
              <span v-if="message.latencyMs">{{ message.latencyMs }}ms</span>
              <ChevronUp v-if="expandedEvidenceIds.includes(message.id)" :size="12" />
              <ChevronDown v-else :size="12" />
            </div>
          </button>

          <div
            v-if="
              message.role === 'assistant' &&
              expandedEvidenceIds.includes(message.id) &&
              message.evidenceItems?.length
            "
            class="message-evidence-list"
          >
            <button
              v-for="item in message.evidenceItems"
              :key="item.id"
              class="message-evidence-item"
              :class="{
                active:
                  selectedEvidence?.messageId === message.id &&
                  selectedEvidence?.evidenceId === item.id
              }"
              type="button"
              @click.stop="emit('selectEvidence', { messageId: message.id, evidenceId: item.id })"
            >
              <div class="message-evidence-item__icon">
                <FileText :size="10" />
              </div>
              <div class="message-evidence-item__body">
                <p>{{ item.title }}</p>
              </div>
              <span v-if="item.score" class="message-evidence-item__score">{{ item.score }}</span>
            </button>
          </div>

          <div
            v-else-if="
              message.role === 'assistant' &&
              expandedEvidenceIds.includes(message.id) &&
              message.evidenceStatus !== 'loading'
            "
            class="message-evidence-empty"
          >
            <div class="empty-inline">{{ message.evidenceNote || '当前回答还没有可展示的来源片段。' }}</div>
          </div>

          <div v-if="message.role === 'assistant'" class="message-card__toolbar">
            <button class="toolbar-btn" type="button" @click.stop="emit('copyMessage', message)">
              <Copy :size="12" />
            </button>
            <button
              class="toolbar-btn"
              :class="{ 'is-busy': exportingMessageId === message.id }"
              :disabled="exportingMessageId === message.id"
              type="button"
              title="导出回答"
              @click.stop="emit('exportMessage', message)"
            >
              <Download :size="12" />
            </button>
            <button class="toolbar-btn" type="button">
              <ThumbsUp :size="12" />
            </button>
            <button class="toolbar-btn" type="button">
              <ThumbsDown :size="12" />
            </button>
            <button
              class="toolbar-btn"
              :class="{ 'is-busy': regeneratingMessageId === message.id }"
              :disabled="sending || regeneratingMessageId === message.id"
              type="button"
              :title="regeneratingMessageId === message.id ? '正在重新提问…' : '重新提问'"
              @click.stop="emit('regenerateMessage', message)"
            >
              <RotateCcw :size="12" />
            </button>
            <span class="message-card__time">{{ message.timestamp }}</span>
          </div>
        </div>
      </article>

      <div ref="messagesEndRef"></div>
    </div>

    <div class="suggestions-bar">
      <div class="suggestions-bar__label">
        <Search :size="11" />
        <span>建议问题</span>
      </div>
      <div class="suggestions-bar__list scrollbar-thin">
        <button
          v-for="question in quickQuestions"
          :key="question"
          class="suggestion-chip"
          type="button"
          @click="draftModel = question"
        >
          {{ question }}
        </button>
      </div>
    </div>

    <div class="composer-shell">
      <div class="composer-frame">
        <textarea
          ref="composerRef"
          v-model="draftModel"
          class="composer-input scrollbar-thin"
          rows="1"
          :placeholder="composerPlaceholder ?? '基于知识图谱提问... (Enter 发送，Shift+Enter 换行)'"
          @keydown.enter.exact.prevent="emit('send')"
        ></textarea>

        <div class="composer-actions">
          <slot name="composer-actions"></slot>
          <button class="send-btn" type="button" :disabled="!canSend" @click="emit('send')">
            <Send :size="13" />
          </button>
        </div>
      </div>

      <div class="composer-meta">
        <slot name="composer-meta"></slot>
      </div>
    </div>
  </main>
</template>

<style scoped>
.chat-main {
  min-width: 0;
  min-height: 0;
  border-radius: 22px;
  border: 1px solid rgba(219, 234, 254, 0.95);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.05);
  backdrop-filter: blur(14px);
}

.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: 18px 18px 12px;
  background:
    radial-gradient(circle at top right, rgba(96, 165, 250, 0.08), transparent 26%),
    linear-gradient(180deg, rgba(248, 251, 255, 0.72), rgba(255, 255, 255, 0.92));
  display: flex;
  flex-direction: column;
}

.empty-chat-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-chat-inner {
  max-width: 360px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.empty-icon {
  color: var(--accent-secondary);
  opacity: 0.65;
}

.empty-chat-inner p {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.message-row {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.message-row.user {
  flex-direction: row-reverse;
}

.message-row.active .message-card {
  border-color: rgba(96, 165, 250, 0.86);
  box-shadow: 0 16px 28px rgba(59, 130, 246, 0.1);
}

.message-avatar {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: rgba(219, 234, 254, 0.96);
  color: var(--accent-primary);
  border: 1px solid rgba(191, 219, 254, 0.95);
  font-size: 11px;
  font-weight: 700;
}

.message-row.user .message-avatar {
  background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
  color: white;
  border-color: transparent;
}

.message-card {
  max-width: min(100%, 780px);
  border-radius: 16px;
  background: white;
  border: 1px solid rgba(239, 246, 255, 0.98);
  box-shadow: 0 8px 18px rgba(59, 130, 246, 0.05);
  overflow: hidden;
}

.message-card__body {
  padding: 14px 16px;
}

.message-content {
  font-size: 13px;
  line-height: 1.78;
  color: var(--text-primary);
  white-space: pre-wrap;
}

.markdown.message-content {
  white-space: normal;
}

.message-row.user .message-card {
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.96), rgba(219, 234, 254, 0.92));
}

.markdown :deep(p) {
  margin: 0 0 10px;
}

.markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown :deep(strong) {
  color: var(--text-primary);
}

.markdown :deep(.inline-subscript) {
  white-space: nowrap;
}

.markdown :deep(.inline-subscript sub) {
  font-size: 0.72em;
  line-height: 0;
  vertical-align: sub;
}

.markdown :deep(blockquote) {
  margin: 10px 0;
  padding: 8px 10px;
  border-left: 2px solid rgba(59, 130, 246, 0.8);
  background: rgba(239, 246, 255, 0.92);
  border-radius: 0 8px 8px 0;
  color: var(--text-secondary);
}

.message-card__toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-top: 1px solid rgba(239, 246, 255, 0.95);
}

.message-evidence-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  border: none;
  border-top: 1px solid rgba(239, 246, 255, 0.95);
  background: rgba(248, 251, 255, 0.82);
  color: var(--text-muted);
  font-size: 10px;
}

.message-evidence-toggle__left,
.message-evidence-toggle__right {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.message-evidence-toggle__left span {
  color: var(--accent-primary);
}

.message-evidence-list {
  display: grid;
  gap: 8px;
  padding: 0 12px 12px;
  background: rgba(248, 251, 255, 0.82);
}

.message-evidence-empty {
  padding: 0 12px 12px;
  background: rgba(248, 251, 255, 0.82);
}

.message-evidence-item {
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px;
  border-radius: 10px;
  border: 1px solid rgba(219, 234, 254, 0.96);
  background: white;
  text-align: left;
}

.message-evidence-item.active {
  border-color: rgba(96, 165, 250, 0.86);
  box-shadow: 0 10px 20px rgba(59, 130, 246, 0.08);
}

.message-evidence-item__icon {
  width: 20px;
  height: 20px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  background: rgba(219, 234, 254, 0.95);
  color: var(--accent-primary);
  flex-shrink: 0;
}

.message-evidence-item__body {
  flex: 1;
  min-width: 0;
}

.message-evidence-item__body p {
  margin: 0;
  color: var(--text-primary);
  font-size: 11px;
  line-height: 1.7;
}

.message-evidence-item__score {
  flex-shrink: 0;
  padding: 2px 6px;
  border-radius: 999px;
  background: var(--success-light);
  color: var(--success);
  font-size: 10px;
}

.toolbar-btn {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  color: var(--text-muted);
  background: transparent;
  border: none;
}

.toolbar-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.toolbar-btn:hover {
  background: rgba(239, 246, 255, 0.96);
  color: var(--accent-primary);
}

.toolbar-btn.is-busy svg {
  animation: spin 0.8s linear infinite;
}

.message-card__time {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-muted);
}

.typing-card {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid rgba(239, 246, 255, 0.95);
  background: white;
}

.typing-card span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent-primary);
  opacity: 0.5;
  animation: pulse 1.1s infinite ease-in-out;
}

.typing-card span:nth-child(2) {
  animation-delay: 0.15s;
}

.typing-card span:nth-child(3) {
  animation-delay: 0.3s;
}

.typing-card em {
  font-style: normal;
  color: var(--text-muted);
  font-size: 11px;
}

.suggestions-bar {
  padding: 10px 14px;
  border-top: 1px solid rgba(239, 246, 255, 0.95);
  border-bottom: 1px solid rgba(239, 246, 255, 0.95);
  background: linear-gradient(180deg, rgba(248, 251, 255, 0.92), rgba(255, 255, 255, 0.82));
}

.suggestions-bar__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.suggestions-bar__list {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  margin-top: 8px;
  padding-bottom: 2px;
}

.suggestion-chip {
  flex-shrink: 0;
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid rgba(219, 234, 254, 0.95);
  background: white;
  color: var(--text-secondary);
  font-size: 11px;
}

.suggestion-chip:hover {
  border-color: rgba(96, 165, 250, 0.95);
  color: var(--accent-primary);
}

.composer-shell {
  padding: 14px 16px 16px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.82), rgba(240, 247, 255, 0.96));
}

.composer-frame {
  padding: 14px;
  border-radius: 18px;
  border: 1px solid rgba(191, 219, 254, 0.95);
  background:
    radial-gradient(circle at top right, rgba(96, 165, 250, 0.08), transparent 30%),
    linear-gradient(180deg, #ffffff, #f8fbff);
  box-shadow: 0 14px 24px rgba(59, 130, 246, 0.06);
}

.composer-input {
  width: 100%;
  min-height: 24px;
  max-height: 124px;
  border: none;
  background: transparent;
  outline: none;
  resize: none;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
}

.composer-input::placeholder {
  color: var(--text-muted);
}

.composer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.send-btn {
  margin-left: auto;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  border: none;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
  color: white;
  box-shadow: 0 8px 18px rgba(59, 130, 246, 0.18);
}

.send-btn:disabled {
  opacity: 0.45;
  box-shadow: none;
}

.composer-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  font-size: 10px;
  color: var(--text-muted);
}

.empty-inline {
  margin-top: 0;
  min-height: 48px;
  border-radius: 10px;
  border: 1px solid rgba(239, 246, 255, 0.96);
  background: rgba(248, 251, 255, 0.92);
  display: grid;
  place-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-muted);
  text-align: center;
}

@keyframes pulse {
  0%,
  80%,
  100% {
    opacity: 0.35;
    transform: scale(0.8);
  }
  40% {
    opacity: 1;
    transform: scale(1);
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1180px) {
  .chat-main {
    min-height: 620px;
  }

  .composer-meta {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
