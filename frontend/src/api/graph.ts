import { apiRequest } from '@/api/http';
import type { GraphNodeDetail, GraphResponse, GraphSummaryResponse } from '@/types/api';

type GraphLabelEntry = {
  label: string;
  entity_type: string;
};

function requireGraphDatasourceId(datasourceId: string) {
  const trimmedDatasourceId = datasourceId.trim();
  if (!trimmedDatasourceId) {
    throw new Error('datasource_id is required for graph requests');
  }
  return trimmedDatasourceId;
}

export async function fetchGraphLabels(datasourceId: string) {
  const resolvedDatasourceId = requireGraphDatasourceId(datasourceId);
  try {
    const entries = await apiRequest<GraphLabelEntry[]>(
      `/graph/label/entries?datasource_id=${encodeURIComponent(resolvedDatasourceId)}&limit=0`
    );
    return entries
      .map((entry) => ({
        label: String(entry.label ?? '').trim(),
        entity_type: String(entry.entity_type ?? 'UNKNOWN').trim() || 'UNKNOWN'
      }))
      .filter((entry) => entry.label);
  } catch {
    const labels = await apiRequest<string[]>(
      `/graph/label/list?datasource_id=${encodeURIComponent(resolvedDatasourceId)}&limit=0`
    );
    return labels
      .map((label) => ({
        label: String(label ?? '').trim(),
        entity_type: 'UNKNOWN'
      }))
      .filter((entry) => entry.label);
  }
}

export async function fetchGraphSummary(datasourceId: string) {
  const resolvedDatasourceId = requireGraphDatasourceId(datasourceId);
  return apiRequest<GraphSummaryResponse>(
    `/graph/summary?datasource_id=${encodeURIComponent(resolvedDatasourceId)}`
  );
}

export async function fetchGraphByLabel(label: string, datasourceId: string) {
  const resolvedDatasourceId = requireGraphDatasourceId(datasourceId);
  return apiRequest<GraphResponse>(
    `/graphs?datasource_id=${encodeURIComponent(resolvedDatasourceId)}&mode=label&label=${encodeURIComponent(label)}`
  );
}

export async function fetchGraphFull(datasourceId: string) {
  const resolvedDatasourceId = requireGraphDatasourceId(datasourceId);
  return apiRequest<GraphResponse>(
    `/graphs?datasource_id=${encodeURIComponent(resolvedDatasourceId)}&mode=full`
  );
}

export async function fetchGraphNodeDetail(label: string, datasourceId: string) {
  const resolvedDatasourceId = requireGraphDatasourceId(datasourceId);
  return apiRequest<GraphNodeDetail>(
    `/graph/node-detail?datasource_id=${encodeURIComponent(resolvedDatasourceId)}&label=${encodeURIComponent(label)}&max_relationships=20`
  );
}
