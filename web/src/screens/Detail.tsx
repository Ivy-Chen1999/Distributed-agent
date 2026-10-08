import { useEffect, useState, type CSSProperties } from 'react';
import { useConsole, type DetailTab } from '../ctx';
import { ACC, CARD, FONT, GREEN, LABEL, NO_DATA_IN_SCOPE, RED, TABULAR, WARN_BG, WARN_INK, ag, fmtS } from '../design';
import { Arrow, Dot, EmptyCard, ErrorCard, HButton, LoadingCard, Seg } from '../components/ui';
import { chainVMs, disagreementVMs, findingSteps, groupImpacts, questionVMs, type GroupMode, type ImpactVM } from '../model/dossier';
import { hasComparableChanges, provisionComparisons, type ComparisonSide, type ComparisonVM, type Segment } from '../model/compare';
import { logLines } from '../model/pipeline';
import { failedExperts, shortRunId } from '../model/overview';
import { humanize, regulationTitle, scenarioName } from '../model/scenario';
import { elapsedAt } from '../model/trace';
import type { ImpactDossier, ProvisionChange } from '../types';
import { useNarrow } from '../hooks';
import { BANDS, NO_FILTER, bandWord, dateText, effortLabel, filterOptions, filterRecords, fiscalFindingsByKey, hotspotRows, payerText, type CostFilter } from '../model/costs';
import type { HotspotDimension, Recurrence } from '../types';

const MONO = { fontFamily: FONT, fontVariantNumeric: 'tabular-nums' } as const;

export function Detail() {
  const C = useConsole();
  const rv = C.rv;

  useEffect(() => {
    if (!C.openImp || !C.focusNonce) return;
    const el = document.getElementById(`imp-${C.openImp}`);
    el?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  }, [C.focusNonce, C.openImp]);

  if (!C.runId) return <EmptyCard title="No run selected" sub="Pick a run on the overview, or start a new one." />;
  if (!C.run && C.runLoading) return <LoadingCard title="Loading dossier" sub="Fetching the run and its Impact Dossier." />;
  if (!C.run) return <ErrorCard title="Could not load the run" message={C.runError ?? 'Unknown error'} onRetry={C.reloadRun} />;

  const run = C.run;
  const d = run.dossier ?? null;
  const changes = C.sources.data?.changes;
  const done = rv.trace.finished && rv.clock >= rv.trace.end;
  const failed = failedExperts(rv);
  const questions = d ? questionVMs(d) : [];
  const disagreements = d ? disagreementVMs(d, run.board, changes) : [];
  const chains = d ? chainVMs(d) : [];
  const counts = [
    { n: d ? String(d.impacts.length) : '—', label: 'impacts' },
    { n: d ? String(questions.length) : '—', label: 'open questions' },
    { n: d ? String(disagreements.length) : '—', label: disagreements.length === 1 ? 'disagreement' : 'disagreements' },
    { n: String(failed.length), label: 'failed experts' },
  ];
  const regTitle = regulationTitle(C.scenario).replace(/^EU /, '').replace(' · ', ' ');
  const git = run.code_identity?.git_sha;

  const tabDefs: [DetailTab, string, string][] = [
    ['impacts', 'Impacts', d ? String(d.impacts.length) : ''],
    ['chains', 'Impact chains', d ? String(chains.length) : ''],
    ['disagree', 'Disagreements', d ? String(disagreements.length) : ''],
    ['questions', 'Open questions', d ? String(questions.length) : ''],
    ['costs', 'Costs', d?.costs ? String(d.costs.records.length) : ''],
    ['log', 'Event log', ''],
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ ...CARD, padding: '18px 20px', display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ ...LABEL, color: 'var(--accInk)' }}>Impact dossier</div>
          <div style={{ fontSize: 24, fontWeight: 600, marginTop: 6, letterSpacing: '-.01em' }}>
            {scenarioName(run.scenario_id)}
            {C.scenario ? ` · ${regTitle}` : ''}
          </div>
          <div style={{ font: `500 12px ${FONT}`, ...TABULAR, color: 'var(--n1)', marginTop: 4 }} title={run.run_id}>
            {shortRunId(run.run_id)} · {run.system_version}
            {git ? ` · git ${git}${run.code_identity?.dirty ? ' (dirty)' : ''}` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
          {counts.map((c) => (
            <div key={c.label} style={{ borderLeft: '1px solid var(--ink)', paddingLeft: 14 }}>
              <div style={{ fontSize: 28, fontWeight: 400, lineHeight: 1.1, ...TABULAR }}>{c.n}</div>
              <div style={{ fontSize: 12, color: 'var(--n1)' }}>{c.label}</div>
            </div>
          ))}
        </div>
      </div>

      <ProvisionComparison />

      {!done && (
        <div style={{ ...CARD, border: '1px dashed var(--line)', padding: '40px 24px', textAlign: 'center' }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>Dossier is assembling</div>
          <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 6 }}>Synthesis merges the board once all experts and the citation check finish. {C.clockText} elapsed.</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520, margin: '18px auto 0' }}>
            <div style={{ height: 14, borderRadius: 3, background: 'var(--soft)' }} />
            <div style={{ height: 14, width: '82%', borderRadius: 3, background: 'var(--soft)' }} />
            <div style={{ height: 14, width: '64%', borderRadius: 3, background: 'var(--soft)' }} />
          </div>
        </div>
      )}

      {done && !d && (
        <ErrorCard
          title="No dossier for this run"
          message={`The run ended as ${run.status}${run.error_kind ? ` (${run.error_kind})` : ''}${run.error ? `: ${run.error}` : ''}. Check the event log on the pipeline screen.`}
        />
      )}

      {done && d && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }} role="tablist">
            {tabDefs.map(([k, l, n]) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={C.tab === k}
                onClick={() => C.setTab(k)}
                style={{ background: 'transparent', border: 'none', borderBottom: `2px solid ${C.tab === k ? ACC : 'transparent'}`, marginBottom: -1, padding: '10px 12px', font: `${C.tab === k ? 700 : 600} 13px ${FONT}`, color: 'var(--ink)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
              >
                {l}
                <span style={{ font: `600 11px ${FONT}`, color: 'var(--n2)' }}>{n}</span>
              </button>
            ))}
          </div>

          {C.tab === 'impacts' && <ImpactsTab d={d} changes={changes} />}

          {C.tab === 'chains' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {chains.length === 0 && <EmptyCard title="No impact chains" sub="Synthesis did not link any impacts causally in this run." />}
              {chains.map((ch, ci) => (
                <div key={ci} style={{ ...CARD, padding: '16px 18px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    {ch.steps.map((cs, i) => (
                      <div key={cs.id + i} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {i > 0 && <Arrow w={18} />}
                        <HButton
                          onClick={() => C.openImpact(cs.id)}
                          title={cs.title}
                          style={{ border: '1px solid var(--line)', background: 'var(--soft)', borderRadius: 4, padding: '6px 10px', font: `700 12px ${FONT}`, ...TABULAR, color: 'var(--ink)', cursor: 'pointer', maxWidth: 220, textAlign: 'left' }}
                          hover={{ borderColor: ACC }}
                        >
                          {cs.id} <span style={{ font: `500 12px ${FONT}`, color: 'var(--n1)' }}>{cs.short}</span>
                        </HButton>
                      </div>
                    ))}
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--n1)', marginTop: 12, maxWidth: '100ch' }}>{ch.desc}</div>
                </div>
              ))}
            </div>
          )}

          {C.tab === 'disagree' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {disagreements.length === 0 && <EmptyCard title="No disagreements" sub="The experts' findings did not conflict in this run." />}
              {disagreements.map((dg, di) => (
                <div key={di} style={{ ...CARD, padding: '18px 20px' }}>
                  <div style={LABEL}>{dg.heading}</div>
                  <div style={{ fontSize: 14, lineHeight: 1.55, marginTop: 8, maxWidth: '95ch' }}>{dg.note}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,240px),1fr))', gap: 12, marginTop: 16 }}>
                    {dg.sides.map((sd) => (
                      <div key={sd.id} style={{ border: '1px solid var(--line)', borderRadius: 10, overflow: 'hidden' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', borderTop: `4px solid ${sd.c}`, background: 'var(--soft)' }}>
                          <span style={{ fontSize: 13, fontWeight: 700 }}>{sd.agentName}</span>
                          <span style={{ marginLeft: 'auto', font: `700 11px ${FONT}` }}>{sd.stance}</span>
                        </div>
                        <div style={{ padding: 12 }}>
                          <div style={{ fontSize: 13, lineHeight: 1.5 }}>{sd.impact}</div>
                          <div style={{ font: `500 11px ${FONT}`, ...TABULAR, color: 'var(--n2)', marginTop: 10 }}>
                            {sd.impactId ? (
                              <button type="button" onClick={() => C.openImpact(sd.impactId!)} style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', color: 'var(--accInk)', cursor: 'pointer', textDecoration: 'underline', textUnderlineOffset: 3 }}>
                                {sd.impactId}
                              </button>
                            ) : null}
                            {sd.impactId ? ' · ' : ''}
                            {sd.id} · confidence {sd.conf}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--n2)', marginTop: 12 }}>We keep both readings in the dossier. An analyst decides which one holds.</div>
                </div>
              ))}
            </div>
          )}

          {C.tab === 'questions' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ ...CARD, overflow: 'hidden' }}>
                {questions.length === 0 && <div style={{ padding: '14px 18px', fontSize: 14, color: 'var(--n1)' }}>No open questions in this run.</div>}
                {questions.map((q) => (
                  <div key={q.n} style={{ display: 'flex', gap: 14, padding: '14px 18px', borderBottom: '1px solid var(--line)', alignItems: 'flex-start' }}>
                    <span style={{ font: `700 12px ${FONT}`, ...TABULAR, color: 'var(--n2)', width: 26, flex: 'none', paddingTop: 2 }}>Q{q.n}</span>
                    <span style={{ flex: 1, fontSize: 14, lineHeight: 1.55, textWrap: 'pretty' }}>{q.q}</span>
                    <span style={{ flex: 'none', font: `700 11px ${FONT}`, borderRadius: 4, padding: '3px 8px', background: q.unresolved ? WARN_BG : 'var(--soft)', color: q.unresolved ? WARN_INK : 'var(--n1)' }}>{q.tag}</span>
                  </div>
                ))}
              </div>
              <div style={{ ...CARD, padding: '16px 18px' }}>
                <div style={LABEL}>Failed experts</div>
                {failed.length === 0 && <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 8 }}>None. All {rv.trace.experts.length} experts returned valid findings.</div>}
                {failed.map((f) => {
                  const lat = elapsedAt(rv.trace, f.agent, Infinity);
                  const msg = d.failed_experts.find((x) => x.agent === f.agent)?.message;
                  return (
                    <div key={f.agent} title={msg} style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, border: `1px solid ${RED}`, borderRadius: 8, padding: '10px 12px', flexWrap: 'wrap' }}>
                      <Dot c={RED} size={8} />
                      <span style={{ fontSize: 13, fontWeight: 700 }}>{ag(f.agent).n}</span>
                      <span style={{ font: `600 12px ${FONT}`, ...TABULAR, color: 'var(--n1)' }}>
                        error_kind: {f.kind}
                        {lat ? ` · ${fmtS(lat)}` : ''}
                      </span>
                      <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--n1)' }}>{f.kind === NO_DATA_IN_SCOPE ? 'Skipped: no data in scope' : 'Run continued as degraded'}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {C.tab === 'costs' && <CostsTab d={d} />}

          {C.tab === 'log' && <EventLog />}
        </div>
      )}
    </div>
  );
}

export function EventLog() {
  const C = useConsole();
  const lines = logLines(C.rv);
  return (
    <div style={{ background: '#0B1112', borderRadius: 12, padding: '14px 0', color: '#D5DEDF', font: `500 13px/1.7 ${FONT}`, ...TABULAR, overflowX: 'auto' }}>
      {lines.length === 0 && <div style={{ padding: '1px 18px', color: '#7F8B8D' }}>No events yet.</div>}
      {lines.map((l) => (
        <div key={l.key} style={{ display: 'grid', gridTemplateColumns: '96px 54px 110px minmax(0,1fr)', gap: 12, padding: '1px 18px' }}>
          <span style={{ color: '#7F8B8D' }}>{l.ts}</span>
          <span style={{ color: l.level === 'INFO' ? '#3ED9E0' : l.level === 'WARN' ? '#FFB547' : '#FF6B6E', fontWeight: 700 }}>{l.level}</span>
          <span style={{ color: l.c }}>{l.node}</span>
          <span>{l.msg}</span>
        </div>
      ))}
    </div>
  );
}

function ImpactsTab({ d, changes }: { d: ImpactDossier; changes: ProvisionChange[] | undefined }) {
  const C = useConsole();
  const groups = groupImpacts(d, C.group, changes);
  const extra = [
    d.unprocessed.length ? `${d.unprocessed.length} supported finding${d.unprocessed.length === 1 ? '' : 's'} not placed by synthesis` : '',
    d.discarded.length ? `${d.discarded.length} discarded by synthesis` : '',
  ].filter(Boolean);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--n2)' }}>Group by</span>
        <Seg<GroupMode>
          options={[
            { v: 'area', label: 'Provision area' },
            { v: 'actor', label: 'Affected actor' },
          ]}
          value={C.group}
          onChange={C.setGroup}
        />
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--n2)' }}>Open a quote to see it in context</span>
      </div>
      {d.impacts.length === 0 && <EmptyCard title="No impacts" sub={d.status === 'no_changes' ? 'The scenario has no provision changes to assess.' : 'Synthesis produced no impacts for this run.'} />}
      {groups.map((gr) => (
        <div key={gr.label + gr.meta} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px 10px', margin: '8px 0 4px', flexWrap: 'wrap' }}>
            <span style={{ flex: 'none', whiteSpace: 'nowrap', fontSize: 15, fontWeight: 600 }}>{gr.label}</span>
            <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', font: `500 11px ${FONT}`, ...TABULAR, color: 'var(--n2)' }}>{gr.meta}</span>
          </div>
          {gr.items.map((im) => (
            <ImpactCard key={im.id} im={im} changes={changes} />
          ))}
        </div>
      ))}
      <div style={{ fontSize: 12, color: 'var(--n2)', textAlign: 'center', padding: 6 }}>
        Showing all {d.impacts.length} impacts{extra.length ? ` · ${extra.join(' · ')}` : ''}.
      </div>
    </div>
  );
}

function ImpactCard({ im, changes }: { im: ImpactVM; changes: ProvisionChange[] | undefined }) {
  const C = useConsole();
  const narrow = useNarrow();
  const open = C.openImp === im.id;
  return (
    <div id={`imp-${im.id}`} style={{ background: 'var(--card)', border: `1px solid ${open ? ACC : 'var(--line)'}`, borderRadius: 12, overflow: 'hidden', scrollMarginTop: 90 }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => C.setOpenImp(open ? null : im.id)}
        style={{ width: '100%', display: 'flex', gap: 14, alignItems: 'flex-start', padding: '14px 18px', border: 'none', background: 'transparent', color: 'var(--ink)', textAlign: 'left', cursor: 'pointer' }}
      >
        <span style={{ font: `700 12px ${FONT}`, ...TABULAR, color: 'var(--accInk)', flex: 'none', width: 30, paddingTop: 2 }}>{im.id}</span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 15, lineHeight: 1.5, fontWeight: 600, textWrap: 'pretty' }}>{im.summary}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8, flexWrap: 'wrap', fontSize: 12, color: 'var(--n1)' }}>
            <span style={{ display: 'flex', gap: 8 }}>
              {im.agents.map((a) => (
                <span key={a.n} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontWeight: 700 }}>
                  <Dot c={a.c} size={8} />
                  {a.n}
                </span>
              ))}
            </span>
            <span style={{ whiteSpace: 'nowrap' }}>confidence {im.conf}</span>
            <span style={{ whiteSpace: 'nowrap' }}>{im.merged}</span>
            <span style={{ whiteSpace: 'nowrap', ...MONO }}>{im.key}</span>
          </span>
        </span>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flex: 'none', marginTop: 2, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div style={{ borderTop: '1px solid var(--line)', padding: narrow ? '16px 18px 18px' : '16px 18px 18px 62px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,300px),1fr))', gap: 20, background: 'var(--soft)' }}>
          {im.findings.map((fd) => {
            const c = ag(fd.agent).c;
            const steps = findingSteps(fd, changes);
            return (
              <div key={fd.finding_id}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <Dot c={c} size={8} />
                  <span style={{ fontSize: 13, fontWeight: 700 }}>{ag(fd.agent).n} finding</span>
                  <span style={{ font: `500 11px ${FONT}`, ...TABULAR, color: 'var(--n2)' }}>{fd.finding_id}</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  {steps.map((st, i) => (
                    <div key={i} style={{ display: 'grid', gridTemplateColumns: '18px minmax(0,1fr)', gap: 10 }}>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                        <span style={{ width: 10, height: 10, borderRadius: '50%', border: `2px solid ${c}`, background: 'var(--card)', marginTop: 3, flex: 'none' }} />
                        <span style={{ flex: 1, width: 1, background: i < steps.length - 1 ? 'var(--line)' : 'transparent', margin: '2px 0' }} />
                      </div>
                      <div style={{ paddingBottom: 12, minWidth: 0 }}>
                        <div style={{ font: `700 11px ${FONT}`, letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--n2)' }}>{st.k}</div>
                        {!st.quote && <div style={{ fontSize: 13, lineHeight: 1.5, marginTop: 2, fontFamily: st.mono ? FONT : 'inherit', overflowWrap: 'anywhere' }}>{st.v}</div>}
                        {st.quote && (
                          <HButton
                            onClick={() => st.quote && C.openSource(st.quote.sourceId, st.quote.quote)}
                            style={{ display: 'block', textAlign: 'left', marginTop: 4, border: '1px solid var(--line)', background: 'var(--card)', borderRadius: 6, padding: '9px 11px', fontSize: 13, lineHeight: 1.5, color: 'var(--ink)', cursor: 'pointer' }}
                            hover={{ borderColor: ACC }}
                          >
                            “{st.v}” <span style={{ display: 'inline-block', font: `700 11px ${FONT}`, color: 'var(--accInk)', textDecoration: 'underline', textUnderlineOffset: 3, marginLeft: 4 }}>View in source</span>
                          </HButton>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Deletions and additions are told apart by strike-through / underline and by screen-reader text,
// not by colour alone.
const DEL: CSSProperties = { background: 'rgba(229,72,77,.16)', color: 'var(--ink)', textDecoration: 'line-through', textDecorationColor: RED, textDecorationThickness: 2, borderRadius: 2 };
const INS: CSSProperties = { background: 'rgba(30,158,106,.18)', color: 'var(--ink)', textDecoration: 'underline', textDecorationColor: GREEN, textDecorationThickness: 2, textUnderlineOffset: 3, borderRadius: 2 };

/** R36 page 2: proposal / final-text comparison, shown for diff scenarios only. */
function ProvisionComparison() {
  const C = useConsole();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const scenarioId = C.run?.scenario_id;
  const src = C.sources;
  // Data of another scenario can linger while the new one loads; never show it.
  const data = src.data && src.data.scenario_id === scenarioId ? src.data : undefined;
  const isDiff = !!C.scenario?.before_version || hasComparableChanges(data?.changes);
  if (!scenarioId || !isDiff) return null;

  const vms = provisionComparisons(data);
  const comparable = vms.some((v) => v.before && v.after);
  let body;
  if (src.status === 'error' && !data) body = <ErrorCard title="Could not load the provision texts" message={src.error} onRetry={C.reloadSources} />;
  else if (!data) body = <LoadingCard title="Loading provision texts" sub="Fetching the proposal and final texts of the changed provisions." />;
  else if (!comparable) body = <EmptyCard title="No provisions to compare" sub="No changed provision of this scenario exists in both the proposal and the final text." />;
  else
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {/* Collapsed by default: full articles are long and the dossier below is the main content. */}
        {vms.map((v) => (
          <ComparisonCard key={v.provisionKey} v={v} open={!!open[v.provisionKey]} onToggle={() => setOpen((o) => ({ ...o, [v.provisionKey]: !o[v.provisionKey] }))} />
        ))}
      </div>
    );

  const first = vms.find((v) => v.before && v.after);
  return (
    <section aria-labelledby="provision-comparison" data-testid="provision-comparison" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px 14px', flexWrap: 'wrap' }}>
        <h2 id="provision-comparison" style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>
          Provision comparison
        </h2>
        {first && (
          <span style={{ fontSize: 12, color: 'var(--n1)' }}>
            {first.before!.stage} {first.before!.version} → {first.after!.stage.toLowerCase()} {first.after!.version}, word by word
          </span>
        )}
        {comparable && (
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12, color: 'var(--n1)' }} aria-hidden="true">
            <span>
              <del style={DEL}>struck through</del> removed
            </span>
            <span>
              <ins style={INS}>underlined</ins> added
            </span>
          </span>
        )}
      </div>
      {body}
    </section>
  );
}

function ComparisonCard({ v, open, onToggle }: { v: ComparisonVM; open: boolean; onToggle: () => void }) {
  const stats = v.missingText ? 'text unavailable' : v.identical ? 'wording unchanged' : `−${v.removed} / +${v.added} words`;
  const panel = `cmp-${v.provisionKey.replace(/[^\w-]/g, '_')}`;
  return (
    <div data-provision={v.provisionKey} style={{ ...CARD, borderColor: open ? ACC : 'var(--line)', overflow: 'hidden' }}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panel}
        onClick={onToggle}
        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: '6px 14px', flexWrap: 'wrap', padding: '12px 18px', border: 'none', background: 'transparent', color: 'var(--ink)', textAlign: 'left', cursor: 'pointer' }}
      >
        <span style={{ font: `700 14px ${FONT}`, ...TABULAR, whiteSpace: 'nowrap' }}>{v.label}</span>
        <span style={{ font: `700 11px ${FONT}`, borderRadius: 4, padding: '3px 8px', background: 'var(--soft)', color: 'var(--n1)' }}>{humanize(v.kind)}</span>
        <span style={{ font: `500 11px ${FONT}`, ...TABULAR, color: 'var(--n2)', overflowWrap: 'anywhere', minWidth: 0 }}>{v.provisionKey}</span>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--n1)', ...TABULAR, whiteSpace: 'nowrap' }}>{stats}</span>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" style={{ flex: 'none', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div id={panel} style={{ borderTop: '1px solid var(--line)', padding: '14px 18px 16px', background: 'var(--soft)' }}>
          {v.identical && v.before!.article !== v.after!.article && (
            <div style={{ fontSize: 12, color: 'var(--n1)', marginBottom: 10 }}>
              Same wording; only the article number changed ({v.before!.article} → {v.after!.article}).
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,300px),1fr))', gap: 12 }}>
            <ComparisonColumn side={v.before} which="before" />
            <ComparisonColumn side={v.after} which="after" />
          </div>
        </div>
      )}
    </div>
  );
}

function ComparisonColumn({ side, which }: { side: ComparisonSide | null; which: 'before' | 'after' }) {
  const head = side ? `${side.stage} · ${side.version} · ${side.article}` : which === 'before' ? 'Proposal' : 'Final text';
  return (
    <div data-side={which} style={{ background: 'var(--card)', border: `1px ${side ? 'solid' : 'dashed'} var(--line)`, borderRadius: 8, minWidth: 0 }}>
      <div style={{ ...LABEL, padding: '9px 12px', borderBottom: '1px solid var(--line)', overflowWrap: 'anywhere' }}>{head}</div>
      <div style={{ padding: '10px 12px', fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {!side && <span style={{ color: 'var(--n1)' }}>{which === 'before' ? 'Not in the proposal: the provision was added.' : 'Not in the final text: the provision was removed.'}</span>}
        {side && !side.segments && <span style={{ color: 'var(--n1)' }}>The text of {side.sourceId} is not among the scenario sources.</span>}
        {side?.segments?.map((sg, i) => <SegmentText key={i} sg={sg} />)}
      </div>
    </div>
  );
}

function SegmentText({ sg }: { sg: Segment }) {
  if (sg.op === 'same') return <>{sg.text}</>;
  const del = sg.op === 'del';
  const Tag = del ? 'del' : 'ins';
  return (
    <Tag style={del ? DEL : INS}>
      <span className="sr-only">{del ? '[removed: ' : '[added: '}</span>
      {sg.text}
      <span className="sr-only">]</span>
    </Tag>
  );
}

// ---------- Costs tab (EU cost plan R5) ----------

const HOTSPOT_TITLES: [HotspotDimension, string][] = [
  ['provision', 'By provision'],
  ['payer', 'By payer'],
  ['effort_type', 'By effort type'],
];

const SELECT: CSSProperties = { font: `600 12px ${FONT}`, border: '1px solid var(--line)', borderRadius: 6, padding: '5px 8px', background: 'var(--card)', color: 'var(--ink)' };
const BADGE: CSSProperties = { font: `700 11px ${FONT}`, borderRadius: 4, padding: '2px 7px', whiteSpace: 'nowrap' };

function CostsTab({ d }: { d: ImpactDossier }) {
  const C = useConsole();
  const [rec, setRec] = useState<Recurrence>('one_off');
  const [filter, setFilter] = useState<CostFilter>(NO_FILTER);
  const s = d.costs;
  if (!s) return <EmptyCard title="No cost section" sub="This version has no cost step." />;
  const cov = s.coverage;
  const shown = filterRecords(s.records, filter);
  const opts = filterOptions(s.records);
  const fiscal = fiscalFindingsByKey(d);
  const set = (k: keyof CostFilter) => (e: { target: { value: string } }) => setFilter({ ...filter, [k]: e.target.value });
  const bases = Object.entries(cov.payers_by_basis).map(([b, n]) => `${n} ${b.replace(/_/g, ' ')}`).join(' · ');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ ...CARD, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={LABEL}>Cost records</div>
        <div style={{ fontSize: 14 }}>
          {cov.relevant} obligations · {cov.estimated} estimated · {cov.not_costed} not costed · {cov.not_estimated} not estimated
        </div>
        <div style={{ fontSize: 12, color: 'var(--n2)' }}>Payers: {bases || '—'}. Bands are ordinal classes per affected entity, never euro totals.</div>
        {s.delta_basis && <div style={{ fontSize: 12, color: 'var(--n2)' }}>Change marks compare {s.delta_basis} · {s.late_added.length} added after the proposal</div>}
        {cov.not_covered_keys.length > 0 && <div style={{ fontSize: 12, color: 'var(--n2)' }}>Not covered (no obligation records): {cov.not_covered_keys.join(', ')}</div>}
        {s.notes.map((n) => (
          <div key={n} style={{ fontSize: 12, color: WARN_INK }}>
            {n}
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--n2)' }}>Hotspots</span>
        <Seg<Recurrence>
          options={[
            { v: 'one_off', label: 'One-off' },
            { v: 'recurring', label: 'Recurring' },
          ]}
          value={rec}
          onChange={setRec}
        />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,280px),1fr))', gap: 12 }}>
        {HOTSPOT_TITLES.map(([dim, title]) => {
          const rows = hotspotRows(s, dim, rec).slice(0, 8);
          return (
            <div key={dim} style={{ ...CARD, padding: '14px 16px' }} aria-label={`Hotspots ${title.toLowerCase()}`}>
              <div style={LABEL}>{title}</div>
              {rows.length === 0 && <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 8 }}>No {rec === 'one_off' ? 'one-off' : 'recurring'} bands.</div>}
              {rows.map((h) => (
                <div key={h.value} style={{ display: 'flex', gap: 10, alignItems: 'baseline', marginTop: 8, fontSize: 13 }}>
                  <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere', ...(dim === 'provision' ? MONO : {}) }}>{h.label}</span>
                  <span style={{ ...MONO, color: 'var(--ink)' }} title="records at medium or high">
                    {h.medium_or_high} med/high
                  </span>
                  <span style={{ ...MONO, color: 'var(--n2)' }} title="records at low">
                    {h.low} low
                  </span>
                </div>
              ))}
            </div>
          );
        })}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <select aria-label="Payer" value={filter.payer} onChange={set('payer')} style={SELECT}>
          <option value="all">All payers</option>
          {opts.payers.map((p) => (
            <option key={p} value={p}>
              {p === 'unknown' ? 'Payer not identified' : p.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
        <select aria-label="Effort type" value={filter.effort} onChange={set('effort')} style={SELECT}>
          <option value="all">All effort types</option>
          {opts.efforts.map((e) => (
            <option key={e} value={e}>
              {effortLabel(e)}
            </option>
          ))}
        </select>
        <select aria-label="Band" value={filter.band} onChange={set('band')} style={SELECT}>
          <option value="all">All bands</option>
          {BANDS.map((b) => (
            <option key={b} value={b}>
              {bandWord(b)}
            </option>
          ))}
        </select>
        <select aria-label="Change after the proposal" value={filter.change} onChange={set('change')} style={SELECT}>
          <option value="all">Any change</option>
          <option value="changed">Changed after the proposal</option>
          <option value="added">Added after the proposal</option>
        </select>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--n2)' }}>
          Showing {shown.length} of {s.records.length} records
        </span>
      </div>

      <div style={{ ...CARD, overflow: 'hidden' }}>
        {shown.length === 0 && <div style={{ padding: '14px 18px', fontSize: 14, color: 'var(--n1)' }}>No records match these filters.</div>}
        {shown.map((r) => {
          const fs = fiscal[r.provision_key] ?? [];
          return (
            <div key={r.obligation_id} data-testid="cost-record" style={{ padding: '12px 18px', borderBottom: '1px solid var(--line)', display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <button type="button" onClick={() => C.openSource(r.source_id, `[${r.obligation_id}]`)} style={{ background: 'none', border: 'none', padding: 0, font: `600 12px ${FONT}`, ...TABULAR, color: 'var(--accInk)', cursor: 'pointer', textDecoration: 'underline', textUnderlineOffset: 3 }}>
                  {r.obligation_id}
                </button>
                <span style={{ font: `500 12px ${FONT}`, ...TABULAR, color: 'var(--n2)' }}>{r.provision_key}</span>
                {r.late_added && <span style={{ ...BADGE, background: WARN_BG, color: WARN_INK }}>Added after the proposal</span>}
                {!r.late_added && r.changed_after_proposal && <span style={{ ...BADGE, background: 'var(--soft)', color: 'var(--n1)' }}>Changed after the proposal</span>}
              </div>
              <div style={{ fontSize: 13, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span>{payerText(r)}</span>
                {r.status === 'estimated' ? (
                  <>
                    <span>{effortLabel(r.effort_type)}</span>
                    <span>One-off: {bandWord(r.one_off)}</span>
                    <span>Recurring: {bandWord(r.recurring)}</span>
                  </>
                ) : (
                  <span style={{ color: 'var(--n2)' }}>{r.status === 'not_costed' ? 'Not costed' : 'Not estimated'}{r.reason ? `: ${r.reason}` : ''}</span>
                )}
                <span style={{ color: 'var(--n2)' }}>Applies from: {dateText(r)}</span>
              </div>
              {r.rationale && <div style={{ fontSize: 12, color: 'var(--n1)' }}>{r.rationale}</div>}
              {fs.length > 0 && (
                <div style={{ fontSize: 12, color: 'var(--n2)' }}>
                  Fiscal findings on this provision: {fs.map((f) => f.impact).join(' · ')}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
