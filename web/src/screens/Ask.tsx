import { useState } from 'react';
import { useConsole } from '../ctx';
import { ACC, CARD, FONT, LABEL } from '../design';
import { EmptyCard, HButton, HInput } from '../components/ui';
import { evidenceCount } from '../model/dossier';
import { shortRunId } from '../model/overview';

export const SUGGESTIONS = ['Who carries the penalty risk?', 'Show the disagreement', 'Which experts ran?', 'What about authorities?'];

export function Ask() {
  const C = useConsole();
  const [draft, setDraft] = useState('');
  if (!C.runId) return <EmptyCard title="No run to ask about" sub="Ask WOMM answers from one run's dossier. Start a run first." />;
  const d = C.run?.dossier;
  const g = C.run?.grounding;
  const send = (q: string) => {
    if (!q.trim() || C.thinking) return;
    C.ask(q.trim());
    setDraft('');
  };
  const canAsk = !!d;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,260px),1fr))', gap: 16, alignItems: 'start' }}>
      <div style={{ ...CARD, display: 'flex', flexDirection: 'column', minHeight: 560 }}>
        <div style={{ flex: 1, padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 14 }} aria-live="polite">
          {C.chat.map((m, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: m.bot ? 'flex-start' : 'flex-end', animation: 'wfade .3s cubic-bezier(.2,0,0,1)' }}>
              <div
                style={{
                  maxWidth: '78%',
                  background: m.bot ? 'var(--soft)' : ACC,
                  color: m.bot ? 'var(--ink)' : '#0F0F0F',
                  border: `1px solid ${m.bot ? (m.error ? '#E5484D' : 'var(--line)') : ACC}`,
                  borderRadius: 10,
                  padding: '11px 14px',
                }}
              >
                {m.bot && (
                  <div style={{ font: `700 11px ${FONT}`, letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--accInk)', marginBottom: 4 }}>
                    WOMM · {m.covered === false ? 'not covered by this dossier' : "from this run's dossier"}
                  </div>
                )}
                <div style={{ fontSize: 14, lineHeight: 1.55, whiteSpace: 'pre-wrap', textWrap: 'pretty' }}>{m.text}</div>
                {!!m.cites?.length && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                    {m.cites.map((c) => (
                      <HButton
                        key={c}
                        onClick={() => C.openImpact(c)}
                        style={{ border: '1px solid var(--line)', background: 'var(--card)', borderRadius: 4, padding: '3px 8px', font: `700 11px ${FONT}`, fontVariantNumeric: 'tabular-nums', color: 'var(--ink)', cursor: 'pointer' }}
                        hover={{ borderColor: ACC }}
                      >
                        {c}
                      </HButton>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          {C.thinking && <div style={{ fontSize: 13, color: 'var(--n2)' }}>Reading the dossier…</div>}
        </div>
        <div style={{ borderTop: '1px solid var(--line)', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {SUGGESTIONS.map((q) => (
              <HButton
                key={q}
                disabled={!canAsk}
                onClick={() => send(q)}
                style={{ border: '1px solid var(--line)', background: 'transparent', borderRadius: 50, padding: '6px 12px', font: `500 12px ${FONT}`, color: 'var(--ink)', cursor: canAsk ? 'pointer' : 'default', opacity: canAsk ? 1 : 0.5 }}
                hover={{ borderColor: ACC }}
              >
                {q}
              </HButton>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <HInput
              aria-label="Question"
              value={draft}
              disabled={!canAsk}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') send(draft);
              }}
              placeholder={canAsk ? 'Ask about an actor, a provision or a finding' : 'Available once the dossier is ready'}
              style={{ flex: 1, minWidth: 0, height: 44, border: '1px solid var(--ink)', borderRadius: 4, padding: '0 14px', font: `400 14px ${FONT}`, background: 'var(--card)', color: 'var(--ink)', outline: 'none' }}
              focusStyle={{ border: `2px solid ${ACC}` }}
            />
            <HButton
              onClick={() => send(draft)}
              disabled={!canAsk || C.thinking}
              style={{ height: 44, background: ACC, color: '#0F0F0F', border: 'none', borderRadius: 4, padding: '0 18px', font: `700 13px ${FONT}`, cursor: 'pointer' }}
              hover={{ background: '#33D3D9' }}
            >
              Ask
            </HButton>
          </div>
        </div>
      </div>
      <div style={{ ...CARD, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={LABEL}>Answering from</div>
        <div style={{ fontSize: 13, lineHeight: 1.5 }}>
          {shortRunId(C.runId)}
          {d ? ` · ${d.impacts.length} impacts · ${g?.passed ?? evidenceCount(d)} grounded quotes` : ' · dossier not ready'}
        </div>
        <div style={{ fontSize: 12, color: 'var(--n1)', lineHeight: 1.5 }}>Answers cite only this run. If the dossier doesn't cover something, WOMM says so instead of guessing.</div>
        <button
          type="button"
          onClick={C.openPicker}
          style={{ alignSelf: 'flex-start', background: 'transparent', border: '1px solid var(--ink)', borderRadius: 4, padding: '8px 12px', font: `700 12px ${FONT}`, color: 'var(--ink)', cursor: 'pointer' }}
        >
          Start a new run
        </button>
      </div>
    </div>
  );
}
