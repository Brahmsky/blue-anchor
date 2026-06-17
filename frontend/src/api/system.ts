import { apiRequest } from '@/api/http';
import type { HealthResponse, SystemCapabilities, SystemConfigResponse } from '@/types/api';

export function fetchHealth() {
  return apiRequest<HealthResponse>('/health');
}

export function fetchSystemCapabilities() {
  return apiRequest<SystemCapabilities>('/system/capabilities');
}

export function fetchSystemConfig() {
  return apiRequest<SystemConfigResponse>('/system/config');
}
