const API_KEY_STORAGE_KEY = 'minirag-api-key';
const API_HEALTHCHECK_PATH = '/health';
const DEFAULT_DEV_API_BASE = 'http://127.0.0.1:9733';
const DEFAULT_DESKTOP_API_BASE = 'http://127.0.0.1:9733';

let resolvedDevApiBasePromise: Promise<string> | null = null;
let resolvedDesktopApiBasePromise: Promise<string> | null = null;

function buildDevApiBaseCandidates() {
  const configuredDevBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').trim();
  if (configuredDevBaseUrl) {
    return [configuredDevBaseUrl];
  }
  return [DEFAULT_DEV_API_BASE];
}

function buildDesktopApiBaseCandidates() {
  const configuredDesktopBaseUrl = (import.meta.env.VITE_DESKTOP_API_BASE_URL ?? '').trim();
  if (configuredDesktopBaseUrl) {
    return [configuredDesktopBaseUrl];
  }
  return [DEFAULT_DESKTOP_API_BASE];
}

function parseJsonResponse<T>(text: string, input: string): T {
  const normalized = text.trim();
  if (!normalized) {
    return undefined as T;
  }

  try {
    return JSON.parse(normalized) as T;
  } catch {
    const preview = normalized.slice(0, 120);
    throw new Error(`接口返回了非 JSON 响应: ${input} -> ${preview}`);
  }
}

export async function readErrorResponseMessage(response: Pick<Response, 'status' | 'text'>) {
  const defaultMessage = `请求失败 (${response.status})`;
  const raw = await response.text();
  const normalized = raw.trim();

  if (!normalized) {
    return defaultMessage;
  }

  try {
    const payload = JSON.parse(normalized) as { detail?: unknown };
    if (typeof payload?.detail === 'string' && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    return normalized;
  }

  return defaultMessage;
}

function joinApiUrl(baseUrl: string, input: string) {
  if (!baseUrl) {
    return input;
  }

  const normalizedBaseUrl = baseUrl.replace(/\/$/, '');
  const normalizedPath = input.startsWith('/') ? input : `/${input}`;
  return `${normalizedBaseUrl}${normalizedPath}`;
}

async function resolveDesktopApiBase() {
  if (!resolvedDesktopApiBasePromise) {
    resolvedDesktopApiBasePromise = (async () => {
      for (const candidate of buildDesktopApiBaseCandidates()) {
        try {
          const response = await fetch(joinApiUrl(candidate, API_HEALTHCHECK_PATH), {
            method: 'GET'
          });
          if (response.ok) {
            return candidate;
          }
        } catch {
          continue;
        }
      }

      return buildDesktopApiBaseCandidates()[0];
    })();
  }

  return resolvedDesktopApiBasePromise;
}

function shouldUseDesktopApiBase() {
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  const tauriWindow = window as Window & { __TAURI_INTERNALS__?: unknown };
  return (
    protocol !== 'http:'
    && protocol !== 'https:'
  ) || hostname === 'tauri.localhost' || Boolean(tauriWindow.__TAURI_INTERNALS__);
}

async function resolveApiUrl(input: string) {
  if (/^https?:\/\//.test(input)) {
    return input;
  }

  const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').trim();
  if (configuredBaseUrl) {
    return joinApiUrl(configuredBaseUrl, input);
  }

  if (shouldUseDesktopApiBase()) {
    return joinApiUrl(await resolveDesktopApiBase(), input);
  }

  if (window.location.port !== '5173') {
    return input;
  }

  const devApiBaseCandidates = buildDevApiBaseCandidates();

  if (!resolvedDevApiBasePromise) {
    resolvedDevApiBasePromise = (async () => {
      for (const candidate of devApiBaseCandidates) {
        try {
          const response = await fetch(joinApiUrl(candidate, API_HEALTHCHECK_PATH), {
            method: 'GET'
          });
          if (response.ok) {
            return candidate;
          }
        } catch {
          continue;
        }
      }

      return devApiBaseCandidates[0];
    })();
  }

  return joinApiUrl(await resolvedDevApiBasePromise, input);
}

export function getApiKey() {
  return window.localStorage.getItem(API_KEY_STORAGE_KEY) ?? '';
}

export function setApiKey(value: string) {
  if (!value) {
    window.localStorage.removeItem(API_KEY_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(API_KEY_STORAGE_KEY, value);
}

export async function apiRequest<T>(input: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers ?? {});
  const apiKey = getApiKey();

  if (apiKey) {
    headers.set('X-API-Key', apiKey);
  }

  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const url = await resolveApiUrl(input);
  console.info('[apiRequest]', input, '->', url);
  const response = await fetch(url, {
    ...init,
    headers
  });

  if (!response.ok) {
    throw new Error(await readErrorResponseMessage(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  return parseJsonResponse<T>(text, input);
}

export async function streamNdjson(
  input: string,
  body: unknown,
  onChunk: (payload: Record<string, string>) => void
) {
  const headers = new Headers({
    'Content-Type': 'application/json'
  });
  const apiKey = getApiKey();
  if (apiKey) {
    headers.set('X-API-Key', apiKey);
  }

  const response = await fetch(await resolveApiUrl(input), {
    method: 'POST',
    headers,
    body: JSON.stringify(body)
  });

  if (!response.ok || !response.body) {
    throw new Error(`流式请求失败 (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) {
        continue;
      }
      onChunk(JSON.parse(trimmed));
    }
  }

  if (buffer.trim()) {
    onChunk(JSON.parse(buffer.trim()));
  }
}

export async function streamText(
  input: string,
  body: unknown,
  onChunk: (chunk: string) => void,
  signal?: AbortSignal
) {
  const headers = new Headers({
    'Content-Type': 'application/json'
  });
  const apiKey = getApiKey();
  if (apiKey) {
    headers.set('X-API-Key', apiKey);
  }

  const response = await fetch(await resolveApiUrl(input), {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`流式请求失败 (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    const text = decoder.decode(value, { stream: true });
    if (text) {
      onChunk(text);
    }
  }
}

export async function apiRequestText(input: string, init: RequestInit = {}): Promise<string> {
  const headers = new Headers(init.headers ?? {});
  const apiKey = getApiKey();

  if (apiKey) {
    headers.set('X-API-Key', apiKey);
  }

  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(await resolveApiUrl(input), {
    ...init,
    headers
  });

  if (!response.ok) {
    throw new Error(await readErrorResponseMessage(response));
  }

  return response.text();
}

export async function apiRequestBlob(input: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers ?? {});
  const apiKey = getApiKey();

  if (apiKey) {
    headers.set('X-API-Key', apiKey);
  }

  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(await resolveApiUrl(input), {
    ...init,
    headers
  });

  if (!response.ok) {
    throw new Error(await readErrorResponseMessage(response));
  }

  return {
    blob: await response.blob(),
    headers: response.headers
  };
}
