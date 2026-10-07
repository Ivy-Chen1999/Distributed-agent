import { useConsole } from '../ctx';
import { useNarrow } from '../hooks';
import { ACC, CARD, FONT, LABEL, PULSE, RED, TABULAR, WARN_BG, WARN_INK } from '../design';
import { Dot, EmptyCard, ErrorCard, HButton, LoadingCard, Seg } from '../components/ui';
import { colIds, decisionVMs, hasCost, feedVM, nodeVM, routerDecider, routerMode, routerNote, selInfo, stripVM } from '../model/pipeline';
import { statusAt } from '../model/trace';

const SPEEDS = [1, 2, 3, 4, 8];

export function Pipeline() {
  const C = useConsole();
  const narrow = useNarrow();
  if (!C.runId) return <EmptyCard title="No run selected" sub="Start a run to watch the agent graph fill in live." />;
  if (!C.run && C.runLoading) return <LoadingCard title="Loading run" sub="Fetching node events." />;
  if (!C.run && C.runError) return <ErrorCard title="Could not load the run" message={C.runError} onRetry={C.reloadRun} />;

  const rv = C.rv;
  const t = C.t;
  const strip = stripVM(rv);
  const cols = colIds(rv.trace.experts, hasCost(rv.trace)).map((ids, i) => ({ ids, i }));
  const decisions = decisionVMs(rv);
  const mode = routerMode(rv);
  const decider = routerDecider(rv);
  const feed = feedVM(rv, C.run?.board, C.run?.dossier);
  const valid = colIds(rv.trace.experts, hasCost(rv.trace)).flat();
  const sel = selInfo(valid.includes(C.sel) ? C.sel : (rv.trace.experts[0] ?? 'planner'), rv);

  const badge = (b: string) =>
    b === 'Quote not found' ? { c: WARN_INK, bg: WARN_BG } : b === 'Quote verified' || b === 'Quotes checked' ? { c: t.accInk, bg: t.accSoft } : { c: 'var(--n1)', bg: 'var(--soft)' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {C.runError && <ErrorCard title="Live updates interrupted" message={C.runError} onRetry={C.reloadRun} />}
      <div style={{ ...CARD, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18, padding: '14px 18px', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
          <div style={{ ...LABEL, whiteSpace: 'nowrap' }}>Agent graph</div>
          {rv.trace.finished && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button
                type="button"
                onClick={C.replaying ? C.stopReplay : C.replay}
                style={{ background: 'transparent', color: 'var(--ink)', border: '1px solid var(--line)', borderRadius: 4, padding: '5px 10px', font: `700 11px ${FONT}`, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 }}
              >
                <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                  <path d={C.replaying ? 'M6 5h4v14H6zM14 5h4v14h-4z' : 'M6 4l14 8-14 8z'} />
                </svg>
                {C.replaying ? 'Stop replay' : 'Replay'}
              </button>
              <Seg options={SPEEDS.map((s) => ({ v: String(s), label: `${s}×` }))} value={String(C.speed)} onChange={(v) => C.setSpeed(Number(v))} pad="3px 8px" font={`700 11px ${FONT}`} />
            </div>
          )}
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginLeft: 'auto' }}>
            {strip.map((s) => (
              <div key={s.label} style={{ display: 'flex', alignItems: 'baseline', gap: 6, whiteSpace: 'nowrap' }}>
                <span style={{ font: `600 15px ${FONT}`, ...TABULAR }}>{s.value}</span>
                <span style={{ fontSize: 12, color: 'var(--n2)' }}>{s.label}</span>
              </div>
            ))}
          </div>
        </div>
        <div role="region" aria-label="Pipeline stages" tabIndex={0} style={{ overflowX: 'auto', padding: '22px 18px 24px' }}>
          <div style={{ display: 'flex', flexDirection: narrow ? 'column' : 'row', alignItems: 'center', minWidth: narrow ? 0 : 'max-content' }}>
            {cols.map(({ ids, i }) => {
              const first = statusAt(rv.trace, ids[0] ?? 'board', rv.clock);
              const multi = ids.length > 1;
              return (
                <div key={i} style={{ display: 'flex', flexDirection: narrow ? 'column' : 'row', alignItems: 'center', maxWidth: '100%' }}>
                  {i > 0 && (
                    <div style={{ width: 26, height: narrow ? 22 : undefined, transform: narrow ? 'rotate(90deg)' : undefined, display: 'flex', alignItems: 'center', justifyContent: 'center', color: first === 'queued' || first === 'skipped' ? 'var(--line)' : ACC }}>
                      <svg width="20" height="10" viewBox="0 0 20 10" fill="none" stroke="currentColor" strokeWidth="1.6">
                        <path d="M0 5h17M13 1l4 4-4 4" />
                      </svg>
                    </div>
                  )}
                  <div style={{ display: 'flex', flexDirection: narrow ? 'row' : 'column', flexWrap: narrow ? 'wrap' : 'nowrap', justifyContent: 'center', gap: 8, padding: multi ? 8 : 0, border: multi ? '1px dashed var(--line)' : 'none', borderRadius: 12 }}>
                    {ids.map((id) => {
                      const nd = nodeVM(id, rv);
                      const s = nd.status;
                      return (
                        <HButton
                          key={id}
                          data-node={id}
                          onClick={() => C.setSel(id)}
                          style={{
                            width: 124,
                            textAlign: 'left',
                            background: s === 'running' ? t.accSoft : 'var(--card)',
                            border: `1px solid ${s === 'failed' ? RED : C.sel === id ? ACC : s === 'running' ? ACC : 'var(--line)'}`,
                            borderRadius: 8,
                            padding: 0,
                            overflow: 'hidden',
                            cursor: 'pointer',
                            color: 'var(--ink)',
                            transition: 'border-color .2s,opacity .2s',
                            opacity: s === 'queued' || s === 'skipped' ? 0.6 : 1,
                          }}
                          hover={{ borderColor: ACC }}
                        >
                          <div style={{ height: 3, background: nd.c }} />
                          <div style={{ padding: '8px 10px 9px' }}>
                            <div style={{ fontSize: 13, fontWeight: 700, lineHeight: 1.25 }}>{nd.name}</div>
                            <div style={{ fontSize: 11, color: 'var(--n2)', marginTop: 1, lineHeight: 1.3 }}>{nd.sub}</div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 8, font: `700 11px ${FONT}` }}>
                              <Dot c={nd.stC} anim={s === 'running' ? PULSE : 'none'} />
                              {nd.stT}
                            </div>
                            <div style={{ font: `600 11px ${FONT}`, ...TABULAR, color: 'var(--n1)', marginTop: 3 }}>{nd.meta}</div>
                            {nd.shadow && (
                              <div style={{ marginTop: 6, display: 'inline-block', font: `700 11px ${FONT}`, letterSpacing: '.06em', textTransform: 'uppercase', border: '1px dashed var(--n2)', color: 'var(--n1)', borderRadius: 4, padding: '1px 5px' }}>
                                {routerMode(rv) === 'off' ? 'off' : 'shadow'}
                              </div>
                            )}
                          </div>
                        </HButton>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,420px),1fr))', gap: 16, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ ...CARD, padding: '16px 18px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={LABEL}>Router decisions</div>
              <span style={{ marginLeft: 'auto', font: `700 11px ${FONT}`, letterSpacing: '.06em', textTransform: 'uppercase', border: '1px dashed var(--n2)', color: 'var(--n1)', borderRadius: 4, padding: '1px 6px' }}>{mode}</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--n1)', lineHeight: 1.5, marginTop: 6 }}>{routerNote(mode)}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14 }}>
              {decisions.map((d) => (
                <div key={d.id} style={{ display: 'grid', gridTemplateColumns: '96px minmax(0,1fr) 64px', gap: 10, alignItems: 'center' }}>
                  <span style={{ fontSize: 13, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 7 }}>
                    <Dot c={d.c} radius={2} />
                    {d.name}
                  </span>
                  <div style={{ height: 6, background: 'var(--soft)', borderRadius: 2, overflow: 'hidden' }}>
                    <div style={{ width: d.w, height: '100%', background: d.bar }} />
                  </div>
                  <span style={{ font: `700 11px ${FONT}`, color: d.tc, textAlign: 'right' }}>{d.decision}</span>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 11, color: 'var(--n2)', marginTop: 12 }}>
              {decider === 'jev' ? 'Decider: Jev. Bar length is the relevance probability.' : 'The decider is a stub for now. Relevance probabilities arrive with Jev.'}
            </div>
          </div>
          <div style={{ ...CARD, padding: '16px 18px' }}>
            <div style={LABEL}>Selected node</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
              <Dot c={sel.c} size={10} radius={3} />
              <span style={{ fontSize: 16, fontWeight: 600 }}>{sel.name}</span>
              <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5, font: `700 11px ${FONT}` }}>
                <Dot c={sel.stC} />
                {sel.stT}
              </span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--n1)', lineHeight: 1.5, marginTop: 6 }}>{sel.desc}</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12 }}>
              {sel.facts.map((f) => (
                <div key={f.k} style={{ background: 'var(--soft)', borderRadius: 6, padding: '8px 10px' }}>
                  <div style={{ fontSize: 11, color: 'var(--n2)' }}>{f.k}</div>
                  <div style={{ font: `600 12px ${FONT}`, ...TABULAR, marginTop: 2, wordBreak: 'break-all' }}>{f.v}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div style={{ ...CARD, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '14px 18px', borderBottom: '1px solid var(--line)' }}>
            <div style={LABEL}>Shared impact board · live</div>
            <span style={{ marginLeft: 'auto', font: `600 11px ${FONT}`, ...TABULAR, color: 'var(--n1)' }}>
              {C.run?.board?.length ? `${feed.items.length} of ${feed.total} findings` : `${feed.total} finding${feed.total === 1 ? '' : 's'} posted`}
            </span>
          </div>
          {feed.items.length === 0 && (
            <div style={{ padding: '40px 24px', textAlign: 'center' }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>Board is empty</div>
              <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 4 }}>Findings appear here as experts post them.</div>
            </div>
          )}
          <div role="region" aria-label="Shared impact board" tabIndex={0} style={{ display: 'flex', flexDirection: 'column', maxHeight: 560, overflowY: 'auto' }}>
            {feed.items.map((f) => {
              const b = badge(f.badge);
              return (
                <div key={f.key} style={{ display: 'flex', gap: 12, padding: '12px 18px', borderBottom: '1px solid var(--line)', animation: 'wfade .4s cubic-bezier(.2,0,0,1)' }}>
                  <span style={{ flex: 'none', width: 4, borderRadius: 2, background: f.c }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 12, fontWeight: 700 }}>{f.agentName}</span>
                      <span style={{ font: `500 11px ${FONT}`, ...TABULAR, color: 'var(--n1)' }}>{f.provision}</span>
                      <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, font: `700 11px ${FONT}`, letterSpacing: '.03em', color: b.c, background: b.bg, borderRadius: 4, padding: '3px 8px' }}>
                        <span style={{ width: 6, height: 6, borderRadius: 1, background: b.c }} />
                        {f.badge}
                      </span>
                    </div>
                    <div style={{ fontSize: 13, lineHeight: 1.45, marginTop: 4, textWrap: 'pretty' }}>{f.impact}</div>
                    <div style={{ fontSize: 12, color: 'var(--n2)', marginTop: 4 }}>
                      {f.actor}
                      {f.conf !== '—' ? ` · confidence ${f.conf}` : ''}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
