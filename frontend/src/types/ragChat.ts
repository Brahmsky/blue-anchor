import type { ChatMessage } from '@/types/api';

export type EvidenceStatus = 'idle' | 'loading' | 'ready' | 'partial' | 'unavailable' | 'error';

export interface EvidenceItem {
  id: string;
  title: string;
  snippet: string;
  raw: string;
  page?: string;
  score?: string;
  sourceId?: string;
}

export interface ContextEntityItem {
  id: string;
  name: string;
  entityType?: string;
  score?: string;
  description: string;
}

export interface EvidencePayload {
  evidenceStatus: EvidenceStatus;
  evidenceItems: EvidenceItem[];
  contextSources: string[];
  contextEntities: ContextEntityItem[];
  contextRaw: string;
  evidenceNote: string;
}

export interface ConversationMessage extends ChatMessage {
  timestamp: string;
  query?: string;
  mode?: string;
  text_only_retrieval?: boolean;
  endpoint?: string;
  latencyMs?: number;
  datasourceId?: string;
  evidenceStatus?: EvidenceStatus;
  evidenceItems?: EvidenceItem[];
  contextSources?: string[];
  contextEntities?: ContextEntityItem[];
  contextRaw?: string;
  evidenceNote?: string;
}

export interface SessionItem {
  id: string;
  title: string;
  updatedAt: number;
  updatedLabel: string;
  messages: ConversationMessage[];
}

export interface SessionListItem extends SessionItem {
  count: number;
}

export interface EvidenceSelection {
  messageId: string;
  evidenceId: string;
}

export interface ReferencedSourceItem {
  id: string;
  name: string;
  fileType?: string;
  count: number;
}

export interface FactItem {
  label: string;
  value: string;
}

export const QUICK_QUESTIONS = [
  '冷藏集装箱压缩机组不能起动时，通常应怎样排查？',
  '安休斯型自动操舵仪系统产生振荡时，随动操舵和自动操舵应分别调什么？',
  '扳动AEG型无级调速克令吊主令器手柄后只能有单方向动作，应重点检查哪些部件?',
  '电动锚机电动机发热严重，除电动机本身故障外，控制与工况方面常见诱因有哪些?'
] as const;
