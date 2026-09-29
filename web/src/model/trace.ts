// Turns the run's node events into a timeline the pipeline, topology and log read at any clock.
import type { CallUsage, EventPayload, RunDetail, RunEvent, RunStatus } from '../types';
import type { NodeStatus } from '../design';

export const FINISHED: RunStatus[] = ['succeeded', 'degraded', 'failed', 'no_changes'];
export const isFinished = (s: RunStatus | undefined | null): boolean => !!s && FINISHED.includes(s);

export interface NodeSpan {
  s?: number;
  e?: number;
  failed?: boolean;
  errorKind?: string;
  payload?: EventPayload;
}

export interface TimedEvent extends RunEvent {
  rel: number;
  id: string | null;
}

export interface Trace {
  t0: number | null;
  /** Seconds from t0 to the last known moment of the run. */
  end: number;
  experts: string[];
  spans: Record<string, NodeSpan>;
  events: TimedEvent[];
  finished: boolean;
}

/** API event node → console node id. */
export function nodeId(node: string): string | null {
  if (node === 'planner' || node === 'router' || node === 'synthesis') return node;
  if (node === 'validate') return 'citation';
  if (node === 'assemble') return 'dossier';
  if (node.startsWith('expert_')) return node.slice('expert_'.length);
  return null;
}

const ms = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
};

export interface TraceInput {
  events: RunEvent[];
  experts: string[];
  run?: Pick<RunDetail, 'status' | 'started_at' | 'created_at' | 'finished_at' | 'failures'> | null;
}

export function buildTrace({ events, experts, run }: TraceInput): Trace {
  const sorted = [...events].sort((a, b) => a.seq - b.seq);
  const firstAt = sorted.length ? ms(sorted[0].at) : null;
  const startedAt = ms(run?.started_at);
  const t0 = [firstAt, startedAt].filter((x): x is number => x !== null).reduce<number | null>((a, b) => (a === null ? b : Math.min(a, b)), null) ?? ms(run?.created_at);
  const rel = (iso: string) => {
    const t = ms(iso);
    return t === null || t0 === null ? 0 : Math.max(0, (t - t0) / 1000);
  };
  const spans: Record<string, NodeSpan> = {};
  const timed: TimedEvent[] = [];
  let dispatched: string[] | null = null;
  const failedKinds: Record<string, string> = {};

  for (const ev of sorted) {
    const id = nodeId(ev.node);
    const r = rel(ev.at);
    timed.push({ ...ev, payload: ev.payload ?? {}, rel: r, id });
    if (!id) continue;
    const sp = (spans[id] ??= {});
    if (ev.event === 'started') {
      if (sp.s === undefined) sp.s = r;
    } else if (ev.event === 'finished' || ev.event === 'failed') {
      if (sp.s === undefined) sp.s = r;
      sp.e = r;
      sp.payload = ev.payload ?? {};
      if (ev.event === 'failed') {
        sp.failed = true;
        sp.errorKind = 'error';
      }
      for (const [agent, kind] of Object.entries(ev.payload?.failures ?? {})) failedKinds[agent] = kind;
      if (id === 'router' && ev.payload?.dispatched) dispatched = ev.payload.dispatched;
    }
  }
  for (const f of run?.failures ?? []) failedKinds[f.agent] ??= f.error_kind;
  for (const [agent, kind] of Object.entries(failedKinds)) {
    const sp = spans[agent];
    if (sp && sp.e !== undefined) {
      sp.failed = true;
      sp.errorKind = kind;
    }
  }

  const ex = dispatched?.length ? dispatched : experts;
  // Regulatory diff is deterministic and done once the planner (or assembly) starts.
  const diffEnd = spans.planner?.s ?? spans.dossier?.s;
  spans.diff = { s: 0, e: diffEnd };
  // The board is complete when every dispatched expert has finished.
  const ends = ex.map((e) => spans[e]?.e);
  if (ex.length && ends.every((x) => x !== undefined)) {
    const last = Math.max(...(ends as number[]));
    spans.board = { s: last, e: last };
  } else {
    spans.board = {};
  }

  const finished = isFinished(run?.status);
  let end = timed.length ? Math.max(...timed.map((e) => e.rel)) : 0;
  const fin = ms(run?.finished_at);
  if (finished && fin !== null && t0 !== null) end = Math.max(end, (fin - t0) / 1000);
  return { t0, end, experts: ex.length ? ex : experts, spans, events: timed, finished };
}

/** Node status at `clock` seconds. Nodes never started in a finished run are skipped. */
export function statusAt(tr: Trace, id: string, clock: number): NodeStatus {
  const sp = tr.spans[id];
  const over = tr.finished && clock >= tr.end;
  if (!sp || sp.s === undefined || clock < sp.s) return over ? 'skipped' : 'queued';
  if (sp.e === undefined || clock < sp.e) return over ? 'failed' : 'running';
  return sp.failed ? 'failed' : 'done';
}

/** Seconds the node has been running at `clock` (its full latency once done). */
export function elapsedAt(tr: Trace, id: string, clock: number): number {
  const sp = tr.spans[id];
  if (!sp || sp.s === undefined || clock < sp.s) return 0;
  const stop = sp.e === undefined ? (tr.finished ? tr.end : clock) : Math.min(clock, sp.e);
  return Math.max(0, stop - sp.s);
}

export interface UsageTotals {
  tin: number;
  tout: number;
  cost: number;
  lat: number;
  model: string | null;
  backend: string | null;
}

/** Summed LLM usage for one console node (planner, synthesis, or an expert id). */
export function usageFor(usage: CallUsage[] | null | undefined, id: string): UsageTotals | null {
  const rows = (usage ?? []).filter((u) =>
    id === 'planner' || id === 'synthesis' ? u.role === id : u.role === 'expert' && u.agent === id,
  );
  if (!rows.length) return null;
  return rows.reduce<UsageTotals>(
    (a, u) => ({
      tin: a.tin + (u.input_tokens || 0),
      tout: a.tout + (u.output_tokens || 0),
      cost: a.cost + (u.cost_usd ?? 0),
      lat: a.lat + (u.latency_s || 0),
      model: a.model ?? u.model,
      backend: a.backend ?? u.backend,
    }),
    { tin: 0, tout: 0, cost: 0, lat: 0, model: null, backend: null },
  );
}

export const LLM_NODES = (experts: string[]) => ['planner', ...experts, 'synthesis'];

/** Tokens and cost of the LLM nodes that have finished by `clock`. */
export function totalsAt(tr: Trace, usage: CallUsage[] | null | undefined, clock: number): { tokens: number; cost: number } {
  let tokens = 0;
  let cost = 0;
  for (const id of LLM_NODES(tr.experts)) {
    const st = statusAt(tr, id, clock);
    if (st !== 'done' && st !== 'failed') continue;
    const u = usageFor(usage, id);
    if (u) {
      tokens += u.tin + u.tout;
      cost += u.cost;
    }
  }
  return { tokens, cost };
}

/** Clock for a live run: wall time since t0, never behind the last received event. */
export function liveClock(tr: Trace, now: number): number {
  if (tr.t0 === null) return tr.end;
  return Math.max(tr.end, (now - tr.t0) / 1000);
}
