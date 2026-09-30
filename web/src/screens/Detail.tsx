import { useEffect } from 'react';
import { useConsole, type DetailTab } from '../ctx';
import { ACC, CARD, FONT, LABEL, RED, TABULAR, WARN_BG, WARN_INK, ag, fmtS } from '../design';
import { Arrow, Dot, EmptyCard, ErrorCard, HButton, LoadingCard, Seg } from '../components/ui';
import { chainVMs, disagreementVMs, findingSteps, groupImpacts, questionVMs, type GroupMode, type ImpactVM } from '../model/dossier';
import { logLines } from '../model/pipeline';
import { failedExperts, shortRunId } from '../model/overview';
import { regulationTitle, scenarioName } from '../model/scenario';
import { elapsedAt } from '../model/trace';
import type { ImpactDossier, ProvisionChange } from '../types';

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
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: 12, marginTop: 16 }}>
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
                      <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--n1)' }}>Run continued as degraded</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

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
        <div style={{ borderTop: '1px solid var(--line)', padding: '16px 18px 18px 62px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 20, background: 'var(--soft)' }}>
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
