import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, TOKEN_KEY, api, describeError, getToken, onUnauthorized, setToken } from './api';

const respond = (status: number, body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));

afterEach(() => vi.unstubAllGlobals());

describe('api client', () => {
  it('sends the stored bearer token', async () => {
    setToken('secret-token-123456');
    const f = respond(200, { runs: [] });
    vi.stubGlobal('fetch', f);
    await api.runs(5);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/runs?limit=5');
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer secret-token-123456');
  });

  it('clears the token and notifies listeners on 401', async () => {
    setToken('stale');
    const seen = vi.fn();
    const off = onUnauthorized(seen);
    vi.stubGlobal('fetch', respond(401, { detail: 'invalid or missing bearer token' }));
    await expect(api.system()).rejects.toMatchObject({ status: 401 });
    expect(getToken()).toBeNull();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(seen).toHaveBeenCalledOnce();
    off();
  });

  it('posts runs with overrides and surfaces 503 / 429 details', async () => {
    const ok = respond(202, { run_id: 'run_1', status: 'queued', system_version: 'sv_x' });
    vi.stubGlobal('fetch', ok);
    await api.submit('eval_sme_impacts', { router_mode: 'active', backends: { planner: 'api' } });
    const [, init] = ok.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ scenario_id: 'eval_sme_impacts', overrides: { router_mode: 'active', backends: { planner: 'api' } } });

    vi.stubGlobal('fetch', respond(503, { detail: 'backend unavailable: RuntimeError: claude not logged in' }));
    const e503 = await api.submit('eval_sme_impacts').catch((e) => e);
    expect(e503).toBeInstanceOf(ApiError);
    expect(describeError(e503)).toBe('Backend unavailable: RuntimeError: claude not logged in');

    vi.stubGlobal('fetch', respond(429, { detail: '20 runs already queued or running' }));
    expect(describeError(await api.submit('x').catch((e) => e))).toBe('Run queue is full. Try again when a run finishes.');
  });

  it('reports network failures as status 0', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))));
    await expect(api.health()).rejects.toMatchObject({ status: 0 });
  });

  it('encodes path segments', async () => {
    const f = respond(200, { run_id: 'a/b', events: [], next_after: 3 });
    vi.stubGlobal('fetch', f);
    await api.events('a/b', 3);
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe('/runs/a%2Fb/events?after=3&limit=500');
  });
});
