<script setup lang="ts">
import {
  AlertCircle,
  BarChart3,
  ChevronDown,
  ChevronUp,
  Clock3,
  Play,
  RotateCcw,
  Square,
  Target,
} from 'lucide-vue-next';
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { benchmarkRun, getBenchmarkStatus, resetBenchmark, stopBenchmarkRun } from '@/api/benchmark';
import type { BenchmarkRunResponse } from '@/api/benchmark';
import { BENCHMARK_SCORE_LABELS } from '@/types/api';
import type { BenchmarkModelOption, BenchmarkPageSnapshot, BenchmarkResultItem, BenchmarkState } from '@/types/api';

type StatusTone = 'info' | 'warning' | 'success' | 'error';
type ActionKind = 'start' | 'stop' | 'reset';

interface StateMeta {
  label: string;
  tone: StatusTone;
  title: string;
  description: string;
}

interface ModeResultCard {
  key: string;
  label: string;
  tone: StatusTone;
  statusLabel: string;
  responseTimeLabel: string;
  recallLabel: string;
  answer: string;
}

interface ModeSummaryCard {
  key: string;
  label: string;
  accuracy: string;
  completed: string;
  avgResponse: string;
  avgRecall: string;
  tone: StatusTone;
}

interface ResultRow {
  key: string;
  tone: StatusTone;
  questionId: string;
  questionTypeLabel: string;
  question: string;
  statusLabel: string;
  responseTimeLabel: string;
  completedAtLabel: string;
  modelAnswer: string;
  goldAnswer: string;
  errorMessage: string;
  primaryModeLabel: string;
  primaryRecallLabel: string;
  modeResults: ModeResultCard[];
}

const BENCHMARK_POLL_INTERVAL_MS = 1000;
const activeStates = new Set<BenchmarkState>(['starting', 'running', 'stopping']);
const benchmarkModeMeta: Record<string, { label: string; description: string }> = {
  graph_text_hybrid: {
    label: '图文混合',
    description: '结合图谱与文本检索。'
  },
  graph_only: {
    label: '纯图谱',
    description: '基于图谱关系与结构回答。'
  },
  text_only: {
    label: '纯文本',
    description: '基于文本检索上下文回答。'
  }
};

const stateMetaByState: Record<BenchmarkState, StateMeta> = {
  idle: {
    label: '待开始',
    tone: 'info',
    title: '暂无进行中的评测。',
    description: '选择模型后开始评测。'
  },
  starting: {
    label: '准备中',
    tone: 'warning',
    title: '评测任务正在启动。',
    description: '评测任务正在准备。'
  },
  running: {
    label: '运行中',
    tone: 'success',
    title: '评测正在执行。',
    description: '结果将持续更新。'
  },
  stopping: {
    label: '停止中',
    tone: 'warning',
    title: '正在停止当前评测。',
    description: '已生成的结果会保留。'
  },
  stopped: {
    label: '已停止',
    tone: 'warning',
    title: '评测已手动停止。',
    description: '可重新开始或重置结果。'
  },
  completed: {
    label: '已完成',
    tone: 'success',
    title: '评测已经完成。',
    description: '可查看结果明细或重新开始。'
  },
  failed: {
    label: '失败',
    tone: 'error',
    title: '评测运行失败。',
    description: '请先查看错误信息，确认环境后再重试。'
  }
};

const snapshot = ref<BenchmarkPageSnapshot | null>(null);
const selectedModel = ref('');
const selectedJudgeModelType = ref<'cloud' | 'local'>('cloud');
const loadingStatus = ref(false);
const actionPending = ref<ActionKind | null>(null);
const statusLoadError = ref('');
const expandedResultKey = ref<string | null>(null);
const hasHydratedOnce = ref(false);
const optimisticRun = ref<{ runId: string; expiresAt: number } | null>(null);

let pollTimer: number | null = null;
let refreshStatusPromise: Promise<void> | null = null;

const modelOptions = computed<BenchmarkModelOption[]>(() => snapshot.value?.available_models ?? []);
const hasAvailableModels = computed(() => modelOptions.value.length > 0);
const currentState = computed<BenchmarkState | null>(() => snapshot.value?.state ?? null);
const stateMeta = computed(() => (currentState.value ? stateMetaByState[currentState.value] : null));
const backendErrorMessage = computed(() => snapshot.value?.error_message?.trim() ?? '');
const inlineErrorMessage = computed(() => statusLoadError.value || backendErrorMessage.value);
const summary = computed(() => snapshot.value?.summary ?? null);
const isInitialLoading = computed(() => loadingStatus.value && !hasHydratedOnce.value);
const isPollingState = computed(() => currentState.value !== null && activeStates.has(currentState.value));

const controlState = computed(() => {
  if (!snapshot.value) {
    return { start: false, stop: false, reset: false };
  }

  return {
    start: snapshot.value.can_start && hasAvailableModels.value && Boolean(selectedModel.value),
    stop: snapshot.value.can_stop,
    reset: snapshot.value.can_reset
  };
});

const progressValue = computed(() => clampPercentage(snapshot.value?.progress_percent ?? 0));
const averageResponseMs = computed(() => summary.value?.avg_response_time_ms ?? 0);
const primaryMode = computed(() => summary.value?.primary_mode ?? 'graph_text_hybrid');
const completedCountLabel = computed(() => {
  if (!summary.value) {
    return '—';
  }
  return `${summary.value.completed}/${summary.value.total}`;
});

const modeSummaryCards = computed<ModeSummaryCard[]>(() => {
  const modeSummaries = summary.value?.mode_summaries;
  if (!modeSummaries) {
    return [];
  }

  return Object.entries(modeSummaries).map(([mode, modeSummary]) => ({
    key: mode,
    label: benchmarkModeLabel(mode),
    accuracy: formatPercent(modeSummary.accuracy_percent),
    completed: `${modeSummary.completed}/${summary.value?.total ?? modeSummary.completed}`,
    avgResponse: formatDuration(modeSummary.avg_response_time_ms),
    avgRecall: formatPercent(modeSummary.avg_recall_rate ?? Number.NaN),
    tone: resolveScoreTone(
      modeSummary.correct_count > 0
        ? 1
        : modeSummary.partial_count > 0
          ? 0
          : modeSummary.wrong_count > 0
            ? -1
            : undefined
    )
  }));
});

const recentResults = computed<ResultRow[]>(() => {
  const items = [...(snapshot.value?.recent_results ?? [])];

  items.sort((left, right) => compareResultOrder(left.completed_at, right.completed_at));

  return items.map((item, index) => ({
    key: `${item.question_id}-${item.completed_at}-${index}`,
    tone: resolveResultTone(item),
    questionId: item.question_id,
    questionTypeLabel: item.question_type?.trim() || '未分类',
    question: item.question,
    statusLabel: item.status_label,
    responseTimeLabel: formatDuration(item.response_time_ms),
    completedAtLabel: formatDateTime(item.completed_at),
    modelAnswer: item.model_answer?.trim() || '未返回模型回答。',
    goldAnswer: item.gold_answer?.trim() || '未提供参考答案。',
    errorMessage: item.error_message?.trim() || '',
    primaryModeLabel: benchmarkModeLabel(item.primary_mode || primaryMode.value),
    primaryRecallLabel: formatPercent(
      item.mode_recall_rates?.[item.primary_mode || primaryMode.value] ?? Number.NaN
    ),
    modeResults: Object.entries(item.mode_answers ?? {}).map(([mode, answer]) => {
      const score = item.mode_scores?.[mode];
      return {
        key: mode,
        label: benchmarkModeLabel(mode),
        tone: resolveScoreTone(score),
        statusLabel: item.mode_status_labels?.[mode] ?? (typeof score === 'number' ? BENCHMARK_SCORE_LABELS[score as 1 | 0 | -1] : '未判分'),
        responseTimeLabel: formatDuration(item.mode_response_time_ms?.[mode] ?? Number.NaN),
        recallLabel: formatPercent(item.mode_recall_rates?.[mode] ?? Number.NaN),
        answer: answer?.trim() || '未返回模型回答。'
      };
    })
  }));
});

const pageLead = computed(() => {
  if (isInitialLoading.value) {
    return {
      title: '正在同步评测状态…',
      description: '正在读取结果。'
    };
  }

  if (recentResults.value.length) {
    return {
      title: '结果已生成',
      description: '展开题目可查看各模式回答、得分和参考答案。'
    };
  }

  return {
    title: stateMeta.value?.title ?? '暂时无法读取评测状态。',
    description: stateMeta.value?.description ?? '请稍后重试，或确认后端服务是否正常运行。'
  };
});

const emptyState = computed(() => {
  if (isInitialLoading.value) {
    return {
      title: '正在读取评测状态',
      description: '正在读取结果。'
    };
  }

  if (currentState.value === 'idle') {
    return {
      title: '当前还没有评测结果',
      description: '选择模型并开始运行后，这里会显示每一道题的最新结果。'
    };
  }

  if (currentState.value === 'failed') {
    return {
      title: '暂无可展示结果',
      description: '请先查看错误信息并确认运行环境，再尝试重新开始。'
    };
  }

  return {
    title: '等待结果返回',
    description: '运行开始后，结果会自动出现在这里。'
  };
});

function formatDuration(value: number) {
  if (!Number.isFinite(value) || value < 0) {
    return '—';
  }
  return `${Math.round(value)} ms`;
}

function formatPercent(value: number) {
  if (!Number.isFinite(value)) {
    return '—';
  }
  return `${value.toFixed(1)}%`;
}

function formatPrecisePercent(value: number) {
  if (!Number.isFinite(value)) {
    return '—';
  }
  return `${value.toFixed(1)}%`;
}

function clampPercentage(value: number) {
  return Math.max(0, Math.min(100, value));
}

function formatDateTime(value: string) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value || '时间未提供';
  }

  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(date);
}

function compareResultOrder(leftCompletedAt: string, rightCompletedAt: string) {
  const leftTimestamp = Date.parse(leftCompletedAt);
  const rightTimestamp = Date.parse(rightCompletedAt);
  const leftValid = Number.isFinite(leftTimestamp);
  const rightValid = Number.isFinite(rightTimestamp);

  if (leftValid && rightValid && leftTimestamp !== rightTimestamp) {
    return rightTimestamp - leftTimestamp;
  }

  if (leftValid && !rightValid) {
    return -1;
  }

  if (!leftValid && rightValid) {
    return 1;
  }

  return 0;
}

function resolveResultTone(item: BenchmarkResultItem): StatusTone {
  if (item.error_message?.trim()) {
    return 'error';
  }

  if (item.raw_score === 1) {
    return 'success';
  }

  if (item.raw_score === 0) {
    return 'warning';
  }

  if (item.raw_score === -1) {
    return 'error';
  }

  return 'info';
}

function benchmarkModeLabel(mode: string) {
  return benchmarkModeMeta[mode]?.label ?? mode;
}

function benchmarkModeDescription(mode: string) {
  return benchmarkModeMeta[mode]?.description ?? '';
}

function resolveScoreTone(score: number | undefined): StatusTone {
  if (score === 1) {
    return 'success';
  }
  if (score === 0) {
    return 'warning';
  }
  if (score === -1) {
    return 'error';
  }
  return 'info';
}

function syncSelectedModel(nextSnapshot: BenchmarkPageSnapshot) {
  const options = nextSnapshot.available_models;
  const hasSnapshotModel = Boolean(nextSnapshot.selected_model)
    && options.some((option) => option.id === nextSnapshot.selected_model);

  if (hasSnapshotModel) {
    selectedModel.value = nextSnapshot.selected_model;
    return;
  }

  if (!options.some((option) => option.id === selectedModel.value)) {
    selectedModel.value = options[0]?.id ?? '';
  }
}

function applyStartedRun(response: BenchmarkRunResponse) {
  const optimisticState: BenchmarkState = activeStates.has(response.state) ? response.state : 'starting';
  optimisticRun.value = {
    runId: response.run_id,
    expiresAt: Date.now() + 10_000
  };

  if (snapshot.value) {
    snapshot.value = {
      ...snapshot.value,
      state: optimisticState,
      run_id: response.run_id,
      selected_model: response.selected_model,
      can_start: false,
      can_stop: true,
      can_reset: false,
      error_message: ''
    };
  } else {
    snapshot.value = {
      state: optimisticState,
      run_id: response.run_id,
      progress_percent: 0,
      summary: undefined,
      recent_results: [],
      available_models: modelOptions.value,
      selected_model: response.selected_model,
      can_start: false,
      can_stop: true,
      can_reset: false,
      error_message: ''
    };
  }

  selectedModel.value = response.selected_model;
}

function toggleResultRow(resultKey: string) {
  expandedResultKey.value = expandedResultKey.value === resultKey ? null : resultKey;
}

function stopStatusPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startStatusPolling() {
  if (pollTimer !== null || !isPollingState.value) {
    return;
  }

  pollTimer = window.setInterval(() => {
    void refreshStatus();
  }, BENCHMARK_POLL_INTERVAL_MS);
}

function refreshStatus() {
  if (refreshStatusPromise) {
    return refreshStatusPromise;
  }

  refreshStatusPromise = (async () => {
    loadingStatus.value = true;
    statusLoadError.value = '';

    try {
      const nextSnapshot = await getBenchmarkStatus();
      const optimistic = optimisticRun.value;
      const shouldIgnoreStaleIdleSnapshot = Boolean(
        optimistic
        && optimistic.expiresAt > Date.now()
        && currentState.value
        && activeStates.has(currentState.value)
        && nextSnapshot.state === 'idle'
        && (!nextSnapshot.run_id || nextSnapshot.run_id !== optimistic.runId)
      );

      if (shouldIgnoreStaleIdleSnapshot) {
        if (snapshot.value) {
          snapshot.value = {
            ...snapshot.value,
            available_models: nextSnapshot.available_models,
            selected_model: nextSnapshot.selected_model || snapshot.value.selected_model
          };
          syncSelectedModel(snapshot.value);
        }
        return;
      }

      snapshot.value = nextSnapshot;
      syncSelectedModel(nextSnapshot);
      if (!activeStates.has(nextSnapshot.state) || nextSnapshot.run_id === optimistic?.runId) {
        optimisticRun.value = null;
      }
    } catch (error) {
      statusLoadError.value = error instanceof Error ? error.message : 'Benchmark 状态读取失败';
      if (snapshot.value) {
        const fallbackSelectedModel = selectedModel.value || snapshot.value.selected_model;
        if (fallbackSelectedModel) {
          selectedModel.value = fallbackSelectedModel;
        }
      }
    } finally {
      loadingStatus.value = false;
      hasHydratedOnce.value = true;
    }
  })().finally(() => {
    refreshStatusPromise = null;
  });

  return refreshStatusPromise;
}

async function handleStart() {
  if (!controlState.value.start || actionPending.value) {
    return;
  }

  actionPending.value = 'start';
  statusLoadError.value = '';

  try {
    const response = await benchmarkRun({ 
      selected_model: selectedModel.value,
      judge_model_type: selectedJudgeModelType.value
    });
    applyStartedRun(response);
    startStatusPolling();
    void refreshStatus();
  } catch (error) {
    statusLoadError.value = error instanceof Error ? error.message : 'Benchmark 启动失败';
  } finally {
    actionPending.value = null;
  }
}

async function handleStop() {
  if (!controlState.value.stop || actionPending.value) {
    return;
  }

  actionPending.value = 'stop';
  statusLoadError.value = '';

  try {
    await stopBenchmarkRun();
    optimisticRun.value = null;
    await refreshStatus();
  } catch (error) {
    statusLoadError.value = error instanceof Error ? error.message : 'Benchmark 停止失败';
  } finally {
    actionPending.value = null;
  }
}

async function handleReset() {
  if (!controlState.value.reset || actionPending.value) {
    return;
  }

  actionPending.value = 'reset';
  statusLoadError.value = '';

  try {
    await resetBenchmark();
    optimisticRun.value = null;
    await refreshStatus();
  } catch (error) {
    statusLoadError.value = error instanceof Error ? error.message : 'Benchmark 重置失败';
  } finally {
    actionPending.value = null;
  }
}

watch(recentResults, (rows) => {
  if (expandedResultKey.value && !rows.some((row) => row.key === expandedResultKey.value)) {
    expandedResultKey.value = null;
  }
});

watch(
  currentState,
  (nextState) => {
    if (nextState && activeStates.has(nextState)) {
      startStatusPolling();
      return;
    }

    stopStatusPolling();
  },
  { immediate: true }
);

onMounted(() => {
  void refreshStatus();
});

onBeforeUnmount(() => {
  stopStatusPolling();
});
</script>

<template>
  <div class="benchmark-page">
    <section class="benchmark-hero card">
      <div class="benchmark-hero__top">
        <div class="benchmark-hero__intro">
          <p class="benchmark-hero__eyebrow">Benchmark 评测</p>
          <div class="benchmark-hero__title-row">
            <h2>多模式问答评测</h2>
            <div
              class="status-chip"
              :class="stateMeta?.tone ?? 'error'"
              data-testid="benchmark-run-status"
            >
              <span class="status-dot"></span>
              {{ stateMeta?.label ?? '状态不可用' }}
            </div>
          </div>
          <p class="benchmark-hero__description">
            每题执行图谱、纯文本、图文混合三种检索模式，展示进度、判分结果与明细对比。
          </p>
        </div>

        <div class="benchmark-hero__controls">
          <div class="control-group">
            <label for="benchmark-model" class="control-label">验证模型</label>
            <select
              id="benchmark-model"
              v-model="selectedModel"
              class="field-input benchmark-model-select"
              :disabled="!hasAvailableModels || Boolean(optimisticRun) || !controlState.start"
            >
              <option v-if="!hasAvailableModels" value="" disabled>加载模型中…</option>
              <option v-for="opt in modelOptions" :key="opt.id" :value="opt.id">
                {{ opt.label || opt.id }}
              </option>
            </select>
          </div>
          <div class="control-group">
            <label for="benchmark-judge" class="control-label">评判模型</label>
            <select
              id="benchmark-judge"
              v-model="selectedJudgeModelType"
              class="field-input benchmark-model-select"
              :disabled="Boolean(optimisticRun) || !controlState.start"
            >
              <option value="cloud">云端</option>
              <option value="local">本地</option>
            </select>
          </div>
        </div>

      </div>

      <div class="benchmark-hero__bottom">
        <div class="benchmark-progress-panel">
          <div class="benchmark-progress-panel__header">
            <div>
              <div class="benchmark-progress-panel__label">当前进度</div>
              <div class="benchmark-progress-panel__value">{{ formatPrecisePercent(progressValue) }}</div>
            </div>
            <div class="benchmark-progress-panel__meta">{{ completedCountLabel }}</div>
          </div>

          <div class="benchmark-progress-track" aria-hidden="true">
            <div class="benchmark-progress-bar" :style="{ width: `${progressValue}%` }"></div>
          </div>

          <div class="benchmark-progress-panel__facts">
            <span>{{ completedCountLabel }} 已完成</span>
            <!-- <span>TTFT {{ summary ? formatDuration(averageResponseMs) : '—' }}</span> -->
            <span>平均召回 {{ summary ? formatPercent(summary.avg_recall_rate ?? Number.NaN) : '—' }}</span>
            <span>主判模式 {{ benchmarkModeLabel(primaryMode) }}</span>
          </div>
        </div>

        <div class="benchmark-sidepanel">
          <div class="control-actions benchmark-hero__actions">
            <button class="primary-btn" type="button" :disabled="!controlState.start" @click="handleStart">
              <Play :size="14" />
              <span>{{ actionPending === 'start' ? '启动中…' : '开始运行' }}</span>
            </button>
            <button class="secondary-btn" type="button" :disabled="!controlState.stop" @click="handleStop">
              <Square :size="14" />
              <span>{{ actionPending === 'stop' ? '停止中…' : '停止运行' }}</span>
            </button>
            <button class="ghost-btn" type="button" :disabled="!controlState.reset" @click="handleReset">
              <RotateCcw :size="14" />
              <span>{{ actionPending === 'reset' ? '重置中…' : '重置结果' }}</span>
            </button>
          </div>

          <div class="benchmark-kpis">
            <article class="benchmark-kpi benchmark-kpi--neutral">
              <div class="benchmark-kpi__label">运行状态</div>
              <div class="benchmark-kpi__value">{{ stateMeta?.title ?? '状态不可用' }}</div>
            </article>

            <article class="benchmark-kpi benchmark-kpi--success">
              <div class="benchmark-kpi__label">正确</div>
              <div class="benchmark-kpi__value">{{ summary ? summary.correct_count : '—' }}</div>
            </article>

            <article class="benchmark-kpi benchmark-kpi--warning">
              <div class="benchmark-kpi__label">回避或不足</div>
              <div class="benchmark-kpi__value">{{ summary ? summary.partial_count : '—' }}</div>
            </article>

            <article class="benchmark-kpi benchmark-kpi--error">
              <div class="benchmark-kpi__label">错误</div>
              <div class="benchmark-kpi__value">{{ summary ? summary.wrong_count : '—' }}</div>
            </article>
          </div>
        </div>
      </div>
    </section>

    <div class="benchmark-layout">
      <section class="benchmark-main">
        <section class="card benchmark-results">
          <header class="card-header benchmark-results__header">
            <div>
              <div class="card-title">运行结果</div>
              <div class="card-subtitle">{{ pageLead.description }}</div>
            </div>
            <div class="benchmark-results__state">{{ pageLead.title }}</div>
          </header>

          <div class="card-body benchmark-results__body">
            <div v-if="modeSummaryCards.length" class="results-inline-summary">
              <article
                v-for="card in modeSummaryCards"
                :key="card.key"
                class="summary-card mode-summary-card"
                :class="`summary-card--${card.tone}`"
              >
                <div class="summary-card__top">
                  <div class="summary-card__label">{{ card.label }}</div>
                  <div class="summary-card__value">{{ card.accuracy }}</div>
                </div>
                <div class="summary-card__facts">
                  <span>完成 {{ card.completed }}</span>
                  <!-- <span>TTFT {{ card.avgResponse }}</span> -->
                  <span>召回 {{ card.avgRecall }}</span>
                </div>
              </article>
            </div>

            <div v-if="inlineErrorMessage" class="inline-banner inline-banner--error">
              <AlertCircle :size="16" />
              <span>{{ inlineErrorMessage }}</span>
            </div>

            <div v-if="recentResults.length" class="result-list">
              <article
                v-for="row in recentResults"
                :key="row.key"
                class="result-row"
                :class="[`result-row--${row.tone}`, { expanded: expandedResultKey === row.key }]"
              >
                <button class="result-row__summary" type="button" @click="toggleResultRow(row.key)">
                  <div class="result-row__stream">
                    <div class="result-row__rail"></div>
                    <div class="result-row__content">
                      <div class="result-row__headline">
                        <span class="result-row__id">{{ row.questionId }}</span>
                        <span class="result-row__type">{{ row.questionTypeLabel }}</span>
                        <span class="status-pill" :class="row.tone">{{ row.statusLabel }}</span>
                        <span class="result-row__primary-mode">{{ row.primaryModeLabel }}</span>
                      </div>

                      <p class="result-row__question">{{ row.question }}</p>

                      <div class="result-row__meta">
                        <span><Clock3 :size="14" /> {{ row.responseTimeLabel }}</span>
                        <span><Target :size="14" /> 召回 {{ row.primaryRecallLabel }}</span>
                        <span><BarChart3 :size="14" /> {{ row.completedAtLabel }}</span>
                      </div>
                    </div>
                    <span class="result-row__toggle">
                      {{ expandedResultKey === row.key ? '收起详情' : '查看详情' }}
                      <ChevronUp v-if="expandedResultKey === row.key" :size="16" />
                      <ChevronDown v-else :size="16" />
                    </span>
                  </div>
                </button>

                <div v-if="expandedResultKey === row.key" class="result-row__detail">
                  <div class="result-answer-grid">
                    <section
                      v-for="modeResult in row.modeResults"
                      :key="modeResult.key"
                      class="answer-card"
                      :class="`answer-card--${modeResult.tone}`"
                    >
                      <div class="answer-card__head">
                        <div>
                          <h4>{{ modeResult.label }}</h4>
                          <div class="answer-card__meta">{{ modeResult.responseTimeLabel }} · 召回 {{ modeResult.recallLabel }}</div>
                        </div>
                        <span class="status-pill" :class="modeResult.tone">{{ modeResult.statusLabel }}</span>
                      </div>
                      <p>{{ modeResult.answer }}</p>
                    </section>
                    <section class="answer-card answer-card--reference">
                      <h4>参考答案</h4>
                      <p>{{ row.goldAnswer }}</p>
                    </section>
                  </div>

                  <div v-if="row.errorMessage" class="inline-banner inline-banner--error inline-banner--compact">
                    <AlertCircle :size="16" />
                    <span>{{ row.errorMessage }}</span>
                  </div>
                </div>
              </article>
            </div>

            <div v-else class="empty-state">
              <Target :size="24" />
              <h3>{{ emptyState.title }}</h3>
              <p>{{ emptyState.description }}</p>
            </div>
          </div>
        </section>
      </section>
    </div>
  </div>
</template>

<style scoped>
.benchmark-page {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 12px;
  min-height: 100%;
}

.benchmark-layout {
  min-height: 0;
}

.benchmark-main,
.benchmark-side {
  min-width: 0;
}

.benchmark-hero {
  padding: 16px 18px;
  border-color: rgba(191, 219, 254, 0.65);
  background:
    radial-gradient(circle at top right, rgba(96, 165, 250, 0.16), transparent 32%),
    radial-gradient(circle at bottom left, rgba(59, 130, 246, 0.08), transparent 28%),
    linear-gradient(145deg, rgba(255, 255, 255, 0.98) 0%, rgba(243, 248, 255, 0.96) 100%);
}

.benchmark-hero__top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}

.benchmark-hero__intro {
  min-width: 0;
  flex: 1 1 auto;
}

.benchmark-hero__controls {
  display: flex;
  gap: 16px;
  align-items: flex-end;
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.control-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.benchmark-model-select {
  width: 180px;
}

.benchmark-hero__title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.benchmark-progress-panel,
.benchmark-sidepanel,
.benchmark-kpi {
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.76);
  backdrop-filter: blur(12px);
}

.benchmark-hero__eyebrow {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent-primary);
}

.benchmark-hero h2 {
  margin: 0;
  font-size: 24px;
  line-height: 1.1;
  letter-spacing: -0.03em;
}

.benchmark-hero__description {
  margin: 8px 0 0;
  max-width: 640px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.45;
}

.benchmark-hero__bottom {
  margin-top: 14px;
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(320px, 1fr);
  gap: 12px;
}

.benchmark-sidepanel {
  padding: 12px;
  display: grid;
  gap: 10px;
}

.benchmark-hero__actions {
  justify-content: flex-start;
}

.control-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid transparent;
  white-space: nowrap;
}

.status-chip.info {
  background: rgba(59, 130, 246, 0.08);
  color: #2563eb;
  border-color: rgba(59, 130, 246, 0.18);
}

.status-chip.warning {
  background: rgba(245, 158, 11, 0.1);
  color: #b45309;
  border-color: rgba(245, 158, 11, 0.2);
}

.status-chip.success {
  background: rgba(16, 185, 129, 0.1);
  color: #047857;
  border-color: rgba(16, 185, 129, 0.18);
}

.status-chip.error {
  background: rgba(239, 68, 68, 0.1);
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.18);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: currentColor;
}

.primary-btn,
.secondary-btn,
.ghost-btn {
  min-height: 34px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid transparent;
  padding: 0 12px;
  white-space: nowrap;
  transition: transform 0.16s ease, box-shadow 0.2s ease, background 0.2s ease, border-color 0.2s ease;
}

.primary-btn {
  color: #fff;
  background: linear-gradient(135deg, #2563eb 0%, #60a5fa 100%);
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.2);
}

.secondary-btn {
  color: #92400e;
  background: rgba(255, 247, 237, 0.96);
  border-color: rgba(245, 158, 11, 0.2);
}

.ghost-btn {
  color: var(--text-secondary);
  background: rgba(248, 250, 252, 0.96);
  border-color: rgba(148, 163, 184, 0.18);
}

.primary-btn:hover:not(:disabled),
.secondary-btn:hover:not(:disabled),
.ghost-btn:hover:not(:disabled) {
  transform: translateY(-1px);
}

.primary-btn:disabled,
.secondary-btn:disabled,
.ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  box-shadow: none;
}

.benchmark-progress-panel {
  margin-top: 0;
  padding: 14px;
}

.benchmark-progress-panel__header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}

.benchmark-progress-panel__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}

.benchmark-progress-panel__value {
  margin-top: 4px;
  font-size: 22px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.04em;
}

.benchmark-progress-panel__meta {
  font-size: 13px;
  color: var(--text-muted);
}

.benchmark-progress-track {
  margin-top: 10px;
  width: 100%;
  height: 8px;
  border-radius: 999px;
  background: rgba(191, 219, 254, 0.5);
  overflow: hidden;
}

.benchmark-progress-bar {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2563eb 0%, #60a5fa 100%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.24);
  transition: width 0.25s ease;
}

.benchmark-progress-panel__facts {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.benchmark-progress-panel__facts span,
.summary-card__facts span {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(241, 245, 249, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.12);
  font-size: 12px;
  color: var(--text-secondary);
}

.benchmark-kpis {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.benchmark-kpi {
  padding: 12px 14px;
}

.benchmark-kpi__label {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-muted);
}

.benchmark-kpi__value {
  margin-top: 6px;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: -0.03em;
}

.benchmark-kpi--success {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.92) 0%, rgba(255, 255, 255, 0.96) 100%);
}

.benchmark-kpi--warning {
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.92) 0%, rgba(255, 255, 255, 0.96) 100%);
}

.benchmark-kpi--error {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.92) 0%, rgba(255, 255, 255, 0.96) 100%);
}

.benchmark-page :deep(.card-header) {
  padding: 16px 18px;
}

.benchmark-page :deep(.card-body) {
  padding: 18px;
}

.benchmark-page :deep(.card-title) {
  font-size: 14px;
}

.benchmark-page :deep(.card-subtitle) {
  font-size: 12px;
}

.benchmark-results__state {
  font-size: 12px;
  color: var(--text-secondary);
  text-align: right;
}

.inline-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.5;
}

.inline-banner--error {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.18);
  color: #b91c1c;
}

.inline-banner--compact {
  margin-top: 12px;
}

.result-list {
  display: grid;
  gap: 0;
}

.result-row {
  border-bottom: 1px solid rgba(226, 232, 240, 0.9);
  overflow: hidden;
  background: transparent;
}

.result-row:first-child {
  border-top: 1px solid rgba(226, 232, 240, 0.9);
}

.result-row--success {
  --result-tone: #10b981;
}

.result-row--warning {
  --result-tone: #f59e0b;
}

.result-row--error {
  --result-tone: #ef4444;
}

.result-row {
  --result-tone: #94a3b8;
}

.result-row__summary {
  width: 100%;
  padding: 14px 4px;
  text-align: left;
  background: transparent;
  border: none;
}

.result-row__stream {
  display: grid;
  grid-template-columns: 12px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
}

.result-row__rail {
  width: 2px;
  min-height: 56px;
  margin: 2px auto 0;
  border-radius: 999px;
  background: linear-gradient(180deg, var(--result-tone) 0%, rgba(148, 163, 184, 0.15) 100%);
}

.result-row__content {
  min-width: 0;
  display: grid;
  gap: 8px;
}

.result-row__headline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.result-row__id {
  font-size: 12px;
  font-weight: 700;
  color: var(--accent-primary-hover);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.result-row__type {
  font-size: 12px;
  color: var(--text-secondary);
  background: rgba(241, 245, 249, 0.9);
  border-radius: 999px;
  padding: 4px 9px;
}

.result-row__primary-mode {
  font-size: 12px;
  color: #475569;
  background: rgba(219, 234, 254, 0.7);
  border-radius: 999px;
  padding: 4px 9px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}

.status-pill.info {
  background: rgba(59, 130, 246, 0.08);
  color: #2563eb;
}

.status-pill.warning {
  background: rgba(245, 158, 11, 0.1);
  color: #b45309;
}

.status-pill.success {
  background: rgba(16, 185, 129, 0.1);
  color: #047857;
}

.status-pill.error {
  background: rgba(239, 68, 68, 0.1);
  color: #b91c1c;
}

.result-row__question {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.45;
  color: var(--text-primary);
}

.result-row__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  color: var(--text-muted);
}

.result-row__meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.result-row__toggle {
  color: var(--accent-primary-hover);
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  padding-top: 2px;
}

.result-row__detail {
  margin-left: 24px;
  padding: 2px 0 16px 0;
  background: linear-gradient(180deg, rgba(248, 251, 255, 0.92) 0%, rgba(255, 255, 255, 0.92) 100%);
}

.result-answer-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.answer-card {
  padding: 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid rgba(226, 232, 240, 0.95);
}

.answer-card--success {
  border-color: rgba(16, 185, 129, 0.22);
}

.answer-card--warning {
  border-color: rgba(245, 158, 11, 0.22);
}

.answer-card--error {
  border-color: rgba(239, 68, 68, 0.22);
}

.answer-card--reference {
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.98) 0%, rgba(255, 255, 255, 0.96) 100%);
}

.answer-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.answer-card__meta {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-muted);
}

.answer-card h4,
.answer-card p {
  margin: 0;
}

.answer-card h4 {
  font-size: 13px;
  margin: 0;
}

.answer-card p {
  font-size: 13px;
  line-height: 1.55;
  color: var(--text-secondary);
  white-space: pre-wrap;
}

.empty-state {
  min-height: 240px;
  display: grid;
  place-items: center;
  text-align: center;
  gap: 8px;
  padding: 28px 18px;
  color: var(--text-muted);
}

.empty-state--side {
  min-height: 180px;
}

.empty-state h3,
.empty-state p {
  margin: 0;
}

.results-inline-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.mode-summary-grid {
  display: grid;
  gap: 12px;
}

.summary-card {
  padding: 14px;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(255, 255, 255, 0.96);
}

.summary-card__top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.summary-card__label {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-secondary);
}

.summary-card__value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.04em;
}

.summary-card__facts {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-card__hint {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-secondary);
}

.summary-card--success {
  border-color: rgba(16, 185, 129, 0.18);
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.92) 0%, rgba(255, 255, 255, 0.96) 100%);
}

.summary-card--warning {
  border-color: rgba(245, 158, 11, 0.18);
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.92) 0%, rgba(255, 255, 255, 0.96) 100%);
}

.summary-card--error {
  border-color: rgba(239, 68, 68, 0.18);
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.92) 0%, rgba(255, 255, 255, 0.96) 100%);
}

@media (max-width: 1180px) {
  .benchmark-hero__bottom {
    grid-template-columns: 1fr;
  }

  .result-answer-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 860px) {
  .benchmark-hero {
    padding: 16px;
  }

  .benchmark-hero__top {
    flex-direction: column;
  }

  .benchmark-kpis {
    grid-template-columns: 1fr;
  }

  .results-inline-summary,
  .result-answer-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .benchmark-page {
    gap: 12px;
  }

  .benchmark-hero__title-row,
  .benchmark-hero__top,
  .benchmark-progress-panel__header,
  .benchmark-results__header,
  .summary-card__top {
    flex-direction: column;
    align-items: flex-start;
  }

  .benchmark-hero {
    padding: 14px;
  }

  .benchmark-hero h2 {
    font-size: 22px;
  }

  .control-actions {
    width: 100%;
  }

  .primary-btn,
  .secondary-btn,
  .ghost-btn {
    flex: 1 1 100%;
  }

  .result-row__toggle {
    padding-top: 0;
  }

  .result-row__stream {
    grid-template-columns: 10px minmax(0, 1fr);
  }

  .result-row__toggle {
    grid-column: 2;
  }
}
</style>
