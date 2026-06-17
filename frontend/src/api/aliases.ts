import { apiRequest } from '@/api/http';
import type { AliasListResponse, AliasRecord, AliasResolveResponse } from '@/types/api';

export interface AliasListParams {
  datasourceId?: string;
  entityType?: string;
  enabled?: boolean | null;
  reviewed?: boolean | null;
  query?: string;
}

export interface AliasPayload {
  datasource_id?: string;
  canonical_name: string;
  entity_type: 'EQUIPMENT' | 'FAULTCASE' | 'COMPONENT';
  alias: string;
  enabled: boolean;
  reviewed: boolean;
}

function buildAliasQuery(params: AliasListParams = {}) {
  const searchParams = new URLSearchParams();
  if (params.datasourceId) searchParams.set('datasource_id', params.datasourceId);
  if (params.entityType) searchParams.set('entity_type', params.entityType);
  if (params.enabled !== null && params.enabled !== undefined) {
    searchParams.set('enabled', String(params.enabled));
  }
  if (params.reviewed !== null && params.reviewed !== undefined) {
    searchParams.set('reviewed', String(params.reviewed));
  }
  if (params.query?.trim()) searchParams.set('q', params.query.trim());
  const query = searchParams.toString();
  return query ? `/aliases?${query}` : '/aliases';
}

export function fetchAliases(params: AliasListParams = {}) {
  return apiRequest<AliasListResponse>(buildAliasQuery(params));
}

export function createAlias(payload: AliasPayload) {
  return apiRequest<AliasRecord>('/aliases', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export function updateAlias(aliasId: string, payload: Partial<AliasPayload>) {
  return apiRequest<AliasRecord>(`/aliases/${aliasId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload)
  });
}

export function deleteAlias(aliasId: string, datasourceId: string) {
  const searchParams = new URLSearchParams({ datasource_id: datasourceId });
  return apiRequest<{ status: string }>(`/aliases/${aliasId}?${searchParams.toString()}`, {
    method: 'DELETE'
  });
}

export function resolveAliasQuery(query: string, datasourceId?: string) {
  const searchParams = new URLSearchParams({ query });
  if (datasourceId) {
    searchParams.set('datasource_id', datasourceId);
  }
  return apiRequest<AliasResolveResponse>(`/aliases/resolve?${searchParams.toString()}`);
}
