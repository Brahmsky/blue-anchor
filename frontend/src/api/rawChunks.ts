import { apiRequest } from '@/api/http';
import type {
  RawChunkEditRequest,
  RawChunkEditResponse,
  RawChunkFileDetailParams,
  RawChunkFileResponse,
  RawChunkMergeRequest,
  RawChunkMergeResponse,
  RawChunkReprocessRequest,
  RawChunkReprocessResponse,
  RawChunkSplitRequest,
  RawChunkSplitResponse,
  RawChunkSummaryResponse
} from '@/types/api';

interface RawChunkDeleteRequest {
  relative_path: string;
  chunk_id: string;
}

interface RawChunkDeleteResponse {
  relative_path: string;
  deleted_chunk_id: string;
  next_chunk_id: string | null;
  remaining_chunk_count: number;
  success: boolean;
}

export function fetchRawChunkSummary() {
  return apiRequest<RawChunkSummaryResponse>('/pipeline/raw-chunks/summary');
}

export function fetchRawChunkFileDetail({ relative_path }: RawChunkFileDetailParams) {
  return apiRequest<RawChunkFileResponse>(
    `/pipeline/raw-chunks/file?relative_path=${encodeURIComponent(relative_path)}`
  );
}

export function editRawChunk(payload: RawChunkEditRequest) {
  return apiRequest<RawChunkEditResponse>('/pipeline/raw-chunks/chunks/edit', {
    method: 'PUT',
    body: JSON.stringify(payload)
  });
}

export function splitRawChunk(payload: RawChunkSplitRequest) {
  return apiRequest<RawChunkSplitResponse>('/pipeline/raw-chunks/chunks/split', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export function mergeRawChunks(payload: RawChunkMergeRequest) {
  return apiRequest<RawChunkMergeResponse>('/pipeline/raw-chunks/chunks/merge', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export function deleteRawChunk(payload: RawChunkDeleteRequest) {
  return apiRequest<RawChunkDeleteResponse>('/pipeline/raw-chunks/chunks/delete', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export function reprocessRawChunkDocument(payload: RawChunkReprocessRequest) {
  return apiRequest<RawChunkReprocessResponse>('/pipeline/raw-chunks/reprocess', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}
