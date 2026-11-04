// Overview pieces for the runs list: the filter bar and the first-run walkthrough.
import { CARD, FONT, LABEL } from '../design';
import { OUTLINE_BTN, Seg } from './ui';
import { STATUS_FILTERS, isFiltering, type RunFilter, type RunStatusFilter } from '../model/runFilter';

const SELECT = {
  height: 32,
  border: '1px solid var(--line)',
  borderRadius: 4,
  padding: '0 8px',
  font: `600 13px ${FONT}`,
  background: 'var(--card)',
  color: 'var(--ink)',
  maxWidth: 260,
} as const;

/** Status and scenario filters for the runs table, with a count of what is shown. */
export function RunFilterBar({
  filter,
  onChange,
  onClear,
  scenarios,
  shown,
  total,
  more,
}: {
  filter: RunFilter;
  onChange: (f: RunFilter) => void;
  onClear: () => void;
  scenarios: { id: string; name: string }[];
  shown: number;
  total: number;
  /** More runs exist beyond those loaded. */
  more: boolean;
}) {
  const filtering = isFiltering(filter);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: '10px 18px', borderBottom: '1px solid var(--line)' }}>
      <div role="group" aria-label="Filter runs by status">
        <Seg<RunStatusFilter>
          options={STATUS_FILTERS}
          value={filter.status}
          onChange={(status) => onChange({ ...filter, status })}
          pad="5px 10px"
          font={`700 13px ${FONT}`}
        />
      </div>
      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--n1)' }}>
        Scenario
        <select value={filter.scenario} onChange={(e) => onChange({ ...filter, scenario: e.target.value })} style={SELECT}>
          <option value="">All scenarios</option>
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      {filtering && (
        <button type="button" onClick={onClear} style={{ ...OUTLINE_BTN, padding: '5px 10px', font: `700 13px ${FONT}` }}>
          Clear filters
        </button>
      )}
      <span aria-live="polite" style={{ marginLeft: 'auto', font: `600 13px ${FONT}`, fontVariantNumeric: 'tabular-nums', color: 'var(--n1)' }}>
        {filtering ? `${shown} of ${total} loaded runs` : `${more ? 'Latest ' : ''}${total} run${total === 1 ? '' : 's'}`}
      </span>
    </div>
  );
}

const STEPS = [
  { title: 'Pick a scenario', body: 'A scenario is a regulation change to assess.' },
  { title: 'Watch the experts work', body: 'Legal, Fiscal and Stakeholder experts read the changed provisions in parallel and post findings to a shared board.' },
  { title: 'Read the findings', body: 'Every impact links to a provision, a quote and its source.' },
];

/** Three-step explainer for a console with no runs yet. */
export function Walkthrough() {
  return (
    <div style={{ ...CARD, padding: '18px 20px' }}>
      <div style={LABEL}>How it works</div>
      <ol style={{ listStyle: 'none', margin: '14px 0 0', padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,240px),1fr))', gap: 16 }}>
        {STEPS.map((s, i) => (
          <li key={s.title} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            <span
              aria-hidden
              style={{ flex: 'none', width: 28, height: 28, borderRadius: '50%', display: 'grid', placeItems: 'center', background: 'var(--accSoft)', color: 'var(--accInk)', font: `700 14px ${FONT}` }}
            >
              {i + 1}
            </span>
            <span>
              <span style={{ display: 'block', fontSize: 15, fontWeight: 700 }}>{s.title}</span>
              <span style={{ display: 'block', fontSize: 13, color: 'var(--n1)', lineHeight: 1.5, marginTop: 3 }}>{s.body}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
