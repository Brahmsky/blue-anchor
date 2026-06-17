<script setup lang="ts">
import { computed } from 'vue';
import { FileText } from 'lucide-vue-next';

import type { ConversationMessage, EvidenceItem } from '@/types/ragChat';
import { renderRichText } from '@/utils/richText';

const props = defineProps<{
  getEvidenceDetailEmptyCopy: (message: ConversationMessage | null) => string;
  selectedEvidenceItem: EvidenceItem | null;
  selectedMessage: ConversationMessage | null;
}>();

const citationBody = computed(() => props.selectedEvidenceItem?.raw?.trim() ?? '');
const citationBodyHtml = computed(() => renderRichText(citationBody.value));
</script>

<template>
  <section class="evidence-panel">
    <div v-if="selectedEvidenceItem" class="evidence-panel__body">
      <div
        v-if="citationBody"
        class="evidence-panel__body-text rich-text-content scrollbar-thin"
        v-html="citationBodyHtml"
      ></div>

      <div v-else class="evidence-panel__empty">
        当前来源片段没有可展示的正文内容。
      </div>
    </div>

    <div v-else class="evidence-panel__empty evidence-panel__empty--panel">
      <FileText :size="18" />
      <span>{{ getEvidenceDetailEmptyCopy(selectedMessage) }}</span>
    </div>
  </section>
</template>

<style scoped>
.evidence-panel {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border-radius: 18px;
  border: 1px solid rgba(219, 234, 254, 0.96);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 251, 255, 0.96));
  overflow: hidden;
}

.evidence-panel__body {
  flex: 1;
  min-height: 0;
  padding: 12px;
}

.evidence-panel__body-text {
  height: 100%;
  min-height: 220px;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 10px;
  border-radius: 12px;
  border: 1px solid rgba(239, 246, 255, 0.96);
  background: rgba(248, 251, 255, 0.96);
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-secondary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.evidence-panel__empty {
  display: grid;
  place-items: center;
  gap: 6px;
  min-height: 96px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(239, 246, 255, 0.96);
  background: rgba(248, 251, 255, 0.92);
  font-size: 11px;
  color: var(--text-muted);
  text-align: center;
}

.evidence-panel__empty--panel {
  margin: 12px;
  flex: 1;
}
</style>
