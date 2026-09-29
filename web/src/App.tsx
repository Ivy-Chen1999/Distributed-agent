import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { api, describeError, getToken, onUnauthorized, setToken } from './api';
import { Ctx, type ChatMessage, type ConsoleCtx, type DetailTab, type Staged } from './ctx';
import { BLUE, FONT, ICON, LABEL, MOON, PULSE, SCREENS, SUN, TH, TITLES, fmtS, type ScreenId } from './design';
import { useLoad, useNow, useRunData } from './hooks';
import { findingIndex } from './model/dossier';
import { shortRunId } from './model/overview';
import { RUN_ST, runStatusLabel, type RunView } from './model/pipeline';
import { regulationSub, regulationTitle, scenarioName } from './model/scenario';
import { buildTrace, isFinished, liveClock } from './model/trace';
import type { GroupMode } from './model/dossier';
import type { RunDetail, RunEvent } from './types';
import { HButton } from './components/ui';
import { RunPicker, SourcePanel, Toast, TokenModal } from './components/overlays';
import { Overview } from './screens/Overview';
import { Pipeline } from './screens/Pipeline';
import { Detail } from './screens/Detail';
import { Agents } from './screens/Agents';
import { Topology } from './screens/Topology';
import { Ask } from './screens/Ask';
import { Settings, overridesFrom } from './screens/Settings';

export const DEFAULT_SCENARIO = 'eval_sme_impacts';
const THEME_KEY = 'womm.theme';
const FALLBACK_EXPERTS = ['legal', 'fiscal', 'stakeholder'];

function readTheme(): 'light' | 'dark' {
  try {
    return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

/** Expert ids in graph order: from the run's events, else the run's node map, else the system. */
export function expertsFor(events: RunEvent[], run: RunDetail | null, systemExperts: string[] | undefined): string[] {
  const seen: string[] = [];
  const add = (node: string) => {
    if (node.startsWith('expert_')) {
      const id = node.slice(7);
      if (!seen.includes(id)) seen.push(id);
    }
  };
  [...events].sort((a, b) => a.seq - b.seq).forEach((e) => add(e.node));
  const dispatched = events.find((e) => e.node === 'router' && e.payload?.dispatched)?.payload.dispatched;
  if (dispatched?.length) return [...dispatched, ...seen.filter((s) => !dispatched.includes(s))];
  if (seen.length) return seen;
  Object.keys(run?.nodes ?? {}).forEach(add);
  if (seen.length) return seen;
  return systemExperts?.length ? systemExperts : FALLBACK_EXPERTS;
}

export function App() {
  const [token, setTok] = useState<string | null>(() => getToken());
  const [expired, setExpired] = useState(false);
  useEffect(
    () =>
      onUnauthorized(() => {
        setTok(null);
        setExpired(true);
      }),
    [],
  );
  const authed = !!token;

  const [theme, setTheme] = useState<'light' | 'dark'>(readTheme);
  const t = TH[theme];
  useEffect(() => {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
    document.body.style.background = t.bg;
  }, [theme, t.bg]);

  const [screen, setScreen] = useState<ScreenId>('overview');
  const [sel, setSel] = useState('legal');
  const [tab, setTab] = useState<DetailTab>('impacts');
  const [group, setGroup] = useState<GroupMode>('area');
  const [openImp, setOpenImp] = useState<string | null>(null);
  const [focusNonce, setFocusNonce] = useState(0);
  const [src, setSrc] = useState<{ sourceId: string; quote: string } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flash = useCallback((text: string, ms = 1600) => {
    clearTimeout(toastTimer.current);
    setToast(text);
    toastTimer.current = setTimeout(() => setToast(null), ms);
  }, []);
  useEffect(() => () => clearTimeout(toastTimer.current), []);

  const [system, reloadSystem] = useLoad(authed ? `system:${token}` : null, api.system);
  const [scenarios] = useLoad(authed ? `scenarios:${token}` : null, api.scenarios);
  const [runs, reloadRuns] = useLoad(authed ? `runs:${token}` : null, () => api.runs(20));

  const [pickedRun, setPickedRun] = useState<string | null>(null);
  const runId = pickedRun ?? runs.data?.runs?.[0]?.run_id ?? null;
  const runData = useRunData(runId, authed, () => reloadRuns());
  const run = runData.run;

  const scenarioId = run?.scenario_id ?? runs.data?.runs?.find((r) => r.run_id === runId)?.scenario_id ?? DEFAULT_SCENARIO;
  const scenario = scenarios.data?.find((s) => s.scenario_id === scenarioId) ?? null;
  const [sources] = useLoad(authed ? `sources:${scenarioId}:${token}` : null, () => api.sources(scenarioId));

  const systemExperts = system.data?.experts.map((e) => e.id);
  const experts = useMemo(() => expertsFor(runData.events, run, systemExperts), [runData.events, run, systemExperts?.join(',')]);
  const trace = useMemo(() => buildTrace({ events: runData.events, experts, run }), [runData.events, experts, run]);

  // Replay of a finished run, driven by event timestamps.
  const [speed, setSpeed] = useState(3);
  const [replay, setReplay] = useState<{ active: boolean; clock: number }>({ active: false, clock: 0 });
  useEffect(() => setReplay({ active: false, clock: 0 }), [runId]);
  useEffect(() => {
    if (!replay.active) return;
    const iv = setInterval(() => {
      setReplay((r) => {
        const c = Math.min(trace.end, r.clock + speed * 1.5);
        return c >= trace.end ? { active: false, clock: trace.end } : { active: true, clock: c };
      });
    }, 100);
    return () => clearInterval(iv);
  }, [replay.active, speed, trace.end]);
  const startReplay = useCallback(() => {
    if (trace.finished) setReplay({ active: true, clock: 0 });
  }, [trace.finished]);
  const stopReplay = useCallback(() => setReplay({ active: false, clock: 0 }), []);

  const active = !!run && !isFinished(run.status);
  const now = useNow(active);
  const clock = replay.active ? replay.clock : trace.finished ? trace.end : liveClock(trace, now);
  const rv: RunView = { trace, clock, run, system: system.data ?? null, scenario, changesCount: sources.data?.changes.length ?? scenario?.provision_keys.length };

  const runLabel = replay.active
    ? 'Replaying'
    : run
      ? runStatusLabel(run.status, run.error_kind)
      : runId
        ? 'Loading'
        : runs.status === 'error'
          ? 'Runs unavailable'
          : 'No runs yet';
  const runDot = replay.active || active ? BLUE : run ? (RUN_ST[run.status]?.c ?? '#8A9699') : '#8A9699';
  const running = replay.active || active;
  const clockText = fmtS(clock);

  // Chat, per run.
  const [chats, setChats] = useState<Record<string, ChatMessage[]>>({});
  const [thinking, setThinking] = useState(false);
  const greeting: ChatMessage = useMemo(() => {
    const d = run?.dossier;
    if (!run || !d) return { bot: true, text: run ? "This run has no dossier yet. Ask WOMM answers from a finished run's dossier." : 'No run selected.' };
    const g = run.grounding;
    const quotes = g ? (g.passed === g.total ? `all ${g.total} evidence quotes found verbatim` : `${g.passed} of ${g.total} evidence quotes found verbatim`) : 'grounding not reported';
    return { bot: true, text: `The ${scenarioName(run.scenario_id)} run finished in ${fmtS(trace.end)}: ${d.impacts.length} impacts, ${quotes}. Ask about any actor, provision or finding.` };
  }, [run, trace.end]);
  const chat = runId ? [greeting, ...(chats[runId] ?? [])] : [greeting];
  const ask = useCallback(
    async (q: string) => {
      if (!runId || thinking) return;
      const push = (m: ChatMessage) => setChats((c) => ({ ...c, [runId]: [...(c[runId] ?? []), m] }));
      push({ bot: false, text: q });
      setThinking(true);
      try {
        const a = await api.ask(runId, q);
        push({ bot: true, text: a.answer, cites: a.cites ?? [], covered: a.covered });
      } catch (e) {
        push({ bot: true, text: `Could not answer: ${describeError(e).replace(/^could not answer:\s*/i, '')}`, error: true });
      } finally {
        setThinking(false);
      }
    },
    [runId, thinking],
  );

  const openImpact = useCallback(
    (id: string) => {
      const d = run?.dossier;
      const impactId = id.startsWith('f_') ? findingIndex(d).get(id) : id;
      if (impactId && d?.impacts.some((i) => i.impact_id === impactId)) {
        setScreen('detail');
        setTab('impacts');
        setOpenImp(impactId);
        setFocusNonce((n) => n + 1);
      } else {
        flash(`${id} is not in this run's dossier`);
      }
    },
    [run, flash],
  );

  const go = useCallback((s: ScreenId, extra?: { tab?: DetailTab; sel?: string; openImp?: string | null }) => {
    setScreen(s);
    if (extra?.tab) setTab(extra.tab);
    if (extra?.sel) setSel(extra.sel);
    if (extra && 'openImp' in extra) setOpenImp(extra.openImp ?? null);
  }, []);

  useEffect(() => {
    if (!src) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setSrc(null);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [src]);

  // Settings staged locally and sent as overrides on the next run.
  const [staged, setStaged] = useState<Staged>({ backends: {}, routerMode: null });
  const overrides = overridesFrom(system.data, staged);
  const stagedText = overrides
    ? [...Object.entries(overrides.backends ?? {}).map(([r, b]) => `${r} → ${b}`), ...(overrides.router_mode ? [`router → ${overrides.router_mode}`] : [])].join(', ')
    : null;

  const [picker, setPicker] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const startRun = async (sid: string) => {
    setSubmitting(true);
    try {
      const res = await api.submit(sid, overrides);
      setPickedRun(res.run_id);
      setPicker(false);
      flash(`Run queued · ${shortRunId(res.run_id)}${res.system_version ? ` · ${res.system_version}` : ''}`, 2400);
      reloadRuns();
      if (screen === 'overview' || screen === 'chat') setScreen('pipeline');
    } catch (e) {
      flash(describeError(e), 4000);
    } finally {
      setSubmitting(false);
    }
  };

  const svLabel = run?.system_version ?? system.data?.version_id ?? '—';
  const copySv = () => {
    try {
      void navigator.clipboard?.writeText(svLabel);
    } catch {
      /* clipboard unavailable */
    }
    flash(`Copied ${svLabel}`);
  };

  const ctx: ConsoleCtx = {
    t,
    screen,
    go,
    system,
    reloadSystem,
    scenarios,
    runs,
    reloadRuns,
    runId,
    selectRun: setPickedRun,
    run,
    runLoading: runData.loading,
    runError: runData.error,
    reloadRun: runData.reload,
    scenario,
    sources,
    rv,
    runLabel,
    runDot,
    running,
    clockText,
    replaying: replay.active,
    speed,
    setSpeed,
    replay: startReplay,
    stopReplay,
    sel,
    setSel,
    tab,
    setTab,
    group,
    setGroup,
    openImp,
    setOpenImp,
    focusNonce,
    openImpact,
    openSource: (sourceId, quote) => setSrc({ sourceId, quote }),
    chat,
    thinking,
    ask,
    staged,
    setStaged,
    openPicker: () => setPicker(true),
    flash,
  };

  const vars = {
    '--bg': t.bg,
    '--card': t.card,
    '--soft': t.soft,
    '--line': t.line,
    '--ink': t.ink,
    '--n1': t.n1,
    '--n2': t.n2,
    '--acc': '#00C4CC',
    '--accInk': t.accInk,
    '--accSoft': t.accSoft,
    '--side': t.side,
    '--hl': t.hl,
  } as CSSProperties;

  const runBtnLabel = submitting ? 'Starting…' : active ? 'Running…' : run ? 'Re-run' : 'Run';

  return (
    <Ctx.Provider value={ctx}>
      <div
        data-screen-label="WOMM Console"
        data-theme={theme}
        style={{ ...vars, display: 'grid', gridTemplateColumns: '236px minmax(0,1fr)', minHeight: '100vh', background: 'var(--bg,#F3F6F6)', color: 'var(--ink,#0F0F0F)', fontFamily: "'Manrope',system-ui,sans-serif" }}
      >
        <aside style={{ background: 'var(--side,#0F0F0F)', color: '#FDFCFD', padding: '20px 16px', display: 'flex', flexDirection: 'column', gap: 22, position: 'sticky', top: 0, height: '100vh' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 4px' }}>
            <div style={{ width: 32, height: 32, borderRadius: 7, background: '#00C4CC', display: 'grid', placeItems: 'center', flex: 'none' }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0F0F0F" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="5" r="2" />
                <circle cx="5" cy="19" r="2" />
                <circle cx="19" cy="19" r="2" />
                <path d="M12 7v5M12 12l-5.5 5.5M12 12l5.5 5.5" />
              </svg>
            </div>
            <div>
              <div style={{ fontWeight: 800, fontSize: 17, letterSpacing: '-.01em' }}>WOMM</div>
              <div style={{ fontSize: 11, color: '#8E9798', marginTop: 1, letterSpacing: '.02em' }}>Regulatory impact · multi-agent</div>
            </div>
          </div>
          <div style={{ background: 'rgba(255,255,255,.06)', borderRadius: 10, padding: '11px 12px' }}>
            <div style={{ ...LABEL, color: '#8E9798' }}>Regulation</div>
            <div style={{ fontSize: 13, fontWeight: 700, marginTop: 4 }}>{regulationTitle(scenario)}</div>
            <div style={{ fontSize: 11, color: '#B7BDBE', marginTop: 2 }}>{regulationSub(scenario, sources.data?.changes) || '—'}</div>
          </div>
          <nav style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {SCREENS.map((k) => {
              const on = screen === k;
              return (
                <HButton
                  key={k}
                  onClick={() => go(k)}
                  aria-current={on ? 'page' : undefined}
                  style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px', borderRadius: 6, border: 'none', background: on ? 'rgba(255,255,255,.09)' : 'transparent', color: on ? '#FFFFFF' : '#B7BDBE', font: `600 14px ${FONT}`, cursor: 'pointer', textAlign: 'left' }}
                  hover={{ color: '#FFFFFF' }}
                >
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke={on ? '#00C4CC' : 'currentColor'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d={ICON[k]} />
                  </svg>
                  <span style={{ flex: 1 }}>{TITLES[k]}</span>
                  {k === 'pipeline' && active && <span style={{ font: `700 11px ${FONT}`, background: '#00C4CC', color: '#0F0F0F', borderRadius: 4, padding: '1px 6px' }}>live</span>}
                </HButton>
              );
            })}
          </nav>
          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: '#B7BDBE' }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: runDot }} />
              <span style={{ whiteSpace: 'nowrap' }}>
                {runLabel} · {clockText}
              </span>
            </div>
            <div style={{ fontSize: 11, color: '#7F8889', lineHeight: 1.5 }}>Every impact traces back to a provision, a quote and a source.</div>
          </div>
        </aside>

        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <header style={{ position: 'sticky', top: 0, zIndex: 5, display: 'flex', alignItems: 'center', gap: 14, padding: '14px 28px', borderBottom: '1px solid var(--line)', background: 'var(--card)', flexWrap: 'wrap' }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 20, letterSpacing: '-.01em' }}>{TITLES[screen]}</div>
              <div style={{ fontSize: 12, color: 'var(--n2)', marginTop: 1 }}>
                {scenarioName(scenarioId)} · <span style={{ fontFamily: FONT, fontVariantNumeric: 'tabular-nums' }}>{scenarioId}</span>
              </div>
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'var(--soft)', borderRadius: 4, padding: '5px 9px', font: `700 11px ${FONT}` }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: runDot, animation: running ? PULSE : 'none' }} />
                {runLabel}
              </span>
              <button
                type="button"
                onClick={copySv}
                title="Copy system version"
                style={{ flex: 'none', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 6, background: 'var(--soft)', border: 'none', borderRadius: 4, padding: '5px 9px', font: `600 11px ${FONT}`, fontVariantNumeric: 'tabular-nums', color: 'var(--n1)', cursor: 'pointer' }}
              >
                {svLabel}
                {overrides ? ' · staged' : ''}
              </button>
              <HButton
                onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
                title="Switch theme"
                style={{ flex: 'none', width: 34, height: 34, display: 'grid', placeItems: 'center', background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--ink)', cursor: 'pointer' }}
                hover={{ borderColor: '#00C4CC' }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d={theme === 'light' ? MOON : SUN} />
                </svg>
              </HButton>
              <HButton
                onClick={() => setPicker(true)}
                disabled={active || submitting || !authed}
                style={{ flex: 'none', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 8, background: '#00C4CC', color: '#0F0F0F', border: 'none', borderRadius: 4, padding: '9px 16px', font: `700 13px ${FONT}`, letterSpacing: '.02em', cursor: 'pointer' }}
                hover={{ background: '#33D3D9' }}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="#0F0F0F">
                  <path d="M6 4l14 8-14 8z" />
                </svg>
                {runBtnLabel}
              </HButton>
            </div>
          </header>

          <main style={{ padding: '24px 28px 48px', display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
            {screen === 'overview' && <Overview />}
            {screen === 'pipeline' && <Pipeline />}
            {screen === 'detail' && <Detail />}
            {screen === 'agents' && <Agents />}
            {screen === 'topo' && <Topology />}
            {screen === 'chat' && <Ask />}
            {screen === 'settings' && <Settings />}
          </main>
        </div>

        {src && <SourcePanel sourceId={src.sourceId} quote={src.quote} sources={sources} onClose={() => setSrc(null)} />}
        {picker && authed && <RunPicker scenarios={scenarios} initial={scenarioId} staged={stagedText} busy={submitting} onRun={startRun} onClose={() => setPicker(false)} />}
        {!authed && (
          <TokenModal
            expired={expired}
            onSave={(v) => {
              setToken(v);
              setExpired(false);
              setTok(v);
            }}
          />
        )}
        {toast && <Toast text={toast} />}
      </div>
    </Ctx.Provider>
  );
}
