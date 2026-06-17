import { apiRequest, apiRequestBlob } from '@/api/http';
import type { ChatExportResponse, ChatStatePayload, ChatStateResponse } from '@/types/api';

export function fetchChatState(datasourceId: string) {
  const trimmedDatasourceId = datasourceId.trim();
  if (!trimmedDatasourceId) {
    throw new Error('datasource_id is required for chat state requests');
  }

  return apiRequest<ChatStateResponse>(
    `/chat/state?datasource_id=${encodeURIComponent(trimmedDatasourceId)}`
  );
}

export function saveChatState(payload: ChatStatePayload) {
  return apiRequest<ChatStateResponse>('/chat/state', {
    method: 'PUT',
    body: JSON.stringify(payload)
  });
}

export function exportChatMarkdown(payload: {
  datasource_id?: string;
  session_id: string;
  message_id?: string;
}) {
  return apiRequest<ChatExportResponse>('/chat/export', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export function downloadChatMarkdown(payload: {
  datasource_id?: string;
  session_id: string;
  message_id?: string;
}) {
  return apiRequestBlob('/chat/export/download', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}
