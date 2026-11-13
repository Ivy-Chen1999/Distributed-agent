import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { api, describeError, getToken, onUnauthorized, setToken } from './api';
import { Ctx, type ActContext, type ConsoleCtx, type DetailTab, type Staged } from './ctx';
import { ACC, ACC_HOVER, BLUE, FONT, ICON, LABEL, MOON, PULSE, SUN, TH, fmtS, type ScreenId } from './design';
import { useLoad, useNarrow, useNow, useRunData } from './hooks';
import { findingIndex } from './model/dossier';
import { shortRunId } from './model/overview';
import { RUN_PAGE, nextRunLimit, type RunFilter } from './model/runFilter';
import { parseRoute, routeHash } from './model/route';
import { RUN_ST, runStatusLabel, type RunView } from './model/pipeline';
import { regulationSub, regulationTitle, scenarioName } from './model/scenario';
import { buildTrace, isFinished, liveClock } from './model/trace';
import type { GroupMode } from './model/dossier';
import type { RunDetail, RunEvent } from './types';
import { HButton, PRIMARY_BTN } from './components/ui';
import { RunPicker, SourcePanel, Toast, TokenModal } from './components/overlays';
import { Overview } from './screens/Overview';
import { Pipeline } from './screens/Pipeline';
import { Detail } from './screens/Detail';
import { Agents } from './screens/Agents';
import { Topology } from './screens/Topology';
import { Settings, overridesFrom } from './screens/Settings';
import { Evolution } from './screens/Evolution';
import { Act } from './screens/Act';
import { Regulations } from './screens/Regulations';
import type { ActTarget } from './model/regulations';
import { loadActor, saveActor } from './model/act';
import { ROLES, canSee, loadRole, roleOf, roleWith, saveRole, screenFor, screenTitle, type Role, type RoleId } from './model/roles';

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

  // A screen named in the URL opens the interface that has it (a shared #/act link lands in the
  // law, a #/agents link in the system); no screen opens the interface's home.
  const [role, setRoleState] = useState<Role>(() => {
    const want = parseRoute(window.location.hash).screen;
    const r = want ? roleWith(loadRole(), want) : loadRole();
    saveRole(r.id);
    return r;
  });
  const roleRef = useRef(role);
  roleRef.current = role;
  const [screen, setScreen] = useState<ScreenId>(() => parseRoute(window.location.hash).screen ?? roleRef.current.home);
  const [pickedRun, setPickedRun] = useState<string | null>(() => parseRoute(window.location.hash).run);
  const [runFilter, setRunFilter] = useState<RunFilter>(() => parseRoute(window.location.hash).filter);
  // Keep the URL hash and the screen, run and Overview filters in step, so a reload keeps them, the
  // back button works and a link opens the same view. The hash is read at event time rather than
  // from the event, so rapid changes cannot revert. Filters live here, not on Overview, so they
  // survive a visit to another screen.
  useEffect(() => {
    const onHash = () => {
      const h = parseRoute(window.location.hash);
      const r = h.screen ? roleWith(roleRef.current, h.screen) : roleRef.current;
      if (r !== roleRef.current) {
        saveRole(r.id);
        setRoleState(r);
      }
      const next = h.screen ?? r.home;
      setScreen(next);
      setPickedRun(h.run);
      if (next === 'overview') setRunFilter(h.filter);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => {
    const want = routeHash(screen, pickedRun, runFilter);
    const have = window.location.hash.replace(/^#/, '');
    if (have === want || (have === '' && want === `/${role.home}`)) return;
    // A hash that names this same view in another spelling (a hand-written link, other parameter
    // order) is tidied in place; a new history entry there would make the back button bounce.
    const cur = parseRoute(window.location.hash);
    if (cur.screen && routeHash(cur.screen, cur.run, cur.filter) === want) window.history.replaceState(window.history.state, '', `#${want}`);
    else window.location.hash = want;
  }, [screen, pickedRun, runFilter, role]);
  const narrow = useNarrow();
  const [drawer, setDrawer] = useState(false);
  useEffect(() => {
    if (!narrow) setDrawer(false);
  }, [narrow]);
  useEffect(() => {
    // Move keyboard focus into the drawer when it opens.
    if (drawer) document.querySelector<HTMLElement>('#womm-nav nav button[aria-current="page"]')?.focus();
  }, [drawer]);
  const [sel, setSel] = useState('legal');
  const [tab, setTab] = useState<DetailTab>('impacts');
  const [group, setGroup] = useState<GroupMode>('area');
  const [openImp, setOpenImp] = useState<string | null>(null);
  const [focusNonce, setFocusNonce] = useState(0);
  const [src, setSrc] = useState<{ sourceId: string; quote: string } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [actContext, setActContext] = useState<ActContext | null>(null);
  const [actTarget, setActTarget] = useState<ActTarget | null>(null);
  const [actor, setActorState] = useState(loadActor);
  const setActor = useCallback((a: string) => {
    setActorState(a);
    saveActor(a);
  }, []);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flash = useCallback((text: string, ms = 1600) => {
    clearTimeout(toastTimer.current);
    setToast(text);
    toastTimer.current = setTimeout(() => setToast(null), ms);
  }, []);
  useEffect(() => () => clearTimeout(toastTimer.current), []);

  const [system, reloadSystem] = useLoad(authed ? `system:${token}` : null, api.system);
  const [scenarios] = useLoad(authed ? `scenarios:${token}` : null, api.scenarios);
  const [runLimit, setRunLimit] = useState(RUN_PAGE);
  const showMoreRuns = useCallback(() => setRunLimit(nextRunLimit), []);
  const [runs, reloadRuns] = useLoad(authed ? `runs:${token}:${runLimit}` : null, () => api.runs(runLimit));

  const runId = pickedRun ?? runs.data?.runs?.[0]?.run_id ?? null;
  const runData = useRunData(runId, authed, () => reloadRuns());
  const run = runData.run;

  const scenarioId = run?.scenario_id ?? runs.data?.runs?.find((r) => r.run_id === runId)?.scenario_id ?? DEFAULT_SCENARIO;
  const scenario = scenarios.data?.find((s) => s.scenario_id === scenarioId) ?? null;
  const [sources, reloadSources] = useLoad(authed ? `sources:${scenarioId}:${token}` : null, () => api.sources(scenarioId));

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
        // `speed` is a real-time multiple: at 8× a 6-minute run replays in 45 s.
        const c = Math.min(trace.end, r.clock + speed * 0.1);
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

  const screenRef = useRef(screen);
  screenRef.current = screen;
  const go = useCallback((s: ScreenId, extra?: { tab?: DetailTab; sel?: string; openImp?: string | null }) => {
    // A new screen starts at the top; a jump into an impact scrolls there after rendering.
    // A screen outside the current role opens the role home instead.
    s = screenFor(roleRef.current, s);
    if (s !== screenRef.current) window.scrollTo({ top: 0 });
    setScreen(s);
    setDrawer(false);
    if (extra?.tab) setTab(extra.tab);
    if (extra?.sel) setSel(extra.sel);
    if (extra && 'openImp' in extra) setOpenImp(extra.openImp ?? null);
  }, []);

  const setRole = useCallback((id: RoleId) => {
    const next = roleOf(id);
    saveRole(id);
    setRoleState(next);
    if (!canSee(next, screenRef.current)) {
      window.scrollTo({ top: 0 });
      setScreen(next.home);
    }
  }, []);

  useEffect(() => {
    if (!src) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setSrc(null);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [src]);

  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setDrawer(false);
      document.querySelector<HTMLElement>('button[aria-controls="womm-nav"]')?.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawer]);

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
      if (screen === 'overview' && canSee(role, 'pipeline')) setScreen('pipeline');
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
    runLimit,
    showMoreRuns,
    runFilter,
    setRunFilter,
    runId,
    pickedRunId: pickedRun,
    selectRun: setPickedRun,
    run,
    runLoading: runData.loading,
    runError: runData.error,
    reloadRun: runData.reload,
    scenario,
    sources,
    reloadSources,
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
    staged,
    setStaged,
    openPicker: () => role.canRun && setPicker(true),
    flash,
    actContext,
    setActContext,
    role,
    setRole,
    can: (s) => canSee(role, s),
    openAct: (target) => {
      setActTarget(target);
      go('act');
    },
    actTarget,
    clearActTarget: () => setActTarget(null),
    actor,
    setActor,
  };
  const act = screen === 'act' ? actContext : null;
  // Choosing a regulation: no run scenario or act is in context yet.
  const browsing = screen === 'regs';
  // The sidebar card: in the law, the regulation the user chose (kept across screens); in the
  // system, the selected run's scenario.
  const card =
    role.id === 'law'
      ? actContext
        ? { title: actContext.regulation, sub: actContext.versions }
        : { title: 'No regulation chosen', sub: 'Find one under Regulations' }
      : { title: regulationTitle(scenario), sub: regulationSub(scenario, sources.data?.changes) || '—' };

  const vars = {
    '--bg': t.bg,
    '--card': t.card,
    '--soft': t.soft,
    '--line': t.line,
    '--ink': t.ink,
    '--n1': t.n1,
    '--n2': t.n2,
    '--acc': ACC,
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
        style={{ ...vars, display: 'grid', gridTemplateColumns: narrow ? 'minmax(0,1fr)' : '236px minmax(0,1fr)', minHeight: '100vh', background: 'var(--bg,#F3F6F6)', color: 'var(--ink,#0F0F0F)', fontFamily: "'Manrope',system-ui,sans-serif" }}
      >
        {narrow && drawer && <div aria-hidden onClick={() => setDrawer(false)} style={{ position: 'fixed', inset: 0, zIndex: 19, background: 'rgba(15,15,15,.45)' }} />}
        <aside
          id="womm-nav"
          aria-label="Navigation"
          hidden={narrow && !drawer}
          style={{
            background: 'var(--side,#0F0F0F)', color: '#FDFCFD', padding: '20px 16px', display: narrow && !drawer ? 'none' : 'flex', flexDirection: 'column', gap: 22, top: 0, height: '100vh', overflowY: 'auto',
            ...(narrow ? { position: 'fixed', left: 0, zIndex: 20, width: 260, maxWidth: '85vw', boxShadow: '0 0 40px rgba(0,0,0,.35)', animation: 'wfade .2s ease-out' } : { position: 'sticky' }),
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 4px' }}>
            <div style={{ width: 32, height: 32, borderRadius: 7, background: ACC, display: 'grid', placeItems: 'center', flex: 'none' }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0F0F0F" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="5" r="2" />
                <circle cx="5" cy="19" r="2" />
                <circle cx="19" cy="19" r="2" />
                <path d="M12 7v5M12 12l-5.5 5.5M12 12l5.5 5.5" />
              </svg>
            </div>
            <div>
              <div style={{ fontWeight: 800, fontSize: 17, letterSpacing: '-.01em' }}>WOMM</div>
              <div style={{ fontSize: 12, color: '#8E9798', marginTop: 1, letterSpacing: '.02em' }}>Regulatory impact · multi-agent</div>
            </div>
          </div>
          <div role="group" aria-label="Interface" aria-describedby="womm-role-desc" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3, background: 'rgba(255,255,255,.06)', borderRadius: 8, padding: 3 }}>
              {ROLES.map((r) => {
                const on = r.id === role.id;
                return (
                  <button
                    key={r.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setRole(r.id)}
                    style={{ border: 'none', borderRadius: 6, padding: '7px 8px', background: on ? '#FDFCFD' : 'transparent', color: on ? '#0F0F0F' : '#B7BDBE', font: `700 13px ${FONT}`, cursor: 'pointer' }}
                  >
                    {r.name}
                  </button>
                );
              })}
            </div>
            <span id="womm-role-desc" style={{ fontSize: 11, color: '#B7BDBE', lineHeight: 1.45, padding: '0 4px' }}>
              {role.description}
            </span>
          </div>
          <div style={{ background: 'rgba(255,255,255,.06)', borderRadius: 10, padding: '11px 12px' }}>
            <div style={{ ...LABEL, color: '#8E9798' }}>Regulation</div>
            <div style={{ fontSize: 13, fontWeight: 700, marginTop: 4 }}>{card.title}</div>
            <div style={{ fontSize: 12, color: '#B7BDBE', marginTop: 2 }}>{card.sub}</div>
          </div>
          <nav aria-label="Screens" style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {role.screens.map((k) => {
              const on = screen === k;
              return (
                <HButton
                  key={k}
                  onClick={() => go(k)}
                  aria-current={on ? 'page' : undefined}
                  style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px', borderRadius: 6, border: 'none', background: on ? 'rgba(255,255,255,.09)' : 'transparent', color: on ? '#FFFFFF' : '#B7BDBE', font: `600 14px ${FONT}`, cursor: 'pointer', textAlign: 'left' }}
                  hover={{ color: '#FFFFFF' }}
                >
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke={on ? ACC : 'currentColor'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d={ICON[k]} />
                  </svg>
                  <span style={{ flex: 1 }}>{screenTitle(role, k)}</span>
                  {k === 'pipeline' && active && <span style={{ font: `700 12px ${FONT}`, background: ACC, color: '#0F0F0F', borderRadius: 4, padding: '1px 6px' }}>live</span>}
                </HButton>
              );
            })}
          </nav>
          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {role.runMachinery && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#B7BDBE' }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: runDot }} />
                <span style={{ whiteSpace: 'nowrap' }}>
                  {runLabel} · {clockText}
                </span>
              </div>
            )}
            <div style={{ fontSize: 12, color: '#7F8889', lineHeight: 1.5 }}>Every impact traces back to a provision, a quote and a source.</div>
          </div>
        </aside>

        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <header style={{ position: 'sticky', top: 0, zIndex: 5, display: 'flex', alignItems: 'center', gap: narrow ? 10 : 14, padding: narrow ? '10px 16px' : '14px 28px', borderBottom: '1px solid var(--line)', background: 'var(--card)', flexWrap: 'wrap' }}>
            {narrow && (
              <button
                type="button"
                aria-label="Open navigation"
                aria-controls="womm-nav"
                aria-expanded={drawer}
                onClick={() => setDrawer(true)}
                style={{ flex: 'none', width: 36, height: 36, display: 'grid', placeItems: 'center', background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--ink)', cursor: 'pointer' }}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
            )}
            <div style={{ minWidth: 0, flex: narrow ? 1 : undefined }}>
              <div style={{ fontWeight: 600, fontSize: narrow ? 17 : 20, letterSpacing: '-.01em' }}>{screenTitle(role, screen)}</div>
              <div style={{ fontSize: 13, color: 'var(--n2)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: narrow ? 'nowrap' : undefined }}>
                {browsing ? (
                  'Acts WOMM has analysed: search by name, number or a EUR-Lex link'
                ) : act ? (
                  act.comparison
                ) : (
                  <>
                    {scenarioName(scenarioId)}
                    {!role.plainAnalysis && (
                      <>
                        {' · '}
                        <span style={{ fontFamily: FONT, fontVariantNumeric: 'tabular-nums' }}>{scenarioId}</span>
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', ...(narrow ? { width: '100%', marginLeft: 0 } : {}) }}>
              {role.runMachinery && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'var(--soft)', borderRadius: 4, padding: '5px 9px', font: `700 12px ${FONT}` }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: runDot, animation: running ? PULSE : 'none' }} />
                  {runLabel}
                </span>
              )}
              {role.tooling && (
              <button
                type="button"
                onClick={copySv}
                title="Copy system version"
                style={{ flex: 'none', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 6, background: 'var(--soft)', border: 'none', borderRadius: 4, padding: '5px 9px', font: `600 12px ${FONT}`, fontVariantNumeric: 'tabular-nums', color: 'var(--n1)', cursor: 'pointer' }}
              >
                {svLabel}
                {overrides ? ' · staged' : ''}
              </button>
              )}
              <HButton
                onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
                title="Switch theme"
                style={{ flex: 'none', width: 34, height: 34, display: 'grid', placeItems: 'center', background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--ink)', cursor: 'pointer' }}
                hover={{ borderColor: ACC }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d={theme === 'light' ? MOON : SUN} />
                </svg>
              </HButton>
              {role.runMachinery && role.canRun && (
              <HButton
                onClick={() => setPicker(true)}
                disabled={active || submitting || !authed}
                style={PRIMARY_BTN}
                hover={{ background: ACC_HOVER }}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="#0F0F0F">
                  <path d="M6 4l14 8-14 8z" />
                </svg>
                {runBtnLabel}
              </HButton>
              )}
            </div>
          </header>

          <main style={{ padding: narrow ? '16px 16px 40px' : '24px 28px 48px', display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
            {screen === 'overview' && <Overview />}
            {screen === 'regs' && <Regulations />}
            {screen === 'act' && <Act />}
            {screen === 'pipeline' && <Pipeline />}
            {screen === 'detail' && <Detail />}
            {screen === 'agents' && <Agents />}
            {screen === 'topo' && <Topology />}
            {screen === 'evolution' && <Evolution />}
            {screen === 'settings' && <Settings />}
          </main>
        </div>

        {src && <SourcePanel sourceId={src.sourceId} quote={src.quote} sources={sources} runSources={run?.citable_sources} onClose={() => setSrc(null)} />}
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
