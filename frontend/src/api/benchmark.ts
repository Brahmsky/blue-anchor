import { apiRequest } from '@/api/http';
import type { BenchmarkPageSnapshot, BenchmarkState } from '@/types/api';

/**
 * Fetch the current benchmark status snapshot.
 * Polled by the benchmark page to update UI state.
 */
export async function getBenchmarkStatus(): Promise<BenchmarkPageSnapshot> {
  return apiRequest<BenchmarkPageSnapshot>('/benchmark/status');
}

/**
 * Request payload for starting a benchmark run.
 */
export interface BenchmarkRunPayload {
  selected_model: string;
  judge_model_type?: 'cloud' | 'local';
}

/**
 * Response from /benchmark/run endpoint.
 */
export interface BenchmarkRunResponse {
  status: string;
  run_id: string;
  state: BenchmarkState;
  selected_model: string;
  message: string;
}

/**
 * Start a new benchmark run.
 * Triggers /benchmark/run with the selected model.
 */
export async function benchmarkRun(payload: BenchmarkRunPayload): Promise<BenchmarkRunResponse> {
  return apiRequest<BenchmarkRunResponse>('/benchmark/run', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

/**
 * Response from /benchmark/stop endpoint.
 */
export interface BenchmarkStopResponse {
  status: string;
  state: BenchmarkState;
  message: string;
}

/**
 * Stop the currently running benchmark.
 * Returns status/state/message from the stop.
 */
export async function stopBenchmarkRun(): Promise<BenchmarkStopResponse> {
  return apiRequest<BenchmarkStopResponse>('/benchmark/stop', {
    method: 'POST',
    body: JSON.stringify({})
  });
}

/**
 * Response from /benchmark/reset endpoint.
 */
export interface BenchmarkResetResponse {
  status: string;
  state: BenchmarkState;
  message: string;
}

/**
 * Reset benchmark state.
 * Clears results and resets to idle.
 */
export async function resetBenchmark(): Promise<BenchmarkResetResponse> {
  return apiRequest<BenchmarkResetResponse>('/benchmark/reset', {
    method: 'POST'
  });
}
