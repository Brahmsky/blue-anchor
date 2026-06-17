<script setup lang="ts">
import RagAnswerMeta from '@/components/rag-chat/RagAnswerMeta.vue';
import RagEvidenceDetail from '@/components/rag-chat/RagEvidenceDetail.vue';

import type { ConversationMessage, EvidenceItem, FactItem } from '@/types/ragChat';

defineProps<{
  getEvidenceDetailEmptyCopy: (message: ConversationMessage | null) => string;
  selectedEvidenceItem: EvidenceItem | null;
  selectedMessage: ConversationMessage | null;
  transportFacts: FactItem[];
}>();
</script>

<template>
  <aside class="context-panel">
    <RagEvidenceDetail
      :get-evidence-detail-empty-copy="getEvidenceDetailEmptyCopy"
      :selected-evidence-item="selectedEvidenceItem"
      :selected-message="selectedMessage"
    />

    <RagAnswerMeta :transport-facts="transportFacts" />
  </aside>
</template>

<style scoped>
.context-panel {
  min-height: 0;
  min-width: 0;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  gap: 12px;
}

@media (max-width: 1180px) {
  .context-panel {
    grid-template-rows: none;
  }
}
</style>
