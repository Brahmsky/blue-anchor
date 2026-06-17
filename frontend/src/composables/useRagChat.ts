import { computed, onMounted, ref, watch } from 'vue';

import { fetchChatState, saveChatState } from '@/api/chat';
import { queryRag, queryRagContext, streamRagQuery } from '@/api/rag';
import { fetchSystemCapabilities } from '@/api/system';
import type { SystemCapabilities } from '@/types/api';
import type {
  ConversationMessage,
  ContextEntityItem,
  EvidenceItem,
  EvidencePayload,
  EvidenceSelection,
  EvidenceStatus,
  FactItem,
  ReferencedSourceItem,
  SessionItem
} from '@/types/ragChat';
import { QUICK_QUESTIONS } from '@/types/ragChat';
import { renderRichText } from '@/utils/richText';

const SESSION_SAVE_DEBOUNCE_MS = 400;
const CONVERSATION_MEMORY_TURNS = 3;

interface ConversationMemoryTurn {
  user: string;
  assistant: string;
}

function getTimestamp() {
  return new Date().toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  });
}

function formatUpdatedLabel(updatedAt: number) {
  const deltaMs = Date.now() - updatedAt;
  if (deltaMs < 60_000) {
    return '刚刚';
  }

  const date = new Date(updatedAt);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return date.toLocaleTimeString('zh-CN', sameDay ? { hour: '2-digit', minute: '2-digit' } : {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function createSessionId() {
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function createEmptySession(id = createSessionId()): SessionItem {
  const updatedAt = Date.now();
  return {
    id,
    title: '当前对话',
    updatedAt,
    updatedLabel: formatUpdatedLabel(updatedAt),
    messages: []
  };
}

function normalizeSession(session: Partial<SessionItem> & { id: string }): SessionItem {
  const updatedAt = typeof session.updatedAt === 'number' ? session.updatedAt : Date.now();
  const messages = Array.isArray(session.messages) ? session.messages : [];

  return {
    id: session.id,
    title: String(session.title || '').trim() || getSessionTitle(messages),
    updatedAt,
    updatedLabel: formatUpdatedLabel(updatedAt),
    messages
  };
}

function isSessionEmpty(session: SessionItem | null | undefined) {
  if (!session) {
    return true;
  }

  return !session.messages.some((message) => {
    if (message.role !== 'user' && message.role !== 'assistant') {
      return false;
    }

    return Boolean(message.content.trim());
  });
}

function getSessionTitle(items: ConversationMessage[]) {
  const firstUserMessage = items.find((message) => message.role === 'user')?.content.trim();
  return firstUserMessage ? firstUserMessage.slice(0, 16) : '当前对话';
}

function getFileExtension(name: string) {
  const match = name.match(/\.([a-z0-9]+)$/i);
  return match?.[1]?.toUpperCase() ?? 'TXT';
}

function getContextSourceBadge(name: string) {
  return name.includes('.') ? getFileExtension(name) : '';
}

function getModeLabel(value: string) {
  if (value === 'graph_text_hybrid') return '图谱+文本';
  if (value === 'keyword_search') return '关键词检索';
  if (value === 'text_only') return '旧版仅文本';
  return value;
}

function normalizeModeValue(value: string | null | undefined) {
  return String(value ?? '').trim().toLowerCase();
}

function isKeywordRetrievalMode(value: string | null | undefined) {
  const normalized = normalizeModeValue(value);
  return normalized === 'keyword' || normalized === 'keyword_search' || normalized === 'keywordsearch';
}

function resolveRetrievalSettingLabel(modeValue: string | null | undefined, textOnly: boolean) {
  if (isKeywordRetrievalMode(modeValue)) {
    return '关键词检索';
  }

  return textOnly ? '仅文本检索' : '图谱+文本';
}

function getEvidenceStatusSummary(message: ConversationMessage | null | undefined) {
  if (!message) {
    return '待生成';
  }

  if (message.evidenceItems?.length) {
    return `${message.evidenceItems.length} 条来源片段`;
  }

  if (message.evidenceStatus === 'loading') {
    return '整理中';
  }

  if (message.evidenceStatus === 'partial') {
    return '部分可用';
  }

  if (message.evidenceStatus === 'unavailable') {
    return '暂不可用';
  }

  if (message.evidenceStatus === 'error') {
    return '请求失败';
  }

  return '待生成';
}

function getEvidenceToggleLabel(message: ConversationMessage) {
  if (message.evidenceStatus === 'loading') {
    return '正在整理数据源上下文…';
  }

  if (message.evidenceItems?.length) {
    return `${message.evidenceItems.length} 条来源片段`;
  }

  if (message.evidenceStatus === 'partial') {
    return '仅返回了部分数据源上下文';
  }

  if (message.evidenceStatus === 'unavailable') {
    return '当前 datasource 暂无可展示证据';
  }

  if (message.evidenceStatus === 'error') {
    return '数据源上下文请求失败';
  }

  return '暂未返回来源片段';
}

function getEvidenceDetailEmptyCopy(message: ConversationMessage | null) {
  if (!message || message.role !== 'assistant') {
    return '点击回答中的来源片段查看详情';
  }

  if (message.evidenceStatus === 'loading') {
    return '正在整理数据源上下文…';
  }

  return message.evidenceNote || '当前回答还没有可展示的来源片段详情。';
}

function getDatasourceContextEmptyCopy(message: ConversationMessage | null) {
  if (!message || message.role !== 'assistant') {
    return '当前回答还没有关联到可展示的数据源上下文。';
  }

  if (message.evidenceStatus === 'loading') {
    return '正在整理数据源上下文…';
  }

  return message.evidenceNote || '当前回答还没有关联到可展示的数据源上下文。';
}

function shouldShowEvidenceToggle(message: ConversationMessage) {
  return message.role === 'assistant' && message.evidenceStatus !== 'idle';
}

function buildConversationMemoryTurns(messages: ConversationMessage[]) {
  const turns: ConversationMemoryTurn[] = [];

  for (let index = 0; index < messages.length - 1; index += 1) {
    const userMessage = messages[index];
    const assistantMessage = messages[index + 1];

    if (userMessage.role !== 'user' || assistantMessage.role !== 'assistant') {
      continue;
    }

    if (assistantMessage.streaming || assistantMessage.error) {
      continue;
    }

    const userContent = userMessage.content.trim();
    const assistantContent = assistantMessage.content.trim();
    if (!userContent || !assistantContent) {
      continue;
    }

    turns.push({
      user: userContent,
      assistant: assistantContent
    });
  }

  return turns.slice(-CONVERSATION_MEMORY_TURNS);
}

function formatExportDateTime() {
  const value = new Date();
  const pad = (input: number) => String(input).padStart(2, '0');
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`;
}

function sanitizeChatFileName(value: string) {
  return String(value || '')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '_')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80) || 'chat-session';
}

function truncateExportEvidenceText(value: string, limit = 500) {
  const normalized = String(value || '').replace(/\s+/g, ' ').trim();
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit).trimEnd()}...`;
}

function collectMessageSources(message: ConversationMessage) {
  if (Array.isArray(message.evidenceItems)) {
    const sources: string[] = [];

    for (const item of message.evidenceItems) {
      const raw = String(item.raw || '').trim();
      const snippet = String(item.snippet || '').trim();
      const title = String(item.title || '').trim();
      const page = String(item.page || '').trim();
      const body = truncateExportEvidenceText(raw || snippet);

      if (body) {
        const label = title ? (page ? `${title} p.${page}` : title) : '';
        sources.push(label ? `${label}: ${body}` : body);
        continue;
      }

      const fallback = String(item.sourceId || item.title || '').trim();
      if (fallback) {
        sources.push(fallback);
      }
    }

    return [...new Set(sources)];
  }

  if (Array.isArray(message.contextSources)) {
    return message.contextSources
      .map((item) => String(item || '').trim())
      .filter(Boolean);
  }

  return [];
}

function appendAssistantSections(lines: string[], message: ConversationMessage) {
  lines.push('### 助手', '', String(message.content || '').trim() || '（空）', '');

  const sources = collectMessageSources(message);
  if (sources.length) {
    lines.push('#### 参考来源', '', ...sources.map((source) => `- ${source}`), '');
  }
}

function buildSessionExportMarkdown(datasourceId: string, session: SessionItem) {
  const lines = [
    '# 历史对话导出',
    '',
    `- datasource_id: ${datasourceId}`,
    `- 会话标题: ${String(session.title || '当前对话')}`,
    `- 导出时间: ${formatExportDateTime()}`,
    ''
  ];

  let turnIndex = 0;
  for (const message of session.messages) {
    if (message.role === 'user') {
      turnIndex += 1;
      lines.push(`## 第${turnIndex}轮`, '', '### 用户', '', String(message.content || '').trim() || '（空）', '');
      continue;
    }

    if (message.role === 'assistant') {
      if (turnIndex === 0) {
        turnIndex = 1;
        lines.push(`## 第${turnIndex}轮`, '');
      }
      appendAssistantSections(lines, message);
    }
  }

  return `${lines.join('\n').trim()}\n`;
}

function buildSingleAnswerExportMarkdown(datasourceId: string, session: SessionItem, message: ConversationMessage) {
  const messageIndex = session.messages.findIndex((item) => item.id === message.id);
  const previousUser = messageIndex > 0
    ? [...session.messages.slice(0, messageIndex)].reverse().find((item) => item.role === 'user') ?? null
    : null;
  const lines = [
    '# 单条回答导出',
    '',
    `- datasource_id: ${datasourceId}`,
    `- 会话标题: ${String(session.title || '当前对话')}`,
    `- 导出时间: ${formatExportDateTime()}`,
    ''
  ];

  if (previousUser) {
    lines.push('## 问题', '', String(previousUser.content || '').trim() || '（空）', '');
  }

  lines.push('## 回答', '');
  appendAssistantSections(lines, message);
  return `${lines.join('\n').trim()}\n`;
}

function triggerChatMarkdownDownload(markdown: string, fileName: string) {
  const blob = new Blob([markdown], {
    type: 'text/markdown;charset=utf-8'
  });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = fileName || 'chat-export.md';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}
function extractStructuredSection(rawText: string, sectionTitle: string) {
  const sectionIndex = rawText.indexOf(sectionTitle);
  if (sectionIndex === -1) {
    return '';
  }

  const fenceStart = rawText.indexOf('```', sectionIndex);
  if (fenceStart === -1) {
    return '';
  }

  const contentStart = rawText.indexOf('\n', fenceStart);
  if (contentStart === -1) {
    return '';
  }

  const fenceEnd = rawText.indexOf('```', contentStart + 1);
  if (fenceEnd === -1) {
    return '';
  }

  return rawText.slice(contentStart + 1, fenceEnd).trim();
}

function parseCsvRows(csvText: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let inQuotes = false;

  for (let index = 0; index < csvText.length; index += 1) {
    const char = csvText[index];

    if (inQuotes) {
      if (char === '"') {
        if (csvText[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        cell += char;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
      continue;
    }

    if (char === ',') {
      row.push(cell);
      cell = '';
      continue;
    }

    if (char === '\r') {
      continue;
    }

    if (char === '\n') {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
      continue;
    }

    cell += char;
  }

  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }

  return rows.filter((entry) => entry.some((value) => value.trim()));
}

function parseCsvObjects(csvText: string) {
  if (!csvText.trim()) {
    return [] as Array<Record<string, string>>;
  }

  const rows = parseCsvRows(csvText);
  if (rows.length < 2) {
    return [] as Array<Record<string, string>>;
  }

  const [header, ...dataRows] = rows;

  return dataRows
    .map((row) => {
      const record: Record<string, string> = {};

      header.forEach((key, index) => {
        record[key.trim()] = row[index] ?? '';
      });

      return record;
    })
    .filter((row) => Object.values(row).some((value) => value.trim()));
}

function buildStructuredEvidencePayload(rawText: string): EvidencePayload | null {
  const normalized = rawText.replace(/\r/g, '').trim();
  if (!normalized.includes('-----Entities-----') && !normalized.includes('-----Sources-----')) {
    return null;
  }

  const entityRows = parseCsvObjects(extractStructuredSection(normalized, '-----Entities-----'));
  const sourceRows = parseCsvObjects(extractStructuredSection(normalized, '-----Sources-----'));

  const contextEntities: ContextEntityItem[] = entityRows.map((row, index) => ({
    id: `entity-${index + 1}`,
    name: row.entity?.trim() || `实体 ${index + 1}`,
    entityType: row.entity_type?.trim() || undefined,
    score: row.score?.trim() || undefined,
    description: row.description?.trim() || ''
  }));

  const contextSources = [...new Set(sourceRows.map((row) => row.id?.trim()).filter(Boolean))];
  const evidenceItems: EvidenceItem[] = sourceRows
    .map((row, index) => {
      const sourceId = row.id?.trim() || `source-${index + 1}`;
      const content = row.content?.trim() || '';
      const description = row.description?.trim() || '';
      const title = description || content.slice(0, 72) || sourceId;

      return {
        id: `source-${index + 1}`,
        title,
        sourceId,
        snippet: content.slice(0, 160),
        raw: content
      };
    })
    .filter((item) => item.raw.trim());

  let evidenceStatus: EvidenceStatus = 'unavailable';
  let evidenceNote = '当前 datasource 暂未返回可展示的数据源上下文。';

  if (evidenceItems.length) {
    evidenceStatus = 'ready';
    evidenceNote = `后端返回了 ${evidenceItems.length} 条来源片段。`;
  } else if (contextSources.length || contextEntities.length) {
    evidenceStatus = 'partial';
    evidenceNote = '后端返回了部分数据源上下文，但缺少可直接展示的来源片段。';
  }

  return {
    evidenceStatus,
    evidenceItems,
    contextSources,
    contextEntities,
    contextRaw: normalized,
    evidenceNote
  };
}

function extractFallbackDocuments(rawText: string) {
  const matches = rawText.match(/[\w./\-\u4e00-\u9fa5]+\.(?:pdf|docx|md|txt|pptx)/gi) ?? [];
  return [...new Set(matches.map((item) => item.trim()))].slice(0, 6);
}

function extractFallbackSourceLabel(block: string) {
  const labelMatch = block.match(
    /(?:source(?:[_\s-]?id)?|document|doc|file|chunk|citation|reference|来源|出处|文档|文件|片段)\s*[:：#-]?\s*([A-Za-z0-9][\w./\-]{2,})/i
  );

  return labelMatch?.[1]?.trim() ?? '';
}

function hasFallbackSourceSignals(block: string) {
  const normalized = block.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return false;
  }

  const documentMatch = normalized.match(/[\w./\-\u4e00-\u9fa5]+\.(?:pdf|docx|md|txt|pptx)/i);
  if (documentMatch) {
    return true;
  }

  const sourceLabel = extractFallbackSourceLabel(normalized);
  return Boolean(sourceLabel && /[._\-/\d]/.test(sourceLabel));
}

function extractFallbackEvidenceItems(rawText: string) {
  const normalized = rawText.replace(/\r/g, '').trim();
  if (!normalized) {
    return [];
  }

  const blocks = normalized
    .split(/\n\s*\n+/)
    .map((block) => block.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .filter((block) => hasFallbackSourceSignals(block))
    .slice(0, 5);

  return blocks
    .map((block, index) => {
      const titleMatch = block.match(/[\w./\-\u4e00-\u9fa5]+\.(?:pdf|docx|md|txt|pptx)/i);
      const pageMatch = block.match(/(?:page|页码|第)\s*[:：]?\s*(\d+)/i);
      const scoreMatch = block.match(/(?:score|relevance|similarity|相关度)\s*[:：]?\s*([0-9.]+%?)/i);
      const sourceLabel = extractFallbackSourceLabel(block);

      return {
        id: `evidence-${index + 1}`,
        title: titleMatch?.[0] ?? sourceLabel,
        snippet: block.slice(0, 160),
        raw: block,
        page: pageMatch?.[1],
        score: scoreMatch?.[1]
      };
    })
    .filter((item) => item.title.trim());
}

function buildFallbackEvidencePayload(rawText: string): EvidencePayload {
  const normalized = rawText.replace(/\r/g, '').trim();

  if (!normalized) {
    return {
      evidenceStatus: 'idle',
      evidenceItems: [],
      contextSources: [],
      contextEntities: [],
      contextRaw: '',
      evidenceNote: ''
    };
  }

  const evidenceItems = extractFallbackEvidenceItems(normalized);
  const contextSources = extractFallbackDocuments(normalized);

  let evidenceStatus: EvidenceStatus = 'unavailable';
  let evidenceNote = '当前 datasource 暂未返回结构化来源片段。';

  if (evidenceItems.length) {
    evidenceStatus = 'partial';
    evidenceNote = '当前后端未返回结构化来源片段，已按兼容模式展示可识别的来源线索。';
  } else if (contextSources.length) {
    evidenceStatus = 'partial';
    evidenceNote = '当前后端未返回结构化来源片段，但识别到了可能的数据源上下文。';
  }

  return {
    evidenceStatus,
    evidenceItems,
    contextSources,
    contextEntities: [],
    contextRaw: normalized,
    evidenceNote
  };
}

export function useRagChat() {
  const capabilities = ref<SystemCapabilities | null>(null);
  const loadingCapabilities = ref(true);
  const capabilityError = ref('');
  const mode = ref('graph_text_hybrid');
  const streamEnabled = ref(true);
  const textOnlyRetrieval = ref(false);
  const onlyNeedContext = ref(false);
  const draft = ref('');
  const sending = ref(false);
  const regeneratingMessageId = ref<string | null>(null);
  const sessions = ref<SessionItem[]>([createEmptySession()]);
  const activeSessionId = ref(sessions.value[0].id);
  const selectedMessageId = ref<string | null>(null);
  const expandedEvidenceIds = ref<string[]>([]);
  const selectedEvidence = ref<EvidenceSelection | null>(null);
  const loadingChatState = ref(false);
  const chatStateError = ref('');
  const exportingMessageId = ref<string | null>(null);
  const exportingSessionId = ref<string | null>(null);
  const hasHydratedChatState = ref(false);
  const conversationMemoryTurns = ref<ConversationMemoryTurn[]>([]);
  let persistTimer: number | null = null;
  let lastPersistSnapshot = '';

  const isKeywordMode = computed({
    get: () => mode.value === 'keyword_search',
    set: (value: boolean) => {
      mode.value = value ? 'keyword_search' : 'graph_text_hybrid';
    }
  });
  const isTextOnlyRetrieval = computed({
    get: () => textOnlyRetrieval.value,
    set: (value: boolean) => {
      textOnlyRetrieval.value = value;
    }
  });

  const activeSession = computed(
    () => sessions.value.find((session) => session.id === activeSessionId.value) ?? sessions.value[0] ?? null
  );
  const messages = computed({
    get: () => activeSession.value?.messages ?? [],
    set: (nextMessages: ConversationMessage[]) => {
      if (!activeSession.value) return;
      activeSession.value.messages = nextMessages;
      activeSession.value.updatedAt = Date.now();
      activeSession.value.updatedLabel = formatUpdatedLabel(activeSession.value.updatedAt);
      activeSession.value.title = getSessionTitle(nextMessages);
    }
  });
  const canSend = computed(() => Boolean(draft.value.trim()) && !sending.value);
  const selectedMessage = computed(() =>
    messages.value.find((message) => message.id === selectedMessageId.value) ??
    [...messages.value].reverse().find((message) => message.role === 'assistant') ??
    null
  );
  const selectedEvidenceItem = computed(() => {
    if (!selectedMessage.value?.evidenceItems?.length) {
      return null;
    }

    if (!selectedEvidence.value || selectedEvidence.value.messageId !== selectedMessage.value.id) {
      return selectedMessage.value.evidenceItems[0];
    }

    return (
      selectedMessage.value.evidenceItems.find((item) => item.id === selectedEvidence.value?.evidenceId) ??
      selectedMessage.value.evidenceItems[0]
    );
  });
  const sessionItems = computed(() =>
    [...sessions.value]
      .sort((left, right) => right.updatedAt - left.updatedAt)
      .map((session) => ({
        ...session,
        count: session.messages.length
      }))
  );
  const answerTags = computed(() => {
    if (selectedMessage.value?.role !== 'assistant') {
      return [];
    }

    const isStreamResponse = selectedMessage.value.endpoint
      ? selectedMessage.value.endpoint.includes('/stream')
      : streamEnabled.value;
    const tags = [getModeLabel(selectedMessage.value.mode ?? mode.value), isStreamResponse ? '流式响应' : '单次响应'];

    if (onlyNeedContext.value) {
      tags.push('仅上下文');
    }

    if (selectedMessage.value.evidenceItems?.length) {
      tags.unshift('含来源片段');
    } else if (selectedMessage.value.evidenceStatus === 'loading') {
      tags.unshift('整理数据源上下文');
    } else if (selectedMessage.value.evidenceStatus === 'partial') {
      tags.unshift('部分数据源上下文');
    } else if (selectedMessage.value.evidenceStatus === 'unavailable') {
      tags.unshift('证据不可用');
    } else if (selectedMessage.value.evidenceStatus === 'error') {
      tags.unshift('证据请求失败');
    }

    if (selectedMessage.value.error) {
      tags.unshift('请求失败');
    } else if (selectedMessage.value.streaming) {
      tags.unshift('生成中');
    } else if (selectedMessage.value.content) {
      tags.unshift('已生成');
    }

    return tags;
  });
  const retrievalSettingLabel = computed(() =>
    resolveRetrievalSettingLabel(mode.value, textOnlyRetrieval.value)
  );
  const transportFacts = computed<FactItem[]>(() => {
    return [
      {
        label: '检索模式',
        value: retrievalSettingLabel.value
      },
      // {
      //   label: '上下文记忆',
      //   value: `最近 ${CONVERSATION_MEMORY_TURNS} 轮问答（当前 ${conversationMemoryTurns.value.length} 轮）`
      // },
      {
        label: '响应方式',
        value: selectedMessage.value?.endpoint
          ? selectedMessage.value.endpoint.includes('/stream')
            ? '流式输出'
            : '一次返回'
          : streamEnabled.value
            ? '流式输出'
            : '一次返回'
      },
      {
        label: '数据源证据',
        value: getEvidenceStatusSummary(selectedMessage.value)
      }
    ];
  });
  const referencedSources = computed<ReferencedSourceItem[]>(() =>
    (selectedMessage.value?.contextSources ?? []).map((source, index) => ({
      id: `${source}-${index}`,
      name: source,
      fileType: getContextSourceBadge(source) || undefined,
      count:
        selectedMessage.value?.evidenceItems?.filter(
          (item) => (item.sourceId ?? item.title).trim() === source
        ).length || 1
    }))
  );
  const hasMessages = computed(() =>
    messages.value.some((message) => {
      if (message.role !== 'user' && message.role !== 'assistant') {
        return false;
      }

      return Boolean(message.content.trim());
    })
  );
  function renderMarkdown(content: string) {
    return renderRichText(content);
  }

  async function loadCapabilities() {
    loadingCapabilities.value = true;
    capabilityError.value = '';

    try {
      const data = await fetchSystemCapabilities();
      capabilities.value = data;
      mode.value =
        data.recommended_mode === 'text_only'
          ? 'graph_text_hybrid'
          : data.recommended_mode || data.default_mode || 'graph_text_hybrid';
      streamEnabled.value = data.supports_stream;
    } catch (error) {
      capabilityError.value = error instanceof Error ? error.message : '读取失败';
    } finally {
      loadingCapabilities.value = false;
    }
  }

  function serializeChatState() {
    return JSON.stringify({
      datasource_id: capabilities.value?.datasource_id ?? '',
      active_session_id: activeSessionId.value,
      sessions: sessions.value.map((session) => ({
        id: session.id,
        title: session.title,
        updatedAt: session.updatedAt,
        messages: session.messages
      }))
    });
  }

  async function persistChatState() {
    if (!hasHydratedChatState.value || !capabilities.value?.datasource_id) {
      return;
    }

    const snapshot = serializeChatState();
    if (snapshot === lastPersistSnapshot) {
      return;
    }

    await saveChatState({
      datasource_id: capabilities.value.datasource_id,
      active_session_id: activeSessionId.value,
      sessions: sessions.value.map((session) => ({
        id: session.id,
        title: session.title,
        updatedAt: session.updatedAt,
        messages: session.messages
      }))
    });

    lastPersistSnapshot = snapshot;
  }

  function schedulePersistChatState() {
    if (!hasHydratedChatState.value) {
      return;
    }

    if (persistTimer) {
      window.clearTimeout(persistTimer);
    }

    persistTimer = window.setTimeout(() => {
      persistTimer = null;
      void persistChatState().catch((error) => {
        chatStateError.value = error instanceof Error ? error.message : '会话保存失败';
      });
    }, SESSION_SAVE_DEBOUNCE_MS);
  }

  async function loadChatState() {
    if (!capabilities.value?.datasource_id) {
      hasHydratedChatState.value = true;
      return;
    }

    loadingChatState.value = true;
    chatStateError.value = '';

    try {
      const payload = await fetchChatState(capabilities.value.datasource_id);
      const nextSessions = payload.sessions.length
        ? payload.sessions.map((session) =>
            normalizeSession({
              id: session.id,
              title: session.title,
              updatedAt: session.updatedAt,
              messages: session.messages
            })
          )
        : [createEmptySession()];
      const validIds = new Set(nextSessions.map((session) => session.id));
      const nextActiveSessionId =
        payload.active_session_id && validIds.has(payload.active_session_id)
          ? payload.active_session_id
          : nextSessions[0].id;

      sessions.value = nextSessions;
      activeSessionId.value = nextActiveSessionId;
      lastPersistSnapshot = JSON.stringify({
        datasource_id: payload.datasource_id,
        active_session_id: nextActiveSessionId,
        sessions: nextSessions.map((session) => ({
          id: session.id,
          title: session.title,
          updatedAt: session.updatedAt,
          messages: session.messages
        }))
      });
    } catch (error) {
      chatStateError.value = error instanceof Error ? error.message : '会话恢复失败';
      sessions.value = [createEmptySession()];
      activeSessionId.value = sessions.value[0].id;
    } finally {
      hasHydratedChatState.value = true;
      loadingChatState.value = false;
    }
  }

  function resetConversation() {
    if (isSessionEmpty(activeSession.value)) {
      if (activeSession.value) {
        activeSession.value.updatedAt = Date.now();
        activeSession.value.updatedLabel = formatUpdatedLabel(activeSession.value.updatedAt);
        activeSession.value.title = '当前对话';
      }
      selectedMessageId.value = null;
      selectedEvidence.value = null;
      expandedEvidenceIds.value = [];
      draft.value = '';
      return;
    }

    const nextSession = createEmptySession();
    sessions.value.unshift(nextSession);
    activeSessionId.value = nextSession.id;
    messages.value = [];
    selectedMessageId.value = null;
    selectedEvidence.value = null;
    expandedEvidenceIds.value = [];
    draft.value = '';
  }

  function clearCurrentSession() {
    if (!activeSession.value) {
      return;
    }

    activeSession.value.messages = [];
    activeSession.value.title = '当前对话';
    activeSession.value.updatedAt = Date.now();
    activeSession.value.updatedLabel = formatUpdatedLabel(activeSession.value.updatedAt);
    selectedMessageId.value = null;
    selectedEvidence.value = null;
    expandedEvidenceIds.value = [];
    draft.value = '';
  }

  function deleteSession(sessionId: string) {
    const remainingSessions = sessions.value.filter((session) => session.id !== sessionId);
    const nextSessions = remainingSessions.length ? remainingSessions : [createEmptySession()];
    sessions.value = nextSessions;

    if (!nextSessions.some((session) => session.id === activeSessionId.value)) {
      activeSessionId.value = nextSessions[0].id;
    }
  }

  async function copyMessage(message: ConversationMessage | null) {
    if (!message) return;
    try {
      await navigator.clipboard.writeText(message.content);
    } catch {
      window.alert('复制失败');
    }
  }

  function buildConversationHistory(memoryTurns: ConversationMemoryTurn[]) {
    return memoryTurns.flatMap((turn) => [
      {
        role: 'user' as const,
        content: turn.user
      },
      {
        role: 'assistant' as const,
        content: turn.assistant
      }
    ]);
  }

  function buildQueryPayload(
    query: string,
    conversationHistory: Array<{ role: 'user' | 'assistant'; content: string }>,
    requestMode: string
  ) {
    const querySemantics: 'natural_language' | 'keyword_search' =
      requestMode === 'keyword_search' ? 'keyword_search' : 'natural_language';
    return {
      query,
      mode: requestMode,
      text_only_retrieval: textOnlyRetrieval.value,
      query_semantics: querySemantics,
      keywords: [],
      stream: streamEnabled.value,
      only_need_context: onlyNeedContext.value,
      conversation_history: requestMode === 'keyword_search' ? [] : conversationHistory
    };
  }

  function applyEvidence(message: ConversationMessage, rawText: string) {
    const payload = buildStructuredEvidencePayload(rawText) ?? buildFallbackEvidencePayload(rawText);

    message.contextRaw = payload.contextRaw;
    message.contextSources = payload.contextSources;
    message.contextEntities = payload.contextEntities;
    message.evidenceItems = payload.evidenceItems;
    message.evidenceStatus = payload.evidenceStatus;
    message.evidenceNote = payload.evidenceNote;

    if (payload.evidenceItems.length) {
      expandedEvidenceIds.value = [...new Set([...expandedEvidenceIds.value, message.id])];
      selectedEvidence.value = { messageId: message.id, evidenceId: payload.evidenceItems[0].id };
      return;
    }

    if (payload.evidenceStatus !== 'idle') {
      expandedEvidenceIds.value = [...new Set([...expandedEvidenceIds.value, message.id])];
    }

    if (selectedEvidence.value?.messageId === message.id) {
      selectedEvidence.value = null;
    }
  }

  async function enrichWithContext(
    query: string,
    message: ConversationMessage,
    conversationHistory: Array<{ role: 'user' | 'assistant'; content: string }>
  ) {
    if (!capabilities.value?.supports_only_need_context || onlyNeedContext.value) {
      return;
    }

    message.evidenceStatus = 'loading';

    try {
      const rawContext = await queryRagContext({
        query,
        mode: message.mode ?? mode.value,
        stream: true,
        only_need_context: true,
        conversation_history: conversationHistory
      });
      applyEvidence(message, rawContext);
    } catch {
      message.evidenceStatus = 'error';
      message.evidenceItems = [];
      message.contextSources = [];
      message.contextEntities = [];
      message.contextRaw = '';
      message.evidenceNote = '数据源上下文请求失败，请稍后重试。';
      expandedEvidenceIds.value = [...new Set([...expandedEvidenceIds.value, message.id])];
    }
  }

  function getRetryQuery(message: ConversationMessage) {
    if (message.query?.trim()) {
      return message.query.trim();
    }

    const messageIndex = messages.value.findIndex((item) => item.id === message.id);
    if (messageIndex <= 0) {
      return '';
    }

    const previousUserMessage = [...messages.value.slice(0, messageIndex)]
      .reverse()
      .find((item) => item.role === 'user');

    return previousUserMessage?.content.trim() ?? '';
  }

  async function submitQuery(query: string, options?: { clearDraft?: boolean; requestMode?: string }) {
    if (!query || sending.value) return null;
    const requestMode = options?.requestMode ?? mode.value;
    const endpoint = streamEnabled.value
      ? capabilities.value?.recommended_query_endpoint ?? '/query/stream/plain'
      : '/query/plain';

    const conversationHistory = buildConversationHistory(conversationMemoryTurns.value);

    const userMessage: ConversationMessage = {
      id: `${Date.now()}-user`,
      role: 'user',
      content: query,
      timestamp: getTimestamp()
    };

    const assistantMessage: ConversationMessage = {
      id: `${Date.now()}-assistant`,
      role: 'assistant',
      content: '',
      streaming: true,
      timestamp: getTimestamp(),
      query,
      mode: requestMode,
      text_only_retrieval: textOnlyRetrieval.value,
      endpoint,
      datasourceId: capabilities.value?.datasource_id,
      evidenceStatus: 'idle'
    };

    messages.value = [...messages.value, userMessage, assistantMessage];
    const liveAssistantMessage =
      messages.value.find((message) => message.id === assistantMessage.id) ?? assistantMessage;

    selectedMessageId.value = liveAssistantMessage.id;
    selectedEvidence.value = null;
    sending.value = true;
    if (options?.clearDraft) {
      draft.value = '';
    }
    const startedAt = performance.now();

    try {
      if (streamEnabled.value) {
        await streamRagQuery(buildQueryPayload(query, conversationHistory, requestMode), (chunk) => {
          liveAssistantMessage.content += chunk;
        });
      } else {
        liveAssistantMessage.content = await queryRag(
          buildQueryPayload(query, conversationHistory, requestMode)
        );
      }

      liveAssistantMessage.latencyMs = Math.round(performance.now() - startedAt);

      if (onlyNeedContext.value) {
        applyEvidence(liveAssistantMessage, liveAssistantMessage.content);
      } else {
        void enrichWithContext(query, liveAssistantMessage, conversationHistory);
      }
    } catch (error) {
      liveAssistantMessage.error = true;
      liveAssistantMessage.content = error instanceof Error ? error.message : '问答失败';
      liveAssistantMessage.evidenceStatus = 'error';
      liveAssistantMessage.evidenceNote = '问答失败，未能获取数据源上下文。';
      liveAssistantMessage.latencyMs = Math.round(performance.now() - startedAt);
    } finally {
      liveAssistantMessage.streaming = false;
      sending.value = false;
      conversationMemoryTurns.value = buildConversationMemoryTurns(messages.value);
      activeSession.value.updatedAt = Date.now();
      activeSession.value.updatedLabel = formatUpdatedLabel(activeSession.value.updatedAt);
      activeSession.value.title = getSessionTitle(messages.value);
    }

    return liveAssistantMessage;
  }

  async function sendMessage() {
    const query = draft.value.trim();
    if (!query || sending.value) return;

    await submitQuery(query, { clearDraft: true });
  }

  async function regenerateMessage(message: ConversationMessage) {
    const query = getRetryQuery(message);
    if (!query || sending.value || regeneratingMessageId.value) return;

    regeneratingMessageId.value = message.id;

    try {
      await submitQuery(query, { clearDraft: false, requestMode: message.mode ?? mode.value });
    } finally {
      regeneratingMessageId.value = null;
    }
  }

  function selectMessage(messageId: string) {
    selectedMessageId.value = messageId;
  }

  function toggleEvidence(messageId: string) {
    expandedEvidenceIds.value = expandedEvidenceIds.value.includes(messageId)
      ? expandedEvidenceIds.value.filter((id) => id !== messageId)
      : [...expandedEvidenceIds.value, messageId];
  }

  function selectEvidence(selection: EvidenceSelection) {
    selectedEvidence.value = selection;
  }

  async function exportSession(sessionId: string) {
    if (!capabilities.value?.datasource_id || exportingSessionId.value) {
      return;
    }

    exportingSessionId.value = sessionId;

    try {
      const session = sessions.value.find((item) => item.id === sessionId);
      if (!session) {
        throw new Error('会话不存在，无法导出');
      }
      const markdown = buildSessionExportMarkdown(capabilities.value.datasource_id, session);
      const fileName = `${sanitizeChatFileName(session.title)}__session_${sanitizeChatFileName(session.id)}.md`;
      triggerChatMarkdownDownload(markdown, fileName);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '会话导出失败');
    } finally {
      exportingSessionId.value = null;
    }
  }

  async function exportMessage(message: ConversationMessage) {
    if (
      message.role !== 'assistant' ||
      !capabilities.value?.datasource_id ||
      !activeSessionId.value ||
      exportingMessageId.value
    ) {
      return;
    }

    exportingMessageId.value = message.id;

    try {
      const session = sessions.value.find((item) => item.id === activeSessionId.value);
      if (!session) {
        throw new Error('当前会话不存在，无法导出');
      }
      const markdown = buildSingleAnswerExportMarkdown(
        capabilities.value.datasource_id,
        session,
        message
      );
      const fileName = `${sanitizeChatFileName(session.title)}__answer_${sanitizeChatFileName(message.id)}.md`;
      triggerChatMarkdownDownload(markdown, fileName);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : '回答导出失败');
    } finally {
      exportingMessageId.value = null;
    }
  }

  watch(activeSessionId, () => {
    conversationMemoryTurns.value = buildConversationMemoryTurns(messages.value);
    const nextAssistant =
      [...messages.value].reverse().find((message) => message.role === 'assistant') ?? null;
    selectedMessageId.value = nextAssistant?.id ?? null;
    expandedEvidenceIds.value =
      nextAssistant?.evidenceStatus && nextAssistant.evidenceStatus !== 'idle' ? [nextAssistant.id] : [];
    selectedEvidence.value = nextAssistant?.evidenceItems?.length
      ? { messageId: nextAssistant.id, evidenceId: nextAssistant.evidenceItems[0].id }
      : null;
  });

  watch(
    [sessions, activeSessionId],
    () => {
      conversationMemoryTurns.value = buildConversationMemoryTurns(messages.value);
      for (const session of sessions.value) {
        session.updatedLabel = formatUpdatedLabel(session.updatedAt);
      }
      schedulePersistChatState();
    },
    { deep: true }
  );

  onMounted(async () => {
    await loadCapabilities();
    await loadChatState();
  });

  return {
    answerTags,
    capabilities,
    capabilityError,
    canSend,
    chatStateError,
    clearCurrentSession,
    copyMessage,
    deleteSession,
    draft,
    expandedEvidenceIds,
    exportMessage,
    exportSession,
    exportingMessageId,
    exportingSessionId,
    getDatasourceContextEmptyCopy,
    getEvidenceDetailEmptyCopy,
    getEvidenceToggleLabel,
    hasMessages,
    isKeywordMode,
    isTextOnlyRetrieval,
    loadingChatState,
    loadingCapabilities,
    messages,
    mode,
    onlyNeedContext,
    referencedSources,
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
    streamEnabled,
    toggleEvidence,
    transportFacts,
    activeSessionId
  };
}
