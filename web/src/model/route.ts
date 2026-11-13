// The console's state in the URL hash: `#/<screen>?run=<id>&status=<filter>&scenario=<id>`.
// Pure, so it can be tested without the UI.
import { SCREENS, type ScreenId } from '../design';
import { NO_RUN_FILTER, STATUS_FILTERS, type RunFilter, type RunStatusFilter } from './runFilter';

export interface Route {
  screen: ScreenId;
  /** The chosen run, or null for "the newest run". */
  run: string | null;
  /** The Overview runs-table filters. */
  filter: RunFilter;
}

const STATUSES = new Set<string>(STATUS_FILTERS.map((s) => s.v));

/** Read a hash (`window.location.hash`). An empty or unknown screen means Overview; an unknown status filter is ignored. */
export function parseRoute(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#\/?/, '').split('?');
  const q = new URLSearchParams(query);
  const status = q.get('status') ?? '';
  return {
    screen: (SCREENS as string[]).includes(path) ? (path as ScreenId) : 'overview',
    run: q.get('run') || null,
    filter: {
      status: STATUSES.has(status) ? (status as RunStatusFilter) : NO_RUN_FILTER.status,
      scenario: q.get('scenario') ?? NO_RUN_FILTER.scenario,
    },
  };
}

/** The hash (without `#`) for a route. Filters are written on Overview only, and only when set. */
export function routeHash(screen: ScreenId, run: string | null, filter: RunFilter): string {
  const q = new URLSearchParams();
  if (run) q.set('run', run);
  if (screen === 'overview') {
    if (filter.status !== NO_RUN_FILTER.status) q.set('status', filter.status);
    if (filter.scenario) q.set('scenario', filter.scenario);
  }
  const query = q.toString();
  return `/${screen}${query ? `?${query}` : ''}`;
}
