// Data hooks: one-shot loads and the per-run poller.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { ApiError, api, describeError } from './api';
import type { RunDetail, RunEvent } from './types';
import { isFinished } from './model/trace';

export type Loadable<T> =
  | { status: 'idle'; data?: undefined; error?: undefined }
  | { status: 'loading'; data?: T; error?: undefined }
  | { status: 'ready'; data: T; error?: undefined }
  | { status: 'error'; data?: T; error: string; code?: number };

/** Load once per `key` change (null key = idle). `reload` refetches. */
export function useLoad<T>(key: string | null, fn: () => Promise<T>): [Loadable<T>, () => void] {
  const [state, setState] = useState<Loadable<T>>({ status: 'idle' });
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (key === null) {
      setState({ status: 'idle' });
      return;
    }
    let live = true;
    setState((s) => ({ status: 'loading', data: s.data }) as Loadable<T>);
    fnRef.current().then(
      (data) => live && setState({ status: 'ready', data }),
      (e) => live && setState({ status: 'error', error: describeError(e), code: e instanceof ApiError ? e.status : undefined }),
    );
    return () => {
      live = false;
    };
  }, [key, nonce]);
  return [state, useCallback(() => setNonce((n) => n + 1), [])];
}

export interface RunData {
  run: RunDetail | null;
  events: RunEvent[];
  loading: boolean;
  error: string | null;
}

export const POLL_MS = 1000;
export const RETRY_MAX_MS = 30_000;

/** Backoff after `n` consecutive failures: 3 s, 6 s, 12 s, ... capped, with ±20% jitter. */
export function retryDelay(n: number, rand = Math.random): number {
  const base = Math.min(RETRY_MAX_MS, POLL_MS * 3 * 2 ** Math.max(0, n - 1));
  return Math.round(base * (0.8 + 0.4 * rand()));
}

/**
 * Fetch a run and its events. While queued/running, poll `GET /runs/{id}/events?after=` every
 * second (and the run itself, to pick up the final status and result).
 */
export function useRunData(runId: string | null, enabled: boolean, onFinished?: (run: RunDetail) => void): RunData & { reload: () => void } {
  const [data, setData] = useState<RunData>({ run: null, events: [], loading: false, error: null });
  const [nonce, setNonce] = useState(0);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  useEffect(() => {
    if (!runId || !enabled) {
      setData({ run: null, events: [], loading: false, error: null });
      return;
    }
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let after = 0;
    let events: RunEvent[] = [];
    let wasActive = false;
    let failures = 0;
    setData({ run: null, events: [], loading: true, error: null });

    const pullEvents = async () => {
      for (;;) {
        const page = await api.events(runId, after);
        if (!live) return;
        if (page.events.length) {
          const seen = new Set(events.map((e) => e.seq));
          events = [...events, ...page.events.filter((e) => !seen.has(e.seq))];
        }
        const next = Math.max(after, page.next_after ?? after);
        const stuck = next === after;
        after = next;
        // A full page that does not move the cursor would loop forever; wait for the next tick.
        if (page.events.length < 500 || stuck) return;
      }
    };

    const tick = async () => {
      try {
        const run = await api.run(runId);
        if (!live) return;
        await pullEvents();
        if (!live) return;
        const done = isFinished(run.status);
        failures = 0;
        setData({ run, events, loading: false, error: null });
        if (!done) {
          wasActive = true;
          timer = setTimeout(tick, POLL_MS);
        } else if (wasActive) {
          finishedRef.current?.(run);
        }
      } catch (e) {
        if (!live) return;
        const msg = describeError(e);
        setData((d) => ({ ...d, loading: false, error: msg }));
        // Retry transient errors with backoff (including a failed first load); stop on auth / not found.
        if (!(e instanceof ApiError && (e.status === 401 || e.status === 404))) timer = setTimeout(tick, retryDelay(++failures));
      }
    };
    tick();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [runId, enabled, nonce]);

  return { ...data, reload: useCallback(() => setNonce((n) => n + 1), []) };
}

/** Re-render every `ms` while `on`; returns Date.now(). */
export function useNow(on: boolean, ms = 200): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const iv = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(iv);
  }, [on, ms]);
  return now;
}

/** Below this width the sidebar becomes a drawer and multi-column layouts stack. */
export const NARROW_PX = 760;

/** True while `query` matches; follows resizes. Without matchMedia (e.g. jsdom) it is false. */
export function useMedia(query: string): boolean {
  const supported = typeof window !== 'undefined' && typeof window.matchMedia === 'function';
  return useSyncExternalStore(
    (notify) => {
      if (!supported) return () => {};
      const mq = window.matchMedia(query);
      mq.addEventListener('change', notify);
      return () => mq.removeEventListener('change', notify);
    },
    () => supported && window.matchMedia(query).matches,
    () => false,
  );
}

export const useNarrow = (): boolean => useMedia(`(max-width: ${NARROW_PX - 1}px)`);
