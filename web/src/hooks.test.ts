import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';
import { RETRY_MAX_MS, VERSION_CHECK_MS, retryDelay, useMedia, useNewVersion, useRunData } from './hooks';
import type { EventsPage, RunDetail } from './types';

describe('useNewVersion', () => {
  const served = (src: string) => `<!doctype html><html><head><script type="module" crossorigin src="${src}"></script></head><body><div id="root"></div></body></html>`;
  let entry: HTMLScriptElement | null = null;
  const running = (src: string) => {
    entry = document.createElement('script');
    entry.type = 'module';
    entry.setAttribute('src', src);
    document.head.appendChild(entry);
  };

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    entry?.remove();
    entry = null;
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('says so once the server names a different build', async () => {
    running('/assets/index-OLD.js');
    const fetchIndex = vi.fn().mockResolvedValueOnce(new Response(served('/assets/index-OLD.js'))).mockResolvedValue(new Response(served('/assets/index-NEW.js')));
    vi.stubGlobal('fetch', fetchIndex);
    const { result } = renderHook(() => useNewVersion());
    await act(async () => { await vi.advanceTimersByTimeAsync(VERSION_CHECK_MS); });
    expect(result.current).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(VERSION_CHECK_MS); });
    expect(result.current).toBe(true);
    expect(fetchIndex).toHaveBeenCalledWith('/', { cache: 'no-store' });
    // Found once is enough: no more polling.
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * VERSION_CHECK_MS); });
    expect(fetchIndex).toHaveBeenCalledTimes(2);
  });

  it('checks again when the tab comes back into view', async () => {
    running('/assets/index-OLD.js');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(served('/assets/index-NEW.js'))));
    const { result } = renderHook(() => useNewVersion());
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(result.current).toBe(true);
  });

  it('ignores a failed or unreadable check', async () => {
    running('/assets/index-OLD.js');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValueOnce(new Response('down', { status: 503 })).mockResolvedValue(new Response('<html></html>')));
    const { result } = renderHook(() => useNewVersion());
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * VERSION_CHECK_MS); });
    expect(result.current).toBe(false);
  });

  it('does nothing in dev, where there is no built entry script', async () => {
    const fetchIndex = vi.fn();
    vi.stubGlobal('fetch', fetchIndex);
    const { result } = renderHook(() => useNewVersion());
    await act(async () => { await vi.advanceTimersByTimeAsync(2 * VERSION_CHECK_MS); });
    expect(result.current).toBe(false);
    expect(fetchIndex).not.toHaveBeenCalled();
  });
});

const run = (status: string) => ({ run_id: 'run_1', status }) as unknown as RunDetail;
const page = (seqs: number[], next_after: number | null): EventsPage =>
  ({ events: seqs.map((seq) => ({ seq, node: 'n', event: 'started' })), next_after }) as unknown as EventsPage;

describe('retryDelay', () => {
  it('doubles from 3 s and caps at 30 s', () => {
    const mid = () => 0.5;
    expect([1, 2, 3, 4, 5, 9].map((n) => retryDelay(n, mid))).toEqual([3000, 6000, 12000, 24000, RETRY_MAX_MS, RETRY_MAX_MS]);
  });

  it('jitters within ±20%', () => {
    expect(retryDelay(1, () => 0)).toBe(2400);
    expect(retryDelay(1, () => 1)).toBe(3600);
  });
});

describe('useRunData', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('retries when the very first load fails on a transient error', async () => {
    const getRun = vi.spyOn(api, 'run').mockRejectedValueOnce(new ApiError(0, 'Cannot reach the WOMM API')).mockResolvedValue(run('succeeded'));
    vi.spyOn(api, 'events').mockResolvedValue(page([], null));
    const { result } = renderHook(() => useRunData('run_1', true));
    await act(async () => {});
    expect(result.current.error).toMatch(/Cannot reach/);
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(getRun).toHaveBeenCalledTimes(2);
    expect(result.current.run?.status).toBe('succeeded');
    expect(result.current.error).toBeNull();
  });

  it('stops on 404', async () => {
    const getRun = vi.spyOn(api, 'run').mockRejectedValue(new ApiError(404, 'unknown run'));
    renderHook(() => useRunData('run_1', true));
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(getRun).toHaveBeenCalledTimes(1);
  });

  it('does not spin when a full events page does not advance the cursor', async () => {
    vi.spyOn(api, 'run').mockResolvedValue(run('succeeded'));
    const full = Array.from({ length: 500 }, (_, i) => i + 1);
    const events = vi.spyOn(api, 'events').mockResolvedValueOnce(page(full, 500)).mockResolvedValue(page(full, 500));
    const { result } = renderHook(() => useRunData('run_1', true));
    await act(async () => {});
    expect(events).toHaveBeenCalledTimes(2);
    expect(result.current.events).toHaveLength(500);
  });
});

describe('useMedia', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('is false where matchMedia does not exist', () => {
    const { result } = renderHook(() => useMedia('(max-width: 759px)'));
    expect(result.current).toBe(false);
  });

  it('follows matchMedia changes', () => {
    let matches = true;
    const listeners = new Set<() => void>();
    vi.stubGlobal('matchMedia', (q: string) => ({
      media: q,
      get matches() { return matches; },
      addEventListener: (_: string, fn: () => void) => listeners.add(fn),
      removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
    }));
    const { result } = renderHook(() => useMedia('(max-width: 759px)'));
    expect(result.current).toBe(true);
    act(() => {
      matches = false;
      listeners.forEach((fn) => fn());
    });
    expect(result.current).toBe(false);
  });
});
