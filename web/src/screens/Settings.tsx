import { useConsole, type Staged } from '../ctx';
import { CARD, FONT, GREEN, RED, TABULAR } from '../design';
import { ErrorCard, LoadingCard, OUTLINE_BTN, Seg } from '../components/ui';
import { routerNote } from '../model/pipeline';
import { humanize } from '../model/scenario';
import type { RunOverrides, SystemInfo } from '../types';

export const BACKEND_ROLES: [string, string][] = [
  ['planner', 'Planner'],
  ['experts', 'Experts'],
  ['synthesis', 'Synthesis'],
  ['judge', 'Coverage judge'],
];

const CHECK_LABELS: Record<string, string> = {
  auth_preflight: 'Claude CLI is logged in',
  init_event: 'Init event received',
  no_tools: 'No tools',
  no_mcp_servers: 'No MCP servers',
  no_memory: 'No memory or CLAUDE.md',
  no_user_plugins: 'No user plugins or hooks',
  no_skills: 'No skills',
  subscription_auth: 'Subscription auth, no API key',
  canary_run_succeeded: 'Canary run succeeded',
  canary_absent: 'No leaked user instructions',
};

export const checkLabel = (k: string) => CHECK_LABELS[k] ?? humanize(k);

/** Current backend of a console role ('experts' = all expert roles; null if they differ). */
export function currentBackend(system: SystemInfo, role: string): string | null {
  if (role !== 'experts') return system.roles[role]?.backend ?? null;
  const bs = [...new Set(system.experts.map((e) => system.roles[`expert:${e.id}`]?.backend ?? e.backend))];
  return bs.length === 1 ? bs[0] : null;
}

/** Overrides for POST /runs from what is staged locally (undefined when nothing differs). */
export function overridesFrom(system: SystemInfo | null | undefined, staged: Staged): RunOverrides | undefined {
  if (!system) return undefined;
  const backends: Record<string, string> = {};
  for (const [role] of BACKEND_ROLES) {
    const v = staged.backends[role];
    if (!v || v === currentBackend(system, role)) continue;
    if (role === 'experts') for (const e of system.experts) backends[`expert:${e.id}`] = v;
    else backends[role] = v;
  }
  const out: RunOverrides = {};
  if (Object.keys(backends).length) out.backends = backends;
  if (staged.routerMode && staged.routerMode !== system.router.mode) out.router_mode = staged.routerMode;
  return Object.keys(out).length ? out : undefined;
}

export function Settings() {
  const C = useConsole();
  if (C.system.status === 'loading' && !C.system.data) return <LoadingCard title="Loading system version" />;
  if (C.system.status === 'error' && !C.system.data) return <ErrorCard title="Could not load the system version" message={C.system.error} onRetry={C.reloadSystem} />;
  const s = C.system.data;
  if (!s) return <LoadingCard title="Loading system version" />;

  const staged = C.staged;
  const overrides = overridesFrom(s, staged);
  const file = s.source_path ? (s.source_path.split('/').pop() ?? s.source_path) : '—';
  const cli = s.code?.claude_cli_version?.replace(/\s*\(Claude Code\)\s*$/, '');
  const svFacts = [
    { k: 'id', v: s.version_id },
    { k: 'file', v: file },
    { k: 'git', v: s.code?.git_sha ? `${s.code.git_sha} (${s.code.dirty ? 'dirty' : 'clean'})` : '—' },
    { k: 'claude CLI', v: cli || '—' },
    { k: 'max parallel calls', v: s.max_parallel_llm_calls != null ? String(s.max_parallel_llm_calls) : '—' },
  ];
  const opts = (role: string) => {
    const cur = currentBackend(s, role);
    return [...new Set([...s.available_backends, ...(cur ? [cur] : [])])];
  };
  const routerVal = staged.routerMode ?? (s.router.mode === 'off' ? null : s.router.mode);
  const checks = Object.entries(s.self_check?.checks ?? {});
  const passed = checks.filter(([, ok]) => ok).length;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,360px),1fr))', gap: 16, alignItems: 'start' }}>
      <div style={{ ...CARD, padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>System version</div>
        <div style={{ fontSize: 13, color: 'var(--n1)', lineHeight: 1.5 }}>
          A content hash of {s.source_path ? <span style={{ fontFamily: FONT, ...TABULAR }}>{s.source_path}</span> : 'the version spec'} and every prompt it references.
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {svFacts.map((f) => (
            <div key={f.k} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--line)', fontSize: 13 }}>
              <span style={{ color: 'var(--n2)' }}>{f.k}</span>
              <span style={{ fontFamily: FONT, ...TABULAR, whiteSpace: 'nowrap' }}>{f.v}</span>
            </div>
          ))}
        </div>
        {!s.backend_ready && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, border: `1px solid ${RED}`, borderRadius: 8, padding: '10px 12px' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: RED, flex: 'none' }} />
            <span style={{ fontSize: 13, lineHeight: 1.5 }}>Backend not ready{s.backend_error ? `: ${s.backend_error}` : ''}</span>
          </div>
        )}
      </div>

      <div style={{ ...CARD, padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>LLM backend per role</div>
        {BACKEND_ROLES.map(([k, l]) => (
          <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ flex: 1, minWidth: 100, fontSize: 13, fontWeight: 600 }}>{l}</span>
            <Seg
              options={opts(k).map((v) => ({ v, label: v }))}
              value={staged.backends[k] ?? currentBackend(s, k)}
              onChange={(v) => C.setStaged({ ...staged, backends: { ...staged.backends, [k]: v } })}
              pad="5px 10px"
              font={`600 12px ${FONT}`}
              variant="ink"
            />
          </div>
        ))}
        <div style={{ fontSize: 12, color: 'var(--n2)', lineHeight: 1.5 }}>claude_code is for local development only. fake returns scripted outputs for tests.</div>
      </div>

      <div style={{ ...CARD, padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>Router</div>
        <Seg<'shadow' | 'active'>
          options={[
            { v: 'shadow', label: 'Shadow' },
            { v: 'active', label: 'Enforce' },
          ]}
          value={routerVal}
          onChange={(v) => C.setStaged({ ...staged, routerMode: v })}
          pad="7px 14px"
          variant="ink"
          style={{ alignSelf: 'flex-start' }}
        />
        <div style={{ fontSize: 13, color: 'var(--n1)', lineHeight: 1.5 }}>{routerNote(routerVal ?? s.router.mode)}</div>
      </div>

      <div style={{ ...CARD, padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 16, fontWeight: 600 }}>Isolation self-check</span>
          <span style={{ marginLeft: 'auto', font: `700 11px ${FONT}`, display: 'inline-flex', alignItems: 'center', gap: 5 }}>{s.self_check ? `${passed} of ${checks.length} passed` : 'not run'}</span>
        </div>
        {!s.self_check && <div style={{ fontSize: 13, color: 'var(--n1)', lineHeight: 1.5, padding: '9px 0' }}>The self-check runs only when a role uses the claude_code backend.</div>}
        {checks.map(([k, ok]) => (
          <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0', borderBottom: '1px solid var(--line)', fontSize: 14 }}>
            <span style={{ flex: 1 }}>{checkLabel(k)}</span>
            <span style={{ font: `700 11px ${FONT}`, letterSpacing: '.06em', textTransform: 'uppercase', color: ok ? GREEN : RED, border: `1px solid ${ok ? GREEN : RED}`, borderRadius: 4, padding: '2px 7px' }}>{ok ? 'Pass' : 'Fail'}</span>
          </div>
        ))}
        {(s.self_check?.problems ?? []).map((p) => (
          <div key={p} style={{ fontSize: 12, color: RED, lineHeight: 1.5 }}>
            {p}
          </div>
        ))}
        <div style={{ fontSize: 12, color: 'var(--n2)' }}>The claude_code backend refuses to run if any check fails.</div>
      </div>

      {overrides && (
        <div style={{ ...CARD, padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', borderColor: '#00C4CC', gridColumn: '1 / -1' }} data-testid="staged">
          <div style={{ flex: 1, minWidth: 240 }}>
            <div style={{ fontSize: 14, fontWeight: 700 }}>Staged for the next run</div>
            <div style={{ fontSize: 13, color: 'var(--n1)', marginTop: 2, lineHeight: 1.5, fontVariantNumeric: 'tabular-nums' }}>
              {[
                ...Object.entries(overrides.backends ?? {}).map(([r, b]) => `${r} → ${b}`),
                ...(overrides.router_mode ? [`router → ${overrides.router_mode === 'active' ? 'enforce' : overrides.router_mode}`] : []),
              ].join(' · ')}
              . The run gets a derived system version, so its id will differ from {s.version_id}.
            </div>
          </div>
          <button type="button" onClick={() => C.setStaged({ backends: {}, routerMode: null })} style={OUTLINE_BTN}>
            Reset
          </button>
          <button type="button" onClick={C.openPicker} style={{ ...OUTLINE_BTN, background: 'var(--ink)', color: 'var(--card)' }}>
            Run with these settings
          </button>
        </div>
      )}
    </div>
  );
}
