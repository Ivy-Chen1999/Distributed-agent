// The console's state in the URL hash: `#/<screen>?run=<id>&status=<filter>&scenario=<id>&cmp=<a->b>`.
// Pure, so it can be tested without the UI.
import { SCREENS, type ScreenId } from '../design';
import { NO_RUN_FILTER, STATUS_FILTERS, type RunFilter, type RunStatusFilter } from './runFilter';

export interface Route {
  /** The screen named in the hash, or null for none or an unknown one (the interface's home). */
  screen: ScreenId | null;
  /** The chosen run, or null for "the newest run". */
  run: string | null;
  /** The Overview runs-table filters. */
  filter: RunFilter;
  /** The Act screen's comparison (`before->after` version ids), or null for its default. */
  cmp: string | null;
}

const STATUSES = new Set<string>(STATUS_FILTERS.map((s) => s.v));

/** Read a hash (`window.location.hash`). An empty or unknown screen is null; an unknown status filter is ignored. */
export function parseRoute(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#\/?/, '').split('?');
  const q = new URLSearchParams(query);
  const status = q.get('status') ?? '';
  return {
    screen: (SCREENS as string[]).includes(path) ? (path as ScreenId) : null,
    run: q.get('run') || null,
    filter: {
      status: STATUSES.has(status) ? (status as RunStatusFilter) : NO_RUN_FILTER.status,
      scenario: q.get('scenario') ?? NO_RUN_FILTER.scenario,
    },
    cmp: q.get('cmp') || null,
  };
}

/** The hash (without `#`) for a route. Filters are written on Overview only, and only when set;
 * the comparison on the Act screen only. */
export function routeHash(screen: ScreenId, run: string | null, filter: RunFilter, cmp: string | null = null): string {
  const q = new URLSearchParams();
  if (run) q.set('run', run);
  if (screen === 'overview') {
    if (filter.status !== NO_RUN_FILTER.status) q.set('status', filter.status);
    if (filter.scenario) q.set('scenario', filter.scenario);
  }
  if (screen === 'act' && cmp) q.set('cmp', cmp);
  const query = q.toString();
  return `/${screen}${query ? `?${query}` : ''}`;
}
