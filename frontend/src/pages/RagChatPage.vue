<script setup lang="ts">
import RagChatContextPanel from '@/components/rag-chat/RagChatContextPanel.vue';
import RagChatHistoryPanel from '@/components/rag-chat/RagChatHistoryPanel.vue';
import RagChatMessagesPanel from '@/components/rag-chat/RagChatMessagesPanel.vue';
import { useRagChat } from '@/composables/useRagChat';
import { QUICK_QUESTIONS } from '@/types/ragChat';

const {
  activeSessionId,
  canSend,
  capabilities,
  chatStateError,
  copyMessage,
  deleteSession,
  draft,
  expandedEvidenceIds,
  exportMessage,
  exportSession,
  exportingMessageId,
  exportingSessionId,
  getEvidenceDetailEmptyCopy,
  getEvidenceToggleLabel,
  isKeywordMode,
  isTextOnlyRetrieval,
  loadingChatState,
  messages,
  regeneratingMessageId,
  regenerateMessage,
  renderMarkdown,
  retrievalSettingLabel,
  resetConversation,
  selectedEvidence,
  selectedEvidenceItem,
  selectedMessage,
  selectedMessageId,
  selectEvidence,
  selectMessage,
  sendMessage,
  sending,
  sessionItems,
  shouldShowEvidenceToggle,
  transportFacts,
  toggleEvidence
} = useRagChat();
</script>

<template>
  <div class="rag-chat-page">
    <RagChatHistoryPanel
      :active-session-id="activeSessionId"
      :exporting-session-id="exportingSessionId"
      :session-items="sessionItems"
      @delete-session="deleteSession"
      @export-session="exportSession"
      @reset="resetConversation"
      @select-session="activeSessionId = $event"
    />

    <div class="rag-chat-main">
      <div v-if="loadingChatState || chatStateError" class="chat-state-banner" :class="{ error: chatStateError }">
        <span v-if="loadingChatState">正在恢复历史会话…</span>
        <span v-else>{{ chatStateError }}</span>
      </div>

      <RagChatMessagesPanel
        :can-send="canSend"
        :draft="draft"
        :expanded-evidence-ids="expandedEvidenceIds"
        :exporting-message-id="exportingMessageId"
        :get-evidence-toggle-label="getEvidenceToggleLabel"
        :messages="messages"
        :quick-questions="QUICK_QUESTIONS"
        :regenerating-message-id="regeneratingMessageId"
        :render-markdown="renderMarkdown"
        :selected-evidence="selectedEvidence"
        :selected-message-id="selectedMessageId"
        :sending="sending"
        :should-show-evidence-toggle="shouldShowEvidenceToggle"
        @copy-message="copyMessage"
        @export-message="exportMessage"
        @regenerate-message="regenerateMessage"
        @select-evidence="selectEvidence"
        @select-message="selectMessage"
        @send="sendMessage"
        @toggle-evidence="toggleEvidence"
        @update:draft="draft = $event"
      >
        <template #composer-actions>
          <label class="toggle-chip" title="只从文本 chunk 里检索，不使用图谱锚点">
            <input v-model="isTextOnlyRetrieval" type="checkbox" />
            <span>仅文本检索</span>
          </label>
          <label class="toggle-chip">
            <input v-model="isKeywordMode" type="checkbox" />
            <span>关键词检索</span>
          </label>
        </template>

        <template #composer-meta>
          <span>
            检索设定: {{ retrievalSettingLabel }}
            · 上下文记忆: 最近 3 轮问答
            · Top-K: 6
          </span>
          <span>{{ draft.length }}/2000</span>
        </template>
      </RagChatMessagesPanel>

    </div>

    <RagChatContextPanel
      :get-evidence-detail-empty-copy="getEvidenceDetailEmptyCopy"
      :selected-evidence-item="selectedEvidenceItem"
      :selected-message="selectedMessage"
      :transport-facts="transportFacts"
    />
  </div>
</template>

<style scoped>
.rag-chat-page {
  display: grid;
  grid-template-columns: 218px minmax(0, 1fr) 288px;
  gap: 14px;
  height: calc(100vh - var(--topbar-height) - 40px);
  min-height: 0;
}

.rag-chat-main {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rag-chat-main :deep(.chat-main) {
  flex: 1;
  min-height: 0;
}

.chat-state-banner {
  padding: 10px 12px;
  border-radius: 14px;
  border: 1px solid rgba(191, 219, 254, 0.95);
  background: rgba(239, 246, 255, 0.88);
  font-size: 12px;
  color: var(--text-secondary);
}

.chat-state-banner.error {
  border-color: rgba(254, 202, 202, 0.95);
  background: rgba(254, 242, 242, 0.96);
  color: #b91c1c;
}

.toggle-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border-radius: 999px;
  background: rgba(239, 246, 255, 0.95);
  border: 1px solid rgba(219, 234, 254, 0.95);
  font-size: 11px;
  color: var(--text-secondary);
}

@media (max-width: 1180px) {
  .rag-chat-page {
    grid-template-columns: 1fr;
    height: auto;
  }
}
</style>
