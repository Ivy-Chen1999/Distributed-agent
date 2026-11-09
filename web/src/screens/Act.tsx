// Act: the whole act as one comparison of two corpus versions (proposal -> adopted text, adopted
// text -> after the Digital Omnibus), read inline, side by side or as a map, with the findings of
// the latest run of that comparison laid over the articles they cite. Data:
// GET /corpus/{id}/comparisons, GET /corpus/{id}/compare/{before}/{after}, GET /runs/{id}.
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { api, describeError } from '../api';
import { useConsole } from '../ctx';
import { ag, CARD, FONT, LABEL, TABULAR } from '../design';
import { useLoad } from '../hooks';
import { Dot, EmptyCard, ErrorCard, LoadingCard, OUTLINE_BTN, PRIMARY_BTN, Seg } from '../components/ui';
import { SegmentText } from './Detail';
import {
  MEASURES,
  articleLabel,
  articleStats,
  comparisonKey,
  comparisonLabel,
  findingsByKey,
  heat,
  heatFill,
  heatInk,
  latestRunFor,
  measureOf,
  paragraphVM,
  reasonText,
  relationText,
  tileLabel,
  unplacedFindings,
  type ActView,
  type ArticleStats,
  type Measure,
} from '../model/act';
import type { ArticleComparison, ComparisonRef, ImpactFinding } from '../types';

const REGULATION = 'ai_act';
const SELECT: CSSProperties = { font: `600 12px ${FONT}`, border: '1px solid var(--line)', borderRadius: 6, padding: '6px 8px', background: 'var(--card)', color: 'var(--ink)', flex: '1 1 auto', minWidth: 0, width: '100%' };
/** A label + select pair that shrinks to the screen width (long comparison names). */
const FIELD: CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flex: '1 1 260px', maxWidth: 560 };
const CHIP: CSSProperties = { font: `700 11px ${FONT}`, borderRadius: 4, padding: '2px 7px', background: 'var(--soft)', color: 'var(--n1)', whiteSpace: 'nowrap', ...TABULAR };
const VIEWS: { v: ActView; label: string }[] = [
  { v: 'inline', label: 'Inline' },
  { v: 'sbs', label: 'Side by side' },
  { v: 'map', label: 'Map' },
];

/** The comparison opened first: the newest one (an amendment of the law in force, if any). */
const defaultComparison = (list: ComparisonRef[]): string | null => (list.length ? comparisonKey(list[list.length - 1]) : null);

export function Act() {
  const C = useConsole();
  const [cmpList, reloadList] = useLoad('comparisons', () => api.comparisons(REGULATION));
  const [picked, setPicked] = useState<string | null>(null);
  const [view, setView] = useState<ActView>('inline');
  const [lastDiff, setLastDiff] = useState<Exclude<ActView, 'map'>>('inline');
  const [measure, setMeasure] = useState<Measure>('changed');
  const [showUnchanged, setShowUnchanged] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const list = cmpList.data ?? [];
  const key = picked ?? defaultComparison(list);
  const cmp = list.find((c) => comparisonKey(c) === key) ?? null;
  const [data, reloadData] = useLoad(cmp ? key : null, () => api.comparison(REGULATION, cmp!.before.version_id, cmp!.after.version_id));

  const latest = latestRunFor(C.runs.data?.runs, cmp);
  const [runData] = useLoad(latest ? latest.run_id : null, () => api.run(latest!.run_id));
  const byKey = useMemo(() => findingsByKey(runData.data), [runData.data]);

  const articles = data.data?.articles ?? [];
  const stats = useMemo(() => new Map(articles.map((a) => [a.provision_key, articleStats(a, byKey.get(a.provision_key)?.length ?? 0)])), [articles, byKey]);
  const unplaced = useMemo(() => unplacedFindings(byKey, articles), [byKey, articles]);

  const chooseView = (v: ActView) => {
    setView(v);
    if (v !== 'map') setLastDiff(v);
  };
  const openArticle = (k: string) => {
    setFocus(k);
    setShowUnchanged((s) => s || stats.get(k)?.changedParagraphs === 0);
    chooseView(lastDiff);
  };
  useEffect(() => {
    if (!focus || view === 'map') return;
    const el = document.getElementById(articleId(focus));
    if (el) {
      el.scrollIntoView?.({ block: 'start' });
      el.focus({ preventScroll: true });
    }
  }, [focus, view, showUnchanged]);

  const runAssessment = async () => {
    const sid = cmp?.scenario_ids[0];
    if (!sid) return;
    setStarting(true);
    try {
      const r = await api.submit(sid);
      C.reloadRuns();
      C.selectRun(r.run_id);
      C.go('pipeline');
    } catch (e) {
      C.flash(describeError(e), 5000);
    } finally {
      setStarting(false);
    }
  };

  if (cmpList.status === 'error') return <ErrorCard title="Cannot load the comparisons" message={cmpList.error} onRetry={reloadList} />;
  if (!cmp) return cmpList.status === 'ready' ? <EmptyCard title="No comparisons" sub="The corpus declares no pair of versions to compare." /> : <LoadingCard title="Loading comparisons" />;

  const all = [...stats.values()];
  const changed = all.filter((s) => s.changedParagraphs > 0).length;

  return (
    <>
      <div role="region" aria-label="Comparison settings" style={{ ...CARD, padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px 18px' }}>
          <label style={FIELD}>
            <span style={LABEL}>Compare</span>
            <select
              aria-label="Comparison"
              value={key ?? ''}
              onChange={(e) => {
                setPicked(e.target.value);
                setFocus(null);
              }}
              style={SELECT}
            >
              {list.map((c) => (
                <option key={comparisonKey(c)} value={comparisonKey(c)}>
                  {comparisonLabel(c)}
                </option>
              ))}
            </select>
          </label>
          <div role="group" aria-label="View" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={LABEL}>View</span>
            <Seg options={VIEWS} value={view} onChange={chooseView} />
          </div>
          <label style={FIELD}>
            <span style={LABEL}>Measure</span>
            <select aria-label="Measure" value={measure} onChange={(e) => setMeasure(e.target.value as Measure)} style={SELECT}>
              {MEASURES.map((m) => (
                <option key={m.v} value={m.v}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <RunStrip cmp={cmp} runId={latest?.run_id ?? null} loading={runData.status === 'loading'} error={runData.status === 'error' ? runData.error : null} findings={[...byKey.values()].reduce((n, f) => n + f.length, 0)} unplaced={unplaced.length} starting={starting} onRun={runAssessment} onOpen={(id) => { C.selectRun(id); C.go('detail'); }} />
      </div>

      {data.status === 'error' && <ErrorCard title="Cannot load the comparison" message={data.error} onRetry={reloadData} />}
      {data.status !== 'error' && !data.data && <LoadingCard title="Comparing the two versions" sub={comparisonLabel(cmp)} />}
      {data.data && (
        <>
          <p data-testid="act-stat" style={{ margin: 0, fontSize: 13, color: 'var(--n1)', ...TABULAR }}>
            <b style={{ color: 'var(--ink)' }}>{changed}</b> of {articles.length} articles and annexes changed · paragraphs matched by their number, so a moved paragraph shows as removed and added · duties from the rule-based extraction
          </p>
          {view === 'map' ? (
            <MapView articles={articles} stats={stats} measure={measure} focus={focus} onOpen={openArticle} />
          ) : (
            <>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                <input type="checkbox" checked={showUnchanged} onChange={(e) => setShowUnchanged(e.target.checked)} />
                Show unchanged articles
              </label>
              <DiffList articles={articles} stats={stats} byKey={byKey} cmp={cmp} sbs={view === 'sbs'} showUnchanged={showUnchanged} />
            </>
          )}
        </>
      )}
    </>
  );
}

const articleId = (key: string) => `act-${key.replace(/[^\w-]/g, '_')}`;

function RunStrip(p: { cmp: ComparisonRef; runId: string | null; loading: boolean; error: string | null; findings: number; unplaced: number; starting: boolean; onRun: () => void; onOpen: (id: string) => void }) {
  const text = !p.runId
    ? 'No finished run of this comparison yet, so no findings are shown.'
    : p.error
      ? `Findings of ${p.runId} could not be loaded: ${p.error}`
      : p.loading
        ? `Loading the findings of ${p.runId}…`
        : `Findings of the latest run, ${p.runId}: ${p.findings}${p.unplaced ? `, of which ${p.unplaced} cite provisions outside this comparison` : ''}.`;
  return (
    <div data-testid="act-run" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 13, color: 'var(--n1)' }}>
      <span style={{ flex: 1, minWidth: 220 }}>{text}</span>
      {p.runId && (
        <button type="button" onClick={() => p.onOpen(p.runId!)} style={OUTLINE_BTN}>
          Open run
        </button>
      )}
      {p.cmp.scenario_ids.length > 0 && (
        <button type="button" onClick={p.onRun} disabled={p.starting} style={{ ...PRIMARY_BTN, padding: '8px 14px', font: `700 12px ${FONT}`, opacity: p.starting ? 0.6 : 1 }}>
          {p.starting ? 'Starting…' : 'Run assessment'}
        </button>
      )}
    </div>
  );
}

function DiffList({ articles, stats, byKey, cmp, sbs, showUnchanged }: { articles: ArticleComparison[]; stats: Map<string, ArticleStats>; byKey: Map<string, ImpactFinding[]>; cmp: ComparisonRef; sbs: boolean; showUnchanged: boolean }) {
  const shown = articles.filter((a) => showUnchanged || (stats.get(a.provision_key)?.changedParagraphs ?? 0) > 0 || byKey.has(a.provision_key));
  if (!shown.length) return <EmptyCard title="Nothing changed" sub="No article differs between the two versions." />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {shown.map((a) => (
        <ArticleCard key={a.provision_key} a={a} s={stats.get(a.provision_key)!} findings={byKey.get(a.provision_key) ?? []} cmp={cmp} sbs={sbs} />
      ))}
    </div>
  );
}

function ArticleCard({ a, s, findings, cmp, sbs }: { a: ArticleComparison; s: ArticleStats; findings: ImpactFinding[]; cmp: ComparisonRef; sbs: boolean }) {
  const title = `${articleLabel(a)}${a.heading ? ` · ${a.heading}` : ''}`;
  return (
    <article id={articleId(a.provision_key)} tabIndex={-1} aria-label={title} data-provision={a.provision_key} style={{ ...CARD, padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 10, scrollMarginTop: 90 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '4px 12px' }}>
        <h3 style={{ margin: 0, font: `700 15px ${FONT}` }}>{title}</h3>
        <span style={{ fontSize: 12, color: 'var(--n2)' }}>{relationText(a, cmp)}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {s.roleDuties > 0 && <span style={CHIP}>{s.roleDuties} deployer {s.roleDuties === 1 ? 'duty' : 'duties'}</span>}
          {s.dateMoved > 0 && <span style={CHIP}>{s.dateMoved} with a moved date</span>}
          {findings.length > 0 && <span style={{ ...CHIP, background: 'var(--accSoft)', color: 'var(--ink)' }}>{findings.length} {findings.length === 1 ? 'finding' : 'findings'}</span>}
        </span>
      </div>
      {sbs && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 16 }}>
          <div style={LABEL}>{cmp.before.label}</div>
          <div style={LABEL}>{cmp.after.label}</div>
        </div>
      )}
      {a.paragraphs.map((p, i) => {
        const vm = paragraphVM(p);
        const meta = (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 2 }}>
            {vm.change !== 'unchanged' && <span style={CHIP}>{vm.change}</span>}
            {vm.roleDuties > 0 && <span style={CHIP}>deployer ×{vm.roleDuties}</span>}
            {vm.duties > vm.roleDuties && <span style={CHIP}>duties ×{vm.duties}</span>}
            {vm.withheld.map((r) => (
              <span key={r} style={CHIP}>
                {reasonText(r)}
              </span>
            ))}
          </div>
        );
        const text = (segs: typeof vm.inline, empty: string) => (
          <div style={{ fontSize: 14, lineHeight: 1.6, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: vm.change === 'unchanged' ? 'var(--n1)' : 'var(--ink)' }}>
            {segs.length ? segs.map((sg, j) => <SegmentText key={j} sg={sg} />) : <span style={{ fontSize: 12, color: 'var(--n2)', fontStyle: 'italic' }}>{empty}</span>}
          </div>
        );
        return (
          <div key={`${p.label}-${i}`} data-change={vm.change} style={{ borderTop: i ? '1px solid var(--line)' : 'none', paddingTop: i ? 8 : 0 }}>
            {meta}
            {sbs ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 16 }}>
                {text(vm.before, 'no counterpart')}
                {text(vm.after, 'removed')}
              </div>
            ) : (
              text(vm.inline, '')
            )}
          </div>
        );
      })}
      {findings.length > 0 && <FindingsList findings={findings} />}
    </article>
  );
}

function FindingsList({ findings }: { findings: ImpactFinding[] }) {
  return (
    <section aria-label="Findings" style={{ borderTop: '1px solid var(--line)', paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <h4 style={{ ...LABEL, margin: 0 }}>Findings of the latest run</h4>
      {findings.map((f) => {
        const s = ag(f.agent);
        return (
          <div key={f.finding_id} data-finding={f.finding_id} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13 }}>
            <Dot c={s.c} size={8} style={{ marginTop: 6 }} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 700 }}>
                {s.n} · {f.affected_actor}
              </div>
              <div style={{ color: 'var(--n1)', lineHeight: 1.5 }}>{f.impact}</div>
            </div>
          </div>
        );
      })}
    </section>
  );
}

function MapView({ articles, stats, measure, focus, onOpen }: { articles: ArticleComparison[]; stats: Map<string, ArticleStats>; measure: Measure; focus: string | null; onOpen: (key: string) => void }) {
  const val = (a: ArticleComparison) => measureOf(stats.get(a.provision_key)!, measure);
  const max = Math.max(0, ...articles.map(val));
  const label = MEASURES.find((m) => m.v === measure)!.label;
  const groups: [string, ArticleComparison[]][] = [
    ['Articles', articles.filter((a) => a.kind === 'article')],
    ['Annexes', articles.filter((a) => a.kind === 'annex')],
  ];
  const top = articles.filter((a) => val(a) > 0).sort((a, b) => val(b) - val(a)).slice(0, 8);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,280px),1fr))', gap: 16, alignItems: 'start' }}>
      <section aria-label="Map" style={{ ...CARD, padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 12, gridColumn: 'span 2', minWidth: 0 }}>
        {groups.map(([name, list]) => (
          <div key={name} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <h3 style={{ ...LABEL, margin: 0 }}>{name}</h3>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {list.map((a) => {
                const v = val(a);
                const lv = heat(v, max);
                const s = stats.get(a.provision_key)!;
                const name = `${articleLabel(a)}${a.heading ? `, ${a.heading}` : ''}: ${v} · ${label}${s.changedParagraphs ? '' : ' · text unchanged'}`;
                return (
                  <button
                    key={a.provision_key}
                    type="button"
                    data-provision={a.provision_key}
                    aria-label={name}
                    title={name}
                    onClick={() => onOpen(a.provision_key)}
                    style={{ width: 46, height: 46, borderRadius: 6, border: `${focus === a.provision_key ? 2 : 1}px solid ${focus === a.provision_key ? 'var(--accInk)' : s.changedParagraphs ? 'var(--n2)' : 'var(--line)'}`, borderStyle: s.changedParagraphs ? 'solid' : 'dashed', background: heatFill(lv), color: heatInk(lv), display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '4px 5px', cursor: 'pointer', textAlign: 'left' }}
                  >
                    <span style={{ font: `600 10px ${FONT}`, ...TABULAR }}>{tileLabel(a)}</span>
                    <span style={{ font: `800 13px ${FONT}`, alignSelf: 'flex-end', ...TABULAR }}>{v || ''}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 14px', fontSize: 12, color: 'var(--n1)' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            Fill: none
            {[0, 1, 2, 3, 4, 5].map((l) => (
              <span key={l} aria-hidden style={{ width: 16, height: 10, borderRadius: 2, background: heatFill(l), border: '1px solid var(--line)' }} />
            ))}
            most ({max})
          </span>
          <span>Solid border: text changed · dashed: unchanged</span>
        </div>
      </section>
      <aside aria-label="Highest on this measure" style={{ ...CARD, padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
        <h3 style={{ ...LABEL, margin: 0 }}>Highest on this measure</h3>
        {top.length === 0 && <div style={{ fontSize: 13, color: 'var(--n1)' }}>Nothing on this measure.</div>}
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' }}>
          {top.map((a) => (
            <li key={a.provision_key} style={{ borderTop: '1px solid var(--line)' }}>
              <button type="button" onClick={() => onOpen(a.provision_key)} style={{ width: '100%', display: 'grid', gridTemplateColumns: '34px minmax(0,1fr)', gap: 8, padding: '7px 0', border: 'none', background: 'transparent', color: 'var(--ink)', textAlign: 'left', cursor: 'pointer', font: `500 13px ${FONT}` }}>
                <span style={{ fontWeight: 800, textAlign: 'right', ...TABULAR }}>{val(a)}</span>
                <span style={{ lineHeight: 1.35 }}>
                  {articleLabel(a)}
                  {a.heading ? ` · ${a.heading}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
