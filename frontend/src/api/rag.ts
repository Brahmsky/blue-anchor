import { apiRequestText, streamText } from '@/api/http';

export interface QueryPayload {
  query: string;
  mode: string;
  stream: boolean;
  only_need_context: boolean;
  text_only_retrieval?: boolean;
  query_semantics?: 'natural_language' | 'keyword_search';
  keywords?: string[];
  conversation_history?: Array<{
    role: 'user' | 'assistant';
    content: string;
  }>;
}

export function queryRag(payload: QueryPayload) {
  return apiRequestText('/query/plain', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export async function queryRagContext(payload: QueryPayload) {
  let response = '';

  await streamText(
    '/query/stream/plain',
    {
      ...payload,
      stream: true
    },
    (chunk) => {
      response += chunk;
    }
  );

  return response;
}

export function streamRagQuery(
  payload: QueryPayload,
  onChunk: (chunk: string) => void,
  signal?: AbortSignal
) {
  return streamText('/query/stream/plain', payload, onChunk, signal);
}
