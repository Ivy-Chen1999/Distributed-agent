// Filtering and paging for the Overview "Recent runs" table. Pure and self-contained (types only).
import type { RunStatus, RunSummary } from '../types';

export type RunStatusFilter = 'all' | 'succeeded' | 'degraded' | 'failed' | 'active';

export const STATUS_FILTERS: { v: RunStatusFilter; label: string }[] = [
  { v: 'all', label: 'All' },
  { v: 'succeeded', label: 'Succeeded' },
  { v: 'degraded', label: 'Degraded' },
  { v: 'failed', label: 'Failed' },
  { v: 'active', label: 'Active' },
];

export interface RunFilter {
  status: RunStatusFilter;
  /** A scenario id, or '' for every scenario. */
  scenario: string;
}

export const NO_RUN_FILTER: RunFilter = { status: 'all', scenario: '' };

export const isFiltering = (f: RunFilter) => f.status !== 'all' || f.scenario !== '';

/** Whether a run's status belongs to a status filter. `active` covers queued and running runs. */
export function matchesStatus(status: RunStatus, filter: RunStatusFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'active') return status === 'queued' || status === 'running';
  return status === filter;
}

export function filterRuns(runs: RunSummary[], f: RunFilter): RunSummary[] {
  return runs.filter((r) => matchesStatus(r.status, f.status) && (!f.scenario || r.scenario_id === f.scenario));
}

/** The scenarios present in the loaded runs, for the scenario menu, sorted by display name. */
export function scenarioChoices(runs: RunSummary[], nameOf: (scenarioId: string) => string): { id: string; name: string }[] {
  const ids = [...new Set(runs.map((r) => r.scenario_id))];
  return ids.map((id) => ({ id, name: nameOf(id) })).sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

/** Runs are loaded newest first, a page at a time: 20 to start, 20 more per click, up to the API's cap. */
export const RUN_PAGE = 20;
export const RUN_CAP = 200;

/** Whether "Show more runs" should be offered: the last page came back full and the cap is not reached. */
export const canShowMore = (loaded: number, limit: number) => loaded >= limit && limit < RUN_CAP;

export const nextRunLimit = (limit: number) => Math.min(RUN_CAP, limit + RUN_PAGE);
