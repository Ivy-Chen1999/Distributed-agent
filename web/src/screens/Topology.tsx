import { useConsole } from '../ctx';
import { ACC, CARD, FONT, LABEL, PULSE, RED, TABULAR, ag } from '../design';
import { Dot, HButton } from '../components/ui';
import { edges, hostLabel, isIsolated, selInfo, topoPositions } from '../model/pipeline';
import { statusAt } from '../model/trace';
import { ST } from '../design';

export function Topology() {
  const C = useConsole();
  const rv = C.rv;
  const t = C.t;
  const ex = rv.trace.experts;
  const POS = topoPositions(ex);
  const E = edges(ex);
  const selId = POS[C.sel] ? C.sel : (ex[0] ?? 'planner');
  const sel = selInfo(selId, rv);
  const system = C.system.data ?? null;

  const chip = (id: string) => (
    <button key={id} type="button" onClick={() => C.setSel(id)} style={{ border: '1px solid var(--line)', background: 'var(--soft)', borderRadius: 4, padding: '3px 8px', font: `700 11px ${FONT}`, color: 'var(--ink)', cursor: 'pointer' }}>
      {ag(id).n}
    </button>
  );

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start' }}>
      <div style={{ flex: '1 1 560px', minWidth: 0, ...CARD, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px 14px', padding: '14px 18px', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
          <div style={{ ...LABEL, whiteSpace: 'nowrap' }}>Process topology</div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 14, fontSize: 12, color: 'var(--n1)', flexWrap: 'wrap' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 18, height: 2, background: ACC }} />
              data flow
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 18, borderTop: '2px dashed var(--n2)' }} />
              shadow decision
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 10, height: 10, border: '1px dashed var(--n2)', borderRadius: 3 }} />
              isolated subprocess
            </span>
          </div>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <div style={{ position: 'relative', height: 480, minWidth: 820 }}>
            <svg viewBox="0 0 1000 480" preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
              {E.map(([a, b], i) => {
                const A = POS[a];
                const B = POS[b];
                const act = selId === a || selId === b;
                const live = statusAt(rv.trace, a, rv.clock) !== 'queued';
                const dash = a === 'router' || (b === 'synthesis' && a === 'board');
                const x1 = A[0] * 10;
                const x2 = B[0] * 10;
                const mx = (x1 + x2) / 2;
                return (
                  <path
                    key={i}
                    d={`M${x1} ${A[1]} C${mx} ${A[1]} ${mx} ${B[1]} ${x2} ${B[1]}`}
                    fill="none"
                    stroke={act ? ACC : live ? t.n2 : t.line}
                    strokeOpacity={act ? 1 : 0.55}
                    strokeWidth={act ? 2.2 : 1.4}
                    strokeDasharray={dash ? '5 4' : 'none'}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
            </svg>
            {Object.keys(POS).map((id) => {
              const s = statusAt(rv.trace, id, rv.clock);
              const iso = isIsolated(id, system, rv);
              const isSel = selId === id;
              return (
                <HButton
                  key={id}
                  data-topo={id}
                  onClick={() => C.setSel(id)}
                  style={{
                    position: 'absolute',
                    left: `${POS[id][0]}%`,
                    top: `${POS[id][1]}px`,
                    transform: 'translate(-50%,-50%)',
                    width: 112,
                    background: 'var(--card)',
                    border: isSel ? `2px solid ${ACC}` : s === 'failed' ? `1px solid ${RED}` : iso ? '1px dashed var(--n2)' : '1px solid var(--line)',
                    borderRadius: 10,
                    padding: '8px 10px',
                    textAlign: 'left',
                    cursor: 'pointer',
                    color: 'var(--ink)',
                    boxShadow: isSel ? '0 4px 12px rgba(0,0,0,.08)' : 'none',
                  }}
                  hover={{ borderColor: ACC }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Dot c={ag(id).c} size={8} radius={2} />
                    <span style={{ fontSize: 12, fontWeight: 700, lineHeight: 1.2 }}>{ag(id).n}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 5, font: `600 11px ${FONT}`, color: 'var(--n1)' }}>
                    <Dot c={ST[s].c} size={6} anim={s === 'running' ? PULSE : 'none'} />
                    {hostLabel(id, system, rv)}
                  </div>
                </HButton>
              );
            })}
          </div>
        </div>
      </div>
      <div style={{ flex: '1 1 280px', maxWidth: '100%', ...CARD, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={LABEL}>Inspector</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Dot c={sel.c} size={10} radius={3} />
          <span style={{ fontSize: 17, fontWeight: 600 }}>{sel.name}</span>
        </div>
        <div style={{ fontSize: 13, color: 'var(--n1)', lineHeight: 1.5 }}>{sel.desc}</div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {sel.facts.map((f) => (
            <div key={f.k} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, padding: '7px 0', borderBottom: '1px solid var(--line)', fontSize: 12 }}>
              <span style={{ color: 'var(--n2)' }}>{f.k}</span>
              <span style={{ fontFamily: FONT, ...TABULAR, textAlign: 'right', wordBreak: 'break-all' }}>{f.v}</span>
            </div>
          ))}
        </div>
        <div>
          <div style={{ fontSize: 11, color: 'var(--n2)', marginBottom: 6 }}>Receives from</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>{sel.ins.length ? sel.ins.map(chip) : <span style={{ fontSize: 12, color: 'var(--n2)' }}>—</span>}</div>
          <div style={{ fontSize: 11, color: 'var(--n2)', margin: '10px 0 6px' }}>Sends to</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>{sel.outs.length ? sel.outs.map(chip) : <span style={{ fontSize: 12, color: 'var(--n2)' }}>—</span>}</div>
        </div>
      </div>
    </div>
  );
}
