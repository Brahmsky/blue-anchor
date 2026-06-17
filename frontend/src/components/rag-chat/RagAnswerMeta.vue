<script setup lang="ts">
import { BookOpen } from 'lucide-vue-next';

import type { FactItem } from '@/types/ragChat';

defineProps<{
  transportFacts: FactItem[];
}>();
</script>

<template>
  <section class="answer-meta">
    <div class="answer-meta__title">
      <BookOpen :size="12" />
      <span>回答信息</span>
    </div>

    <div class="answer-meta__list">
      <div v-for="item in transportFacts" :key="item.label" class="answer-meta__row">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </div>
    </div>
  </section>
</template>

<style scoped>
.answer-meta {
  min-width: 0;
  padding: 12px;
  border-radius: 16px;
  border: 1px solid rgba(239, 246, 255, 0.96);
  background: rgba(255, 255, 255, 0.96);
}

.answer-meta__title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.answer-meta__list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.answer-meta__row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 10px;
  background: rgba(248, 251, 255, 0.96);
  font-size: 10px;
}

.answer-meta__row span {
  color: var(--text-muted);
}

.answer-meta__row strong {
  color: var(--text-primary);
  max-width: 132px;
  text-align: right;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
</style>
