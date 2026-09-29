// Typed client for the WOMM API. Same origin in production; proxied by Vite in development.
import type {
  AskAnswer,
  EventsPage,
  RunAccepted,
  RunDetail,
  RunOverrides,
  RunSummary,
  Scenario,
  ScenarioSources,
  SystemInfo,
} from './types';

export const TOKEN_KEY = 'womm.token';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();

/** Subscribe to 401 responses (the token has already been cleared when this fires). */
export function onUnauthorized(fn: Listener): () => void {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage unavailable: the token lives only for this request cycle */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x) => (x && typeof x === 'object' && 'msg' in x ? String(x.msg) : String(x))).join('; ');
  }
  return fallback;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch (e) {
    throw new ApiError(0, `Cannot reach the WOMM API (${e instanceof Error ? e.message : 'network error'})`);
  }
  if (res.status === 401) {
    clearToken();
    unauthorizedListeners.forEach((fn) => fn());
    throw new ApiError(401, 'Invalid or missing API token');
  }
  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) throw new ApiError(res.status, detailOf(body, `${res.status} ${res.statusText || 'error'}`));
  return body as T;
}

const enc = encodeURIComponent;

export const api = {
  health: () => request<{ status: string; system_version: string; backend_ready: boolean }>('/health'),
  system: () => request<SystemInfo>('/system'),
  scenarios: () => request<Scenario[]>('/scenarios'),
  sources: (scenarioId: string) => request<ScenarioSources>(`/scenarios/${enc(scenarioId)}/sources`),
  runs: (limit = 20) => request<{ runs: RunSummary[] }>(`/runs?limit=${limit}`),
  run: (runId: string) => request<RunDetail>(`/runs/${enc(runId)}`),
  events: (runId: string, after = 0, limit = 500) =>
    request<EventsPage>(`/runs/${enc(runId)}/events?after=${after}&limit=${limit}`),
  submit: (scenarioId: string, overrides?: RunOverrides) =>
    request<RunAccepted>('/runs', {
      method: 'POST',
      body: JSON.stringify(overrides ? { scenario_id: scenarioId, overrides } : { scenario_id: scenarioId }),
    }),
  ask: (runId: string, question: string) =>
    request<AskAnswer>(`/runs/${enc(runId)}/ask`, { method: 'POST', body: JSON.stringify({ question }) }),
};

/** Human message for a failed call, in the console's voice. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) return `Backend unavailable: ${e.message.replace(/^backend unavailable:\s*/i, '')}`;
    if (e.status === 429) return 'Run queue is full. Try again when a run finishes.';
    if (e.status === 404) return `Not found: ${e.message}`;
    if (e.status === 409) return e.message || 'This run has no dossier yet.';
    if (e.status === 422) return `Invalid request: ${e.message}`;
    // A 502/504 with an API error body is the API reporting an upstream (LLM) failure; without
    // one it comes from a proxy in front of an API that is down.
    if (e.status === 502 || e.status === 504) return e.message.startsWith(String(e.status)) ? `Cannot reach the WOMM API (${e.status}). Is it running?` : e.message;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
