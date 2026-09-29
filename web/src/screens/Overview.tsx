import { useConsole } from '../ctx';
import { CARD, FONT, LABEL, TABULAR } from '../design';
import { Arrow, Chevron, Dot, EmptyCard, ErrorCard, HButton, LoadingCard, PRIMARY_BTN } from '../components/ui';
import { attention, fmtDate, kpis, runRows } from '../model/overview';
import { latBars } from '../model/pipeline';
import { scenarioBlurb, scenarioHeadline } from '../model/scenario';
import { ag } from '../design';

const RUN_COLS = '1.2fr 1.6fr 1fr .7fr .8fr .8fr 1.1fr';

export function Overview() {
  const C = useConsole();
  const runs = C.runs.data?.runs ?? [];

  if (!C.runId) {
    if (C.runs.status === 'loading' || C.runs.status === 'idle') return <LoadingCard title="Loading runs" sub="Fetching the latest runs from the WOMM API." />;
    // When the run list could not be loaded we do not know whether runs exist: show only the error.
    if (C.runs.status === 'error') return <ErrorCard title="Could not load runs" message={C.runs.error} onRetry={C.reloadRuns} />;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <EmptyCard
          title="No runs yet"
          sub="Start a run to see what a regulation change means for whom. Every impact traces back to a provision, a quote and a source."
          action={
            <HButton onClick={C.openPicker} style={PRIMARY_BTN} hover={{ background: '#33D3D9' }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="#0F0F0F"><path d="M6 4l14 8-14 8z" /></svg>Start a run
            </HButton>
          }
        />
      </div>
    );
  }

  const rv = C.rv;
  const changes = C.sources.data?.changes;
  const rows = runRows(runs, C.scenarios.data);
  const tiles = kpis(rv, C.runLabel, C.runDot, C.system.data?.experts[0]?.model);
  const att = attention(rv, changes);
  const bars = latBars(rv);
  const flow = ['diff', 'planner', 'router', ...rv.trace.experts, 'synthesis', 'dossier'];
  const goAtt = (k: string) => C.go('detail', { tab: k === 'disagree' ? 'disagree' : 'questions' });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 280 }}>
          <div style={{ ...LABEL, color: 'var(--accInk)' }}>Latest run{C.run?.created_at ? ` · ${fmtDate(C.run.created_at)}` : ''}</div>
          <h1 style={{ fontSize: 32, fontWeight: 600, letterSpacing: '-.015em', margin: '8px 0 10px', lineHeight: 1.25, textWrap: 'balance' }}>{scenarioHeadline(C.scenario)}</h1>
          <p style={{ fontSize: 14, color: 'var(--n1)', lineHeight: 1.55, margin: 0, maxWidth: '64ch', textWrap: 'pretty' }}>{scenarioBlurb(C.scenario, changes, rv.trace.experts.length)}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <HButton
            onClick={() => C.go('detail')}
            style={{ background: 'var(--ink)', color: 'var(--card)', border: 'none', borderRadius: 4, padding: '11px 18px', font: `700 13px ${FONT}`, letterSpacing: '.02em', cursor: 'pointer', whiteSpace: 'nowrap' }}
            hover={{ background: '#00C4CC', color: '#0F0F0F' }}
          >
            Open impact dossier
          </HButton>
          <button
            type="button"
            onClick={() => {
              C.go('pipeline');
              if (rv.trace.finished) C.replay();
            }}
            style={{ background: 'transparent', color: 'var(--ink)', border: '1px solid var(--ink)', borderRadius: 4, padding: '10px 16px', font: `700 13px ${FONT}`, cursor: 'pointer', whiteSpace: 'nowrap' }}
          >
            Watch pipeline
          </button>
        </div>
      </div>

      {C.runError && !C.run && <ErrorCard title="Could not load the run" message={C.runError} onRetry={C.reloadRun} />}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12 }}>
        {tiles.map((k) => (
          <div key={k.label} style={{ ...CARD, padding: '14px 16px' }}>
            <div style={LABEL}>{k.label}</div>
            <div style={{ fontSize: 28, fontWeight: 600, marginTop: 8, ...TABULAR, letterSpacing: '-.01em', display: 'flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap' }}>
              {k.dot && <Dot c={k.dot} size={9} />}
              {C.runLoading && !C.run ? '—' : k.value}
            </div>
            <div style={{ fontSize: 12, color: 'var(--n2)', marginTop: 2 }}>{k.sub}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,420px),1fr))', gap: 16, alignItems: 'start' }}>
        <div style={{ ...CARD, padding: '16px 18px 18px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={LABEL}>Agent latency</div>
            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--n2)' }}>Experts run in parallel</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, margin: '12px 0 16px', flexWrap: 'wrap' }}>
            {flow.map((id, i) => (
              <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                {i > 0 && !rv.trace.experts.slice(1).includes(id) && <Arrow w={14} />}
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, border: '1px solid var(--line)', borderRadius: 4, padding: '4px 8px', font: `700 11px ${FONT}` }}>
                  <Dot c={ag(id).c} radius={2} />
                  {ag(id).n}
                </span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {bars.map((b) => (
              <div key={b.id} style={{ display: 'grid', gridTemplateColumns: '110px minmax(0,1fr) 110px', gap: 12, alignItems: 'center' }}>
                <span style={{ fontSize: 13, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Dot c={b.c} radius={2} />
                  {b.name}
                </span>
                <div style={{ height: 8, background: 'var(--soft)', borderRadius: 2, overflow: 'hidden' }}>
                  <div style={{ width: b.w, height: '100%', background: b.bar, borderRadius: 2, transition: 'width .3s' }} />
                </div>
                <span style={{ font: `600 12px ${FONT}`, ...TABULAR, color: b.tc, textAlign: 'right' }}>{b.meta}</span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ ...CARD, padding: '16px 18px 10px' }}>
          <div style={LABEL}>Needs your review</div>
          <div style={{ display: 'flex', flexDirection: 'column', marginTop: 6 }}>
            {att.map((a) => (
              <HButton
                key={a.key}
                onClick={() => goAtt(a.key)}
                style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', color: 'var(--ink)', textAlign: 'left', cursor: 'pointer' }}
                hover={{ color: '#00A9B0' }}
              >
                <span style={{ fontSize: 24, fontWeight: 600, width: 34, ...TABULAR }}>{a.n}</span>
                <span style={{ flex: 1 }}>
                  <span style={{ display: 'block', fontSize: 14, fontWeight: 700 }}>{a.title}</span>
                  <span style={{ display: 'block', fontSize: 12, color: 'var(--n2)', marginTop: 2, lineHeight: 1.4 }}>{a.sub}</span>
                </span>
                <Chevron />
              </HButton>
            ))}
          </div>
        </div>
      </div>

      <div style={{ ...CARD, overflow: 'hidden' }}>
        <div style={{ ...LABEL, padding: '14px 18px', borderBottom: '1px solid var(--line)' }}>Recent runs</div>
        {C.runs.status === 'error' ? (
          <div style={{ padding: '14px 18px', fontSize: 13, color: 'var(--n1)' }}>
            Could not load the run list: {C.runs.error}{' '}
            <button type="button" onClick={C.reloadRuns} style={{ background: 'none', border: 'none', padding: 0, font: `700 13px ${FONT}`, color: 'var(--ink)', textDecoration: 'underline', textUnderlineOffset: 4, cursor: 'pointer' }}>
              Retry
            </button>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <div style={{ minWidth: 760 }}>
              <div style={{ display: 'grid', gridTemplateColumns: RUN_COLS, gap: 12, padding: '9px 18px', font: `700 11px ${FONT}`, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--n2)', background: 'var(--soft)' }}>
                <span>Run</span>
                <span>Scenario</span>
                <span>Status</span>
                <span>Impacts</span>
                <span>Grounding</span>
                <span>Time</span>
                <span>System version</span>
              </div>
              {rows.length === 0 && <div style={{ padding: '12px 18px', borderTop: '1px solid var(--line)', fontSize: 13, color: 'var(--n1)' }}>{C.runs.status === 'loading' ? 'Loading runs…' : 'No runs yet.'}</div>}
              {rows.map((r) => (
                <HButton
                  key={r.runId}
                  onClick={() => {
                    C.selectRun(r.runId);
                    C.go('detail');
                  }}
                  title={r.runId}
                  style={{ width: '100%', display: 'grid', gridTemplateColumns: RUN_COLS, gap: 12, padding: '12px 18px', border: 'none', borderTop: '1px solid var(--line)', background: r.runId === C.runId ? 'var(--soft)' : 'transparent', color: 'var(--ink)', textAlign: 'left', fontSize: 13, cursor: 'pointer', alignItems: 'center' }}
                  hover={{ background: 'var(--soft)' }}
                >
                  <span style={{ font: `600 12px ${FONT}`, ...TABULAR }}>{r.id}</span>
                  <span>
                    <span style={{ display: 'block', fontWeight: 600 }}>{r.name}</span>
                    <span style={{ display: 'block', fontSize: 11, color: 'var(--n2)' }}>{r.kind}</span>
                  </span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                    <Dot c={r.dot} />
                    {r.status}
                  </span>
                  <span>{r.impacts}</span>
                  <span style={TABULAR}>{r.grounding}</span>
                  <span style={TABULAR}>{r.time}</span>
                  <span style={{ fontFamily: FONT, ...TABULAR, fontSize: 12, color: 'var(--n1)' }}>{r.sv}</span>
                </HButton>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
