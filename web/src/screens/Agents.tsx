import { useConsole } from '../ctx';
import { FONT, RED, TABULAR } from '../design';
import { Dot, ErrorCard, LoadingCard } from '../components/ui';
import { agentCards } from '../model/pipeline';

export function Agents() {
  const C = useConsole();
  if (C.system.status === 'loading' && !C.system.data && !C.run) return <LoadingCard title="Loading agents" sub="Reading the system version." />;
  const cards = agentCards(C.rv);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <p style={{ fontSize: 14, color: 'var(--n1)', lineHeight: 1.55, margin: 0, maxWidth: '72ch' }}>
        Each agent runs in its own isolated process with a versioned prompt. The router picks relevant experts, experts post to a shared board, and synthesis merges what they found.
      </p>
      {C.system.status === 'error' && <ErrorCard title="Could not load the system version" message={C.system.error} onRetry={C.reloadSystem} />}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(300px,1fr))', gap: 14 }}>
        {cards.map((a) => (
          <div key={a.id} data-agent={a.id} style={{ background: 'var(--card)', border: `1px solid ${a.status === 'failed' ? RED : 'var(--line)'}`, borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ height: 4, background: a.c }} />
            <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 16, fontWeight: 600 }}>{a.name}</span>
                <span style={{ font: `700 11px ${FONT}`, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--n2)' }}>{a.role}</span>
                <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5, font: `700 11px ${FONT}` }}>
                  <Dot c={a.stC} />
                  {a.stT}
                </span>
              </div>
              <div style={{ fontSize: 13, color: 'var(--n1)', lineHeight: 1.5 }}>{a.desc}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8 }}>
                {a.stats.map((s) => (
                  <div key={s.k} style={{ background: 'var(--soft)', borderRadius: 6, padding: '8px 10px' }}>
                    <div style={{ font: `600 15px ${FONT}`, ...TABULAR }}>{s.v}</div>
                    <div style={{ fontSize: 11, color: 'var(--n2)' }}>{s.k}</div>
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 'auto' }}>
                {a.tags.map((tg) => (
                  <span key={tg} style={{ font: `500 11px ${FONT}`, ...TABULAR, border: '1px solid var(--line)', borderRadius: 4, padding: '2px 6px', color: 'var(--n1)' }}>
                    {tg}
                  </span>
                ))}
              </div>
              <button
                type="button"
                onClick={() => C.go('topo', { sel: a.id })}
                style={{ alignSelf: 'flex-start', background: 'transparent', border: 'none', padding: 0, font: `700 13px ${FONT}`, color: 'var(--ink)', textDecoration: 'underline', textUnderlineOffset: 4, cursor: 'pointer' }}
              >
                Open in topology
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
