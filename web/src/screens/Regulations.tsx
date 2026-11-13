// Regulations: where a reader starts. Search by name or number, or paste a EUR-Lex link, then
// open the act (its version comparison on the Act screen). Data: GET /regulations, GET /runs.
// Example entries are mock data for acts WOMM has not imported; they are off by default and
// always labelled.
import { useMemo, useState, type CSSProperties } from 'react';
import { api } from '../api';
import { useConsole } from '../ctx';
import { CARD, FONT, LABEL, TABULAR } from '../design';
import { useLoad } from '../hooks';
import { EmptyCard, ErrorCard, LoadingCard, PRIMARY_BTN } from '../components/ui';
import { DEPTH_TEXT, EXAMPLE_REGULATIONS, analysesFor, fromApi, parseQuery, search, targetFor, type AnalysisRow, type CatalogueEntry, type Query } from '../model/regulations';

const INPUT: CSSProperties = { flex: 1, minWidth: 0, font: `500 14px ${FONT}`, padding: '10px 12px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--card)', color: 'var(--ink)' };
const CHIP: CSSProperties = { font: `700 11px ${FONT}`, borderRadius: 4, padding: '2px 7px', background: 'var(--soft)', color: 'var(--n1)', whiteSpace: 'nowrap', ...TABULAR };
const TRY = ['AI Act', 'COM(2021) 206', 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689#art_26', 'GDPR'];

export function Regulations() {
  const C = useConsole();
  const [regs, reload] = useLoad('regulations', () => api.regulations());
  const [text, setText] = useState('');
  const [examples, setExamples] = useState(false);

  const entries = useMemo(() => [...(regs.data ?? []).map(fromApi), ...(examples ? EXAMPLE_REGULATIONS : [])], [regs.data, examples]);
  const query = parseQuery(text);
  const hits = search(entries, query);

  if (regs.status === 'error') return <ErrorCard title="Cannot load the regulations" message={regs.error} onRetry={reload} />;
  if (!regs.data) return <LoadingCard title="Loading regulations" />;

  const open = (e: CatalogueEntry, q: Query) => {
    const t = targetFor(e, q);
    if (t) C.openAct(t);
  };

  return (
    <>
      <div role="region" aria-label="Regulation search" style={{ ...CARD, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>
        <label htmlFor="reg-search" style={{ font: `700 14px ${FONT}` }}>
          Find a regulation
        </label>
        <form
          role="search"
          onSubmit={(ev) => {
            ev.preventDefault();
            if (hits.length === 1) open(hits[0], query);
          }}
          style={{ display: 'flex', gap: 8 }}
        >
          <input id="reg-search" type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Name, number (2024/1689, COM(2021) 206) or a EUR-Lex link" autoComplete="off" spellCheck={false} style={INPUT} />
        </form>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--n1)' }}>
          <span>Try:</span>
          {TRY.map((t) => (
            <button key={t} type="button" onClick={() => setText(t)} style={{ ...CHIP, border: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {t}
            </button>
          ))}
          {/* Mock data for designing the list: development builds only, never for outside users. */}
          {import.meta.env.DEV && (
            <label style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={examples} onChange={(e) => setExamples(e.target.checked)} />
              Show example entries (development)
            </label>
          )}
        </div>
        <QueryNote query={query} hits={hits.length} />
      </div>

      <p data-testid="regs-count" style={{ margin: 0, fontSize: 13, color: 'var(--n1)', ...TABULAR }}>
        {query.kind === 'empty' ? `${entries.length} ${entries.length === 1 ? 'regulation' : 'regulations'}` : `${hits.length} of ${entries.length} match`}
        {examples ? ' · example entries are illustrative and cannot be opened' : ''}
      </p>

      {hits.length === 0 ? (
        <EmptyCard title="No regulation matches" sub="WOMM lists only acts it has analysed. Try a name, a CELEX or COM number, or a EUR-Lex link." />
      ) : (
        <ul aria-label="Regulations" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {hits.map((e) => (
            <RegulationCard
              key={e.regulation_id}
              e={e}
              analyses={analysesFor(e, C.runs.data?.runs)}
              onOpen={() => open(e, query)}
              onOpenAnalysis={(row) => {
                C.setActContext({ comparison: `Whole act · ${e.title}`, regulation: e.title, versions: row.what });
                C.selectRun(row.runId);
                C.go('detail');
              }}
              query={query}
            />
          ))}
        </ul>
      )}
    </>
  );
}

function QueryNote({ query, hits }: { query: Query; hits: number }) {
  if (query.kind !== 'id') return null;
  const art = query.article ? `, article ${query.article}` : '';
  const msg = hits ? `Recognised ${query.celex}${art}${query.consolidated ? ' (consolidated text)' : ''}.` : `Recognised ${query.celex}${art}, but WOMM has not analysed that act.`;
  return (
    <div data-testid="regs-query" style={{ fontSize: 12, color: 'var(--n1)' }}>
      {msg}
    </div>
  );
}

function RegulationCard({ e, analyses, onOpen, onOpenAnalysis, query }: { e: CatalogueEntry; analyses: AnalysisRow[]; onOpen: () => void; onOpenAnalysis: (row: AnalysisRow) => void; query: Query }) {
  const [showAnalyses, setShowAnalyses] = useState(false);
  const opens = e.depth === 'comparison' && !e.example;
  const n = analyses.length;
  const listId = `analyses-${e.regulation_id}`;
  const article = query.kind === 'id' && query.article ? ` at Article ${query.article}` : '';
  return (
    <li data-regulation={e.regulation_id} style={{ ...CARD, padding: '14px 18px', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px 16px', borderStyle: e.example ? 'dashed' : 'solid' }}>
      <div style={{ flex: '1 1 320px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '4px 10px' }}>
          <h3 style={{ margin: 0, font: `700 15px ${FONT}` }}>{e.title}</h3>
          {e.example && <span style={{ ...CHIP, border: '1px dashed var(--n2)', background: 'transparent' }}>Example entry</span>}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {e.versions.map((v) => (
            <span key={v.label} style={{ ...CHIP, whiteSpace: 'normal' }} title={v.date}>
              {v.status}: {v.label}
            </span>
          ))}
        </div>
        <div style={{ fontSize: 12, color: 'var(--n1)', ...TABULAR }}>
          <span style={{ ...LABEL, fontSize: 10, marginRight: 6 }}>{DEPTH_TEXT[e.depth]}</span>
          {e.comparisons.length > 0 && `${e.comparisons.length} ${e.comparisons.length === 1 ? 'comparison' : 'comparisons'} · `}
          {n === 0 ? (
            'not analysed yet'
          ) : (
            <button type="button" aria-expanded={showAnalyses} aria-controls={listId} onClick={() => setShowAnalyses((x) => !x)} style={{ border: 'none', background: 'none', padding: 0, font: 'inherit', color: 'var(--accInk)', textDecoration: 'underline', cursor: 'pointer' }}>
              {n} finished {n === 1 ? 'analysis' : 'analyses'}
            </button>
          )}
        </div>
      </div>
      {opens ? (
        <button type="button" onClick={onOpen} style={{ ...PRIMARY_BTN, padding: '8px 14px', font: `700 12px ${FONT}` }}>
          Open{article}
        </button>
      ) : (
        <span style={{ fontSize: 12, color: 'var(--n2)' }}>{e.example ? 'Illustrative only' : 'Nothing to open yet'}</span>
      )}
      {showAnalyses && n > 0 && <AnalysesList id={listId} rows={analyses} onOpen={onOpenAnalysis} />}
    </li>
  );
}

const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : 'date unknown');

/** Finished analyses of one act, newest first; each opens as a document (Run detail). */
function AnalysesList({ id, rows, onOpen }: { id: string; rows: AnalysisRow[]; onOpen: (row: AnalysisRow) => void }) {
  return (
    <ul id={id} aria-label="Analyses" style={{ listStyle: 'none', margin: 0, padding: 0, flexBasis: '100%', borderTop: '1px solid var(--line)' }}>
      {rows.map((r) => (
        <li key={r.runId} data-run={r.runId} style={{ borderBottom: '1px solid var(--line)' }}>
          <button
            type="button"
            onClick={() => onOpen(r)}
            title={r.description}
            style={{ width: '100%', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px 14px', padding: '9px 2px', border: 'none', background: 'transparent', color: 'var(--ink)', textAlign: 'left', cursor: 'pointer', font: `500 13px ${FONT}` }}
          >
            <span style={{ color: 'var(--n1)', ...TABULAR, minWidth: 92 }}>{fmtDate(r.date)}</span>
            <span style={{ flex: '1 1 240px', minWidth: 0, fontWeight: 600 }}>{r.what}</span>
            <span style={{ color: 'var(--n1)', ...TABULAR }}>{r.impacts ?? '—'} impacts</span>
            <span style={{ ...CHIP, ...(r.complete ? {} : { background: 'transparent', border: '1px dashed var(--n2)' }) }}>{r.complete ? 'Complete' : 'Incomplete'}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
