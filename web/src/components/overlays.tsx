import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ACC, FONT, TABULAR } from '../design';
import type { Loadable } from '../hooks';
import type { Scenario, ScenarioSources } from '../types';
import { highlightQuote } from '../model/quote';
import { scenarioKind, scenarioName } from '../model/scenario';
import { HButton, HInput, PRIMARY_BTN } from './ui';

const CLOSE = 'M18 6L6 18M6 6l12 12';

function CloseBtn({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" aria-label="Close" onClick={onClick} style={{ marginLeft: 'auto', width: 32, height: 32, border: '1px solid var(--line)', borderRadius: 4, background: 'transparent', color: 'var(--ink)', cursor: 'pointer', display: 'grid', placeItems: 'center' }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
        <path d={CLOSE} />
      </svg>
    </button>
  );
}

/** Right-hand source panel with the evidence quote highlighted in the source text. */
export function SourcePanel({ sourceId, quote, sources, onClose }: { sourceId: string; quote: string; sources: Loadable<ScenarioSources>; onClose: () => void }) {
  const doc = sources.data?.sources.find((s) => s.source_id === sourceId);
  const hl = doc ? highlightQuote(doc.text, quote) : null;
  const markRef = useRef<HTMLElement>(null);
  useEffect(() => {
    markRef.current?.scrollIntoView?.({ block: 'center' });
  }, [hl?.found, sourceId, quote]);
  let body: ReactNode;
  let note: string;
  if (doc && hl) {
    note = hl.found ? 'Quote matched word for word in the source' : 'Quote not found in this source text; showing the full text';
    body = (
      <div style={{ fontSize: 14, lineHeight: 1.7, whiteSpace: 'pre-wrap', borderTop: '1px solid var(--line)', paddingTop: 14 }}>
        {hl.before}
        {hl.found && <mark ref={markRef} style={{ background: 'var(--hl)', color: 'var(--ink)', padding: '1px 2px', borderRadius: 2, textDecoration: 'underline', textDecorationColor: ACC, textUnderlineOffset: 3 }}>{hl.quote}</mark>}
        {hl.after}
      </div>
    );
  } else if (sources.status === 'loading' || sources.status === 'idle') {
    note = 'Loading source text…';
    body = <div style={{ height: 14, borderRadius: 3, background: 'var(--soft)' }} />;
  } else {
    note = sources.status === 'error' ? `Could not load sources: ${sources.error}` : 'This source is not part of the scenario.';
    body = (
      <div style={{ fontSize: 14, lineHeight: 1.7, whiteSpace: 'pre-wrap', borderTop: '1px solid var(--line)', paddingTop: 14 }}>
        <mark style={{ background: 'var(--hl)', color: 'var(--ink)', padding: '1px 2px', borderRadius: 2 }}>{quote}</mark>
      </div>
    );
  }
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(10,16,17,.5)', zIndex: 40, display: 'flex', justifyContent: 'flex-end' }}>
      <div
        role="dialog"
        aria-label="Source"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 480, maxWidth: '92vw', height: '100%', background: 'var(--card)', color: 'var(--ink)', overflowY: 'auto', padding: '22px 24px 32px', animation: 'wfade .3s cubic-bezier(.2,0,0,1)', display: 'flex', flexDirection: 'column', gap: 14 }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ font: `600 12px ${FONT}`, ...TABULAR, color: 'var(--n1)' }}>{sourceId}</span>
          <CloseBtn onClick={onClose} />
        </div>
        <div style={{ fontSize: 20, fontWeight: 600, lineHeight: 1.3 }}>{doc?.title ?? sourceId}</div>
        <div style={{ fontSize: 12, color: 'var(--n2)' }}>{note}</div>
        {body}
      </div>
    </div>
  );
}

export function Toast({ text }: { text: string }) {
  return (
    <div role="status" style={{ position: 'fixed', bottom: 20, left: '50%', transform: 'translateX(-50%)', background: '#0F0F0F', color: '#FDFCFD', borderRadius: 6, padding: '9px 14px', font: `600 13px ${FONT}`, boxShadow: '0 4px 12px rgba(0,0,0,.18)', zIndex: 50, maxWidth: '90vw' }}>
      {text}
    </div>
  );
}

function Modal({ children, onClose, label }: { children: ReactNode; onClose?: () => void; label: string }) {
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(10,16,17,.5)', zIndex: 45, display: 'grid', placeItems: 'center', padding: 16 }}>
      <div role="dialog" aria-label={label} onClick={(e) => e.stopPropagation()} style={{ width: 420, maxWidth: '100%', background: 'var(--card)', color: 'var(--ink)', border: '1px solid var(--line)', borderRadius: 12, padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 12, animation: 'wfade .3s cubic-bezier(.2,0,0,1)', boxShadow: '0 4px 12px rgba(0,0,0,.18)' }}>
        {children}
      </div>
    </div>
  );
}

const INPUT = { flex: 1, minWidth: 0, height: 44, border: '1px solid var(--ink)', borderRadius: 4, padding: '0 14px', font: `400 14px ${FONT}`, background: 'var(--card)', color: 'var(--ink)', outline: 'none' } as const;

/** Bearer-token prompt; the token is kept in localStorage `womm.token`. */
export function TokenModal({ onSave, expired }: { onSave: (token: string) => void; expired: boolean }) {
  const [v, setV] = useState('');
  const save = () => v.trim() && onSave(v.trim());
  return (
    <Modal label="Connect to the WOMM API">
      <div style={{ font: `700 11px/1.4 ${FONT}`, letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--accInk)' }}>WOMM API</div>
      <div style={{ fontSize: 20, fontWeight: 600, lineHeight: 1.3 }}>{expired ? 'Token rejected' : 'Connect to the API'}</div>
      <div style={{ fontSize: 13, color: 'var(--n1)', lineHeight: 1.5 }}>
        {expired ? 'The API answered 401. Paste a valid token to continue.' : 'Paste the bearer token (WOMM_API_TOKEN). It stays in this browser only.'}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <HInput
          type="password"
          aria-label="API token"
          autoFocus
          value={v}
          onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && save()}
          placeholder="Bearer token"
          style={INPUT}
          focusStyle={{ border: `2px solid ${ACC}` }}
        />
        <HButton onClick={save} style={{ height: 44, background: ACC, color: '#0F0F0F', border: 'none', borderRadius: 4, padding: '0 18px', font: `700 13px ${FONT}`, cursor: 'pointer' }} hover={{ background: '#33D3D9' }}>
          Connect
        </HButton>
      </div>
    </Modal>
  );
}

/** Scenario picker shown by the Run button. */
export function RunPicker({
  scenarios,
  initial,
  staged,
  busy,
  onRun,
  onClose,
}: {
  scenarios: Loadable<Scenario[]>;
  initial: string;
  staged: string | null;
  busy: boolean;
  onRun: (scenarioId: string) => void;
  onClose: () => void;
}) {
  const list = scenarios.data ?? [];
  const [pick, setPick] = useState(list.find((s) => s.scenario_id === initial)?.scenario_id ?? list[0]?.scenario_id ?? initial);
  return (
    <Modal label="Start a run" onClose={onClose}>
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <div style={{ fontSize: 20, fontWeight: 600, lineHeight: 1.3 }}>Start a run</div>
        <CloseBtn onClick={onClose} />
      </div>
      <div style={{ fontSize: 13, color: 'var(--n1)', lineHeight: 1.5 }}>Pick a scenario. The planner, experts and synthesis run on the current system version.</div>
      {scenarios.status === 'loading' && <div style={{ fontSize: 13, color: 'var(--n2)' }}>Loading scenarios…</div>}
      {scenarios.status === 'error' && <div style={{ fontSize: 13, color: '#E5484D' }}>Could not load scenarios: {scenarios.error}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', border: '1px solid var(--line)', borderRadius: 10, overflow: 'hidden' }} role="radiogroup">
        {list.map((s, i) => {
          const on = s.scenario_id === pick;
          return (
            <HButton
              key={s.scenario_id}
              role="radio"
              aria-checked={on}
              onClick={() => setPick(s.scenario_id)}
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px', border: 'none', borderTop: i ? '1px solid var(--line)' : 'none', background: on ? 'var(--accSoft)' : 'transparent', color: 'var(--ink)', textAlign: 'left', cursor: 'pointer' }}
              hover={{ background: on ? 'var(--accSoft)' : 'var(--soft)' }}
            >
              <span style={{ width: 14, height: 14, borderRadius: '50%', border: `2px solid ${on ? ACC : 'var(--n2)'}`, background: on ? ACC : 'transparent', flex: 'none' }} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 14, fontWeight: 700 }}>{scenarioName(s.scenario_id)}</span>
                <span style={{ display: 'block', fontSize: 11, color: 'var(--n2)', ...TABULAR }}>
                  {s.scenario_id} · {scenarioKind(s)}
                </span>
              </span>
            </HButton>
          );
        })}
      </div>
      {staged && <div style={{ fontSize: 12, color: 'var(--n1)', lineHeight: 1.5 }}>With staged settings: {staged}. The run gets a new system version id.</div>}
      <HButton onClick={() => pick && onRun(pick)} disabled={busy || !pick} style={{ ...PRIMARY_BTN, alignSelf: 'flex-start', opacity: busy ? 0.7 : 1 }} hover={{ background: '#33D3D9' }}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="#0F0F0F">
          <path d="M6 4l14 8-14 8z" />
        </svg>
        {busy ? 'Starting…' : 'Run'}
      </HButton>
    </Modal>
  );
}
