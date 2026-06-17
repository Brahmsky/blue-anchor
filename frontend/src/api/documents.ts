import { apiRequest } from '@/api/http';
import type {
  DocumentDetailResponse,
  DocumentMutationResponse,
  DocumentSummaryResponse,
  ScanProgressResponse
} from '@/types/api';

export function fetchDocumentSummary(datasourceId: string) {
  const trimmedDatasourceId = datasourceId.trim();
  if (!trimmedDatasourceId) {
    throw new Error('datasource_id is required for document summary requests');
  }

  const query = `?datasource_id=${encodeURIComponent(trimmedDatasourceId)}`;
  return apiRequest<DocumentSummaryResponse>(`/documents/summary${query}`);
}

export function fetchDocumentDetail(datasourceId: string, relativePath: string) {
  return apiRequest<DocumentDetailResponse>(
    `/documents/file?datasource_id=${encodeURIComponent(datasourceId)}&relative_path=${encodeURIComponent(relativePath)}`
  );
}

export function reindexDocument(datasourceId: string, relativePath: string) {
  return apiRequest<DocumentMutationResponse>(
    `/documents/file/reindex?datasource_id=${encodeURIComponent(datasourceId)}&relative_path=${encodeURIComponent(relativePath)}`,
    {
      method: 'POST'
    }
  );
}

export function reprocessDocument(datasourceId: string, relativePath: string) {
  return apiRequest<DocumentMutationResponse>(
    `/documents/file/reprocess?datasource_id=${encodeURIComponent(datasourceId)}&relative_path=${encodeURIComponent(relativePath)}`,
    {
      method: 'POST'
    }
  );
}

export function makeDocumentReadyToQuery(datasourceId: string, relativePath: string) {
  return apiRequest<DocumentMutationResponse>(
    `/documents/file/ready-to-query?datasource_id=${encodeURIComponent(datasourceId)}&relative_path=${encodeURIComponent(relativePath)}`,
    {
      method: 'POST'
    }
  );
}

export function deleteDocument(datasourceId: string, relativePath: string) {
  return apiRequest<DocumentMutationResponse>(
    `/documents/file?datasource_id=${encodeURIComponent(datasourceId)}&relative_path=${encodeURIComponent(relativePath)}`,
    {
      method: 'DELETE'
    }
  );
}

export function triggerDocumentScan() {
  return apiRequest<{ status: string; indexed_count?: number; total_documents?: number }>(
    '/documents/scan',
    {
      method: 'POST'
    }
  );
}

export function fetchScanProgress() {
  return apiRequest<ScanProgressResponse>('/documents/scan-progress');
}

export function uploadDocument(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return apiRequest<{ status: string; message: string }>('/documents/upload', {
    method: 'POST',
    body: formData
  });
}
